"""
泛化对抗测试：user identity 提取不应只修复白名单 case，
而要对真实分布中的占位词攻击、真名变体、口语化自报、英文名、首字符攻击健壮。

每个 case 用全新 owner_id，避免交叉污染。
验证维度：
  (a) primary 唯一（不会出现重复 primary）
  (b) identity.name 终态正确（占位词被拦 / 真名写入）
  (c) 没有意外把真名误判成占位被拦（假阴性）
"""
import asyncio
import uuid
import json
import sys
from typing import Any

import asyncpg
import httpx

API = "http://localhost:8010/api/v1/memory/chat"
PG_DSN = "postgresql://memory:memory@127.0.0.1:5434/memory"


# ─────────────────────────────────────────────────────────────
# 测试 case 分类：
#  CAT-A 已知白名单（应被拦）—— 回归基线
#  CAT-B 白名单外占位词（当前修复的真实漏洞区）
#  CAT-C 真名（必须通过，不能被误拦）
#  CAT-D 口语化自报 / name_signal 正则覆盖
#  CAT-E 首字符 / 符号 / 变体（防绕过）
# ─────────────────────────────────────────────────────────────
CASES = [
    # CAT-A baseline（白名单内，应被拦）
    {"id": "A1", "utterance": "我叫用户", "expect": "placeholder",  "note": "硬白名单'用户'"},
    {"id": "A2", "utterance": "我叫 Anonymous", "expect": "placeholder", "note": "白名单英文"},

    # CAT-B 白名单外占位词（真实漏洞）
    {"id": "B1", "utterance": "我叫亲爱的",   "expect": "placeholder_or_none", "note": "'亲爱的'不在白名单"},
    {"id": "B2", "utterance": "我叫小可爱",   "expect": "placeholder_or_none", "note": "'小可爱'不在白名单"},
    {"id": "B3", "utterance": "我叫那个人",   "expect": "placeholder_or_none", "note": "'那个人'指代词"},
    {"id": "B4", "utterance": "我叫对方",     "expect": "placeholder_or_none", "note": "'对方'指代词"},
    {"id": "B5", "utterance": "我叫本人",     "expect": "placeholder_or_none", "note": "'本人'自称词"},
    {"id": "B6", "utterance": "我叫无名氏",   "expect": "placeholder_or_none", "note": "'无名氏'显式匿名"},
    {"id": "B7", "utterance": "我叫路人甲",   "expect": "placeholder_or_none", "note": "白名单有'路人'但无'路人甲'"},

    # CAT-C 真名（必须通过）
    {"id": "C1", "utterance": "我叫刘杨",     "expect": "real:刘杨",     "note": "双字真名"},
    {"id": "C2", "utterance": "我叫欧阳娜娜", "expect": "real:欧阳娜娜", "note": "复姓四字"},
    {"id": "C3", "utterance": "我叫王思聪",   "expect": "real:王思聪",   "note": "三字真名"},
    {"id": "C4", "utterance": "我叫李明",     "expect": "real:李明",     "note": "常见双字真名"},

    # CAT-D 口语化自报（测 name_signal 正则泛化）
    {"id": "D1", "utterance": "大家好我陈浩", "expect": "real_or_none:陈浩", "note": "无'我叫'但有'我X'"},
    {"id": "D2", "utterance": "叫我小芳吧",   "expect": "real:小芳",         "note": "叫我XX"},
    {"id": "D3", "utterance": "我的名字是周杰伦", "expect": "real:周杰伦",   "note": "我的名字是"},
    {"id": "D4", "utterance": "本人张伟",     "expect": "real_or_none:张伟", "note": "本人XX"},

    # CAT-E 变体 / 防绕过
    {"id": "E1", "utterance": "我叫 用 户",       "expect": "placeholder", "note": "带空格绕过"},
    {"id": "E2", "utterance": "我叫　用户",       "expect": "placeholder", "note": "全角空格 U+3000"},
    {"id": "E3", "utterance": "我叫UsEr",         "expect": "placeholder", "note": "大小写混写"},
    {"id": "E4", "utterance": "我叫ＡＮＯＮＹＭＯＵＳ", "expect": "placeholder", "note": "全角字母 NFKC"},
]


