"""
llm/client.py — OpenAI-style LLM interface backed by Anthropic Claude or Qwen.

Usage:
    client = get_llm_client()
    response = await client.chat([{"role": "user", "content": "hi"}])
    data = await client.chat_json([...])
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Literal

import anthropic
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import settings

try:
    import openai
except ImportError:
    openai = None

logger = logging.getLogger(__name__)

# ── 并发控制 ─────────────────────────────────────────────────────────────────

# LLM 并发限制：防止打爆本地模型或超出 API 限额
_llm_semaphore: asyncio.Semaphore | None = None
_LLM_MAX_CONCURRENT = int(os.environ.get("LLM_MAX_CONCURRENT", "10"))


def _get_semaphore() -> asyncio.Semaphore:
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(_LLM_MAX_CONCURRENT)
    return _llm_semaphore

# ── Types ────────────────────────────────────────────────────────────────────

LLMMessage = dict  # {"role": "system"|"user"|"assistant", "content": str}

_JSON_FORCE_SUFFIX = "\n\n你必须只输出纯 JSON，不要任何解释文字或 markdown 代码块。"


@dataclass
class LLMResponse:
    content: str
    model: str
    input_tokens: int
    output_tokens: int


# ── Client ───────────────────────────────────────────────────────────────────

class LLMClient:
    def __init__(
        self,
        anthropic_client: anthropic.AsyncAnthropic | None = None,
        openai_client: openai.AsyncOpenAI | None = None,
        default_model: str | None = None,
    ) -> None:
        self._provider = settings.llm_provider  # "anthropic" 或 "qwen"

        if self._provider == "qwen":
            if not openai_client and openai:
                self._client = openai.AsyncOpenAI(
                    api_key=settings.qwen_api_key,
                    base_url=settings.qwen_api_url,
                )
            else:
                self._client = openai_client
        else:
            api_key = settings.anthropic_api_key or None
            self._client = anthropic_client or anthropic.AsyncAnthropic(
                **({"api_key": api_key} if api_key else {})
            )

        self._default_model = default_model or (
            settings.qwen_model_name if self._provider == "qwen" else settings.llm_model
        )

    @retry(
        retry=retry_if_exception_type(
            (anthropic.RateLimitError, anthropic.APIConnectionError) if openai is None else (Exception,)
        ),
        wait=wait_exponential(multiplier=1, min=0, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
        response_format: Literal["text", "json"] = "text",
    ) -> LLMResponse:
        sem = _get_semaphore()
        async with sem:
            if self._provider == "qwen":
                return await self._chat_qwen(messages, model, temperature, max_tokens, response_format)
            else:
                return await self._chat_anthropic(messages, model, temperature, max_tokens, response_format)

    async def _chat_anthropic(
        self,
        messages: list[LLMMessage],
        model: str | None,
        temperature: float,
        max_tokens: int,
        response_format: Literal["text", "json"],
    ) -> LLMResponse:
        # Separate system message from user/assistant turns
        system_parts: list[str] = []
        turns: list[dict] = []
        for m in messages:
            if m["role"] == "system":
                system_parts.append(m["content"])
            else:
                turns.append(m)

        if response_format == "json":
            system_parts.append(_JSON_FORCE_SUFFIX)

        system_text = "\n\n".join(system_parts) if system_parts else anthropic.NOT_GIVEN

        t0 = time.monotonic()
        msg = await self._client.messages.create(
            model=model or self._default_model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_text,
            messages=turns,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        content = msg.content[0].text if msg.content else ""
        logger.debug(
            "llm_call model=%s in=%d out=%d latency=%dms",
            msg.model,
            msg.usage.input_tokens,
            msg.usage.output_tokens,
            latency_ms,
        )
        return LLMResponse(
            content=content,
            model=msg.model,
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
        )

    async def _chat_qwen(
        self,
        messages: list[LLMMessage],
        model: str | None,
        temperature: float,
        max_tokens: int,
        response_format: Literal["text", "json"],
    ) -> LLMResponse:
        # OpenAI 兼容 API
        system_text = ""
        turns = []
        for m in messages:
            if m["role"] == "system":
                system_text += m["content"] + "\n\n"
            else:
                turns.append(m)

        if response_format == "json":
            system_text += _JSON_FORCE_SUFFIX

        if system_text:
            turns.insert(0, {"role": "system", "content": system_text})

        t0 = time.monotonic()
        response = await self._client.chat.completions.create(
            model=model or self._default_model,
            messages=turns,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        content = response.choices[0].message.content if response.choices else ""
        logger.debug(
            "llm_call model=%s in=%d out=%d latency=%dms",
            response.model,
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
            latency_ms,
        )
        return LLMResponse(
            content=content,
            model=response.model,
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
        )

    async def chat_json(
        self,
        messages: list[LLMMessage],
        **kwargs,
    ) -> dict:
        kwargs["response_format"] = "json"
        response = await self.chat(messages, **kwargs)

        text = response.content.strip()
        # 移除 markdown 代码块（多种格式）
        text = re.sub(r"^```(?:json|JSON)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试提取 JSON 数组
            match = re.search(r'\[[\s\S]*\]', text)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            # 尝试提取第一个完整 JSON 对象
            match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            logger.error(f"JSON 解析失败\nContent: {text[:200]}")
            raise json.JSONDecodeError("无法解析 JSON", text, 0)


# ── Singleton ────────────────────────────────────────────────────────────────

_client_instance: LLMClient | None = None
_client_provider: str | None = None


def get_llm_client() -> LLMClient:
    global _client_instance, _client_provider
    if _client_instance is None or _client_provider != settings.llm_provider:
        _client_instance = LLMClient()
        _client_provider = settings.llm_provider
    return _client_instance
