"""记忆压力测试：生成式留出集，严格测"该记的到底有没有被召回进指令"。

反作弊设计（回应"不许把测试数据写进系统/不许特例自欺/要泛化"）：
1. 测试用例由"槽位池 × 组合模板"**生成**，不是手写清单；系统永不接触答案键。
2. 槽位池按奇偶下标切成 dev/test 两半，**填充词不重叠**：
   只在 dev 池调系统，最终用 test 池（系统从没见过的名字/喜好/人物）报告。
   若有人硬编码任何特例，test 分数立刻露馅——把"自欺"变成可见的。
3. 指标确定性、不依赖大模型：测 gold 事实是否出现在 prepare_turn 产出的
   指令（to_prompt_context）里——秒级、可复现、无法用花言巧语蒙混。

测量矩阵：5 种情境 × 6 类记忆。
  情境：直接回忆 / 改述别称 / 上下文丢失 / 隔月再问 / 突然提及
  类型：名字 / 喜好 / 朋友 / 几天前的事 / 一个月前的事 / 最在乎的事

用法：
  python eval/memory_stress.py --pool dev    # 开发期看缺口
  python eval/memory_stress.py --pool test   # 留出集报告（最终成绩）
  python eval/memory_stress.py --pool test --port http://127.0.0.1:8010   # 接远端记忆看天花板
"""

from __future__ import annotations

import argparse
import random
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from relationshape import CompanionEngine, EngineConfig  # noqa: E402
from relationshape.memory_port import MemorySystemAdapter  # noqa: E402

# ── 槽位池（仅存在于测试侧，绝不进 relationshape/）；奇偶切 dev/test，填充不重叠 ──
NAMES = ["小明","乐乐","朵朵","浩浩","婷婷","睿睿","欣怡","子轩","一诺","梓萱",
         "天天","糖糖","豆豆","果果","琪琪","航航","ноа","布丁","可乐","团团",
         "小鱼","阿宝","花卷","年糕","贝贝","桐桐","暖暖","闹闹","咪咪","壮壮"]
# 喜好：(标准词, [别称/改述])
PREFS = [
    ("恐龙",["霸王龙","那些远古的大家伙","会吼的大蜥蜴"]),
    ("钢琴",["弹琴","那架黑白键的乐器"]),
    ("画画",["涂涂画画","拿起画笔"]),
    ("足球",["踢球","那个圆滚滚的黑白球"]),
    ("乐高",["拼积木","那些小颗粒"]),
    ("游泳",["扑腾水","泡在泳池里"]),
    ("唱歌",["哼歌","开嗓"]),
    ("跳舞",["蹦跶","踩着节拍转圈"]),
    ("折纸",["叠纸","把纸折成小动物"]),
    ("天文",["看星星","抬头找星座"]),
    ("机器人",["会动的铁家伙","钢铁小人"]),
    ("做手工",["动手做小东西","捣鼓材料"]),
    ("看绘本",["翻图画书","读小人书"]),
    ("养花",["种小苗","侍弄盆栽"]),
    ("下棋",["对弈","摆棋子"]),
    ("滑板",["踩板子","玩滑板"]),
    ("书法",["写毛笔字","练字帖"]),
    ("围棋",["落黑白子","下围棋"]),
    ("篮球",["投篮","拍球上篮"]),
    ("摄影",["拍照片","按快门"]),
]
RELATIONS = ["同桌","好朋友","闺蜜","哥哥","姐姐","弟弟","妹妹","发小","同学","邻居"]
# 事件：(动作, 宾语关键词)；事件回忆的 gold 用宾语关键词
EVENTS = [
    ("参加了","钢琴比赛"),("摔了一跤","膝盖"),("养了","小乌龟"),("搬了","新家"),
    ("转学到","实验小学"),("学会了","骑自行车"),("丢了","心爱的水壶"),("得了","三好学生"),
    ("看了","海豚表演"),("种下","一棵小树"),("第一次","坐高铁"),("拔了","一颗牙"),
    ("收到","生日礼物"),("去了","海边"),("做了","噩梦"),("捡到","一只流浪猫"),
    ("赢了","拔河比赛"),("画完","一幅大画"),("爬了","后山"),("烤了","小饼干"),
]
# 最在乎：(关键词, 一句强调表达模板)
CARED = [
    "画画梦想","转学的好朋友","生病的奶奶","养的小狗旺财","钢琴考级","太空梦",
    "走丢的猫","爸爸的承诺","跳舞比赛","当科学家","写的那本小说","守护的秘密基地",
    "种的向日葵","攒钱买的望远镜","和妈妈的约定","比赛的名次","转学前的合影","养的蚕宝宝",
]


