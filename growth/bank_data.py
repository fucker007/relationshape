"""种子题库：7-9 岁（小学 1-3 年级）的挑战内容。

题库是"个性化挑战"的廉价底座——预生成、可离线、答对走规则、零边际成本；
只有"答错的讲解引申"在真实产品里才花一次 LLM（本系统用预置 explain/extend 模拟）。

每条题标注 kind / ability / difficulty(1-3) / 判分方式。客观题给 answer + accept
（关键词子串即算对，容忍口语）；表达/创造是 EFFORT 题，只看是否认真参与。

注意：这里是"够演示、能跑通自适应"的启动集，不是最终题库。真实产品按年龄段扩到
每能力数百条并去重——本文件的结构就是扩库的契约。
"""

from __future__ import annotations

from growth.types import Ability, Challenge, ChallengeKind, ScoreMode

_RAW = [
    # ---------------- 脑力热身（专注）：30 秒、快、即时判 ----------------
    dict(kind=ChallengeKind.WARMUP, difficulty=1,
         prompt="数一数：1, 3, 5, 7, ? 下一个数字是几？",
         answer="9", accept=["9"],
         explain="这是奇数，每次加 2：7 + 2 = 9。",
         extend="那再往下一个呢？9 + 2 = 11。"),
    dict(kind=ChallengeKind.WARMUP, difficulty=1,
         prompt="一个星期有几天？",
         answer="7", accept=["7", "七"],
         explain="一个星期有 7 天：周一到周日。",
         extend="那两个星期呢？7 + 7 = 14 天。"),
    dict(kind=ChallengeKind.WARMUP, difficulty=2,
         prompt="倒着数：5, 4, 3, … 接下来两个数字是？",
         answer="2,1", accept=["2,1", "21", "2、1", "2 1", "2和1"],
         explain="倒着数就是每次减 1：3 → 2 → 1。",
         extend="再倒一个就是 0，再往下就要用到负数啦。"),
    dict(kind=ChallengeKind.WARMUP, difficulty=2,
         prompt="红、黄、红、黄、红，按规律下一个是什么颜色？",
         answer="黄", accept=["黄", "黄色"],
         explain="红黄交替出现，红的后面轮到黄。",
         extend="如果再加一个，黄的后面又轮到红。"),
    dict(kind=ChallengeKind.WARMUP, difficulty=3,
         prompt="快速心算：3 + 4 + 5 等于几？",
         answer="12", accept=["12"],
         explain="3 + 4 = 7，7 + 5 = 12。",
         extend="换个顺序也一样：5 + 5 = 10，再 + 2 = 12，加法可以挑好算的先算。"),

    # ---------------- 逻辑挑战 ----------------
    dict(kind=ChallengeKind.LOGIC, difficulty=2,
         prompt="三只兔子住三间房：白兔不住左边，灰兔不住中间，黑兔住右边。谁住中间？",
         answer="白兔", accept=["白兔", "白"],
         explain="黑兔在右边；灰兔不在中间，只能在左边；剩下中间就是白兔。",
         extend="这种题可以先把'确定的'填上（黑兔右边），再排除，最后剩下的就是答案。"),
    dict(kind=ChallengeKind.LOGIC, difficulty=1,
         prompt="小明比小红高，小红比小刚高。谁最矮？",
         answer="小刚", accept=["小刚", "刚"],
         explain="小明 > 小红 > 小刚，最矮的是小刚。",
         extend="那最高的是谁呢？是小明。"),
    dict(kind=ChallengeKind.LOGIC, difficulty=1,
         prompt="妈妈买了 5 个苹果，吃了 2 个，又买了 3 个，现在有几个？",
         answer="6", accept=["6", "六"],
         explain="5 - 2 = 3，3 + 3 = 6。",
         extend="按发生的顺序一步步算，就不会乱。"),
    dict(kind=ChallengeKind.LOGIC, difficulty=2,
         prompt="如果今天是星期三，那么后天是星期几？",
         answer="星期五", accept=["星期五", "周五", "礼拜五", "五"],
         explain="后天是往后数两天：三 → 四 → 五。",
         extend="那大后天呢？再加一天，是星期六。"),
    dict(kind=ChallengeKind.LOGIC, difficulty=3,
         prompt="找规律：2, 4, 8, 16, 下一个数字是几？",
         answer="32", accept=["32"],
         explain="每个数都是前一个的 2 倍：16 × 2 = 32。",
         extend="这叫'翻倍'。再下一个就是 32 × 2 = 64，长得很快吧。"),

    # ---------------- 表达挑战（EFFORT：只看参与）----------------
    dict(kind=ChallengeKind.EXPRESSION, difficulty=1,
         prompt="请用 30 秒，说一说今天最开心的一件事。",
         explain="说清楚三件小事就很棒：在哪里、发生了什么、你当时什么感觉。",
         extend="下次可以再加一句'为什么开心'，别人就更能感同身受。"),
    dict(kind=ChallengeKind.EXPRESSION, difficulty=2,
         prompt="你最好的朋友是什么样的人？用三句话介绍他/她。",
         explain="介绍一个人，可以说：长什么样、喜欢做什么、你们一起做过什么。",
         extend="举一个具体的小例子，比形容词更打动人。"),
    dict(kind=ChallengeKind.EXPRESSION, difficulty=3,
         prompt="要把'下雨天'讲给一个从没见过雨的人听，你会怎么说？",
         explain="可以从看到的、听到的、闻到的、摸到的去描述，让他像亲身经历一样。",
         extend="用比喻会更好懂，比如'雨点打在伞上像有人在敲小鼓'。"),

    # ---------------- 观察挑战 ----------------
    dict(kind=ChallengeKind.OBSERVATION, difficulty=1,
         prompt="听这个小故事，找出不合理的地方：'早上，太阳从西边升起，小明吃完早饭去上学。'",
         answer="太阳从西边升起", accept=["西", "太阳", "东边", "东"],
         explain="太阳是从'东边'升起、'西边'落下的，故事里说反了。",
         extend="观察就是和你已经知道的常识比一比，对不上的地方就是线索。"),
    dict(kind=ChallengeKind.OBSERVATION, difficulty=2,
         prompt="哪里不合理：'夏天最热的中午，小红穿着厚厚的棉袄去游泳。'",
         answer="夏天穿棉袄", accept=["棉袄", "厚", "夏天", "热"],
         explain="夏天很热，穿厚棉袄不合理，何况是去游泳。",
         extend="一句话里可能不止一个线索，多读两遍能找到更多。"),
    dict(kind=ChallengeKind.OBSERVATION, difficulty=2,
         prompt="一张图里，一只猫在天上自由地飞。这合理吗？为什么？",
         answer="不合理", accept=["不合理", "不能飞", "不会飞", "没有翅膀", "猫不会飞"],
         explain="猫没有翅膀，不会飞，所以不合理。",
         extend="如果给猫画上翅膀，那就是'想象'啦——分清'现实'和'想象'也是观察力。"),
    dict(kind=ChallengeKind.OBSERVATION, difficulty=3,
         prompt="找不合理：'小华把冰块放进冰箱冷冻室，过了一会儿，冰块变成了一杯热水。'",
         answer="冰块变成热水", accept=["热水", "冷冻", "冰", "融化", "变热"],
         explain="冷冻室很冷，冰块只会更硬，不可能变成热水。",
         extend="温度是关键线索：冷的地方东西会变冷，不会变热。"),

    # ---------------- 创造挑战（EFFORT：只看参与）----------------
    dict(kind=ChallengeKind.CREATION, difficulty=1,
         prompt="如果你有一只会飞的恐龙，你最想带它去哪里？为什么？",
         explain="没有标准答案——你的理由越特别越好玩。",
         extend="再想想：到了那里，你和恐龙会一起做什么？"),
    dict(kind=ChallengeKind.CREATION, difficulty=2,
         prompt="发明一种新文具，它能帮你做一件现在做不到的事。它叫什么、能做什么？",
         explain="先想'我有什么烦恼'，再想'什么东西能解决它'，发明就出来了。",
         extend="给它取个名字，画面感会更强，比如'橡皮飞船'。"),
    dict(kind=ChallengeKind.CREATION, difficulty=3,
         prompt="给天上的云朵起一个新名字，并说说为什么这么叫。",
         explain="可以从云的样子、颜色、让你想到什么去起名，理由最重要。",
         extend="同一朵云，不同的人会起不同的名字——这就是想象力的魅力。"),
]


def load_bank() -> list[Challenge]:
    """把原始字典编译成 Challenge 列表，自动补全 ability / score_mode / cid。"""
    from growth.types import KIND_ABILITY

    out: list[Challenge] = []
    counters: dict[str, int] = {}
    for raw in _RAW:
        kind: ChallengeKind = raw["kind"]
        n = counters.get(kind.value, 0) + 1
        counters[kind.value] = n
        cid = f"{kind.value}-{n:02d}"
        mode = ScoreMode.OBJECTIVE if raw.get("answer") else ScoreMode.EFFORT
        out.append(Challenge(
            cid=cid,
            kind=kind,
            ability=KIND_ABILITY[kind],
            difficulty=raw["difficulty"],
            prompt=raw["prompt"],
            score_mode=mode,
            answer=raw.get("answer"),
            accept=list(raw.get("accept", [])),
            options=list(raw.get("options", [])),
            explain=raw.get("explain", ""),
            extend=raw.get("extend", ""),
        ))
    return out
