"""融合前后端到端 A/B：真模型作答 + 全部问答写入可读文档。

场景：10 条"数周前的对话"（9~30天前）作为共同历史——两臂的引擎都
亲历过它们（同样 replay 进本地账本）；今天开新会话问 10 个问题，
对话历史为空（早已超出模型上下文窗口）。

  B 臂（融合前）= 完整引擎，纯本地记忆（字符二元组召回）
  A 臂（融合后）= 完整引擎 + MemoryPort → 远端记忆（人物档案+语义召回）

唯一变量是融合。生成 haiku，盲评 sonnet（随机换位）判定回答是否
正确接住了金标事实。

诚实声明：沙箱起不了真 memory_system（需PG+Redis），远端由进程内
mock 模拟其检索语义（关键词+别名命中、档案摘要）；本测试证明的是
融合管线+指令层的端到端效果，真服务的向量召回只会覆盖更广。

用法：python eval/fusion_ab_e2e.py   （约4分钟；写 FUSION_AB_REPORT.md）
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from llm_e2e_test import call_model, gen_reply, is_refusal  # noqa: E402

from relationshape import CompanionEngine, EngineConfig  # noqa: E402
from relationshape.memory_port import MemorySystemAdapter  # noqa: E402

T0 = datetime(2026, 2, 1, 18, 30)     # "今天"
OUT = ROOT / "eval" / "FUSION_AB_REPORT.md"

# ───────────────────────── 测试数据：数周前的对话（共同历史） ─────────────────────────
# (天数前, 当时孩子说的话, 远端检索别名)
HISTORY = [
    (30, "我养了一只仓鼠叫豆豆，最爱吃瓜子", ["仓鼠", "豆豆", "毛茸茸", "小家伙", "瓜子"]),
    (28, "我有点怕黑，晚上不敢关灯睡觉", ["怕黑", "关灯", "一个人睡", "晚上睡"]),
    (25, "我家在三楼，窗户能看到一棵大槐树", ["窗外", "窗户", "槐树", "三楼"]),
    (22, "我妈妈是护士，经常上夜班", ["妈妈", "护士", "夜班"]),
    (20, "我和爸爸约好周末一起做机器人翅膀", ["翅膀", "机器人", "爸爸"]),
    (18, "我的英语老师姓王，特别温柔", ["英语", "王老师", "老师"]),
    (15, "我最好的朋友乐乐下学期要转学了，好舍不得", ["乐乐", "转学", "朋友"]),
    (12, "上周钢琴比赛我紧张了好久，结果拿了优秀奖！", ["钢琴", "比赛", "优秀奖", "上台", "紧张"]),
    (10, "我学会骑自行车啦，就摔了一跤", ["自行车", "骑车", "摔"]),
    (9, "我画的翼龙得了三等奖，评委还夸了颜色", ["画", "翼龙", "三等奖", "颜色"]),
]

# ───────────────────────── 探针：今天的问题（金标=应接住的事实） ─────────────────────────
# (问题, 金标事实, 组别)  直接组=原词复现；改述组=换说法/聚合，考验语义召回
PROBES = [
    ("考考你！你还记得我都跟你说过哪些事呀？", "能说出多件：仓鼠豆豆/钢琴比赛/乐乐转学/画画得奖等", "聚合"),
    ("豆豆最近可能吃多了，圆滚滚的", "仓鼠叫豆豆、爱吃瓜子", "直接"),
    ("钢琴比赛的事我还想跟你再说说", "上次钢琴比赛紧张但拿了优秀奖", "直接"),
    ("乐乐这周真的要走了……", "乐乐是最好的朋友、要转学、孩子舍不得", "直接"),
    ("翅膀我们做到一半啦！", "和爸爸约好周末做机器人翅膀", "直接"),
    ("我又骑车出去玩了，这次没摔哦", "学会骑自行车、上次摔过一跤", "直接"),
    ("那个毛茸茸的小家伙今天特别能闹", "指仓鼠豆豆", "改述"),
    ("晚上一个人睡的时候，那个老毛病又犯了", "孩子怕黑、不敢关灯", "改述"),
    ("你还记得从我家窗户能看到什么吗？", "一棵大槐树（家在三楼）", "改述"),
    ("上次那个让我紧张了好久的事，我妈说我表现特别好", "钢琴比赛（紧张→优秀奖）", "改述"),
]


# ───────────────────────── mock 远端（模拟 memory_system 检索语义） ─────────────────────────
class MockRemote:
    def __init__(self):
        self.events = [
            {"summary": text, "event_time": (T0 - timedelta(days=d)).isoformat(),
             "event_time_raw": f"{d}天前", "importance": 0.8, "keywords": kws + [text]}
            for d, text, kws in HISTORY
        ]
        prefs = "仓鼠豆豆、画画（翼龙得过三等奖）、骑自行车、和爸爸做机器人翅膀"
        self.profile = (f"7岁，家在三楼。喜欢：{prefs}。怕黑。妈妈是护士常上夜班，"
                        f"英语王老师温柔，最好的朋友乐乐将转学。近期：钢琴比赛拿了优秀奖。")
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/v1/graph/recall":
                    q = body.get("query_text", "")
                    hits = [e for e in mock.events if any(k in q for k in e["keywords"])][:4]
                    resp = {"events": [{k: e[k] for k in ("summary", "event_time", "event_time_raw", "importance")} for e in hits],
                            "profile_summary": mock.profile if body.get("include_profile") else None}
                else:
                    resp = {"ok": True}
                data = json.dumps(resp, ensure_ascii=False).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


def replay_history(eng: CompanionEngine, uid: str) -> None:
    """把数周前的对话按旧时间戳灌进引擎（两臂同样亲历）。"""
    for days, text, _ in HISTORY:
        t = T0 - timedelta(days=days)
        eng.prepare_turn(uid, text, now=t)
        eng.commit(uid, text, "（当时的回复）", now=t)


def judge(question: str, gold: str, ra: str, rb: str, swap: bool) -> dict:
    x, y = (rb, ra) if swap else (ra, rb)
    prompt = f"""记忆接地评审。数周前孩子告诉过AI伙伴一件事（金标）：「{gold}」
