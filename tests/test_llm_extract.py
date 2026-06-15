"""LLM 抽取层契约测试（用进程内假 extractor，无网络）：补召回不破精确率。"""

from relationshape import CompanionEngine, EngineConfig
from relationshape.extract_port import ExtractedFacts, parse_extraction
from relationshape.memory import MemoryBank


class _Fake:
    """按关键词造结构化事实，模拟 LLM 抽取（含一次幻觉，验证子串接地拦截）。"""

    def __init__(self):
        self.seen = []

    def extract(self, text):
        self.seen.append(text)
        f = ExtractedFacts()
        if "小名" in text and "核桃" in text:
            f.name = "核桃"
        if "一对" in text and "小满" in text:
            f.friends = [("小满", "死党")]
        if "不喜欢" in text and "木耳" in text:
            f.dislikes = ["木耳"]
        if "腻" in text and "城堡" in text:
            f.retract_likes = ["城堡"]
        if "造谣" in text:
            f.likes = ["火星基地"]          # 原文无此词
        return f


def _eng(**kw):
    return CompanionEngine(config=EngineConfig(state_dir="/tmp/t_llm"), **kw)


def test_default_no_extractor_unchanged():
    eng = _eng()
    assert eng.extractor is None
    eng.prepare_turn("u", "我喜欢恐龙")
    eng.commit("u", "我喜欢恐龙", "ok")
    assert "恐龙" in eng._cache["u"].memory.preferences


def test_llm_fills_rule_gaps():
    eng = _eng(extractor=_Fake())
    for t in ["嗨，我小名核桃", "我和小满是一对死党", "我打心眼里不喜欢木耳"]:
        eng.prepare_turn("v", t)
        eng.commit("v", t, "ok")
    m = eng._cache["v"].memory
    assert m.user_name == "核桃"
    assert "小满" in [p[0] for p in m.user_profile_facts()["people"]]
    assert "木耳" in m.aversions


def test_substring_grounding_blocks_hallucination():
    eng = _eng(extractor=_Fake())
    eng.prepare_turn("h", "我跟你随便造谣聊聊")     # extractor 想塞"火星基地"，原文没有
    eng.commit("h", "我跟你随便造谣聊聊", "ok")
    assert "火星基地" not in eng._cache["h"].memory.preferences


def test_extractor_not_called_on_questions():
    fake = _Fake()
    eng = _eng(extractor=fake)
    q = "你还记得我叫什么吗"
    eng.prepare_turn("q", q)
    eng.commit("q", q, "ok")
    assert q not in fake.seen          # 问句被 is_memory_query 拦在 LLM 之前


def test_fallback_mode_skips_llm_when_rules_hit():
    fake = _Fake()
    eng = _eng(extractor=fake, extractor_mode="fallback")
    t = "我喜欢恐龙"                    # 规则能抽 → fallback 不调 LLM
    eng.prepare_turn("f", t)
    eng.commit("f", t, "ok")
    assert t not in fake.seen


def test_apply_extracted_retraction_and_dedup():
    m = MemoryBank()
    m.preferences = ["城堡"]
    learned = m.apply_extracted(ExtractedFacts(retract_likes=["城堡"]), source_text="我玩腻城堡了")
    assert "城堡" not in m.preferences
    assert any("不再喜欢" in s for s in learned)


def test_apply_extracted_rejects_role_word_name():
    m = MemoryBank()
    m.apply_extracted(ExtractedFacts(name="妈妈"), source_text="我妈妈")
    assert m.user_name != "妈妈"


def test_parse_extraction_is_defensive():
    assert parse_extraction("not json").is_empty()
    assert parse_extraction("").is_empty()
    f = parse_extraction('prefix {"likes":["画画"],"friends":[{"name":"小满","relation":"死党"}]} suffix')
    assert f.likes == ["画画"]
    assert f.friends == [("小满", "死党")]