def half(pool, which):
    return pool[0::2] if which == "dev" else pool[1::2]


class Gen:
    def __init__(self, which, seed):
        self.r = random.Random(seed)
        self._names = half(NAMES, which)
        self._prefs = half(PREFS, which)
        self._rel = RELATIONS
        self._events = half(EVENTS, which)
        self._cared = half(CARED, which)

    # 每类记忆：返回 (store_utterances[(text,day)], probe(text,day), gold, mtype)
    def name(self, situ):
        n = self.r.choice(self._names)
        tmpl = self.r.choice(["我叫{0}","我的名字是{0}","我的名字叫{0}","你可以叫我{0}","我是{0}，今年7岁"])
        store = [(tmpl.format(n), 0)]
        return self._wrap(store, situ, gold=n, mtype="名字",
                          direct="你还记得我叫什么名字吗",
                          sudden="对了，{0}今天想跟你多聊会儿".format(n),
                          paraq="你还记得怎么称呼我吗")

    def pref(self, situ):
        canon, aliases = self.r.choice(self._prefs)
        tmpl = self.r.choice(["我最喜欢{0}了","我超爱{0}","我特别喜欢{0}","我可喜欢{0}啦","我最爱的就是{0}"])
        store = [(tmpl.format(canon), 0)]
        alias = self.r.choice(aliases)
        return self._wrap(store, situ, gold=canon, mtype="喜好",
                          direct="你记得我最喜欢什么吗",
                          sudden="我今天又{0}了，开心".format(alias if situ=="改述别称" else canon),
                          paraq="我最近老想着{0}，你猜我为啥".format(alias))

    def person(self, situ):
        pn = self.r.choice(self._names)
        rel = self.r.choice(self._rel)
        tmpl = self.r.choice(["{0}是我最好的{1}","我有个{1}叫{0}","我的{1}{0}对我特别好","{0}是我的{1}"])
        store = [(tmpl.format(pn, rel), 0)]
        return self._wrap(store, situ, gold=pn, mtype="朋友",
                          direct="你记得我那个{0}叫什么吗".format(rel),
                          sudden="{0}今天又来找我玩了".format(pn),
                          paraq="我那个{0}最近怎么样你还记得吗".format(rel))

    def recent_event(self, situ):
        act, obj = self.r.choice(self._events)
        store = [("前几天我{0}{1}".format(act, obj), 0)]
        return self._wrap(store, situ, gold=obj, mtype="几天前的事", base_day=3,
                          direct="还记得我前几天{0}的事吗".format(act),
                          sudden="{0}的事后来有进展啦".format(obj),
                          paraq="上次那件事你还记得吗")

    def old_event(self, situ):
        act, obj = self.r.choice(self._events)
        store = [("上个月我{0}{1}".format(act, obj), 0)]
        return self._wrap(store, situ, gold=obj, mtype="一个月前的事", base_day=32,
                          direct="还记得上个月我{0}的事吗".format(act),
                          sudden="{0}那件事我又想起来了".format(obj),
                          paraq="好久前跟你说的那件事你还记得不")

    def cared(self, situ):
        c = self.r.choice(self._cared)
        tmpl = self.r.choice(["我最在乎的就是{0}","对我来说最重要的是{0}","{0}是我心里最看重的","我最放不下的是{0}"])
        store = [(tmpl.format(c), 0)]
        return self._wrap(store, situ, gold=c, mtype="最在乎的事", base_day=30,
                          direct="你知道我心里最在乎什么吗",
                          sudden="{0}的事我一直挂在心上".format(c),
                          paraq="你还记得对我最重要的那件事吗")

    def _wrap(self, store, situ, gold, mtype, direct, sudden, paraq, base_day=0):
        # 按情境组装 probe 与时间线
        if situ == "直接回忆":
            probe = (direct, max(base_day, 0))
        elif situ == "改述别称":
            probe = (paraq, max(base_day, 0))
        elif situ == "上下文丢失":
            # 中间插 8 条无关闲聊（滑出上下文窗口），同会话内再问
            fillers = [("今天天气真好呀", 0), ("我们聊点别的吧", 0), ("嗯嗯", 0),
                       ("你猜我在想什么", 0), ("哈哈哈", 0), ("好呀好呀", 0),
                       ("然后呢", 0), ("再说说", 0)]
            store = store + fillers
            probe = (direct, 0)
        elif situ == "隔月再问":
            probe = (direct, max(base_day, 32))
        else:  # 突然提及
            probe = (sudden, max(base_day, 2))
        return store, probe, gold, mtype


