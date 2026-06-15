"""记忆压力测试：生成式留出集（每情境可达 10000 条），严格测召回与抽取。

反作弊（回应"不许把测试数据写进系统/不许特例自欺/要泛化/不许减量"）：
1. 用例由"槽位池 × 大量句式模板"**生成**，系统永不接触答案键。
2. 槽位池按奇偶切 dev/test，**填充词不重叠**：只在 dev 调系统，留出 test 报告。
   程序化生成数百名字 + 每类十几种口语句式 → 真·上万条互异用例。
3. 指标确定性：测 gold 是否进入指令的"记忆通道"（排除当前输入回声），秒级可复现。
4. 失败样本分类回收：定位是哪种句式打挂了抽取/召回，据此修通用机制。

矩阵：5 情境 × 6 类记忆。
用法：
  python eval/memory_stress.py --pool dev  --per 10000   # 找缺口
  python eval/memory_stress.py --pool test --per 10000   # 留出集成绩
  python eval/memory_stress.py --mixed --pool test --per 2000   # 混合负载 torture
"""

from __future__ import annotations

import argparse
import random
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from relationshape import CompanionEngine, EngineConfig  # noqa: E402
from relationshape.memory_port import MemorySystemAdapter  # noqa: E402
from relationshape.memory import MemoryBank  # noqa: E402

# ── 程序化名字池：单字叠词(乐乐) + 双字组合(欣怡)，生成数百个，奇偶切 dev/test ──
_SYL_A = list("乐朵浩婷睿欣子一梓天糖豆果琪航鑫宇涵悦杰宁晨阳雨梦佳宝贝桐暖闹咪壮鱼安然可思雅文博睿艺彤浩鹏")
_SYL_B = list("怡轩诺萱然睿涵宇泽轩晨曦欣妍彤瑶娜婷豪杰宁悦阳明俊豪琳菲洁航辰瑞康乐安宁恬静好")


def _gen_names():
    out = []
    seen = set()
    for c in _SYL_A:
        nm = c + c                      # 叠词：乐乐
        if nm not in seen:
            seen.add(nm); out.append(nm)
    for a in _SYL_A:
        for b in _SYL_B:
            nm = a + b                  # 双字：欣怡
            if nm not in seen and a != b:
                seen.add(nm); out.append(nm)
    return out


NAMES = _gen_names()                    # ~1500 个

PREFS = [
    ("恐龙",["霸王龙","那些远古大家伙","会吼的大蜥蜴"]),("钢琴",["弹琴","那架黑白键"]),
    ("画画",["涂涂画画","拿起画笔"]),("足球",["踢球","那个黑白球"]),("乐高",["拼积木","那些小颗粒"]),
    ("游泳",["扑腾水","泡泳池"]),("唱歌",["哼歌","开嗓"]),("跳舞",["蹦跶","踩节拍转圈"]),
    ("折纸",["叠纸","折小动物"]),("天文",["看星星","找星座"]),("机器人",["会动的铁家伙","钢铁小人"]),
    ("做手工",["捣鼓材料","动手做小东西"]),("看绘本",["翻图画书","读小人书"]),("养花",["种小苗","侍弄盆栽"]),
    ("下棋",["对弈","摆棋子"]),("滑板",["踩板子","玩板"]),("书法",["写毛笔字","练字帖"]),
    ("围棋",["落黑白子","下围棋"]),("篮球",["投篮","拍球上篮"]),("摄影",["拍照片","按快门"]),
    ("跑步",["晨跑","压马路"]),("骑车",["蹬车","骑单车"]),("做饭",["下厨","捣鼓吃的"]),
    ("看动画",["追番","看卡通"]),("玩拼图",["拼图块","凑图案"]),("种菜",["侍弄菜地","种小菜"]),
    ("钓鱼",["甩竿","守着鱼漂"]),("做实验",["捣鼓瓶瓶罐罐","搞小实验"]),("写日记",["记小本本","写心情"]),
    ("收集贴纸",["攒贴画","集小贴纸"]),("玩魔方",["拧方块","复原六面"]),("打羽毛球",["挥拍","抽羽毛球"]),
    ("看科普",["读十万个为什么","翻科普书"]),("养小动物",["照顾小宠物","喂小家伙"]),("种多肉",["养肉肉","摆多肉"]),
    ("玩积木",["搭积木","垒方块"]),("听故事",["听睡前故事","催故事"]),("做陶艺",["捏泥巴","拉坯"]),
    ("学英语",["背单词","念abc"]),("打太极",["比划太极","推手"]),
]
RELATIONS = ["同桌","好朋友","闺蜜","哥哥","姐姐","弟弟","妹妹","发小","同学","邻居","队友","死党"]
_EV_ACT_OBJ = [
    ("参加了","钢琴比赛"),("摔了一跤磕到","膝盖"),("养了只","小乌龟"),("搬了","新家"),
    ("转学到","实验小学"),("学会了","骑自行车"),("丢了","心爱的水壶"),("得了","三好学生"),
    ("看了","海豚表演"),("种下","一棵小树"),("第一次","坐高铁"),("拔了","一颗牙"),
    ("收到","生日礼物"),("去了","海边"),("做了","噩梦"),("捡到","一只流浪猫"),
    ("赢了","拔河比赛"),("画完","一幅大画"),("爬了","后山"),("烤了","小饼干"),
    ("参观了","博物馆"),("学了","架子鼓"),("买了","新书包"),("看望了","姥姥"),
    ("剪了","新发型"),("种了","向日葵"),("捉了","蝴蝶"),("做了","手工灯笼"),
    ("打碎了","花瓶"),("跑赢了","运动会"),("学会了","系鞋带"),("领养了","小狗"),
]
EVENTS = _EV_ACT_OBJ
CARED = [
    "画画梦想","转学的好朋友","生病的奶奶","养的小狗旺财","钢琴考级","太空梦","走丢的猫",
    "爸爸的承诺","跳舞比赛","当科学家","写的那本小说","守护的秘密基地","种的向日葵","攒钱买的望远镜",
    "和妈妈的约定","比赛的名次","转学前的合影","养的蚕宝宝","出国留学的梦","学校的乐队","暗恋的同桌",
    "瘫痪的爷爷","收养的流浪狗","环游世界的愿望","当画家的志向","失而复得的手链","班级的荣誉","早逝的金鱼",
]

