"""
pipeline/focus_tracker.py — 近期关注追踪

从每轮对话中提取主题关键词，更新 PersonNode.current_focus。

规则：
  - 匹配已有 focus → frequency+1, weight 重置
  - 未匹配 → 新增 focus
  - 每天衰减 weight *= 0.85（~7天后降到 0.3 自动淘汰）
  - 最多保留 5 个 focus（按 weight 排序）
"""
from __future__ import annotations

import json
import re
from datetime import date

from storage.pg_store import GraphStore


# 主题提取规则（从对话内容提取关注点）
_TOPIC_RULES: list[tuple[re.Pattern, str, str]] = [
    # (pattern, topic_name, default_sentiment)
    (re.compile(r'考试|期末|期中|测验|模考|大考|月考'), "考试", "negative"),
    (re.compile(r'作业|写作业|做作业|功课'), "作业", "neutral"),
    (re.compile(r'数学|语文|英语|物理|化学|生物'), "学科", "neutral"),
    (re.compile(r'篮球|足球|排球|游泳|跑步|运动'), "运动", "positive"),
    (re.compile(r'画画|美术|艺术|手工'), "画画", "positive"),
    (re.compile(r'游戏|王者|吃鸡|手游|网游'), "游戏", "positive"),
    (re.compile(r'生日|过生日|庆祝'), "生日", "positive"),
    (re.compile(r'生病|感冒|发烧|不舒服|医院|吃药'), "健康", "negative"),
    (re.compile(r'转学|换学校|新学校|新班级'), "转学", "negative"),
    (re.compile(r'吵架|打架|矛盾|闹翻|冲突'), "人际冲突", "negative"),
    (re.compile(r'孤独|没人|一个人|寂寞|被排挤'), "孤独感", "negative"),
    (re.compile(r'焦虑|紧张|压力|害怕|担心'), "焦虑", "negative"),
    (re.compile(r'开心|高兴|快乐|兴奋'), "快乐", "positive"),
    (re.compile(r'难过|伤心|委屈|哭'), "难过", "negative"),
    (re.compile(r'旅游|旅行|出去玩|度假|出游'), "旅行", "positive"),
    (re.compile(r'宠物|猫|狗|小动物|养'), "宠物", "positive"),
    (re.compile(r'动漫|漫画|动画|番|cosplay'), "动漫", "positive"),
    (re.compile(r'音乐|唱歌|弹琴|钢琴|吉他'), "音乐", "positive"),
    (re.compile(r'减肥|控制体重|胖了|瘦'), "体重", "negative"),
    (re.compile(r'睡不着|失眠|熬夜|晚睡'), "睡眠", "negative"),
]

# 情绪覆盖规则：如果文本情绪明确，覆盖默认 sentiment
_NEG_EMOTION_PAT = re.compile(r'难过|伤心|害怕|焦虑|烦|不想|不开心|痛苦')
_POS_EMOTION_PAT = re.compile(r'开心|高兴|快乐|期待|兴奋|棒')

DECAY_RATE = 0.85
MAX_FOCUS = 5


class FocusTracker:
    """追踪用户近期关注的话题"""

    def __init__(self, graph_store: GraphStore):
        
        self._gs = graph_store

    async def update(
        self, person_id: str, text: str,
        related_people: list[str] | None = None,
    ) -> list[dict]:
        """
        从本轮对话更新 current_focus。
        返回更新后的 focus 列表。
        """
        node = await self._gs.get_person_node(person_id)
        if not node:
            return []

        focus_raw = node["current_focus"]
        if isinstance(focus_raw, str):
            focus_list: list[dict] = json.loads(focus_raw or "[]")
        else:
            focus_list = list(focus_raw) if focus_raw else []

        today = date.today().isoformat()

        # 1. 时间衰减（对所有已有 focus）
        from datetime import datetime
        for f in focus_list:
            if f.get("last_seen"):
                last_seen_date = datetime.fromisoformat(f["last_seen"]).date()
                days_since = (date.today() - last_seen_date).days
                f["days_since_last_seen"] = days_since

                if last_seen_date != date.today():
                    # 按天数衰减
                    f["weight"] = round(f.get("weight", 1.0) * (DECAY_RATE ** days_since), 3)

        # 2. 提取本轮话题
        matched_topics: list[tuple[str, str]] = []
        for pat, topic, default_sent in _TOPIC_RULES:
            if pat.search(text):
                # 确定 sentiment
                if _NEG_EMOTION_PAT.search(text):
                    sent = "negative"
                elif _POS_EMOTION_PAT.search(text):
                    sent = "positive"
                else:
                    sent = default_sent
                matched_topics.append((topic, sent))

        # 3. 更新或新增
        existing_topics = {f["topic"]: f for f in focus_list}
        for topic, sent in matched_topics:
            if topic in existing_topics:
                f = existing_topics[topic]
                f["frequency"] = f.get("frequency", 1) + 1
                f["last_seen"] = today
                f["weight"] = 1.0  # 重新激活
                f["sentiment"] = sent
                if related_people:
                    rp = f.get("related_people", [])
                    for p in related_people:
                        if p not in rp:
                            rp.append(p)
                    f["related_people"] = rp
            else:
                focus_list.append({
                    "topic": topic,
                    "frequency": 1,
                    "sentiment": sent,
                    "first_seen": today,
                    "last_seen": today,
                    "related_people": related_people or [],
                    "weight": 1.0,
                })

        # 4. 淘汰低权重 + 保留 top N
        focus_list = [f for f in focus_list if f.get("weight", 0) >= 0.2]
        focus_list.sort(key=lambda x: x.get("weight", 0), reverse=True)
        focus_list = focus_list[:MAX_FOCUS]

        # 5. 写入
        await self._gs.update_person_field(person_id, "current_focus", focus_list)

        return focus_list
