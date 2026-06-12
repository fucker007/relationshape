"""
扩展泛化测试 V2：
  1. 多语种占位词（繁体/拼音/英文常见假名/日文）
  2. 小孩子真实对话（产品核心场景）—— 含口齿不清 / 不完整句 / 撒娇语气
  3. identity 多字段提取（age/school/grade/gender/birthday）
  4. 多轮身份演化（先自报名字 → 再报年龄 → 再报学校）
  5. 攻击面（冒名/混淆/他人信息误归）

每个 case 用全新 owner_id，避免交叉污染。
"""
import asyncio
import uuid
import json
import sys

import asyncpg
import httpx

API = "http://localhost:8010/api/v1/memory/chat"
PG_DSN = "postgresql://memory:memory@127.0.0.1:5434/memory"


# ─────────────────────────────────────────────────────────────
# 单轮 case：expect 类型
#   placeholder          : identity.name 应为 None（白名单/判据命中）
#   placeholder_or_none  : 白名单外占位，允许 LLM 未填或被拦
#   real:XXX             : identity.name 必须 == XXX
#   real_or_none:XXX     : 允许真名写入或 LLM 未填（不算 bug）
#   field:key=val        : 校验 identity 某字段（如 age=7）
# ─────────────────────────────────────────────────────────────
SINGLE_CASES = [
    # ========== F 组: 多语种占位词（白名单外） ==========
    {"id": "F1", "utterance": "我叫閣下",     "expect": "placeholder_or_none", "note": "繁体尊称"},
    {"id": "F2", "utterance": "我叫小朋友",   "expect": "placeholder", "note": "白名单内中文（基线）"},
    {"id": "F3", "utterance": "我叫 John Doe", "expect": "placeholder_or_none", "note": "英文通用假名"},
    {"id": "F4", "utterance": "我叫 Jane Doe", "expect": "placeholder_or_none", "note": "英文通用假名(女)"},
    {"id": "F5", "utterance": "我叫某某",     "expect": "placeholder", "note": "R2 序数/匿名字'某'"},
    {"id": "F6", "utterance": "我叫老王",     "expect": "placeholder_or_none", "note": "'老X'泛称（可通过）"},

    # ========== G 组: 小孩子自报姓名（真实场景 — 必须通过）==========
    {"id": "G1", "utterance": "我叫乐乐",     "expect": "real:乐乐",    "note": "叠字小名"},
    {"id": "G2", "utterance": "我叫豆豆",     "expect": "real:豆豆",    "note": "叠字小名"},
    {"id": "G3", "utterance": "我叫笑笑",     "expect": "real:笑笑",    "note": "叠字小名（与'笑笑'可能歧义）"},
    {"id": "G4", "utterance": "我是明明",     "expect": "real_or_none:明明",  "note": "我是+叠字"},
    {"id": "G5", "utterance": "我叫小石头",   "expect": "real_or_none:小石头","note": "小X 三字（石头非形容词）"},
    {"id": "G6", "utterance": "叫我朵朵就好", "expect": "real_or_none:朵朵","note": "口语化"},

    # ========== H 组: 小孩子身份多字段 ==========
    {"id": "H1", "utterance": "我七岁啦",         "expect": "field:age=7",        "note": "中文数字年龄"},
    {"id": "H2", "utterance": "我今年8岁",        "expect": "field:age=8",        "note": "阿拉伯年龄"},
    {"id": "H3", "utterance": "我上二年级",       "expect": "field:grade=二年级",  "note": "年级提取"},
    {"id": "H4", "utterance": "我在阳光小学读书", "expect": "field:school=阳光小学","note": "学校提取"},
    {"id": "H5", "utterance": "我是女孩子",       "expect": "field:gender=female",  "note": "性别-女"},
    {"id": "H6", "utterance": "我是个男孩",       "expect": "field:gender=male",    "note": "性别-男"},

    # ========== I 组: 小孩对话语气 / 撒娇 / 不完整 ==========
    {"id": "I1", "utterance": "我我我我叫小雨",   "expect": "real_or_none:小雨", "note": "口吃重复"},
    {"id": "I2", "utterance": "嘻嘻我叫糖糖",     "expect": "real_or_none:糖糖", "note": "开头语气词"},
    {"id": "I3", "utterance": "人家叫妞妞",       "expect": "real_or_none:妞妞", "note": "第三人称自称（人家）"},
    {"id": "I4", "utterance": "你猜我叫啥",       "expect": "placeholder",       "note": "疑问句应跳过（_is_question）"},
    {"id": "I5", "utterance": "我叫什么呢",       "expect": "placeholder",       "note": "纯疑问自问"},

    # ========== J 组: 攻击面 / 他人信息不归我 ==========
    {"id": "J1", "utterance": "我叫刘德华过来",   "expect": "placeholder", "note": "'我叫X过来'—X 非真名"},
    {"id": "J2", "utterance": "我叫他来帮忙",     "expect": "placeholder", "note": "'我叫他'—X 非真名"},
    {"id": "J3", "utterance": "我儿子叫小明",     "expect": "placeholder_or_none", "note": "他人名字不应归我"},
    {"id": "J4", "utterance": "我朋友叫王伟",     "expect": "placeholder_or_none", "note": "他人名字不应归我"},
    {"id": "J5", "utterance": "我妈叫张丽",       "expect": "placeholder_or_none", "note": "他人名字不应归我"},
]