# ── 句式模板（刻意多样：口语/多子句/标点/语气词，逼出正则盲区）──
T_NAME = ["我叫{n}","我的名字是{n}","我的名字叫{n}","你可以叫我{n}","我是{n}，今年7岁",
          "叫我{n}就好","大家都叫我{n}","我名叫{n}","人家叫{n}啦","我叫{n}啦","记住哦我是{n}",
          "我小名叫{n}","我，叫{n}","我的名字呀，是{n}"]
T_PREF = ["我最喜欢{x}了","我超爱{x}","我特别喜欢{x}","我可喜欢{x}啦","我最爱的就是{x}",
          "我爱死{x}了","{x}是我的最爱","我就喜欢{x}","最近迷上了{x}","我对{x}特别着迷",
          "我喜欢{x}喜欢得不行","我呀，最爱{x}","说真的我超迷{x}"]
T_PERSON = ["{p}是我最好的{r}","我有个{r}叫{p}","我的{r}{p}对我特别好","{p}是我的{r}",
            "{p}是我{r}","我跟{p}是{r}","我和{p}是{r}","{p}，我{r}","我那个{r}叫{p}","{p}就是我的好{r}"]
T_EVENT_RECENT = ["前几天我{a}{o}","前几天，我{a}{o}","我前几天{a}{o}","这周我{a}{o}",
                  "前阵子我{a}{o}","那天我{a}{o}","记得吗，我前几天{a}{o}"]
T_EVENT_OLD = ["上个月我{a}{o}","上个月，我{a}{o}","我上个月{a}{o}","一个月前我{a}{o}",
               "好久前我{a}{o}","之前我{a}{o}了，就是{o}那件"]
T_CARED = ["我最在乎的就是{c}","对我来说最重要的是{c}","{c}是我心里最看重的","我最放不下的是{c}",
           "我这辈子最在乎{c}","{c}对我来说就是一切","我心心念念的就是{c}","我最珍惜的就是{c}",
           "没什么比{c}更重要","{c}是我最看重的"]

P_NAME = ["你还记得我叫什么名字吗","你知道我叫什么吗","你还记得我的名字吗","我叫啥来着你记得不","还记得我是谁吗"]
P_NAME_PARA = ["你还记得怎么称呼我吗","你还知道该叫我什么吗","你还记得我是谁不"]
P_PREF = ["你记得我最喜欢什么吗","你知道我爱玩什么吗","我最喜欢的那个，你还记得吗","你还记得我的爱好吗"]
P_PERSON = ["你记得我那个{r}叫什么吗","我那个{r}你还记得名字不","你还记得我{r}是谁吗"]
P_EVENT_DIRECT = ["还记得我{a}的事吗","我{a}{o}那件事你记得吗","你还记得我{a}的事不"]
P_VAGUE = ["上次那件事你还记得吗","好久前跟你说的那件事你还记得不","之前那件事你记得吗","还记得我上回说的事吗"]
P_CARED = ["你知道我心里最在乎什么吗","你还记得对我最重要的那件事吗","我最放不下的是什么你记得吗"]


def half(pool, which):
    return pool[0::2] if which == "dev" else pool[1::2]


