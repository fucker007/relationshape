"""中文轻量文本工具：无依赖的近似分词、笑声/问句/风格信号检测。

这些是默认启发式实现。语义理解的精度天花板在这里，
上层都通过结构化结果消费，将来可整体替换为分类模型而不动其他模块。
"""

from __future__ import annotations

import re

_PUNCT_RE = re.compile(
    r"[，。！？、…：；,.!?:;\s　\"'“”‘’()（）【】\[\]<>《》~～\-—_·]+"
)

# 单字停用词：用于切出"内容词"片段
_STOP_CHARS = set(
    "的了呢吗啊呀吧哦噢嗯哎诶是就都很也还在有和与跟对把被让从到不没这那个一又再才只要会能可去来上下里外们我你他她它咱您"
)

LAUGH_RE = re.compile(r"(哈哈|嘿嘿|嘻嘻|hhh+|233+|笑死|太逗|好好笑|笑喷|绷不住|lol|🤣|😂|😆)", re.IGNORECASE)

_ADVICE_RE = re.compile(r"(怎么办|该怎么|咋办|你说我(该|要|应该)|有什么(建议|办法|主意)|帮我想想|给点建议)")

_QUESTION_TAIL_RE = re.compile(r"(吗|呢|么)\s*[?？]?\s*$")
_QUESTION_WORD_RE = re.compile(
    r"(怎么|为什么|为啥|什么|啥|哪|几点|多少|咋|谁|何时"
    r"|是不是|会不会|能不能|行不行|好不好|要不要|有没有|对不对)"   # 正反问（A-not-A）
)

_FORMAL_MARKERS = ("您", "请问", "麻烦", "感谢", "劳驾", "打扰")
_CASUAL_MARKERS = ("哈哈", "啦", "呗", "嘛", "哎", "卧槽", "牛", "绝了", "yyds", "emo", "蛮", "超")


def normalize(text: str) -> str:
    return (text or "").strip()


def content_runs(text: str) -> list[str]:
    """切出连续的内容词片段（≥2字），近似"话题词"。"""
    cleaned = _PUNCT_RE.sub(" ", text)
    runs: list[str] = []
    cur = ""
    for ch in cleaned:
        if ch == " " or ch in _STOP_CHARS:
            if len(cur) >= 2:
                runs.append(cur)
            cur = ""
        else:
            cur += ch
    if len(cur) >= 2:
        runs.append(cur)
    # 去重保序
    seen: set[str] = set()
    out = []
    for r in runs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def bigrams(text: str) -> set[str]:
    """字符二元组集合：无分词条件下做主题重叠度的稳健近似。"""
    cleaned = _PUNCT_RE.sub("", text)
    if len(cleaned) < 2:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


def is_laughter(text: str) -> bool:
    return bool(LAUGH_RE.search(text))


def is_question(text: str) -> bool:
    t = text.strip()
    if "?" in t or "？" in t:
        return True
    if _QUESTION_TAIL_RE.search(t):
        return True
    return bool(_QUESTION_WORD_RE.search(t))


def asks_advice(text: str) -> bool:
    return bool(_ADVICE_RE.search(text))


def formality_signal(text: str) -> float | None:
    """返回 0(随意)..1(正式)，无信号时返回 None。"""
    formal = sum(1 for m in _FORMAL_MARKERS if m in text)
    casual = sum(1 for m in _CASUAL_MARKERS if m in text)
    if formal == 0 and casual == 0:
        return None
    return formal / (formal + casual)


def energy_signal(text: str) -> float:
    """0..1 的唤起度近似：感叹、笑声、重复标点。"""
    score = 0.25
    score += 0.18 * min(3, text.count("！") + text.count("!"))
    if is_laughter(text):
        score += 0.2
    if re.search(r"(超|特别|非常|太)..{0,4}(了|啦)", text):
        score += 0.1
    return min(1.0, score)