# ─────────────────────────────────────────────────────────────
# 多轮 case：身份演化
#   每步一个 utterance，最终 DB 状态应满足 final_expect
# ─────────────────────────────────────────────────────────────
# L 组: 第一人称代词变种（小孩/方言/网络口语 —— 必须识别为"我"）
SINGLE_CASES += [
    {"id": "L1", "utterance": "人家叫妞妞",       "expect": "real:妞妞", "note": "人家=我（撒娇）"},
    {"id": "L2", "utterance": "人家叫乐乐啦",     "expect": "real:乐乐", "note": "人家+语气"},
    {"id": "L3", "utterance": "人家名字是糖糖",   "expect": "real:糖糖", "note": "人家+名字"},
    {"id": "L4", "utterance": "咱叫小虎",         "expect": "real:小虎", "note": "咱=我（北方）"},
    {"id": "L5", "utterance": "俺叫二狗",         "expect": "real:二狗", "note": "俺=我（方言）"},
    {"id": "L6", "utterance": "偶叫萌萌",         "expect": "real:萌萌", "note": "偶=我（网络）"},
    {"id": "L7", "utterance": "本宝宝叫芊芊",     "expect": "real:芊芊", "note": "本宝宝=我（撒娇）"},
    {"id": "L8", "utterance": "小的叫阿福",       "expect": "real:阿福", "note": "小的=我（自谦）"},
    {"id": "L9", "utterance": "老子叫赵铁柱",     "expect": "real:赵铁柱", "note": "老子=我（粗话但第一人称）"},
    {"id": "L10","utterance": "人家七岁啦",       "expect": "field:age=7", "note": "人家+年龄"},
    {"id": "L11","utterance": "咱在阳光小学",     "expect": "field:school=阳光小学", "note": "咱+学校"},
    {"id": "L12","utterance": "人家叫小朋友",     "expect": "placeholder", "note": "人家+占位（必须仍拦）"},
    {"id": "L13","utterance": "人家叫刘德华过来", "expect": "placeholder", "note": "人家+叫X来（攻击）"},
]

MULTI_TURN_CASES = [
    {
        "id": "K1",
        "note": "小朋友多轮自报：名字→年龄→学校→年级",
        "turns": [
            "我叫小果",
            "我今年7岁",
            "我在实验小学上学",
            "我读一年级",
        ],
        "final_expect": {"name": "小果", "age": 7, "school": "实验小学", "grade": "一年级"},
    },
    {
        "id": "K2",
        "note": "先占位后真名 — 不应被占位污染",
        "turns": [
            "我叫用户",       # 应被拦
            "我叫小米",       # 真名应写入
        ],
        "final_expect": {"name": "小米"},
    },
    {
        "id": "K3",
        "note": "改名：真名 A → 真名 B（后者应覆盖前者）",
        "turns": [
            "我叫李雷",
            "其实我叫韩梅梅",
        ],
        "final_expect_one_of": [{"name": "李雷"}, {"name": "韩梅梅"}],  # 允许任一（保守）
    },
    {
        "id": "K4",
        "note": "他人信息不应污染 primary",
        "turns": [
            "我叫小美",
            "我弟弟叫小强",
            "我妈妈叫王芳",
        ],
        "final_expect": {"name": "小美"},
    },
    {
        "id": "K5",
        "note": "小孩撒娇 + 口吃 + 年龄",
        "turns": [
            "嘻嘻嘻我我叫豆豆",
            "人家今年六岁啦",
        ],
        "final_expect_one_of": [
            {"name": "豆豆", "age": 6},
            {"name": "豆豆"},
            {"age": 6},
        ],
    },
]


async def _post(client, owner_id, session_id, msg, user_name="用户"):
    payload = {
        "owner_id": owner_id,
        "session_id": session_id,
        "user_name": user_name,
        "user_message": msg,
        "assistant_message": "好的，记住啦。",
    }
    r = await client.post(API, json=payload, timeout=30.0)
    return r


async def _read_identity(pg, owner_id):
    rows = await pg.fetch(
        "SELECT person_id::text AS pid, name, identity, role FROM person_nodes WHERE owner_id=$1",
        uuid.UUID(owner_id),
    )
    primaries = [r for r in rows if r["role"] == "primary"]
    if not primaries:
        return 0, None
    p = primaries[0]
    ident = p["identity"] if isinstance(p["identity"], dict) else json.loads(p["identity"] or "{}")
    return len(primaries), ident