class Gen:
    def __init__(self, which, seed):
        self.r = random.Random(seed)
        self.names = half(NAMES, which)
        self.prefs = half(PREFS, which)
        self.rel = RELATIONS
        self.events = half(EVENTS, which)
        self.cared = half(CARED, which)

    def make(self, mtype, situ):
        r = self.r
        if mtype == "name":
            n = r.choice(self.names)
            store = [(r.choice(T_NAME).format(n=n), 0)]
            return store, self._probe(situ, P_NAME, P_NAME_PARA, "对了，{0}今天想多聊会儿".format(n), 0), n
        if mtype == "pref":
            canon, al = r.choice(self.prefs)
            store = [(r.choice(T_PREF).format(x=canon), 0)]
            alias = r.choice(al)
            sudden = "我今天又{0}了，开心".format(alias if situ == "改述别称" else canon)
            paraq = "我最近老想着{0}，你猜为啥".format(alias)
            return store, self._probe(situ, P_PREF, [paraq], sudden, 0), canon
        if mtype == "person":
            pn = r.choice(self.names); rel = r.choice(self.rel)
            store = [(r.choice(T_PERSON).format(p=pn, r=rel), 0)]
            direct = [t.format(r=rel) for t in P_PERSON]
            return store, self._probe(situ, direct, direct, "{0}今天又来找我玩了".format(pn), 0), pn
        if mtype in ("recent_event", "old_event"):
            a, o = r.choice(self.events)
            tset = T_EVENT_RECENT if mtype == "recent_event" else T_EVENT_OLD
            base = 3 if mtype == "recent_event" else 32
            store = [(r.choice(tset).format(a=a, o=o), 0)]
            direct = [t.format(a=a, o=o) for t in P_EVENT_DIRECT]
            return store, self._probe(situ, direct, P_VAGUE, "{0}的事后来有进展啦".format(o), base), o
        # cared
        c = r.choice(self.cared)
        store = [(r.choice(T_CARED).format(c=c), 0)]
        return store, self._probe(situ, P_CARED, P_CARED, "{0}的事我一直挂心上".format(c), 30), c

    def _probe(self, situ, direct, para, sudden, base):
        r = self.r
        if situ == "直接回忆":
            return (r.choice(direct), max(base, 0)), []
        if situ == "改述别称":
            return (r.choice(para), max(base, 0)), []
        if situ == "上下文丢失":
            fil = [("今天天气真好呀", 0), ("我们聊点别的吧", 0), ("嗯嗯", 0), ("你猜我在想什么", 0),
                   ("哈哈哈", 0), ("好呀好呀", 0), ("然后呢", 0), ("再说说", 0)]
            return (r.choice(direct), 0), fil
        if situ == "隔月再问":
            return (r.choice(direct), max(base, 32)), []
        return (sudden, max(base, 2)), []     # 突然提及


SITUATIONS = ["直接回忆", "改述别称", "上下文丢失", "隔月再问", "突然提及"]
TYPES = ["name", "pref", "person", "recent_event", "old_event", "cared"]
ZH = {"name": "名字", "pref": "喜好", "person": "朋友", "recent_event": "几天前",
      "old_event": "一个月前", "cared": "最在乎"}


def _engine(port):
    eng = CompanionEngine(config=EngineConfig(state_dir=tempfile.mkdtemp()),
                          memory_port=MemorySystemAdapter(port) if port else None)
    eng.store.save = lambda st: None      # 测试侧跳过磁盘（系统代码不动），5万条才跑得动
    return eng


def run(pool, seed, per, port):
    gen = Gen(pool, seed)
    T0 = datetime(2026, 1, 1, 18, 0)
    eng = _engine(port)
    grid = {s: {t: [0, 0] for t in TYPES} for s in SITUATIONS}
    fails = {t: [] for t in TYPES}
    n = 0
    for situ in SITUATIONS:
        for i in range(per):
            mtype = TYPES[i % len(TYPES)]
            store, (probe, fillers), gold = _unpack(gen.make(mtype, situ))
            uid = f"u{n}"; n += 1
            seq = store + fillers          # fillers 已是 (text,day) 列表
            for text, day in seq:
                t = T0 + timedelta(days=day, minutes=len(text) % 7)
                eng.prepare_turn(uid, text, now=t); eng.commit(uid, text, "（回复）", now=t)
            ptext, pday = probe
            d = eng.prepare_turn(uid, ptext, now=T0 + timedelta(days=pday, hours=1))
            surface = " ".join(m.text for m in d.memories) + " " + (d.profile_summary or "") + " " + (d.user_facts or "")
            hit = gold in surface
            grid[situ][mtype][0] += hit; grid[situ][mtype][1] += 1
            if not hit and len(fails[mtype]) < 25:
                fails[mtype].append((store[0][0], ptext, gold))
            del eng._cache[uid]

    _report(pool, seed, per, port, grid, fails)
    return grid


def _unpack(made):
    store, probe_pack, gold = made
    probe, fillers = probe_pack
    return store, (probe, fillers), gold