SITUATIONS = ["直接回忆", "改述别称", "上下文丢失", "隔月再问", "突然提及"]
TYPES = ["name", "pref", "person", "recent_event", "old_event", "cared"]


def run(pool: str, seed: int, per: int, port: str | None):
    gen = Gen(pool, seed)
    typefns = {"name": gen.name, "pref": gen.pref, "person": gen.person,
               "recent_event": gen.recent_event, "old_event": gen.old_event, "cared": gen.cared}
    T0 = datetime(2026, 1, 1, 18, 0)
    adapter = MemorySystemAdapter(port) if port else None

    grid = {s: {t: [0, 0] for t in TYPES} for s in SITUATIONS}   # [hit,total]
    for situ in SITUATIONS:
        for i in range(per):
            mtype = TYPES[i % len(TYPES)]
            store, probe, gold, _ = typefns[mtype](situ)
            eng = CompanionEngine(config=EngineConfig(state_dir=tempfile.mkdtemp()),
                                  memory_port=adapter)
            uid = f"{situ}-{i}"
            for text, day in store:
                t = T0 + timedelta(days=day, minutes=len(text) % 7)
                eng.prepare_turn(uid, text, now=t)
                eng.commit(uid, text, "（回复）", now=t)
            ptext, pday = probe
            d = eng.prepare_turn(uid, ptext, now=T0 + timedelta(days=pday, hours=1))
            # 严格口径：只认"记忆通道"里的 gold（召回记忆 + 远端档案 + 用户档案块），
            # 绝不把当前输入文本的回声算作召回——否则"突然提及"会假性满分（自欺）
            surface = " ".join(m.text for m in d.memories)
            surface += " " + (d.profile_summary or "")
            surface += " " + (d.user_facts or "")
            hit = gold in surface
            grid[situ][mtype][0] += hit
            grid[situ][mtype][1] += 1

    # ── 报告 ──
    print(f"\n记忆压力测试 · 池={pool} · seed={seed} · 每情境{per}条 · {'接远端' if port else '纯本地'}")
    print("=" * 78)
    hdr = f"{'情境/类型':<12}" + "".join(f"{t:>11}" for t in ["名字","喜好","朋友","几天前","一个月前","最在乎"])
    print(hdr)
    tot_h = tot_n = 0
    for s in SITUATIONS:
        row = f"{s:<12}"
        for t in TYPES:
            h, n = grid[s][t]; tot_h += h; tot_n += n
            row += f"{h}/{n:<3}({100*h//max(n,1):>3}%)"[:11].rjust(11)
        print(row)
    print("-" * 78)
    # 按类型汇总
    print("按记忆类型汇总：")
    for t, zh in zip(TYPES, ["名字","喜好","朋友","几天前的事","一个月前的事","最在乎的事"]):
        h = sum(grid[s][t][0] for s in SITUATIONS); n = sum(grid[s][t][1] for s in SITUATIONS)
        print(f"  {zh:<8} {h}/{n}  {100*h//max(n,1)}%")
    print(f"\n总召回率：{tot_h}/{tot_n} = {100*tot_h//max(tot_n,1)}%")
    return tot_h, tot_n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", choices=["dev", "test"], default="dev")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--per", type=int, default=100)
    ap.add_argument("--port", default=None)
    a = ap.parse_args()
    seed = a.seed if a.seed is not None else (1 if a.pool == "dev" else 7)
    run(a.pool, seed, a.per, a.port)


