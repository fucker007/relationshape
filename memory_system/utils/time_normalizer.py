"""
时间标准化：把相对时间（昨天/上周/3月15号）转换为实际日期
"""
from datetime import date, timedelta
import re


def normalize_time_expr(time_expr: str | None) -> str | None:
    """
    将时间表达式标准化为 YYYY-MM-DD 格式

    支持：
    - 相对时间：昨天/今天/明天/上周/上个月/去年
    - 绝对时间：3月15号/2026-03-26/03-26
    - 模糊时间：最近/这几天/春天

    返回 None 如果无法解析
    """
    if not time_expr or not isinstance(time_expr, str):
        return None

    time_expr = time_expr.strip()
    today = date.today()

    # 相对时间
    if time_expr == "今天":
        return str(today)
    elif time_expr == "昨天":
        return str(today - timedelta(days=1))
    elif time_expr == "明天":
        return str(today + timedelta(days=1))
    elif time_expr == "前天":
        return str(today - timedelta(days=2))
    elif time_expr == "后天":
        return str(today + timedelta(days=2))

    # N天前
    m = re.match(r"(\d+)天前", time_expr)
    if m:
        days = int(m.group(1))
        return str(today - timedelta(days=days))

    # 上周/下周
    if time_expr == "上周":
        return str(today - timedelta(weeks=1))
    elif time_expr == "下周":
        return str(today + timedelta(weeks=1))

    # 上个月/下个月
    if time_expr == "上个月":
        if today.month == 1:
            return f"{today.year-1}-12-{today.day:02d}"
        else:
            return f"{today.year}-{today.month-1:02d}-{today.day:02d}"
    elif time_expr == "下个月":
        if today.month == 12:
            return f"{today.year+1}-01-{today.day:02d}"
        else:
            return f"{today.year}-{today.month+1:02d}-{today.day:02d}"

    # 去年/今年/明年
    if time_expr == "去年":
        return f"{today.year-1}-{today.month:02d}-{today.day:02d}"
    elif time_expr == "今年":
        return str(today)
    elif time_expr == "明年":
        return f"{today.year+1}-{today.month:02d}-{today.day:02d}"

    # 月日格式：3月15号 / 03-15 / 3-15
    m = re.match(r"(\d{1,2})月(\d{1,2})号?", time_expr)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{today.year}-{month:02d}-{day:02d}"

    # 已是 YYYY-MM-DD 格式
    if re.match(r"\d{4}-\d{2}-\d{2}", time_expr):
        return time_expr

    # 无法解析，返回原值（让后续处理决定）
    return None