def _report(pool, seed, per, port, grid, fails):
    print(f"\n记忆压力测试 · 池={pool} · seed={seed} · 每情境{per}条 · {'接远端' if port else '纯本地'}")
    print("=" * 80)
    print(f"{'情境/类型':<11}" + "".join(f"{ZH[t]:>11}" for t in TYPES))
    th = tn = 0
    for s in SITUATIONS:
        row = f"{s:<11}"
        for t in TYPES:
            h, nn = grid[s][t]; th += h; tn += nn
            row += f"{100*h//max(nn,1):>9}% "
        print(row)
    print("-" * 80)
    for t in TYPES:
        h = sum(grid[s][t][0] for s in SITUATIONS); nn = sum(grid[s][t][1] for s in SITUATIONS)
        print(f"  {ZH[t]:<8} {h}/{nn}  {100*h//max(nn,1)}%")
    print(f"\n总召回率：{th}/{tn} = {100*th//max(tn,1)}%")
    # 失败样本（定位句式盲区）
    shown = False
    for t in TYPES:
        if fails[t]:
            if not shown:
                print("\n失败样本（定位句式盲区）："); shown = True
            print(f"  [{ZH[t]}] 共{sum(grid[s][t][1]-grid[s][t][0] for s in SITUATIONS)}例，样本：")
            for store_txt, probe, gold in fails[t][:6]:
                print(f"     存「{store_txt}」问「{probe}」期望含「{gold}」")


