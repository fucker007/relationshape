"""
config package — settings + language configuration

向后兼容: `from config import settings` 仍然有效
新增: `from config.lang_config import get_lang_config`
"""
# 向后兼容：原 config.py 中的 Settings/settings 直接在此导出
from config.settings import Settings, settings

# 语言配置
from config.lang_config import get_lang_config, reload_lang_config, LangConfig

__all__ = [
    "Settings", "settings",
    "get_lang_config", "reload_lang_config", "LangConfig",
]