async def run_case(case: dict, client: httpx.AsyncClient, pg: asyncpg.Connection) -> dict:
    owner_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    payload = {
        "owner_id": owner_id,
        "session_id": session_id,
        "user_name": "用户",  # 客户端默认，和生产一致
        "user_message": case["utterance"],
        "assistant_message": "好的，记住了。",
    }
    r = await client.post(API, json=payload, timeout=30.0)
    if r.status_code != 200:
        return {**case, "status": "HTTP_FAIL", "http": r.status_code, "body": r.text[:200]}

    # 给 realtime_identity 一点时间写 DB（它是 asyncio.gather 内的协程，
    # 理论上返回前已完成，但 update_person_field 偶尔有 fsync 毫秒级延迟）
    await asyncio.sleep(0.3)

    rows = await pg.fetch(
        """
        SELECT person_id::text AS pid, name, identity, role
        FROM person_nodes WHERE owner_id = $1
        """,
        uuid.UUID(owner_id),
    )
    primaries = [r for r in rows if r["role"] == "primary"]
    primary_count = len(primaries)
    if primaries:
        p = primaries[0]
        ident = p["identity"] if isinstance(p["identity"], dict) else json.loads(p["identity"] or "{}")
        name_final = ident.get("name")
    else:
        name_final = None

    # 判定
    exp = case["expect"]
    verdict = "UNKNOWN"
    reason = ""
    if primary_count != 1:
        verdict = "FAIL"
        reason = f"primary_count={primary_count} (预期=1)"
    elif exp == "placeholder":
        if name_final is None:
            verdict = "PASS"; reason = "占位词被拦，identity.name=None"
        else:
            verdict = "FAIL"; reason = f"占位词未拦，name={name_final!r}"
    elif exp == "placeholder_or_none":
        if name_final is None:
            verdict = "PASS"; reason = "name=None（拦住 or LLM 未填）"
        else:
            verdict = "LEAK"; reason = f"白名单外占位被写入：name={name_final!r}"
    elif exp.startswith("real:"):
        want = exp.split(":", 1)[1]
        if name_final == want:
            verdict = "PASS"; reason = f"真名写入={name_final!r}"
        elif name_final is None:
            verdict = "FAIL"; reason = f"真名丢失（LLM 没提 or 被误拦），预期={want!r}"
        else:
            verdict = "FAIL"; reason = f"name={name_final!r} 预期={want!r}"
    elif exp.startswith("real_or_none:"):
        want = exp.split(":", 1)[1]
        if name_final == want:
            verdict = "PASS"; reason = f"真名写入={name_final!r}"
        elif name_final is None:
            verdict = "TOLERATED"; reason = f"LLM 或 name_signal 未命中，name=None（不算 bug 但欠泛化）"
        else:
            verdict = "FAIL"; reason = f"name={name_final!r} 预期={want!r}"

    return {
        **case,
        "status": "OK",
        "primary_count": primary_count,
        "name_final": name_final,
        "verdict": verdict,
        "reason": reason,
        "owner_id": owner_id,
    }


async def main():
    pg = await asyncpg.connect(PG_DSN)
    results = []
    async with httpx.AsyncClient() as client:
        for case in CASES:
            res = await run_case(case, client, pg)
            results.append(res)
            vt = res.get("verdict", res.get("status"))
            print(f"[{res['id']}] {vt:10s} {res['utterance']!r:30s} -> name={res.get('name_final')!r}  // {res.get('reason', res.get('status'))}")
    await pg.close()

    # 汇总
    by_verdict = {}
    for r in results:
        v = r.get("verdict", r.get("status"))
        by_verdict.setdefault(v, []).append(r["id"])

    print("\n=== 汇总 ===")
    for v, ids in by_verdict.items():
        print(f"  {v:12s} {len(ids):2d} : {','.join(ids)}")

    # 写 JSON 落盘
    out_path = "/tmp/generalization_identity_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n详细 JSON -> {out_path}")

    # 退出码：有 FAIL 返回非零
    fails = [r for r in results if r.get("verdict") == "FAIL"]
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
