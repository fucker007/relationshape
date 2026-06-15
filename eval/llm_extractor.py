"""DeepSeek 事实抽取适配器：MemoryExtractorPort 的参考实现（放 eval/，不污染零依赖内核）。

把它接进引擎即可让 LLM 补齐规则漏掉的口语句式：
    from eval.llm_extractor import DeepSeekExtractor
    engine = CompanionEngine(extractor=DeepSeekExtractor())

· 端点/鉴权沿用本仓 DeepSeek 约定：DEEPSEEK_API_KEY / DEEPSEEK_API_URL / DEEPSEEK_MODEL。
· 按原文做**精确缓存**：模板×槽位高度重复，缓存后即便上万轮也只打几千次真实请求。
· 任何网络/解析异常都返回 None（引擎自动退化为纯规则，主流程不受影响）。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from relationshape.extract_port import ExtractedFacts, build_messages, parse_extraction  # noqa: E402

API_URL = os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise SystemExit("需要环境变量 DEEPSEEK_API_KEY")
    return key


class DeepSeekExtractor:
    """实现 MemoryExtractorPort.extract(text) -> ExtractedFacts | None。"""

    def __init__(self, key: Optional[str] = None, *, temperature: float = 0.0,
                 max_tokens: int = 256, retries: Optional[int] = None,
                 timeout: Optional[int] = None):
        self.key = key or _api_key()
        self.temperature = temperature      # 抽取要确定性，温度 0
        self.max_tokens = max_tokens
        self.retries = _env_int("DEEPSEEK_RETRIES", 2) if retries is None else retries
        self.timeout = _env_int("DEEPSEEK_TIMEOUT", 60) if timeout is None else timeout
        self._cache: dict[str, ExtractedFacts] = {}
        self.calls = 0                       # 真实请求计数（缓存命中不计）

    def extract(self, text: str) -> Optional[ExtractedFacts]:
        text = (text or "").strip()
        if not text:
            return None
        if text in self._cache:
            return self._cache[text]
        raw = self._post(build_messages(text))
        facts = parse_extraction(raw) if raw is not None else None
        if facts is not None:
            self._cache[text] = facts
        return facts

    def _post(self, messages) -> Optional[str]:
        payload = {
            "model": MODEL,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(
                    API_URL, data=data,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {self.key}"},
                    method="POST",
                )
                self.calls += 1
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    obj = json.loads(resp.read().decode("utf-8"))
                return obj["choices"][0]["message"]["content"].strip()
            except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
        return None


if __name__ == "__main__":
    # 烟测：手动看几句的抽取结果
    ex = DeepSeekExtractor()
    for s in ["嗨，我小名核桃", "我妈妈总喜欢给我煮面条，但我不喜欢吃面条",
              "我和小满是一对死党", "你还记得我爱玩什么吗", "我现在不爱搭城堡了"]:
        print(f"{s}\n  -> {ex.extract(s)}")