def run_mixed(pool, seed, users, port=None):
    r = random.Random(seed * 31 + 7)
    names = half(NAMES, pool); prefs = half(PREFS, pool); rel = RELATIONS
    events = half(EVENTS, pool); cared = half(CARED, pool)
    T0 = datetime(2026, 1, 1, 9, 0)
    eng = _engine(port)
    cat = {"名字": [0, 0], "喜好": [0, 0], "朋友": [0, 0], "事件": [0, 0], "最在乎": [0, 0]}
    for u in range(users):
        uid = f"life{u}"
        name = r.choice(names); my_prefs = r.sample(prefs, 4); my_people = r.sample(names, 3)
        my_rel = [r.choice(rel) for _ in range(3)]; my_events = r.sample(events, 4); my_cared = r.sample(cared, 2)
        script = [(r.choice(T_NAME).format(n=name), 0)]
        script += [(r.choice(T_PREF).format(x=p[0]), 1 + i) for i, p in enumerate(my_prefs)]
        script += [(r.choice(T_PERSON).format(p=pp, r=rr), 4 + i) for i, (pp, rr) in enumerate(zip(my_people, my_rel))]
        script += [(r.choice(T_EVENT_OLD).format(a=a, o=o), 6 + i) for i, (a, o) in enumerate(my_events[:2])]
        script += [(r.choice(T_EVENT_RECENT).format(a=a, o=o), 20 + i) for i, (a, o) in enumerate(my_events[2:])]
        script += [(r.choice(T_CARED).format(c=c), 24 + i) for i, c in enumerate(my_cared)]
        for text, day in script:
            t = T0 + timedelta(days=day, minutes=len(text) % 5)
            eng.prepare_turn(uid, text, now=t); eng.commit(uid, text, "（回复）", now=t)

        def surf(q):
            d = eng.prepare_turn(uid, q, now=T0 + timedelta(days=40))
            return " ".join(m.text for m in d.memories) + " " + (d.user_facts or "")
        s = surf(r.choice(P_NAME)); cat["名字"][1] += 1; cat["名字"][0] += name in s
        for p in my_prefs:
            s = surf(r.choice(P_PREF)); cat["喜好"][1] += 1; cat["喜好"][0] += p[0] in s
        for pp in my_people:
            s = surf("你记得我那些朋友吗"); cat["朋友"][1] += 1; cat["朋友"][0] += pp in s
        for a, o in my_events:
            s = surf("还记得我{0}的事吗".format(a)); cat["事件"][1] += 1; cat["事件"][0] += o in s
        for c in my_cared:
            s = surf(r.choice(P_CARED)); cat["最在乎"][1] += 1; cat["最在乎"][0] += c in s
        del eng._cache[uid]
    print(f"\n混合负载 torture · 池={pool} · {users}用户每人十几条事实 · 一个月后竞争追问")
    print("=" * 60)
    th = tn = 0
    for k, (h, nn) in cat.items():
        th += h; tn += nn
        print(f"  {k:<8} {h}/{nn}  {100*h//max(nn,1)}%")
    print(f"  {'总计':<8} {th}/{tn}  {100*th//max(tn,1)}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", choices=["dev", "test"], default="dev")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--per", type=int, default=10000)
    ap.add_argument("--port", default=None)
    ap.add_argument("--mixed", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--qualified", action="store_true")
    ap.add_argument("--precision", action="store_true")
    a = ap.parse_args()
    seed = a.seed if a.seed is not None else (1 if a.pool == "dev" else 7)
    if a.precision:
        run_precision(a.pool, seed, a.per)
    elif a.qualified:
        run_qualified(a.pool, seed, a.per)
    elif a.update:
        run_update(a.pool, seed, a.per)
    elif a.mixed:
        run_mixed(a.pool, seed, a.per, a.port)
    else:
        run(a.pool, seed, a.per, a.port)



# ── 多事实冲突更新 健壮性压测（偏好失效/更替/转移），留出池，刁钻句式 ──
T_PREF_POS = ["我最喜欢{0}了","我超爱{0}","我特别喜欢{0}","我可喜欢{0}啦","我最爱的就是{0}",
              "我爱死{0}了","我就喜欢{0}","最近迷上了{0}","我对{0}特别着迷","我超迷{0}"]
T_PREF_NEG = ["我不喜欢{0}了","我现在不爱{0}了","我不太喜欢{0}了","我不怎么喜欢{0}了",
              "我对{0}没兴趣了","我不想再玩{0}了","{0}我已经不喜欢了","我再也不喜欢{0}了",
              "{0}玩腻了","我对{0}腻了","现在不喜欢{0}了","我不爱{0}了"]
T_PREF_REPL = ["我不喜欢{0}了，现在最喜欢{1}","不爱{0}了，改喜欢{1}了","我不喜欢{0}了，最近迷上了{1}",
               "{0}玩腻了，现在超爱{1}","我对{0}没兴趣了，现在喜欢{1}","不玩{0}了，改喜欢{1}"]
T_PREF_SHIFT = ["我现在更喜欢{1}了","比起{0}我现在更爱{1}","最近我更迷{1}了","我现在最爱{1}"]


def run_update(pool, seed, per):
    r = random.Random(seed)
    prefs = [p[0] for p in half(PREFS, pool)]
    cat = {"撤回": [0, 0], "更替": [0, 0], "转移": [0, 0], "部分撤回": [0, 0]}
    fails = {k: [] for k in cat}
    for _ in range(per):
        scen = r.choice(["撤回", "更替", "转移", "部分撤回"])
        b = MemoryBank()
        if scen == "撤回":
            x = r.choice(prefs)
            s1 = r.choice(T_PREF_POS).format(x); s2 = r.choice(T_PREF_NEG).format(x)
            b.extract_facts(s1, []); b.extract_facts(s2, [])
            pf = b.user_profile_facts()["preferences"]
            ok = x not in pf and x not in b.aversions
            why = (s1, s2, f"{x}应消失")
        elif scen == "更替":
            x, y = r.sample(prefs, 2)
            s1 = r.choice(T_PREF_POS).format(x); s2 = r.choice(T_PREF_REPL).format(x, y)
            b.extract_facts(s1, []); b.extract_facts(s2, [])
            pf = b.user_profile_facts()["preferences"]
            ok = x not in pf and y in pf
            why = (s1, s2, f"{x}消失且{y}在")
        elif scen == "转移":
            x, y = r.sample(prefs, 2)
            s1 = r.choice(T_PREF_POS).format(x); s2 = r.choice(T_PREF_SHIFT).format(x, y)
            b.extract_facts(s1, []); b.extract_facts(s2, [])
            pf = b.user_profile_facts()["preferences"]
            ok = y in pf                                 # 转移：新的在即可（旧的可留）
            why = (s1, s2, f"{y}在")
        else:  # 部分撤回：3个偏好撤掉中间1个，另两个必须留
            xs = r.sample(prefs, 3)
            for x in xs:
                b.extract_facts(r.choice(T_PREF_POS).format(x), [])
            drop = xs[1]
            neg = r.choice(T_PREF_NEG).format(drop)
            b.extract_facts(neg, [])
            pf = b.user_profile_facts()["preferences"]
            ok = drop not in pf and xs[0] in pf and xs[2] in pf
            why = (f"存{xs}", neg, f"{drop}消失,{xs[0]}/{xs[2]}留")
        cat[scen][0] += ok; cat[scen][1] += 1
        if not ok and len(fails[scen]) < 12:
            fails[scen].append(why)
    print(f"\n冲突更新健壮性压测 · 池={pool} · seed={seed} · {per}条 · 留出泛化")
    print("=" * 64)
    th = tn = 0
    for k, (h, nn) in cat.items():
        th += h; tn += nn
        print(f"  {k:<8} {h}/{nn}  {100*h//max(nn,1)}%")
    print(f"  {'总计':<8} {th}/{tn}  {100*th//max(tn,1)}%")
    for k in cat:
        if fails[k]:
            print(f"\n  [{k}] 失败样本：")
            for a, bb, g in fails[k][:6]:
                print(f"     「{a}」+「{bb}」期望：{g}")


# ── 带区分属性的同类多实体 注入攻击测试（朋友按活动 / 喜好按品类 / 厌恶按品类）──
ACTIVITIES = ["打篮球","打羽毛球","踢足球","下围棋","弹钢琴","画画","跳舞","游泳","唱歌",
              "玩游戏","骑车","跑步","钓鱼","写代码","看书","做手工","滑板","打乒乓","练书法","摄影"]
ATTR_FRIEND = ["{a}的","经常一起{a}的","跟我一起{a}的","特别会{a}的"]
T_QFRIEND = ["我有一个{attr}{r}叫{n}","还有一个{attr}{r}叫{n}","我认识一个{attr}{r}叫{n}",
             "{n}是我{attr}{r}","我有个{attr}{r}是{n}","还有个{attr}{r}{n}"]
CAT_LIKE = [("水果","苹果"),("水果","香蕉"),("运动","篮球"),("运动","足球"),("颜色","蓝色"),
            ("颜色","红色"),("动物","熊猫"),("动物","老虎"),("科目","数学"),("科目","语文"),
            ("食物","饺子"),("食物","披萨"),("季节","夏天"),("饮料","可乐"),("零食","薯片"),
            ("游戏","象棋"),("乐器","小提琴"),("花","玫瑰"),("城市","北京"),("书","西游记")]
T_QLIKE = ["我喜欢的{c}是{i}","我最喜欢的{c}是{i}","要说{c}我最爱{i}","{c}里我最喜欢{i}","我喜欢的{c}是{i}哦"]
T_QDISLIKE = ["我讨厌的{c}是{i}","我最怕的{c}是{i}","{c}里我最讨厌{i}","我不喜欢的{c}是{i}"]
P_QLIKE = ["我喜欢的{c}是什么","你记得我爱吃/玩的{c}吗","我最喜欢哪个{c}"]


def run_qualified(pool, seed, per):
    r = random.Random(seed)
    names = half(NAMES, pool)
    cat_like = [CAT_LIKE[i] for i in range(len(CAT_LIKE)) if (i % 2 == 0) == (pool == "dev")]
    cat = {"朋友计数": [0, 0], "朋友按属性": [0, 0], "喜好按品类": [0, 0], "厌恶按品类": [0, 0]}
    fails = {k: [] for k in cat}
    for _ in range(per):
        scen = r.choice(["朋友", "喜好", "厌恶"])
        b = MemoryBank()
        if scen == "朋友":
            k = r.randint(2, 4)
            picks = r.sample(names, k); acts = r.sample(ACTIVITIES, k)
            stores = []
            for nm, ac in zip(picks, acts):
                attr = r.choice(ATTR_FRIEND).format(a=ac)
                stores.append(r.choice(T_QFRIEND).format(attr=attr, r="朋友", n=nm))
            for s in stores:
                b.extract_facts(s, [])
            ppl = {p[0]: p for p in b.user_profile_facts()["people"]}
            # 计数：所有名字都在档案
            cnt_ok = all(nm in ppl for nm in picks)
            cat["朋友计数"][0] += cnt_ok; cat["朋友计数"][1] += 1
            if not cnt_ok and len(fails["朋友计数"]) < 8:
                fails["朋友计数"].append((stores, list(ppl)))
            # 按属性：随机挑一个，其属性+名字都在档案且相连
            idx = r.randrange(k); tn, ta = picks[idx], acts[idx]
            ent = ppl.get(tn)
            attr_ok = ent is not None and ta in (ent[2] or "")
            cat["朋友按属性"][0] += attr_ok; cat["朋友按属性"][1] += 1
            if not attr_ok and len(fails["朋友按属性"]) < 8:
                fails["朋友按属性"].append((stores, f"{ta}→{tn}", ppl.get(tn)))
        elif scen == "喜好":
            k = r.randint(2, 3)
            picks = r.sample(cat_like, k)
            for c, i in picks:
                b.extract_facts(r.choice(T_QLIKE).format(c=c, i=i), [])
            tc, ti = r.choice(picks)
            cp = b.categorized_prefs() if hasattr(b, "categorized_prefs") else {}
            ok = cp.get(tc) == ti or (ti in b.preferences)
            cat["喜好按品类"][0] += ok; cat["喜好按品类"][1] += 1
            if not ok and len(fails["喜好按品类"]) < 8:
                fails["喜好按品类"].append(([T_QLIKE[0].format(c=c, i=i) for c, i in picks], f"{tc}→{ti}", cp))
        else:  # 厌恶
            k = r.randint(2, 3)
            picks = r.sample(cat_like, k)
            for c, i in picks:
                b.extract_facts(r.choice(T_QDISLIKE).format(c=c, i=i), [])
            tc, ti = r.choice(picks)
            ok = ti in b.aversions
            cat["厌恶按品类"][0] += ok; cat["厌恶按品类"][1] += 1
            if not ok and len(fails["厌恶按品类"]) < 8:
                fails["厌恶按品类"].append(([T_QDISLIKE[0].format(c=c, i=i) for c, i in picks], f"{tc}→{ti}", list(b.aversions)))
    print(f"\n带属性多实体 注入攻击 · 池={pool} · seed={seed} · {per}条 · 留出泛化")
    print("=" * 64)
    th = tn = 0
    for kk, (h, nn) in cat.items():
        th += h; tn += nn
        print(f"  {kk:<10} {h}/{nn}  {100*h//max(nn,1)}%")
    print(f"  {'总计':<10} {th}/{tn}  {100*th//max(tn,1)}%")
    for kk in cat:
        if fails[kk]:
            print(f"\n  [{kk}] 失败样本：")
            for f in fails[kk][:5]:
                print(f"     {f}")



# ── 精确率注入攻击（生产日志暴露的5类污染，10万条）──
# 亲属称谓全集（封闭类）：祖辈/父母/同辈/叔伯姑舅姨及配偶/堂表/姻亲/拟亲 + 非亲第三方
KINSHIP = ["妈妈","妈","母亲","老妈","爸爸","爸","父亲","老爸",
           "爷爷","爷","奶奶","奶","外公","姥爷","外婆","姥姥","太爷爷","太奶奶",
           "哥哥","哥","姐姐","姐","弟弟","弟","妹妹","妹",
           "叔叔","叔","伯伯","大伯","伯父","舅舅","舅","舅妈",
           "姑姑","姑妈","姨妈","阿姨","姨","婶婶","婶","姑父","姨夫","姨父",
           "表哥","表姐","表弟","表妹","堂哥","堂姐","堂弟","堂妹",
           "嫂子","姐夫","干妈","干爹","继母","后妈","老师","教练","同学","同桌"]
FOODS = ["面条","青椒","胡萝卜","香菜","茄子","苦瓜","洋葱","西蓝花","肥肉","豆腐","蘑菇","芹菜","南瓜","秋葵"]
Q_FRIEND = ["我最好的朋友是谁","我打篮球的朋友是谁","跟我一起打羽毛球的朋友是谁","我最喜欢的朋友是谁",
            "我有几个朋友","我那个同桌叫什么来着","我经常一起玩的朋友是谁"]
Q_PREF = ["我最喜欢什么","我喜欢的水果是什么","我爱玩什么来着","我讨厌吃什么"]
T_THIRD = ["我{k}喜欢{x}","{k}很喜欢{x}","我{k}爱{x}","{k}最爱{x}","我{k}就喜欢{x}"]
T_AVER = ["我不喜欢吃{f}","我不爱吃{f}","我讨厌吃{f}","{f}我不爱吃","{f}我最讨厌了","我最怕吃{f}","我可不爱吃{f}"]
T_PRONOUN = ["那个{r}对我很好","这个{r}挺好的","我{k}对我很好","那家伙是我{r}"]
# 多子句"第三方做饭 + 用户厌恶"——生产日志原句类（"我妈妈总喜欢给我煮面条，但我不喜欢吃面条"）
# 同时考验：①不把"吃X"记成偏好（漏看否定）②连词不当食物③第三方不进人名④不拿厌恶/第三方碎片做hook
_COOK = ["煮","做","炒","蒸","烧","炖","烤","煎","熬","焖","拌","卤"]
T_THIRD_AVER = [
    "我{k}总喜欢给我{v}{f}，但我不喜欢吃{f}",
    "{k}老是给我{v}{f}，可我不爱吃{f}",
    "我{k}爱给我{v}{f}，不过我讨厌吃{f}",
    "我{k}天天{v}{f}，但说实话我不爱吃{f}",
    "{k}总{v}{f}给我吃，我其实最讨厌{f}了",
    "我{k}喜欢{v}{f}，但{f}我一点都不爱吃",
    "我{k}经常{v}{f}，可是我不喜欢吃{f}",
    "{k}总给我{v}{f}，我却不爱吃{f}",
]
# 测hook泄漏用的负向词表（连词为闭类虚词，非测试特例）
_CONJ_CHK = ["但","可","不过","却","其实","然后","所以","而","就","也","还","又","都"]


def run_precision(pool, seed, per):
    import tempfile as _tf
    from relationshape import CompanionEngine, EngineConfig
    r = random.Random(seed)
    foods = foods_h = [FOODS[i] for i in range(len(FOODS)) if (i % 2 == 0) == (pool == "dev")]
    cook_h = [_COOK[i] for i in range(len(_COOK)) if (i % 2 == 0) == (pool == "dev")]
    cat = {"问句不入事实": [0, 0], "三方主体不记成我": [0, 0], "指代式厌恶": [0, 0],
           "代词角色非人名": [0, 0], "问句不生成hook": [0, 0], "多子句三方厌恶不污染": [0, 0]}
    fails = {k: [] for k in cat}
    T0 = datetime(2026, 1, 1, 9, 0)
    for _ in range(per):
        scen = r.choice(list(cat))
        if scen in ("问句不入事实", "问句不生成hook"):
            eng = CompanionEngine(config=EngineConfig(state_dir=_tf.mkdtemp()))
            eng.store.save = lambda st: None
            q = r.choice(Q_FRIEND + Q_PREF)
            eng.prepare_turn("u", q, now=T0); eng.commit("u", q, "（回复）", now=T0)
            mem = eng._state("u").memory
            if scen == "问句不入事实":
                # 问句不该污染：people无新名、prefs无新增、episode不存问句
                ok = (not mem.preferences and not [p for p in mem.user_profile_facts()["people"]]
                      and not any("是谁" in e.text or "什么" in e.text or "几个" in e.text for e in mem.episodes))
                if not ok and len(fails[scen]) < 8:
                    fails[scen].append((q, mem.preferences, list(mem.people), [e.text for e in mem.episodes]))
            else:
                hook = eng._state("u").last_hook
                ok = not hook or not _is_query(q)  # 问句不该生成 hook
                ok = (hook is None)
                if not ok and len(fails[scen]) < 8:
                    fails[scen].append((q, hook))
            cat[scen][0] += ok; cat[scen][1] += 1
        elif scen == "三方主体不记成我":
            k = r.choice(KINSHIP); x = r.choice(foods_h + ["游泳", "跳广场舞", "做饭", "看电视"])
            b = MemoryBank(); b.extract_facts(r.choice(T_THIRD).format(k=k, x=x), [])
            ok = x not in b.preferences
            cat[scen][0] += ok; cat[scen][1] += 1
            if not ok and len(fails[scen]) < 8:
                fails[scen].append((r.choice(T_THIRD).format(k=k, x=x), b.preferences))
        elif scen == "指代式厌恶":
            f = r.choice(foods)
            b = MemoryBank(); b.extract_facts(r.choice(T_AVER).format(f=f), [])
            ok = f in b.aversions and f not in b.preferences
            cat[scen][0] += ok; cat[scen][1] += 1
            if not ok and len(fails[scen]) < 8:
                fails[scen].append((r.choice(T_AVER).format(f=f), b.aversions, b.preferences))
        elif scen == "代词角色非人名":
            k = r.choice(KINSHIP); rel = r.choice(["朋友", "同学", "同桌"])
            b = MemoryBank(); b.extract_facts(r.choice(T_PRONOUN).format(r=rel, k=k), [])
            ppl = [p[0] for p in b.user_profile_facts()["people"]]
            ok = not any(x in ("那", "这", "那个", "这个", rel, k, "那家伙", "我"+k) for x in ppl) and not ppl
            cat[scen][0] += ok; cat[scen][1] += 1
            if not ok and len(fails[scen]) < 8:
                fails[scen].append((r.choice(T_PRONOUN).format(r=rel, k=k), ppl))
        else:  # 多子句三方厌恶不污染（生产日志原句类，须走全引擎拿 hook）
            k = r.choice(KINSHIP); f = r.choice(foods_h); v = r.choice(cook_h)
            s = r.choice(T_THIRD_AVER).format(k=k, f=f, v=v)
            eng = CompanionEngine(config=EngineConfig(state_dir=_tf.mkdtemp()))
            eng.store.save = lambda st: None
            eng.prepare_turn("u", s, now=T0); eng.commit("u", s, "（回复）", now=T0)
            st = eng._state("u"); mem = st.memory
            ppl = [p[0] for p in mem.user_profile_facts()["people"]]
            hook = st.last_hook or ""
            ok = (
                f not in mem.preferences                         # ① 否定没漏：不记成偏好
                and f in mem.aversions                            # 厌恶正确捕获
                and not any(c in mem.aversions for c in _CONJ_CHK)  # ② 连词没被当食物
                and not ppl                                       # 第三方不进人名
                and f not in hook and k not in hook               # ③ hook不泄漏厌恶/第三方
                and not any(c in hook for c in _CONJ_CHK)
            )
            cat[scen][0] += ok; cat[scen][1] += 1
            if not ok and len(fails[scen]) < 8:
                fails[scen].append((s, "prefs=" + str(mem.preferences), "avers=" + str(mem.aversions),
                                    "ppl=" + str(ppl), "hook=" + repr(st.last_hook)))
    print(f"\n精确率注入攻击 · 池={pool} · seed={seed} · {per}条 · 留出泛化")
    print("=" * 64)
    th = tn = 0
    for kk, (h, nn) in cat.items():
        th += h; tn += nn
        print(f"  {kk:<14} {h}/{nn}  {100*h//max(nn,1)}%")
    print(f"  {'总计':<14} {th}/{tn}  {100*th//max(tn,1)}%")
    for kk in cat:
        if fails[kk]:
            print(f"\n  [{kk}] 失败样本：")
            for f in fails[kk][:5]:
                print(f"     {f}")


def _is_query(t):
    return t.rstrip().endswith(("？", "?")) or any(w in t for w in ("是谁", "几个", "什么", "哪个", "叫什么"))


if __name__ == "__main__":
    main()
