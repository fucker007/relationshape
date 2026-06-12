"""
pipeline/person_normalizer.py — 人物名称归一化

职责：
1. 家庭称谓同义词 → canonical name（硬规则，零延迟）
2. 未来可扩展：相似人名 LLM 判断（"艾佛森" vs "艾弗森"）

称谓映射从 config/i18n/{lang}.yaml 的 family_canonical 段加载。
"""
from __future__ import annotations

from config.lang_config import get_lang_config


def normalize_person_name(name: str) -> str:
    """归一化人物名称。家庭称谓返回 canonical，其他原样返回。"""
    cfg = get_lang_config()
    return cfg.family_canonical.get(name, name)


def is_family_alias(name: str) -> bool:
    """判断是否是家庭称谓（可归一化的）"""
    cfg = get_lang_config()
    return name in cfg.family_canonical
