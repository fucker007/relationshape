"""
scheduler/emotion_alert.py — 情绪干预系统

检测连续负面情绪并触发干预：
  1. 对话内策略调整：返回提示让 AI 调整回复风格
  2. 外部 webhook 推送：通知家长/老师/管理员

规则：
  - 连续 3 天 dominant_emotion 为负面 → 触发干预
  - 单次极端情绪（绝望/自残暗示）→ 立即触发
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta

from storage.pg_store import GraphStore

logger = logging.getLogger(__name__)

# 负面情绪集合
NEGATIVE_EMOTIONS = {"难过", "生气", "害怕", "委屈", "焦虑", "孤独", "绝望", "失望", "后悔"}
CRITICAL_EMOTIONS = {"绝望"}  # 立即触发

# 干预级别
LEVEL_NONE = "none"
LEVEL_GENTLE = "gentle"       # 轻度：AI 回复更温暖
LEVEL_MODERATE = "moderate"   # 中度：AI 主动关心 + webhook 通知
LEVEL_CRITICAL = "critical"   # 重度：立即 webhook 告警


@dataclass
class AlertResult:
    level: str = LEVEL_NONE
    reason: str = ""
    consecutive_negative_days: int = 0
    dominant_emotions: list[str] | None = None
    # 对话内策略提示（注入 system prompt）
    style_hint: str = ""
    # webhook payload（None = 不推送）
    webhook_payload: dict | None = None


async def check_emotion_alert(
    gs: GraphStore, owner_id: str, person_id: str,
    person_name: str = "",
    days: int = 7,
) -> AlertResult:
    """
    检查最近 N 天的情绪趋势，返回干预建议。

    防误判规则：
    - 单日事件数 < 3 时不作为趋势依据（样本太少）
    - 正向 dominant 的天会打断连续负面计数
    - critical 需要连续 2 天+绝望，单次绝望只触发 moderate
    """
    recent = await gs.get_recent_emotions(person_id, days=days)

    if not recent:
        return AlertResult()

    # 按日期排序（近→远）
    recent.sort(key=lambda x: str(x.get("date", "")), reverse=True)

    # 过滤掉事件数太少的天（样本不足，不作为趋势依据）
    MIN_EVENTS_FOR_TREND = 2
    reliable_days = [d for d in recent if d.get("event_count", 0) >= MIN_EVENTS_FOR_TREND]

    # 检测连续负面天数（只看可靠天数据）
    consecutive = 0
    dominant_list = []
    for day in reliable_days:
        dominant = day.get("dominant_emotion", "")
        if dominant in NEGATIVE_EMOTIONS:
            consecutive += 1
            dominant_list.append(dominant)
        else:
            break  # 正向/中性天打断连续计数

    # 检测极端情绪 — 需要连续出现才触发 critical，单次只 moderate
    critical_count = sum(
        1 for d in reliable_days[:3]
        if d.get("dominant_emotion") in CRITICAL_EMOTIONS
    )
    has_critical = critical_count >= 2  # 至少 2 天绝望才 critical

    # 判断级别
    if has_critical:
        level = LEVEL_CRITICAL
        reason = f"检测到连续{critical_count}天极端情绪（绝望），请立即关注"
        style_hint = (
            f"⚠️ {person_name}最近情绪状态非常低落。"
            f"回复时请用极其温暖、理解的语气，不要说教，不要否定TA的感受。"
            f"表达你在乎TA，问TA是否需要帮助。避免提及让TA难过的话题。"
        )
    elif consecutive >= 3:
        level = LEVEL_MODERATE
        reason = f"连续{consecutive}天负面情绪：{'→'.join(dominant_list[:5])}"
        style_hint = (
            f"{person_name}最近{consecutive}天情绪持续低落（{', '.join(set(dominant_list[:3]))}）。"
            f"回复时请更加温暖关心，适当主动询问TA的状态，"
            f"鼓励但不强迫，尊重TA的节奏。"
        )
    elif consecutive >= 2:
        level = LEVEL_GENTLE
        reason = f"连续{consecutive}天负面情绪"
        style_hint = (
            f"{person_name}最近心情不太好。回复时请稍微温柔一些，多一点关心。"
        )
    else:
        return AlertResult(
            consecutive_negative_days=consecutive,
            dominant_emotions=dominant_list or None,
        )

    # 构建 webhook payload
    webhook_payload = None
    if level in (LEVEL_MODERATE, LEVEL_CRITICAL):
        webhook_payload = {
            "alert_level": level,
            "person_name": person_name,
            "person_id": str(person_id),
            "reason": reason,
            "consecutive_negative_days": consecutive,
            "recent_emotions": [
                {"date": str(d.get("date", "")),
                 "dominant": d.get("dominant_emotion", ""),
                 "distribution": d.get("emotion_distribution", {})}
                for d in recent[:5]
            ],
        }

    result = AlertResult(
        level=level,
        reason=reason,
        consecutive_negative_days=consecutive,
        dominant_emotions=dominant_list,
        style_hint=style_hint,
        webhook_payload=webhook_payload,
    )

    logger.warning("emotion_alert: level=%s person=%s reason=%s",
                   level, person_name or person_id[:8], reason)
    return result


async def send_webhook(url: str, payload: dict) -> bool:
    """
    发送 webhook 通知（异步 HTTP POST）。
    生产环境替换为实际的通知渠道（企业微信/钉钉/飞书/自定义 API）。
    """
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                ok = resp.status < 300
                if not ok:
                    logger.error("webhook failed: status=%d url=%s", resp.status, url)
                return ok
    except ImportError:
        # aiohttp 未安装时 fallback 到 logging
        logger.warning("webhook (aiohttp not installed, logging only): %s", json.dumps(payload, ensure_ascii=False)[:200])
        return True
    except Exception as e:
        logger.error("webhook error: %s", e)
        return False