if __name__ == "__main__":
    main()


# ── 混合负载 torture：一个用户存十几条事实，逐条在竞争下追问（防单事实自欺）──
def run_mixed(pool: str, seed: int, users: int):
    r = random.Random(seed * 31 + 7)
    names = half(NAMES, pool); prefs = half(PREFS, pool)
    rel = RELATIONS; events = half(EVENTS, pool); cared = half(CARED, pool)
    T0 = datetime(2026, 1, 1, 9, 0)
    cat = {"名字": [0, 0], "喜好": [0, 0], "朋友": [0, 0], "事件": [0, 0], "最在乎": [0, 0]}
    for u in range(users):
        eng = CompanionEngine(config=EngineConfig(state_dir=tempfile.mkdtemp()))
        uid = f"life{u}"
        name = r.choice(names)
        my_prefs = r.sample(prefs, 4); my_people = r.sample(names, 3)
        my_rel = [r.choice(rel) for _ in range(3)]
        my_events = r.sample(events, 4); my_cared = r.sample(cared, 2)
        # 一段"生活"：把十几条事实在多天里自然说出
        script = [(f"我叫{name}", 0)]
        script += [(f"我超喜欢{p[0]}", 1 + i) for i, p in enumerate(my_prefs)]
        script += [(f"{pp}是我的{rr}", 4 + i) for i, (pp, rr) in enumerate(zip(my_people, my_rel))]
        script += [(f"上个月我{a}{o}", 6 + i) for i, (a, o) in enumerate(my_events[:2])]
        script += [(f"前几天我{a}{o}", 20 + i) for i, (a, o) in enumerate(my_events[2:])]
        script += [(f"我最在乎的就是{c}", 24 + i) for i, c in enumerate(my_cared)]
        for text, day in script:
            t = T0 + timedelta(days=day, minutes=len(text) % 5)
            eng.prepare_turn(uid, text, now=t); eng.commit(uid, text, "（回复）", now=t)
        probe_day = 40    # 一个月后逐条追问（竞争 + 衰减）

        def surf(q):
            d = eng.prepare_turn(uid, q, now=T0 + timedelta(days=probe_day))
            return " ".join(m.text for m in d.memories) + " " + (d.user_facts or "")

        s = surf("你还记得我叫什么名字吗"); cat["名字"][1] += 1; cat["名字"][0] += name in s
        for p in my_prefs:
            s = surf("你记得我喜欢什么吗"); cat["喜好"][1] += 1; cat["喜好"][0] += p[0] in s
        for pp in my_people:
            s = surf("你记得我那些朋友吗"); cat["朋友"][1] += 1; cat["朋友"][0] += pp in s
        for a, o in my_events:
            s = surf(f"还记得我{a}的事吗"); cat["事件"][1] += 1; cat["事件"][0] += o in s
        for c in my_cared:
            s = surf("你知道我最在乎什么吗"); cat["最在乎"][1] += 1; cat["最在乎"][0] += c in s

    print(f"\n混合负载 torture · 池={pool} · {users}个用户每人十几条事实 · 一个月后竞争追问")
    print("=" * 60)
    th = tn = 0
    for k, (h, n) in cat.items():
        th += h; tn += n
        print(f"  {k:<8} {h}/{n}  {100*h//max(n,1)}%")
    print(f"  {'总计':<8} {th}/{tn}  {100*th//max(tn,1)}%")