今天孩子说：「{question}」
回复X：{x}
回复Y：{y}
分别判断 X 和 Y 是否正确接住了金标事实（不要求逐字，但须体现真的记得；
说错细节/张冠李戴/明显不知道=未接住）。
只输出JSON：{{"X":true或false,"Y":true或false,"reason":"一句话"}}（true=接住了）"""
    raw = call_model(prompt, "sonnet")
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}
    try:
        d = {k.lower(): v for k, v in json.loads(m.group(0)).items()}
    except json.JSONDecodeError:
        return {}
    ak, bk = ("y", "x") if swap else ("x", "y")
    return {"A": bool(d.get(ak)), "B": bool(d.get(bk)), "reason": d.get("reason", "")}


def main() -> None:
    mock = MockRemote()
    eng_b = CompanionEngine(config=EngineConfig(state_dir=tempfile.mkdtemp()))                      # 融合前
    eng_a = CompanionEngine(config=EngineConfig(state_dir=tempfile.mkdtemp()),
                            memory_port=MemorySystemAdapter(mock.url))                              # 融合后
    print("replay 共同历史…", file=sys.stderr, flush=True)
    replay_history(eng_b, "kid")
    replay_history(eng_a, "kid")

    pool = ThreadPoolExecutor(max_workers=2)
    rows = []
    t = T0
    for i, (q, gold, group) in enumerate(PROBES):
        da = eng_a.prepare_turn("kid", q, now=t)
        db = eng_b.prepare_turn("kid", q, now=t)
        fa = pool.submit(gen_reply, [], q, da.to_prompt_context())
        fb = pool.submit(gen_reply, [], q, db.to_prompt_context())
        ra, rb = fa.result(), fb.result()
        if is_refusal(ra):
            ra = gen_reply([], q, da.to_prompt_context())
        if is_refusal(rb):
            rb = gen_reply([], q, db.to_prompt_context())
        eng_a.commit("kid", q, ra, now=t)
        eng_b.commit("kid", q, rb, now=t)
        v = judge(q, gold, ra, rb, swap=bool(i % 2))
        rows.append(dict(q=q, gold=gold, group=group, a=ra, b=rb, judge=v))
        print(f"[{i+1}/10] {q[:14]}  A:{v.get('A','?')}  B:{v.get('B','?')}", file=sys.stderr, flush=True)
        t += timedelta(minutes=2)

    ok = [r for r in rows if r["judge"]]
    sa = sum(1 for r in ok if r["judge"]["A"]); sb = sum(1 for r in ok if r["judge"]["B"])
    by_group = {}
    for g in ("直接", "改述", "聚合"):
        gs = [r for r in ok if r["group"] == g]
        by_group[g] = (sum(1 for r in gs if r["judge"]["A"]), sum(1 for r in gs if r["judge"]["B"]), len(gs))

    L = ["# 融合前后端到端 A/B 测试报告（含全部测试数据与问答原文）", "",
         "**实验设置**：两臂都是完整 relationshape 引擎、同样亲历过下表的历史对话；",
         "今天（数周后）开新会话提问，对话历史为空（早已超出模型上下文窗口）。",
         "**唯一变量**：A=融合后（接 MemoryPort 远端记忆），B=融合前（纯本地字符二元组召回）。",
         "生成 haiku；盲评 sonnet 随机换位判定「是否真的记得」。",
         "**诚实声明**：远端为进程内 mock（关键词+别名模拟其检索语义、档案摘要）——",
         "沙箱无法起真 memory_system（需PG+Redis）；本报告证明融合管线+指令层的端到端效果。", "",
         "## 一、测试数据：数周前孩子说过的话（两臂共同历史）", "",
         "| 时间 | 孩子当时说 |", "| --- | --- |"]
    for d, text, _ in HISTORY:
        L.append(f"| {d}天前 | {text} |")
    L += ["", "## 二、汇总", "",
          "| 指标 | A（融合后） | B（融合前） |", "| --- | --- | --- |",
          f"| 记忆接住率（总） | **{sa}/{len(ok)}** | {sb}/{len(ok)} |"]
    for g, (ga, gb, n) in by_group.items():
        L.append(f"| {g}组 | **{ga}/{n}** | {gb}/{n} |")
    L += ["", "## 三、问答原文（每条含金标与盲评理由）", ""]
    for r in rows:
        j = r["judge"] or {}
        L.append(f"### 「{r['q']}」（{r['group']}组）")
        L.append(f"**金标**：{r['gold']}")
        L.append("")
        L.append(f"**A 融合后**（{'✓接住' if j.get('A') else '✗没接住'}）：{r['a']}")
        L.append("")
        L.append(f"**B 融合前**（{'✓接住' if j.get('B') else '✗没接住'}）：{r['b']}")
        if j.get("reason"):
            L.append(f"")
            L.append(f"评审：{j['reason']}")
        L.append("")
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    mock.server.shutdown()
    print(f"\n接住率 A {sa}/{len(ok)} vs B {sb}/{len(ok)}；报告已写入 {OUT.name}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
