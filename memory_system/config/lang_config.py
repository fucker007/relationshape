"""
config/lang_config.py — 语言配置加载器

职责：
1. 读取 MEMORY_LANG 环境变量（默认 zh）
2. 加载对应 config/i18n/{lang}.yaml
3. 提供全局单例 LangConfig，各模块通过 get_lang_config() 获取
4. 编译正则表达式（一次性，启动时完成）

用法：
    from config.lang_config import get_lang_config
    cfg = get_lang_config()
    cfg.intent_rules          # dict[str, list[re.Pattern]]
    cfg.person_extraction     # dict
    cfg.profile_template      # dict
    cfg.extract               # dict
    cfg.family_canonical      # dict[str, str]
"""
from __future__ import annotations

import os
import re
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent / "i18n"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 让 MEMORY_LANG 可以从项目根目录 .env 生效
load_dotenv(_PROJECT_ROOT / ".env", override=False)


@dataclass
class LangConfig:
    """语言配置（不可变，启动后不再修改）"""
    lang: str = "zh"

    # 编译后的 intent 规则：intent_name → [compiled regex]
    intent_rules: dict[str, list[re.Pattern]] = field(default_factory=dict)

    # 原始 intent 规则顺序（保持 yaml 中的优先级）
    intent_order: list[str] = field(default_factory=list)

    # 人名提取配置（原始 dict，各语言结构不同，由调用方解释）
    person_extraction: dict[str, Any] = field(default_factory=dict)

    # Profile 摘要模板
    profile_template: dict[str, Any] = field(default_factory=dict)

    # Extract 提取配置（system_prompt, critical_keywords, emotion_keywords 等）
    extract: dict[str, Any] = field(default_factory=dict)

    # 家庭称谓归一化
    family_canonical: dict[str, str] = field(default_factory=dict)

    # 原始 yaml 数据（备用）
    _raw: dict[str, Any] = field(default_factory=dict)


def _compile_intent_rules(raw_rules: dict[str, list[str]]) -> tuple[dict[str, list[re.Pattern]], list[str]]:
    """编译 intent 正则规则，保持顺序"""
    compiled = {}
    order = []
    for intent_name, patterns in raw_rules.items():
        compiled[intent_name] = [re.compile(p, re.IGNORECASE) for p in patterns]
        order.append(intent_name)
    return compiled, order


def load_lang_config(lang: str | None = None) -> LangConfig:
    """加载指定语言配置，返回 LangConfig 实例"""
    if lang is None:
        lang = os.environ.get("MEMORY_LANG", "zh").lower().strip()

    config_file = _CONFIG_DIR / f"{lang}.yaml"
    if not config_file.exists():
        logger.warning(f"Language config not found: {config_file}, falling back to zh")
        lang = "zh"
        config_file = _CONFIG_DIR / "zh.yaml"

    if not config_file.exists():
        raise FileNotFoundError(f"Language config file not found: {config_file}")

    with open(config_file, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # 编译 intent 规则
    intent_compiled, intent_order = _compile_intent_rules(raw.get("intent_rules", {}))

    cfg = LangConfig(
        lang=lang,
        intent_rules=intent_compiled,
        intent_order=intent_order,
        person_extraction=raw.get("person_extraction", {}),
        profile_template=raw.get("profile_template", {}),
        extract=raw.get("extract", {}),
        family_canonical=raw.get("family_canonical", {}),
        _raw=raw,
    )

    logger.info(f"Loaded language config: {lang} ({config_file})")
    logger.info(f"  intent_rules: {list(cfg.intent_rules.keys())}")
    logger.info(f"  critical_keywords: {len(cfg.extract.get('critical_keywords', []))} items")
    logger.info(f"  family_canonical: {len(cfg.family_canonical)} mappings")

    return cfg


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------
_global_config: LangConfig | None = None


def get_lang_config() -> LangConfig:
    """获取全局语言配置（懒加载单例）"""
    global _global_config
    if _global_config is None:
        _global_config = load_lang_config()
    return _global_config


def reload_lang_config(lang: str | None = None) -> LangConfig:
    """重新加载语言配置（用于测试或动态切换）"""
    global _global_config
    _global_config = load_lang_config(lang)
    return _global_config
