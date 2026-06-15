"""独立多轮对话模糊测试（与既有任何测试池/模板/检查零共享）。

目的：前面所有压测都是我自己写的槽位×模板——天然有"对着自己的考卷复习"的嫌疑。
这里换一套**完全独立**的词表、句式、对话结构和判定逻辑，只调用被测系统的公开接口
（prepare_turn/commit）与最终记忆状态，端到端验证：

  随机生成 N 组对话、每组 10 轮，每组里混入【自述事实 / 第三方事实 / 提问 / 闲聊 / 撤回】，
  生成时记录"地面真值"（哪些是用户自己的事、哪些是别人的事/问句/闲聊），跑完后对账。

判定（用字符二元组重叠做表层匹配，容忍抽取的前后缀差异）：
  · 精确率（强不变量，任何输入都该≈100%）：
      - 记忆里的每条偏好/厌恶都能追溯到某条"用户自述"，不是凭空冒出来的（无幻觉）
      - 第三方说的喜好绝不进用户偏好；提问/闲聊不产生事实
  · 召回率（软指标，随口语句式覆盖度，自述事实应被记住）

匹配用二元组而非精确串：'搭城堡'→抽成'城堡'仍算命中，'冬瓜'与'城堡'无共享二元组不会误配。
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from relationshape import CompanionEngine, EngineConfig  # noqa: E402

# ── 全新词表：刻意避开既有测试用词，且都是 ≥2 字的辨识度高的名词/活动 ──
LIKES = [
    "荡秋千", "跳房子", "捉迷藏", "放风筝", "吹泡泡", "踢毽子", "打水漂", "滚铁环",
    "编手链", "集邮票", "做航模", "玩弹珠", "跳皮筋", "翻花绳", "打陀螺", "踩高跷",
    "过家家", "搭城堡", "看漫画", "养蚕宝", "捏橡皮泥", "堆雪人", "抓蝌蚪", "钓龙虾",
    "玩滑梯", "拍洋画", "斗蛐蛐", "折星星", "串珠子", "捡贝壳", "拼地图", "做泥塑",
]
DISLIKES = [
    "葱花", "蒜泥", "生姜", "韭菜", "菠菜", "芥末", "腌菜", "咸鱼", "木耳", "海带",
    "粉条", "豆芽", "冬瓜", "丝瓜", "莴笋", "皮蛋", "纳豆", "薄荷糖", "黑咖啡", "折耳根",
    "猪肝", "羊杂", "鱼腥草", "苦菊", "话梅", "山楂片", "蓝纹奶酪", "酸黄瓜",
]
NAMES = [
    "小满", "阿福", "石头", "桃子", "阿凯", "铁柱", "麦子", "小荷", "小鹿", "小帆",
    "阿月", "小柚", "核桃", "阿杉", "豆丁", "团子", "栗子", "小航儿", "胖虎", "阿水",
    "小米粒", "笨笨", "球球", "小元宝", "阿杰仔", "西瓜瓤", "小汤圆", "卷卷",
]
# 关系是封闭小类（同亲属称谓）：用系统词汇，结构换新——独立性体现在内容词与句式，不在闭类
RELS = ["好朋友", "同桌", "邻居", "发小", "闺蜜", "死党", "同学", "队友"]
EVENTS = [
    ("掉进了", "水沟"), ("赢了", "象棋比赛"), ("弄丢了", "门牌钥匙"), ("头回坐了", "缆车"),
    ("看了场", "皮影戏"), ("钓上来", "一条鲶鱼"), ("拼完了", "千片拼图"), ("被蛰了一下", "手背"),
    ("学会了", "打响指"), ("种活了", "仙人掌"), ("捡到了", "半块化石"), ("摔裂了", "门牙"),
    ("跑赢了", "隔壁班"), ("缝好了", "布娃娃"), ("放飞了", "孔明灯"), ("孵出了", "小鸡仔"),
]
CARES = [
    "攒了三年的邮册", "外婆留下的顶针", "院里那棵老枣树", "走失的橘猫团团", "没寄出的那封信",
    "舞台剧的主角梦", "生病住院的搭档", "搬走前的全班合照", "亲手种的紫藤", "想当兽医的志愿",
]
KIN = ["妈", "爸", "爷爷", "奶奶", "外公", "外婆", "舅舅", "姑姑", "姨", "叔", "伯伯", "表哥", "堂姐", "嫂子"]
# 闲聊：确保不含任何可抽取的事实信号（无 叫/喜欢/讨厌/朋友/最在乎…）
CHITCHAT = [
    "今天天有点闷", "嗯嗯我在听着呢", "哎呀刚打了个喷嚏", "窗外飞过一只麻雀", "这屋灯光有点暗",
    "我有点犯困了", "啦啦啦随便哼两句", "你说这会儿几点啦", "刚喝了口温水", "外头好像在下毛毛雨",
    "我挠了挠后脑勺", "桌上摆着一只空杯子", "时间过得真快呀", "我晃了晃椅子",
]

# 自述句式：用日常关键词（喜欢/讨厌/叫…，真实用户就这么说），但句子结构全新、与既有模板不同。
# 这才是公平的"泛化到新句式"检验——独立性在内容词与结构，不在于刻意躲开关键词。
T_NAME = [
    "对了，你就喊我{n}吧", "大伙儿平时都叫我{n}", "我的名字是{n}哦", "我叫{n}，记好啦",
    "嗨，我小名{n}", "你喊我{n}就成", "我大名{n}", "记住喽，我名字叫{n}",
]
T_LIKE = [
    "要说喜欢，我顶喜欢{x}", "我可爱{x}了，玩不够", "我超级喜欢{x}这件事",
    "说真的我就稀罕{x}", "我对{x}着迷得很", "我最迷的就是{x}",
    "我打小就特别喜欢{x}", "{x}是我最爱，没有之一",
]
T_DISLIKE = [
    "我顶讨厌{x}了", "我可不喜欢{x}", "{x}我是真心讨厌", "我特别怕{x}",
    "说起来我最讨厌吃{x}", "{x}我一口都不爱吃", "我受不了{x}", "我打心眼里不喜欢{x}",
]
T_FRIEND = [
    "我有个{r}叫{p}", "{p}是我最好的{r}", "我那个{r}叫{p}，处得可好",
    "{p}就是我的好{r}", "我和{p}是一对{r}",
]
T_EVENT = [
    "前两天我{a}{o}，可把我闹的", "跟你讲个事，我昨儿{a}{o}", "我上礼拜{a}{o}",
    "你猜怎么着，我{a}{o}了", "前阵子我{a}{o}，到现在还记着",
]
T_CARE = [
    "我心里头头一份的，是{c}", "要说我最割舍不下的，就数{c}", "{c}在我这儿比啥都金贵",
    "我这人别的不说，最看重{c}", "夜里偶尔会想起{c}",
]
T_THIRD = [
    "我{k}就好{x}那口", "我{k}平时老爱{x}", "我{k}特中意{x}", "我{k}对{x}爱得不行",
    "要说{x}，我{k}比谁都上心", "我{k}三天两头就{x}",
]
# 提问（检索，绝不能被学成事实）
T_ASK = [
    "哎你还记着我叫啥不", "我刚说我顶喜欢啥来着", "你倒是猜猜我最咽不下哪样",
    "我那个{r}叫什么名字来着", "我跟你提过我最在乎的是啥吗", "你还记不记得我前阵子干的事",
]
REPLIES = ["好呀，我记着呢", "嗯嗯", "哈哈，听你说呢", "这样啊", "我都听着呢"]


def _bgr(s: str) -> set:
    s = s or ""
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else ({s} if s else set())


def _hit(item: str, pool) -> bool:
    """item 与 pool 中任一串共享 ≥1 字符二元组 → 视为同一事物（容忍前后缀抽取差异）。"""
    ib = _bgr(item)
    return any(ib & _bgr(p) for p in pool)


def gen(rng: random.Random):
    """造一组对话 + 地面真值。槽位在组内全局取不重复，保证表层匹配无歧义。"""
    bag = rng.sample(LIKES, 6) + rng.sample(DISLIKES, 4)
    rng.shuffle(bag)
    likes = [bag.pop() for _ in range(rng.randint(1, 3)) if bag]
    likes = [x for x in likes if x in LIKES] or [rng.choice([b for b in bag if b in LIKES] or LIKES)]
    dis = [x for x in (bag.pop() for _ in range(rng.randint(1, 2)) if bag) if x in DISLIKES]
    tp_pool = [x for x in (LIKES + DISLIKES) if x not in likes and x not in dis]
    tp_item = rng.choice(tp_pool)
    tp_kin = rng.choice(KIN)
    name = rng.choice(NAMES)
    fr_name, fr_rel = rng.choice(NAMES), rng.choice(RELS)
    ev_a, ev_o = rng.choice(EVENTS)
    care = rng.choice(CARES)
    retract = rng.random() < 0.25 and len(likes) >= 2
    retracted = likes[-1] if retract else None

    # 必上的事实轮（自述），加上若干第三方/提问/闲聊轮，凑满 10 轮、顺序打乱（名字尽量靠前）
    turns = []
    turns.append(("name", rng.choice(T_NAME).format(n=name)))
    for x in likes:
        turns.append(("like", rng.choice(T_LIKE).format(x=x)))
    for x in dis:
        turns.append(("dislike", rng.choice(T_DISLIKE).format(x=x)))
    turns.append(("friend", rng.choice(T_FRIEND).format(r=fr_rel, p=fr_name)))
    turns.append(("event", rng.choice(T_EVENT).format(a=ev_a, o=ev_o)))
    turns.append(("care", rng.choice(T_CARE).format(c=care)))
    turns.append(("third", rng.choice(T_THIRD).format(k=tp_kin, x=tp_item)))
    # 用提问/闲聊补足（给撤回留一格）
    cap = 10 - (1 if retracted else 0)
    while len(turns) < cap:
        if rng.random() < 0.5:
            turns.append(("ask", rng.choice(T_ASK).format(r=fr_rel)))
        else:
            turns.append(("chit", rng.choice(CHITCHAT)))
    turns = turns[:cap]
    # 名字轮固定最前，中间打乱（模拟穿插），撤回轮固定最后——撤回必在陈述之后（真实对话顺序）
    head, rest = turns[0], turns[1:]
    rng.shuffle(rest)
    turns = [head] + rest
    if retracted:
        turns.append(("retract", f"我现在不爱{retracted}了，玩腻了"))

    active_likes = [x for x in likes if x != retracted]
    truth = dict(name=name, likes=active_likes, dislikes=dis, retracted=retracted,
                 friend=fr_name, tp_item=tp_item, care=care)
    return [t[1] for t in turns], truth


def run(n: int, seed: int, verbose_fail: int = 6, llm: bool = False, llm_mode: str = "fallback"):
    extractor = None
    if llm:
        from llm_extractor import DeepSeekExtractor      # 仅 --llm 时才需网络/密钥
        extractor = DeepSeekExtractor()
    eng = CompanionEngine(config=EngineConfig(state_dir="/tmp/convofuzz"),
                          extractor=extractor, extractor_mode=llm_mode)
    eng.store.save = lambda st: None        # 不落盘
    rng = random.Random(seed)
    t0 = datetime(2026, 3, 1, 8, 0)
    agg = {k: [0, 0] for k in (
        "名字召回", "喜好召回", "厌恶召回", "朋友召回",
        "无幻觉偏好", "无幻觉厌恶", "第三方不入偏好", "撤回已生效", "提问闲聊不污染")}
    fails = {k: [] for k in agg}

    for i in range(n):
        convo, tr = gen(rng)
        uid = f"c{i}"
        now = t0 + timedelta(hours=i % 5000)
        for j, ut in enumerate(convo):
            eng.prepare_turn(uid, ut, now=now + timedelta(minutes=j))
            eng.commit(uid, ut, rng.choice(REPLIES), now=now + timedelta(minutes=j))
        mem = eng._state(uid).memory
        prefs, avers = mem.preferences, mem.aversions
        ppl = [p[0] for p in mem.user_profile_facts()["people"]]

        def rec(key, ok, detail):
            agg[key][0] += ok
            agg[key][1] += 1
            if not ok and len(fails[key]) < verbose_fail:
                fails[key].append(detail)

        # 召回（软）
        rec("名字召回", bool(mem.user_name) and _hit(mem.user_name, [tr["name"]]),
            (tr["name"], mem.user_name))
        for x in tr["likes"]:
            rec("喜好召回", _hit(x, prefs), (x, prefs))
        for x in tr["dislikes"]:
            rec("厌恶召回", _hit(x, avers), (x, avers))
        rec("朋友召回", _hit(tr["friend"], ppl), (tr["friend"], ppl))

        # 精确率（强）：记忆里不该有追溯不到自述的东西
        for p in prefs:
            rec("无幻觉偏好", _hit(p, tr["likes"]), (p, "应属", tr["likes"], convo))
        for a in avers:
            rec("无幻觉厌恶", _hit(a, tr["dislikes"]), (a, "应属", tr["dislikes"], convo))
        rec("第三方不入偏好", not _hit(tr["tp_item"], prefs), (tr["tp_item"], prefs, convo))
        if tr["retracted"]:
            rec("撤回已生效", not _hit(tr["retracted"], prefs), (tr["retracted"], prefs))
        # 提问/闲聊不污染：人物库不该出现非朋友名的杂项；偏好/厌恶已由"无幻觉"覆盖
        junk_ppl = [x for x in ppl if not _hit(x, [tr["friend"]])]
        rec("提问闲聊不污染", not junk_ppl, (junk_ppl, convo))

        del eng._cache[uid]                 # 防 10 万用户态堆积内存

    tag = "纯规则" if extractor is None else f"规则+LLM({llm_mode})"
    extra = f" · LLM真实请求 {extractor.calls} 次（其余命中缓存）" if extractor is not None else ""
    print(f"\n独立多轮对话模糊测试 · {n} 组 × 10 轮 = {n*10} 轮 · seed={seed} · {tag}{extra}")
    print("=" * 66)
    print("  〔精确率·强不变量〕")
    for k in ("无幻觉偏好", "无幻觉厌恶", "第三方不入偏好", "撤回已生效", "提问闲聊不污染"):
        h, t = agg[k]
        print(f"    {k:<12} {h}/{t}  {100*h/max(t,1):.2f}%")
    print("  〔召回率·软指标〕")
    for k in ("名字召回", "喜好召回", "厌恶召回", "朋友召回"):
        h, t = agg[k]
        print(f"    {k:<12} {h}/{t}  {100*h/max(t,1):.2f}%")
    for k in agg:
        if fails[k]:
            print(f"\n  [{k}] 失败样本：")
            for f in fails[k][:5]:
                print(f"     {f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260615)
    ap.add_argument("--llm", action="store_true", help="接入 DeepSeek 抽取层补召回（需 DEEPSEEK_API_KEY）")
    ap.add_argument("--llm-mode", choices=["fallback", "always"], default="fallback",
                    help="fallback=仅规则未命中时调LLM（省钱）；always=每实质轮都调")
    a = ap.parse_args()
    s = time.time()
    run(a.n, a.seed, llm=a.llm, llm_mode=a.llm_mode)
    print(f"\n用时 {time.time()-s:.1f}s")