async def run_single(case, client, pg):
    owner_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    r = await _post(client, owner_id, session_id, case["utterance"])
    if r.status_code != 200:
        return {**case, "verdict": "HTTP_FAIL", "http": r.status_code}
    await asyncio.sleep(1.5)  # realtime_identity 改 fire-and-forget 后需等 LLM ~600ms p95
    primary_count, ident = await _read_identity(pg, owner_id)
    ident = ident or {}
    name_final = ident.get("name")

    exp = case["expect"]
    if primary_count != 1:
        return {**case, "verdict": "FAIL", "reason": f"primary_count={primary_count}", "ident": ident}
    if exp == "placeholder":
        v = "PASS" if name_final is None else "FAIL"
        reason = "拦住" if v == "PASS" else f"未拦 name={name_final!r}"
    elif exp == "placeholder_or_none":
        v = "PASS" if name_final is None else "LEAK"
        reason = "拦住/LLM未填" if v == "PASS" else f"泄漏 name={name_final!r}"
    elif exp.startswith("real:"):
        want = exp.split(":", 1)[1]
        if name_final == want:
            v, reason = "PASS", f"name={name_final!r}"
        elif name_final is None:
            v, reason = "FAIL", f"真名丢失,预期={want!r}"
        else:
            v, reason = "FAIL", f"name={name_final!r} != {want!r}"
    elif exp.startswith("real_or_none:"):
        want = exp.split(":", 1)[1]
        if name_final == want:
            v, reason = "PASS", f"name={name_final!r}"
        elif name_final is None:
            v, reason = "TOLERATED", f"LLM未填,预期={want!r}"
        else:
            v, reason = "FAIL", f"name={name_final!r} != {want!r}"
    elif exp.startswith("field:"):
        kv = exp.split(":", 1)[1]
        k, want = kv.split("=", 1)
        got = ident.get(k)
        if str(got) == want:
            v, reason = "PASS", f"{k}={got}"
        elif got is None:
            v, reason = "TOLERATED", f"{k} 未填,预期={want}"
        else:
            v, reason = "FAIL", f"{k}={got} != {want}"
    else:
        v, reason = "UNKNOWN", f"unknown expect={exp}"

    return {**case, "verdict": v, "reason": reason, "primary_count": primary_count, "ident": ident}


async def run_multi(case, client, pg):
    owner_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    for msg in case["turns"]:
        r = await _post(client, owner_id, session_id, msg)
        if r.status_code != 200:
            return {**case, "verdict": "HTTP_FAIL"}
        await asyncio.sleep(1.2)  # 等 fire-and-forget realtime_identity 完成
    primary_count, ident = await _read_identity(pg, owner_id)
    ident = ident or {}

    if primary_count != 1:
        return {**case, "verdict": "FAIL", "reason": f"primary={primary_count}", "ident": ident}

    def _match(expected):
        return all(str(ident.get(k)) == str(v) for k, v in expected.items())

    if "final_expect" in case:
        ok = _match(case["final_expect"])
        v = "PASS" if ok else "FAIL"
        reason = f"got={ {k: ident.get(k) for k in case['final_expect']} }"
    elif "final_expect_one_of" in case:
        ok = any(_match(e) for e in case["final_expect_one_of"])
        v = "PASS" if ok else "FAIL"
        reason = f"got={ident}"
    else:
        v, reason = "UNKNOWN", "no expect"
    return {**case, "verdict": v, "reason": reason, "ident": ident}


async def main():
    pg = await asyncpg.connect(PG_DSN)
    all_results = []
    async with httpx.AsyncClient() as client:
        print("=== 单轮 case ===")
        for c in SINGLE_CASES:
            res = await run_single(c, client, pg)
            all_results.append(res)
            print(f"[{res['id']}] {res['verdict']:10s} {res['utterance']!r:32s} // {res['reason']}  ({res['note']})")

        print("\n=== 多轮 case ===")
        for c in MULTI_TURN_CASES:
            res = await run_multi(c, client, pg)
            all_results.append(res)
            turns_preview = " → ".join(c["turns"])
            print(f"[{res['id']}] {res['verdict']:10s} {turns_preview}")
            print(f"       ident={res['ident']}   // {res['reason']}")
    await pg.close()

    # 汇总
    by = {}
    for r in all_results:
        by.setdefault(r["verdict"], []).append(r["id"])
    print("\n=== 汇总 ===")
    for v, ids in sorted(by.items()):
        print(f"  {v:12s} {len(ids):2d} : {','.join(ids)}")

    with open("/tmp/generalization_v2_results.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print("\nJSON → /tmp/generalization_v2_results.json")
    fails = [r for r in all_results if r["verdict"] in ("FAIL", "LEAK", "HTTP_FAIL")]
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
