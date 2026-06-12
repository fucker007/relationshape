from enum import Enum


class MemoryType(str, Enum):
    IDENTITY      = "identity"       # 身份：姓名、年龄、职业、地域
    PERSONALITY   = "personality"    # 性格：内向/外向、理性/感性、价值观
    BEHAVIOR      = "behavior"       # 行为习惯：作息、沟通方式、决策模式
    PREFERENCE    = "preference"     # 喜好：食物、活动、话题、风格
    AVERSION      = "aversion"       # 厌恶：禁忌话题、反感行为
    EXPERIENCE    = "experience"     # 经历：做过什么事、重要事件
    JOY           = "joy"            # 开心的事：喜悦来源、令人高兴的事
    PAIN          = "pain"           # 不开心的事：创伤、遗憾、痛苦来源
    RELATIONSHIP  = "relationship"   # 人物关系：家人、朋友、老师、对立人物
