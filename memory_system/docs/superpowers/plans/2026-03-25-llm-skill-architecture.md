# LLM Skill Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LLM-driven memory merging, gate-based filtering, and decay-based forgetting to prevent unbounded memory growth.

**Architecture:** Four focused Skill modules (ExtractionGate, UsageGate, MergeJudge, ForgetJudge) powered by a unified `LLMClient` wrapper; an async bounded `MergeWorker` handles write-time deduplication in the background; a scheduled `forget_scan_job` handles decay + capacity-based pruning.

**Tech Stack:** Python 3.11+, `anthropic` SDK, `tenacity`, `asyncio`, `pytest-asyncio`, existing `Settings`/`PgStore`/`RedisStore` infrastructure.

**Spec:** `memory_system/README_DESIGN.md`

---

## File Map

### New Files
| File | Responsibility |
|---|---|
| `llm/__init__.py` | Package init |
| `llm/client.py` | `LLMClient`, `LLMMessage`, `LLMResponse`, `get_llm_client()` |
| `llm/skills/__init__.py` | Package init |
| `llm/skills/extraction_gate.py` | `ExtractionGateInput/Output`, `ExtractionGate.check()` |
| `llm/skills/usage_gate.py` | `UsageGateInput/Output`, `UsageGate.decide()` |
| `llm/skills/merge_judge.py` | `MergeJudgeInput/Output`, `MergeJudge.decide()` |
| `llm/skills/forget_judge.py` | `ForgetJudgeInput/Output`, `ForgetJudge.decide()` |
| `pipeline/merge_worker.py` | `MergeTask`, `MergeWorker`, `MergeTaskQueue` |
| `tests/unit/test_llm_client.py` | LLMClient unit tests |
| `tests/unit/test_extraction_gate.py` | ExtractionGate accuracy tests |
| `tests/unit/test_usage_gate.py` | UsageGate accuracy tests (real LLM) |
| `tests/unit/test_merge_judge.py` | MergeJudge accuracy tests (real LLM) ⭐ |
| `tests/unit/test_forget_judge.py` | ForgetJudge accuracy tests |
| `tests/integration/test_merge_worker.py` | Concurrency + resource release |
| `tests/integration/test_forget_scan.py` | Decay formula + dual-track trigger |
| `tests/e2e/test_recall_with_gate.py` | Full recall pipeline with UsageGate |

### Modified Files
| File | Change |
|---|---|
| `config.py` | Add LLM model names, merge/forget config fields |
| `models/memory.py` | Add `last_accessed_at` field to `MemoryEntry` |
| `storage/pg_store.py` | Add `soft_delete()`, `hard_delete_later()`, `get_candidates_for_forget()` |
| `pipeline/extractor.py` | Replace `anthropic.AsyncAnthropic` with `LLMClient` |
| `pipeline/worker.py` | Add ExtractionGate before extraction; route writes to `MergeTaskQueue` |
| `retrieval/recall.py` | Add `GateResult`, `recall_with_gate()` |
| `scheduler/jobs.py` | Add `forget_scan_job()` + register in `create_scheduler()` |

---

## Task 1: Config + Data Model Foundation

**Files:**
- Modify: `config.py`
- Modify: `models/memory.py`

- [ ] **Step 1: Write failing test for new config fields**

```python
# tests/unit/test_config.py (append to existing or create)
from config import Settings

def test_new_llm_config_fields():
    s = Settings()
    assert hasattr(s, "llm_model")
    assert hasattr(s, "llm_fast_model")
    assert hasattr(s, "merge_concurrency")
    assert hasattr(s, "merge_queue_maxsize")
    assert hasattr(s, "merge_similar_limit")
    assert hasattr(s, "merge_similarity_threshold")
    assert hasattr(s, "forget_score_threshold")
    assert hasattr(s, "forget_importance_protect")
    assert hasattr(s, "forget_batch_size")
    assert hasattr(s, "forget_judge_concurrency")
    assert hasattr(s, "forget_hard_delete_delay")
    assert hasattr(s, "memory_quota")
    assert hasattr(s, "forget_lambda")
    assert s.merge_concurrency == 20
    assert s.forget_score_threshold == 0.05

def test_memory_entry_has_last_accessed_at():
    from models.memory import MemoryEntry
    import inspect
    fields = {f for f in MemoryEntry.model_fields}
    assert "last_accessed_at" in fields
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /home/zihai/workspace/Agent_server_design/memory_system
python -m pytest tests/unit/test_config.py::test_new_llm_config_fields -v 2>&1 | tail -5
```

Expected: `AttributeError` or `AssertionError`

- [ ] **Step 3: Add new fields to `config.py`**

Append to `Settings` class in `config.py`:

```python
    # LLM model config
    llm_model: str = "claude-sonnet-4-6-20250514"
    llm_fast_model: str = "claude-haiku-4-5-20251001"

    # Merge Worker
    merge_concurrency: int = 20
    merge_queue_maxsize: int = 500
    merge_similar_limit: int = 5
    merge_similarity_threshold: float = 0.82

    # Forgetting
    forget_score_threshold: float = 0.05
    forget_importance_protect: float = 0.8
    forget_batch_size: int = 50
    forget_judge_concurrency: int = 5
    forget_hard_delete_delay: int = 3600

    # Capacity quotas (per person, per type)
    memory_quota: dict = {
        "identity": 20_000, "personality": 15_000,
        "behavior": 30_000, "preference": 50_000,
        "aversion": 50_000, "experience": 150_000,
        "joy": 80_000, "pain": 80_000,
    }

    # Decay rates per type
    forget_lambda: dict = {
        "identity": 0.001, "personality": 0.002,
        "behavior": 0.005, "experience": 0.008,
        "preference": 0.010, "aversion": 0.010,
        "joy": 0.015, "pain": 0.015,
    }
```

- [ ] **Step 4: Add `last_accessed_at` to `MemoryEntry` in `models/memory.py`**

Find the `MemoryEntry` class and add after `created_at`:

```python
    last_accessed_at: datetime | None = None  # Reset on each recall hit (decay clock)
```

- [ ] **Step 5: Run tests to confirm pass**

```bash
python -m pytest tests/unit/test_config.py -v 2>&1 | tail -10
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add config.py models/memory.py tests/unit/test_config.py
git commit -m "feat: add llm/merge/forget config fields and last_accessed_at to MemoryEntry"
```

---

## Task 2: PgStore — Soft Delete + Hard Delete

**Files:**
- Modify: `storage/pg_store.py`
- Test: `tests/unit/test_pg_store_delete.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_pg_store_delete.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_soft_delete_marks_is_deleted():
    """soft_delete() sets is_deleted=True for given IDs."""
    pg = MagicMock()
    pg.pool = AsyncMock()
    conn = AsyncMock()
    pg.pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pg.pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    from storage.pg_store import PgStore
    store = PgStore.__new__(PgStore)
    store.pool = pg.pool

    await store.soft_delete(["id-1", "id-2"])
    conn.execute.assert_called_once()
    call_args = conn.execute.call_args[0][0]
    assert "is_deleted" in call_args.lower() or "UPDATE" in call_args

@pytest.mark.asyncio
async def test_hard_delete_later_deletes_after_delay():
    """hard_delete_later() physically removes records after delay seconds."""
    import asyncio
    from storage.pg_store import PgStore
    store = PgStore.__new__(PgStore)
    store.pool = AsyncMock()
    conn = AsyncMock()
    store.pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    store.pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    # delay=0 for test speed
    await store.hard_delete_later(["id-1"], delay=0)
    conn.execute.assert_called_once()
    call_args = conn.execute.call_args[0][0]
    assert "DELETE" in call_args or "delete" in call_args.lower()
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/unit/test_pg_store_delete.py -v 2>&1 | tail -5
```

Expected: `AttributeError: 'PgStore' object has no attribute 'soft_delete'`

- [ ] **Step 3: Add methods to `PgStore` in `storage/pg_store.py`**

Append to `PgStore` class:

```python
    async def soft_delete(self, memory_ids: list[str]) -> None:
        """Mark memories as deleted (immediately removed from search)."""
        if not memory_ids:
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE memory_entries SET is_deleted = TRUE WHERE memory_id = ANY($1::uuid[])",
                memory_ids,
            )

    async def hard_delete_later(self, memory_ids: list[str], delay: int = 3600) -> None:
        """Physically delete memories after `delay` seconds (gives window for undo)."""
        if not memory_ids:
            return
        import asyncio as _asyncio
        await _asyncio.sleep(delay)
        async with self.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM memory_entries WHERE memory_id = ANY($1::uuid[]) AND is_deleted = TRUE",
                memory_ids,
            )

    async def get_candidates_for_forget(
        self,
        person_id: str,
        forget_score_threshold: float,
        limit: int = 1000,
    ) -> list[dict]:
        """Return memories with low forget_score for a person.
        forget_score = importance_score * exp(-lambda * days_since_accessed)
        Calculated in Python after retrieval (no stored column needed).
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT memory_id, memory_type, content, importance_score,
                       last_accessed_at, created_at
                FROM memory_entries
                WHERE person_id = $1
                  AND is_deleted = FALSE
                  AND is_merged = FALSE
                ORDER BY importance_score ASC
                LIMIT $2
                """,
                person_id,
                limit,
            )
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/unit/test_pg_store_delete.py -v 2>&1 | tail -10
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add storage/pg_store.py tests/unit/test_pg_store_delete.py
git commit -m "feat: add soft_delete, hard_delete_later, get_candidates_for_forget to PgStore"
```

---

## Task 3: LLM Client

**Files:**
- Create: `llm/__init__.py`
- Create: `llm/client.py`
- Create: `llm/skills/__init__.py`
- Test: `tests/unit/test_llm_client.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_llm_client.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_chat_returns_llm_response():
    from llm.client import LLMClient, LLMResponse
    mock_anthropic = MagicMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="hello")]
    mock_msg.model = "claude-haiku-4-5-20251001"
    mock_msg.usage.input_tokens = 10
    mock_msg.usage.output_tokens = 5
    mock_anthropic.messages.create = AsyncMock(return_value=mock_msg)

    client = LLMClient(anthropic_client=mock_anthropic, default_model="claude-haiku-4-5-20251001")
    response = await client.chat([{"role": "user", "content": "hi"}])

    assert isinstance(response, LLMResponse)
    assert response.content == "hello"
    assert response.input_tokens == 10

@pytest.mark.asyncio
async def test_chat_json_appends_json_instruction():
    from llm.client import LLMClient
    mock_anthropic = MagicMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text='{"key": "value"}')]
    mock_msg.model = "claude-haiku-4-5-20251001"
    mock_msg.usage.input_tokens = 10
    mock_msg.usage.output_tokens = 5
    mock_anthropic.messages.create = AsyncMock(return_value=mock_msg)

    client = LLMClient(anthropic_client=mock_anthropic, default_model="test-model")
    result = await client.chat_json([
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "test"},
    ])

    assert result == {"key": "value"}
    call_kwargs = mock_anthropic.messages.create.call_args[1]
    system_content = call_kwargs.get("system", "")
    assert "JSON" in system_content

@pytest.mark.asyncio
async def test_retry_on_rate_limit():
    import anthropic
    from llm.client import LLMClient
    mock_anthropic = MagicMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="ok")]
    mock_msg.model = "m"
    mock_msg.usage.input_tokens = 1
    mock_msg.usage.output_tokens = 1

    call_count = 0
    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise anthropic.RateLimitError("rate limit", response=MagicMock(), body={})
        return mock_msg

    mock_anthropic.messages.create = side_effect
    client = LLMClient(anthropic_client=mock_anthropic, default_model="m")
    response = await client.chat([{"role": "user", "content": "test"}])
    assert call_count == 3
    assert response.content == "ok"

def test_get_llm_client_returns_singleton():
    from llm.client import get_llm_client
    c1 = get_llm_client()
    c2 = get_llm_client()
    assert c1 is c2
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/unit/test_llm_client.py -v 2>&1 | tail -5
```

Expected: `ModuleNotFoundError: No module named 'llm'`

- [ ] **Step 3: Create `llm/__init__.py` and `llm/skills/__init__.py`**

```bash
mkdir -p llm/skills
touch llm/__init__.py llm/skills/__init__.py
```

- [ ] **Step 4: Create `llm/client.py`**

```python
"""
llm/client.py — OpenAI-style LLM interface backed by Anthropic Claude.

Usage:
    client = get_llm_client()
    response = await client.chat([{"role": "user", "content": "hi"}])
    data = await client.chat_json([...])
"""
from __future__ import annotations

import json
import logging
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

logger = logging.getLogger(__name__)

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
        default_model: str | None = None,
    ) -> None:
        self._client = anthropic_client or anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key
        )
        self._default_model = default_model or settings.llm_model

    @retry(
        retry=retry_if_exception_type(
            (anthropic.RateLimitError, anthropic.APIConnectionError)
        ),
        wait=wait_exponential(multiplier=1, min=1, max=30),
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

    async def chat_json(
        self,
        messages: list[LLMMessage],
        **kwargs,
    ) -> dict:
        kwargs["response_format"] = "json"
        response = await self.chat(messages, **kwargs)
        # Strip markdown code fences if present
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.content.strip())
        return json.loads(text)


# ── Singleton ────────────────────────────────────────────────────────────────

_client_instance: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client_instance
    if _client_instance is None:
        _client_instance = LLMClient()
    return _client_instance
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/unit/test_llm_client.py -v 2>&1 | tail -15
```

Expected: 4 PASS

- [ ] **Step 6: Commit**

```bash
git add llm/ tests/unit/test_llm_client.py
git commit -m "feat: add LLMClient with OpenAI-style interface, retry, JSON mode, singleton"
```

---

## Task 4: Skill 1 — ExtractionGate

**Files:**
- Create: `llm/skills/extraction_gate.py`
- Test: `tests/unit/test_extraction_gate.py`

- [ ] **Step 1: Write failing tests (mock LLM)**

```python
# tests/unit/test_extraction_gate.py
import pytest
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_extraction_gate_passes_personal_fact():
    from llm.skills.extraction_gate import ExtractionGate, ExtractionGateInput
    mock_client = MagicMock()
    mock_client.chat_json = AsyncMock(return_value={
        "should_extract": True, "reason": "contains personal preference", "confidence": 0.9
    })
    gate = ExtractionGate(client=mock_client)
    result = await gate.check(ExtractionGateInput(
        message="我喜欢吃草莓", role="user"
    ))
    assert result.should_extract is True

@pytest.mark.asyncio
async def test_extraction_gate_blocks_small_talk():
    from llm.skills.extraction_gate import ExtractionGate, ExtractionGateInput
    mock_client = MagicMock()
    mock_client.chat_json = AsyncMock(return_value={
        "should_extract": False, "reason": "pure greeting", "confidence": 0.95
    })
    gate = ExtractionGate(client=mock_client)
    result = await gate.check(ExtractionGateInput(message="好的", role="user"))
    assert result.should_extract is False

@pytest.mark.asyncio
async def test_extraction_gate_uses_fast_model():
    from llm.skills.extraction_gate import ExtractionGate, ExtractionGateInput
    from config import settings
    mock_client = MagicMock()
    mock_client.chat_json = AsyncMock(return_value={
        "should_extract": True, "reason": "test", "confidence": 0.8
    })
    gate = ExtractionGate(client=mock_client)
    await gate.check(ExtractionGateInput(message="test", role="user"))
    call_kwargs = mock_client.chat_json.call_args[1]
    assert call_kwargs.get("model") == settings.llm_fast_model
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/unit/test_extraction_gate.py -v 2>&1 | tail -5
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `llm/skills/extraction_gate.py`**

```python
"""
llm/skills/extraction_gate.py — Skill 1: Should this message be extracted?

Filters out low-value messages (greetings, commands, pure questions)
before the expensive Claude extraction call.

Target: recall >= 90% (valid memories pass through), false-positive <= 30%.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from config import settings
from llm.client import LLMClient, get_llm_client

_SYSTEM_PROMPT = """\
你是一个记忆提取门控系统。判断用户消息是否值得提取为长期记忆。

判断标准：
- 包含个人事实（身份/喜好/经历/情绪/习惯） → should_extract: true
- 纯闲聊、礼貌用语（"好的"、"嗯嗯"、"谢谢"）→ should_extract: false
- 纯问句、无陈述内容（"今天几号？"）→ should_extract: false
- 指令操作（"帮我查一下..."）→ should_extract: false

输出格式（严格 JSON）：
{"should_extract": true/false, "reason": "一句话理由", "confidence": 0.0到1.0}
"""


@dataclass
class ExtractionGateInput:
    message: str
    role: str
    recent_context: str = ""


@dataclass
class ExtractionGateOutput:
    should_extract: bool
    reason: str
    confidence: float


class ExtractionGate:
    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client or get_llm_client()

    async def check(self, inp: ExtractionGateInput) -> ExtractionGateOutput:
        context_note = f"\n最近对话摘要：{inp.recent_context}" if inp.recent_context else ""
        user_content = f"角色：{inp.role}\n消息：{inp.message}{context_note}"

        data = await self._client.chat_json(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            model=settings.llm_fast_model,
            max_tokens=128,
            temperature=0.0,
        )
        return ExtractionGateOutput(
            should_extract=bool(data.get("should_extract", True)),
            reason=str(data.get("reason", "")),
            confidence=float(data.get("confidence", 0.5)),
        )
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/unit/test_extraction_gate.py -v 2>&1 | tail -10
```

Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add llm/skills/extraction_gate.py tests/unit/test_extraction_gate.py
git commit -m "feat: add ExtractionGate skill (Skill 1) with prompt and tests"
```

---

## Task 5: Skill 2 — UsageGate

**Files:**
- Create: `llm/skills/usage_gate.py`
- Test: `tests/unit/test_usage_gate.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_usage_gate.py
import pytest
from unittest.mock import AsyncMock, MagicMock

FIXTURE_MEMORIES = [
    {"memory_id": "m1", "memory_type": "preference", "content": "用户喜欢吃辣", "importance_score": 0.8},
    {"memory_id": "m2", "memory_type": "preference", "content": "用户喜欢草莓", "importance_score": 0.7},
    {"memory_id": "m3", "memory_type": "identity", "content": "用户叫小明", "importance_score": 0.9},
]

@pytest.mark.asyncio
async def test_usage_gate_selects_relevant():
    from llm.skills.usage_gate import UsageGate, UsageGateInput
    mock_client = MagicMock()
    mock_client.chat_json = AsyncMock(return_value={
        "selected": ["m1"], "reason": "辣食相关"
    })
    gate = UsageGate(client=mock_client)
    result = await gate.decide(UsageGateInput(
        query="我最近不想吃辣了", recalled_memories=FIXTURE_MEMORIES
    ))
    assert "m1" in result.selected

@pytest.mark.asyncio
async def test_usage_gate_returns_empty_when_irrelevant():
    from llm.skills.usage_gate import UsageGate, UsageGateInput
    mock_client = MagicMock()
    mock_client.chat_json = AsyncMock(return_value={"selected": [], "reason": "无关"})
    gate = UsageGate(client=mock_client)
    result = await gate.decide(UsageGateInput(
        query="今天天气真好", recalled_memories=FIXTURE_MEMORIES
    ))
    assert result.selected == []

@pytest.mark.asyncio
async def test_usage_gate_caps_at_three():
    from llm.skills.usage_gate import UsageGate, UsageGateInput
    mock_client = MagicMock()
    mock_client.chat_json = AsyncMock(return_value={
        "selected": ["m1", "m2", "m3", "m4", "m5"], "reason": "many"
    })
    gate = UsageGate(client=mock_client)
    result = await gate.decide(UsageGateInput(query="test", recalled_memories=FIXTURE_MEMORIES))
    assert len(result.selected) <= 3

@pytest.mark.asyncio
async def test_usage_gate_uses_fast_model():
    from llm.skills.usage_gate import UsageGate, UsageGateInput
    from config import settings
    mock_client = MagicMock()
    mock_client.chat_json = AsyncMock(return_value={"selected": [], "reason": ""})
    gate = UsageGate(client=mock_client)
    await gate.decide(UsageGateInput(query="test", recalled_memories=[]))
    call_kwargs = mock_client.chat_json.call_args[1]
    assert call_kwargs.get("model") == settings.llm_fast_model
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/unit/test_usage_gate.py -v 2>&1 | tail -5
```

- [ ] **Step 3: Create `llm/skills/usage_gate.py`**

```python
"""
llm/skills/usage_gate.py — Skill 2: Which recalled memories should be used?

Runs AFTER Phase 1 rule-based scoring (Top 5 candidates).
Makes the final semantic judgment: is this memory truly relevant to the query?

Target: precision >= 90%, recall >= 85%.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from config import settings
from llm.client import LLMClient, get_llm_client

_SYSTEM_PROMPT = """\
你是一个记忆使用决策系统。给定用户当前说的话和候选记忆列表，判断哪些记忆值得用于回答。

判断标准（三条，全部满足才选中）：
1. 确实是用户提到过的事物，不是泛泛相关
2. 与当前问句高度匹配，能让回答更准确、更贴近用户
3. 不词不达意——语义相关但用上去会显得牵强的，也不选

最多选 3 条。如果没有满足条件的，返回空列表。

输出格式（严格 JSON）：
{"selected": ["memory_id_1", "memory_id_2"], "reason": "一句话说明"}
"""


@dataclass
class UsageGateInput:
    query: str
    recalled_memories: list[dict]


@dataclass
class UsageGateOutput:
    selected: list[str]        # memory_ids, max 3
    reason: str


class UsageGate:
    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client or get_llm_client()

    async def decide(self, inp: UsageGateInput) -> UsageGateOutput:
        if not inp.recalled_memories:
            return UsageGateOutput(selected=[], reason="no candidates")

        memories_text = json.dumps(
            [
                {
                    "memory_id": m.get("memory_id", ""),
                    "type": m.get("memory_type", ""),
                    "content": m.get("content", "")[:120],
                    "importance": m.get("importance_score", 0.5),
                }
                for m in inp.recalled_memories[:5]
            ],
            ensure_ascii=False,
        )

        data = await self._client.chat_json(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"用户说：{inp.query}\n\n候选记忆：{memories_text}"},
            ],
            model=settings.llm_fast_model,
            max_tokens=256,
            temperature=0.0,
        )

        selected = data.get("selected", [])
        # Hard cap at 3
        selected = selected[:3]

        return UsageGateOutput(
            selected=[str(s) for s in selected],
            reason=str(data.get("reason", "")),
        )
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/unit/test_usage_gate.py -v 2>&1 | tail -10
```

Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add llm/skills/usage_gate.py tests/unit/test_usage_gate.py
git commit -m "feat: add UsageGate skill (Skill 2) with 3-item cap and tests"
```

---

## Task 6: Skill 3 — MergeJudge ⭐

**Files:**
- Create: `llm/skills/merge_judge.py`
- Test: `tests/unit/test_merge_judge.py`

- [ ] **Step 1: Write failing tests (mock + real LLM accuracy)**

```python
# tests/unit/test_merge_judge.py
import pytest
from unittest.mock import AsyncMock, MagicMock

# ── Mock tests (fast, no API cost) ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_merge_judge_returns_add():
    from llm.skills.merge_judge import MergeJudge, MergeJudgeInput
    mock_client = MagicMock()
    mock_client.chat_json = AsyncMock(return_value={
        "action": "ADD", "target_id": None, "merged_content": None, "reason": "new info"
    })
    judge = MergeJudge(client=mock_client)
    result = await judge.decide(MergeJudgeInput(
        new_memory={"type": "preference", "content": "喜欢打篮球", "importance": 0.7},
        similar_existing=[],
    ))
    assert result.action == "ADD"
    assert result.target_id is None

@pytest.mark.asyncio
async def test_merge_judge_returns_none_for_duplicate():
    from llm.skills.merge_judge import MergeJudge, MergeJudgeInput
    mock_client = MagicMock()
    mock_client.chat_json = AsyncMock(return_value={
        "action": "NONE", "target_id": "existing-id", "merged_content": None,
        "reason": "identical info"
    })
    judge = MergeJudge(client=mock_client)
    result = await judge.decide(MergeJudgeInput(
        new_memory={"type": "identity", "content": "用户今年8岁", "importance": 0.9},
        similar_existing=[{"memory_id": "existing-id", "content": "用户8岁"}],
    ))
    assert result.action == "NONE"

@pytest.mark.asyncio
async def test_merge_judge_uses_sonnet_model():
    from llm.skills.merge_judge import MergeJudge, MergeJudgeInput
    from config import settings
    mock_client = MagicMock()
    mock_client.chat_json = AsyncMock(return_value={
        "action": "ADD", "target_id": None, "merged_content": None, "reason": ""
    })
    judge = MergeJudge(client=mock_client)
    await judge.decide(MergeJudgeInput(
        new_memory={"type": "preference", "content": "test", "importance": 0.5},
        similar_existing=[],
    ))
    call_kwargs = mock_client.chat_json.call_args[1]
    assert call_kwargs.get("model") == settings.llm_model

# ── Real LLM accuracy tests (requires ANTHROPIC_API_KEY) ─────────────────────
# Run with: pytest tests/unit/test_merge_judge.py -k real -v --tb=short

REAL_CASES = [
    # (new_content, existing_content, expected_action)
    ("喜欢打篮球", None, "ADD"),
    ("喜欢川菜，特别爱麻辣口味", "喜欢吃辣", "UPDATE"),
    ("最近不想吃辣了", "喜欢吃辣", "DELETE"),
    ("用户今年8岁", "用户8岁", "NONE"),
    ("喜欢西瓜", "喜欢草莓", "ADD"),     # different items of same category
    ("非常热爱画画", "有点喜欢画画", "UPDATE"),
]

@pytest.mark.asyncio
@pytest.mark.real_llm
async def test_merge_judge_accuracy_real():
    """Real LLM test — requires ANTHROPIC_API_KEY. Target: >= 85% accuracy."""
    from llm.skills.merge_judge import MergeJudge, MergeJudgeInput
    from llm.client import LLMClient
    import anthropic, os
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    judge = MergeJudge()
    correct = 0
    for new_content, existing_content, expected in REAL_CASES:
        similar = []
        if existing_content:
            similar = [{"memory_id": "ex-1", "content": existing_content,
                       "memory_type": "preference", "importance_score": 0.7}]
        result = await judge.decide(MergeJudgeInput(
            new_memory={"type": "preference", "content": new_content, "importance": 0.7},
            similar_existing=similar,
        ))
        if result.action == expected:
            correct += 1
        else:
            print(f"  MISS: new='{new_content}' existing='{existing_content}' "
                  f"expected={expected} got={result.action} reason={result.reason}")

    accuracy = correct / len(REAL_CASES)
    print(f"\nMergeJudge accuracy: {correct}/{len(REAL_CASES)} = {accuracy:.1%}")
    assert accuracy >= 0.85, f"MergeJudge accuracy {accuracy:.1%} < 85%"
```

- [ ] **Step 2: Run mock tests to confirm failure**

```bash
python -m pytest tests/unit/test_merge_judge.py -k "not real" -v 2>&1 | tail -5
```

- [ ] **Step 3: Create `llm/skills/merge_judge.py`**

```python
"""
llm/skills/merge_judge.py — Skill 3: How should a new memory be written?

Called BEFORE writing a new memory. Searches for similar existing memories
and asks the LLM to decide: ADD / UPDATE / DELETE / NONE.

Model: settings.llm_model (Sonnet) — accuracy over speed (async background path).
Target: action accuracy >= 85%, UPDATE content quality >= 80%.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from config import settings
from llm.client import LLMClient, get_llm_client

_SYSTEM_PROMPT = """\
你是一个记忆合并系统。给定一条新提取的记忆和若干条相似的已有记忆，
决定如何处理这条新记忆。

操作选项：
- ADD: 全新信息，没有相似记忆，直接添加
- UPDATE: 找到相似记忆，新信息更完整/更新 → 合并覆盖旧记忆（保留旧 memory_id）
- DELETE: 新信息与旧信息矛盾 → 删除旧记忆（然后系统会自动 ADD 新记忆）
- NONE: 信息完全重复，已知内容 → 丢弃新记忆

UPDATE 规则：如果旧记忆"喜欢吃辣"，新记忆"喜欢川菜特别爱麻辣"，
merged_content 应该是更完整的版本："喜欢川菜，特别爱麻辣口味"

输出格式（严格 JSON）：
{
  "action": "ADD"|"UPDATE"|"DELETE"|"NONE",
  "target_id": "旧记忆的 memory_id（UPDATE/DELETE 时填写，ADD/NONE 时为 null）",
  "merged_content": "UPDATE 时的合并后内容（其他操作为 null）",
  "reason": "一句话理由"
}
"""


@dataclass
class MergeJudgeInput:
    new_memory: dict             # {type, content, importance}
    similar_existing: list[dict] # [{memory_id, content, memory_type, importance_score}, ...]


@dataclass
class MergeJudgeOutput:
    action: Literal["ADD", "UPDATE", "DELETE", "NONE"]
    target_id: str | None
    merged_content: str | None
    reason: str


class MergeJudge:
    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client or get_llm_client()

    async def decide(self, inp: MergeJudgeInput) -> MergeJudgeOutput:
        existing_text = json.dumps(
            [
                {
                    "memory_id": m.get("memory_id", ""),
                    "content": m.get("content", "")[:150],
                    "type": m.get("memory_type", m.get("type", "")),
                    "importance": m.get("importance_score", m.get("importance", 0.5)),
                }
                for m in inp.similar_existing[:5]
            ],
            ensure_ascii=False,
        ) if inp.similar_existing else "[]"

        new_text = json.dumps({
            "type": inp.new_memory.get("type", ""),
            "content": inp.new_memory.get("content", ""),
            "importance": inp.new_memory.get("importance", 0.5),
        }, ensure_ascii=False)

        data = await self._client.chat_json(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"新记忆：{new_text}\n\n"
                    f"相似已有记忆（最多5条）：{existing_text}"
                )},
            ],
            model=settings.llm_model,
            max_tokens=256,
            temperature=0.0,
        )

        action = data.get("action", "ADD").upper()
        if action not in ("ADD", "UPDATE", "DELETE", "NONE"):
            action = "ADD"

        return MergeJudgeOutput(
            action=action,
            target_id=data.get("target_id") or None,
            merged_content=data.get("merged_content") or None,
            reason=str(data.get("reason", "")),
        )
```

- [ ] **Step 4: Run mock tests**

```bash
python -m pytest tests/unit/test_merge_judge.py -k "not real" -v 2>&1 | tail -10
```

Expected: 3 PASS

- [ ] **Step 5: Run real LLM accuracy test (optional, costs ~$0.01)**

```bash
python -m pytest tests/unit/test_merge_judge.py -k real -v -s 2>&1 | tail -15
```

Expected: `MergeJudge accuracy: X/6 >= 85%`

- [ ] **Step 6: Commit**

```bash
git add llm/skills/merge_judge.py tests/unit/test_merge_judge.py
git commit -m "feat: add MergeJudge skill (Skill 3) with ADD/UPDATE/DELETE/NONE logic"
```

---

## Task 7: Skill 4 — ForgetJudge

**Files:**
- Create: `llm/skills/forget_judge.py`
- Test: `tests/unit/test_forget_judge.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_forget_judge.py
import pytest
from unittest.mock import AsyncMock, MagicMock

FIXTURE_CANDIDATES = [
    {"memory_id": "m1", "memory_type": "joy", "content": "今天玩得开心",
     "importance_score": 0.3, "days_old": 300, "forget_score": 0.02},
    {"memory_id": "m2", "memory_type": "identity", "content": "用户叫小明",
     "importance_score": 0.9, "days_old": 500, "forget_score": 0.04},
    {"memory_id": "m3", "memory_type": "preference", "content": "喜欢吃草莓",
     "importance_score": 0.4, "days_old": 350, "forget_score": 0.03},
]

@pytest.mark.asyncio
async def test_forget_judge_deletes_low_value():
    from llm.skills.forget_judge import ForgetJudge, ForgetJudgeInput
    mock_client = MagicMock()
    mock_client.chat_json = AsyncMock(return_value={
        "delete_ids": ["m1"], "keep_ids": ["m2", "m3"], "reason": "m1 is trivial"
    })
    judge = ForgetJudge(client=mock_client)
    result = await judge.decide(ForgetJudgeInput(candidates=FIXTURE_CANDIDATES))
    assert "m1" in result.delete_ids
    assert "m2" in result.keep_ids

@pytest.mark.asyncio
async def test_forget_judge_uses_fast_model():
    from llm.skills.forget_judge import ForgetJudge, ForgetJudgeInput
    from config import settings
    mock_client = MagicMock()
    mock_client.chat_json = AsyncMock(return_value={
        "delete_ids": [], "keep_ids": [], "reason": ""
    })
    judge = ForgetJudge(client=mock_client)
    await judge.decide(ForgetJudgeInput(candidates=[]))
    call_kwargs = mock_client.chat_json.call_args[1]
    assert call_kwargs.get("model") == settings.llm_fast_model
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/unit/test_forget_judge.py -v 2>&1 | tail -5
```

- [ ] **Step 3: Create `llm/skills/forget_judge.py`**

```python
"""
llm/skills/forget_judge.py — Skill 4: Which low-score memories should be deleted?

Called during the scheduled forget_scan_job. Receives a batch of candidates
(already pre-filtered by decay score < threshold and hard-protection rules).
Makes the final call: delete or keep.

Model: settings.llm_fast_model (Haiku) — batch processing, cost-sensitive.
Batch size: max 50 (caller's responsibility to chunk).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from config import settings
from llm.client import LLMClient, get_llm_client

_SYSTEM_PROMPT = """\
你是一个记忆遗忘决策系统。给定一批遗忘分数低的候选记忆，决定哪些应该删除。

删除标准：
- forget_score < 0.05 且内容普通（日常闲聊、无特殊意义的情绪）→ 删除
- 与其他记忆内容高度重复 → 删除
- 虽然分数低，但内容是用户关键信息（重要事件、特殊经历）→ 保留

输出格式（严格 JSON）：
{"delete_ids": ["id1", "id2"], "keep_ids": ["id3"], "reason": "一句话说明"}
"""


@dataclass
class ForgetJudgeInput:
    candidates: list[dict]  # max 50, pre-filtered by caller


@dataclass
class ForgetJudgeOutput:
    delete_ids: list[str]
    keep_ids: list[str]
    reason: str


class ForgetJudge:
    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client or get_llm_client()

    async def decide(self, inp: ForgetJudgeInput) -> ForgetJudgeOutput:
        if not inp.candidates:
            return ForgetJudgeOutput(delete_ids=[], keep_ids=[], reason="no candidates")

        candidates_text = json.dumps(
            [
                {
                    "memory_id": c.get("memory_id", ""),
                    "type": c.get("memory_type", ""),
                    "content": str(c.get("content", ""))[:100],
                    "importance": c.get("importance_score", 0.5),
                    "days_old": c.get("days_old", 0),
                    "forget_score": round(c.get("forget_score", 0.0), 4),
                }
                for c in inp.candidates[:50]
            ],
            ensure_ascii=False,
        )

        data = await self._client.chat_json(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"候选记忆列表：{candidates_text}"},
            ],
            model=settings.llm_fast_model,
            max_tokens=512,
            temperature=0.0,
        )

        return ForgetJudgeOutput(
            delete_ids=[str(i) for i in data.get("delete_ids", [])],
            keep_ids=[str(i) for i in data.get("keep_ids", [])],
            reason=str(data.get("reason", "")),
        )
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/unit/test_forget_judge.py -v 2>&1 | tail -10
```

Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add llm/skills/forget_judge.py tests/unit/test_forget_judge.py
git commit -m "feat: add ForgetJudge skill (Skill 4) for LLM-assisted memory pruning"
```

---

## Task 8: Merge Worker

**Files:**
- Create: `pipeline/merge_worker.py`
- Test: `tests/integration/test_merge_worker.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/integration/test_merge_worker.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

@pytest.mark.asyncio
async def test_semaphore_bounds_concurrency():
    """Peak concurrent tasks must not exceed merge_concurrency."""
    from pipeline.merge_worker import MergeWorker, MergeTask
    from config import settings

    peak = 0
    current = 0

    async def fake_merge(task):
        nonlocal peak, current
        current += 1
        peak = max(peak, current)
        await asyncio.sleep(0.01)
        current -= 1

    worker = MergeWorker(merge_fn=fake_merge)
    await worker.start()

    tasks = [
        MergeTask(person_id="p1", memory_entry={"content": f"m{i}"},
                  session_id="s1", turn_index=i)
        for i in range(50)
    ]
    for t in tasks:
        await worker.enqueue(t)

    await worker.drain()
    await worker.stop()

    assert peak <= settings.merge_concurrency

@pytest.mark.asyncio
async def test_queue_full_drops_gracefully():
    """When queue is full, overflow is handled without raising."""
    from pipeline.merge_worker import MergeWorker, MergeTask

    # Small queue for test
    worker = MergeWorker(queue_maxsize=5, merge_fn=AsyncMock())
    await worker.start()

    dropped = 0
    for i in range(10):
        accepted = await worker.enqueue(
            MergeTask(person_id="p1", memory_entry={"content": f"m{i}"},
                      session_id="s1", turn_index=i),
            timeout=0,
        )
        if not accepted:
            dropped += 1

    await worker.stop()
    assert dropped >= 5  # at least some were dropped

@pytest.mark.asyncio
async def test_semaphore_fully_released_after_tasks():
    """After all tasks complete, semaphore value must equal initial capacity."""
    from pipeline.merge_worker import MergeWorker, MergeTask
    from config import settings

    worker = MergeWorker(merge_fn=AsyncMock())
    await worker.start()

    tasks = [
        MergeTask(person_id="p1", memory_entry={"content": f"m{i}"},
                  session_id="s1", turn_index=i)
        for i in range(10)
    ]
    for t in tasks:
        await worker.enqueue(t)

    await worker.drain()
    await worker.stop()

    assert worker._semaphore._value == settings.merge_concurrency
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/integration/test_merge_worker.py -v 2>&1 | tail -5
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `pipeline/merge_worker.py`**

```python
"""
pipeline/merge_worker.py — Async bounded worker for write-time memory merging.

Architecture:
    ExtractionWorker (existing)
        └─ enqueue() → MergeTaskQueue (bounded asyncio.Queue)
                └─ MergeWorker (semaphore-bounded coroutines)
                        └─ vector search → MergeJudge → write/update/delete

Resource guarantees:
    - Semaphore ensures at most `merge_concurrency` concurrent LLM calls
    - async with semaphore releases on exception too (no leaked slots)
    - Queue backpressure: overflow drops silently with a warning log
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Awaitable

from config import settings

logger = logging.getLogger(__name__)


@dataclass
class MergeTask:
    person_id: str
    memory_entry: dict        # {type, content, importance, confidence, ...}
    session_id: str
    turn_index: int
    created_at: datetime = field(default_factory=datetime.utcnow)
    priority: int = 0         # reserved, unused


class MergeWorker:
    """
    Async bounded worker. Call start() before use, stop() when done.

    Args:
        merge_fn: Async callable that does the actual merge logic.
                  Injected for testability.
        concurrency: Max simultaneous merge_fn calls.
        queue_maxsize: Max queued tasks before dropping.
    """

    def __init__(
        self,
        merge_fn: Callable[[MergeTask], Awaitable[None]] | None = None,
        concurrency: int | None = None,
        queue_maxsize: int | None = None,
    ) -> None:
        self._merge_fn = merge_fn or self._default_merge
        self._concurrency = concurrency or settings.merge_concurrency
        self._queue_maxsize = queue_maxsize or settings.merge_queue_maxsize
        self._queue: asyncio.Queue[MergeTask] = asyncio.Queue(maxsize=self._queue_maxsize)
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(self._concurrency)
        self._running = False
        self._consumer_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True
        self._consumer_task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        self._running = False
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass

    async def drain(self) -> None:
        """Wait until queue is empty and all tasks complete."""
        await self._queue.join()

    async def enqueue(self, task: MergeTask, timeout: float = 0.1) -> bool:
        """
        Enqueue a merge task. Returns True if accepted, False if dropped.
        On queue full: new ADD tasks fall through (direct write); updates discarded.
        """
        try:
            self._queue.put_nowait(task)
            return True
        except asyncio.QueueFull:
            logger.warning(
                "merge_worker queue full (size=%d), dropping task person=%s",
                self._queue_maxsize,
                task.person_id,
            )
            return False

    async def _consume(self) -> None:
        while self._running:
            try:
                task = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                asyncio.create_task(self._process(task))
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    async def _process(self, task: MergeTask) -> None:
        async with self._semaphore:
            try:
                await self._merge_fn(task)
            except Exception:
                logger.exception("merge_worker error for person=%s", task.person_id)
            finally:
                self._queue.task_done()

    async def _default_merge(self, task: MergeTask) -> None:
        """
        Production merge logic:
        1. Vector search for similar memories
        2. If similarity < threshold → ADD directly
        3. Else → call MergeJudge
        4. Execute result: ADD / UPDATE / DELETE / NONE
        5. Invalidate recall cache
        """
        # Import here to avoid circular imports at module load time
        from llm.skills.merge_judge import MergeJudge, MergeJudgeInput

        judge = MergeJudge()
        new_mem = task.memory_entry

        # TODO: inject pg/redis stores via DI in production
        # For now this is a skeleton — wired in Task 9 (worker.py integration)
        similar: list[dict] = []  # placeholder

        result = await judge.decide(MergeJudgeInput(
            new_memory=new_mem,
            similar_existing=similar,
        ))

        logger.debug(
            "merge_worker action=%s person=%s content=%.40s reason=%s",
            result.action, task.person_id,
            new_mem.get("content", ""), result.reason,
        )


# Module-level singleton for use by pipeline/worker.py
_worker_instance: MergeWorker | None = None


def get_merge_worker() -> MergeWorker:
    global _worker_instance
    if _worker_instance is None:
        _worker_instance = MergeWorker()
    return _worker_instance
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/integration/test_merge_worker.py -v 2>&1 | tail -15
```

Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/merge_worker.py tests/integration/test_merge_worker.py
git commit -m "feat: add MergeWorker with bounded semaphore, queue backpressure, resource release"
```

---

## Task 9: Recall Gate — `recall_with_gate()`

**Files:**
- Modify: `retrieval/recall.py`
- Test: `tests/e2e/test_recall_with_gate.py`

Note: The existing function is `recall()`, not `recall_with_confidence()`.

- [ ] **Step 1: Write failing tests**

```python
# tests/e2e/test_recall_with_gate.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_recall_with_gate_returns_gate_result():
    from retrieval.recall import recall_with_gate, GateResult

    mock_redis = MagicMock()
    mock_pg = MagicMock()

    with patch("retrieval.recall.recall") as mock_recall, \
         patch("retrieval.recall.UsageGate") as MockGate:

        mock_recall.return_value = (
            [{"memory_id": "m1", "content": "test", "memory_type": "preference",
              "importance_score": 0.8}],
            {"vector": 1},
            "high",
        )
        gate_instance = MagicMock()
        gate_instance.decide = AsyncMock(return_value=MagicMock(
            selected=["m1"], reason="relevant"
        ))
        MockGate.return_value = gate_instance

        selected, gate_result = await recall_with_gate(
            query="test query",
            person_id="person-1",
            redis=mock_redis,
            pg=mock_pg,
        )

        assert isinstance(gate_result, GateResult)
        assert gate_result.used_gate is True

@pytest.mark.asyncio
async def test_recall_skips_gate_when_confidence_low():
    from retrieval.recall import recall_with_gate, GateResult

    with patch("retrieval.recall.recall") as mock_recall:
        mock_recall.return_value = ([], {}, "empty")

        selected, gate_result = await recall_with_gate(
            query="test",
            person_id="p1",
            redis=MagicMock(),
            pg=MagicMock(),
        )

        assert selected == []
        assert gate_result.used_gate is False

@pytest.mark.asyncio
async def test_recall_gate_caps_at_three():
    from retrieval.recall import recall_with_gate

    with patch("retrieval.recall.recall") as mock_recall, \
         patch("retrieval.recall.UsageGate") as MockGate:

        mock_recall.return_value = (
            [{"memory_id": f"m{i}", "content": f"c{i}", "memory_type": "preference",
              "importance_score": 0.5} for i in range(5)],
            {}, "high",
        )
        gate_instance = MagicMock()
        gate_instance.decide = AsyncMock(return_value=MagicMock(
            selected=["m0", "m1", "m2", "m3", "m4"], reason="all"
        ))
        MockGate.return_value = gate_instance

        selected, _ = await recall_with_gate(
            query="test", person_id="p1",
            redis=MagicMock(), pg=MagicMock(),
        )
        assert len(selected) <= 3
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/e2e/test_recall_with_gate.py -v 2>&1 | tail -5
```

Expected: `ImportError: cannot import name 'recall_with_gate'`

- [ ] **Step 3: Add `GateResult` and `recall_with_gate()` to `retrieval/recall.py`**

At the top of `retrieval/recall.py`, add imports:

```python
from dataclasses import dataclass, field
```

After existing imports, add the `GateResult` dataclass:

```python
@dataclass
class GateResult:
    selected: list[dict]   # memories selected by UsageGate (max 3)
    used_gate: bool        # True if UsageGate was actually called
    reason: str = ""       # UsageGate reasoning (debug)
    confidence: str = ""   # upstream recall() confidence level
```

At the end of `retrieval/recall.py`, add:

```python
async def recall_with_gate(
    query: str,
    person_id: str,
    redis,
    pg,
    *,
    use_usage_gate: bool = True,
    limit: int | None = None,
    types: list[str] | None = None,
) -> tuple[list[dict], GateResult]:
    """
    Full recall pipeline: three-path search → Phase 1 → UsageGate LLM.

    Returns:
        (selected_memories, gate_result)
        selected_memories: list of memory dicts to inject into LLM context (max 3)
        gate_result: metadata about gate decision
    """
    from llm.skills.usage_gate import UsageGate, UsageGateInput

    entries, _sources, confidence = await recall(
        person_id=person_id,
        context=query,
        redis=redis,
        pg=pg,
        limit=limit,
        types=types,
    )

    # Skip gate when no results or confidence is too low
    if confidence in ("empty", "low") or not entries:
        return [], GateResult(selected=[], used_gate=False, confidence=confidence)

    if not use_usage_gate:
        return entries, GateResult(selected=entries, used_gate=False, confidence=confidence)

    gate = UsageGate()
    gate_output = await gate.decide(UsageGateInput(
        query=query,
        recalled_memories=entries[:5],
    ))

    # Resolve selected IDs back to full memory dicts
    id_to_entry = {e.get("memory_id", ""): e for e in entries}
    selected = [
        id_to_entry[mid] for mid in gate_output.selected[:3]
        if mid in id_to_entry
    ]

    return selected, GateResult(
        selected=selected,
        used_gate=True,
        reason=gate_output.reason,
        confidence=confidence,
    )
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/e2e/test_recall_with_gate.py -v 2>&1 | tail -15
```

Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add retrieval/recall.py tests/e2e/test_recall_with_gate.py
git commit -m "feat: add recall_with_gate() and GateResult — UsageGate integrated into recall pipeline"
```

---

## Task 10: Forget Scan Job

**Files:**
- Modify: `scheduler/jobs.py`
- Test: `tests/integration/test_forget_scan.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/integration/test_forget_scan.py
import math
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

def test_decay_formula_precision():
    from scheduler.jobs import compute_forget_score
    # importance=0.6, lambda=0.01, days=100 → 0.6 * e^(-1) ≈ 0.221
    score = compute_forget_score(importance=0.6, lam=0.01, days=100)
    assert abs(score - 0.6 * math.exp(-1.0)) < 0.001

def test_hard_protection_filters_identity_names():
    from scheduler.jobs import apply_hard_protection
    candidates = [
        {"memory_id": "m1", "memory_type": "identity", "content": "用户姓名是小明", "importance_score": 0.3},
        {"memory_id": "m2", "memory_type": "joy", "content": "今天很开心", "importance_score": 0.3},
        {"memory_id": "m3", "memory_type": "identity", "content": "用户喜欢玩耍", "importance_score": 0.3},
    ]
    filtered = apply_hard_protection(candidates)
    ids = [c["memory_id"] for c in filtered]
    assert "m1" not in ids   # identity + 姓名 → protected
    assert "m2" in ids       # joy → not protected
    assert "m3" in ids       # identity but no 姓名/年龄/性别 → not protected

def test_hard_protection_filters_high_importance():
    from scheduler.jobs import apply_hard_protection
    candidates = [
        {"memory_id": "m1", "memory_type": "preference", "content": "喜欢草莓",
         "importance_score": 0.9},  # >= 0.8 → protected
        {"memory_id": "m2", "memory_type": "preference", "content": "喜欢西瓜",
         "importance_score": 0.5},  # < 0.8 → not protected
    ]
    filtered = apply_hard_protection(candidates)
    ids = [c["memory_id"] for c in filtered]
    assert "m1" not in ids
    assert "m2" in ids
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/integration/test_forget_scan.py -v 2>&1 | tail -5
```

Expected: `ImportError: cannot import name 'compute_forget_score'`

- [ ] **Step 3: Add forget functions to `scheduler/jobs.py`**

Add at the top of `scheduler/jobs.py` (after existing imports):

```python
import math
import asyncio as _asyncio
from config import settings
```

Add these standalone functions (before `create_scheduler()`):

```python
def compute_forget_score(importance: float, lam: float, days: float) -> float:
    """forget_score = importance × e^(−λ × days)"""
    return importance * math.exp(-lam * days)


_HARD_PROTECT_KEYWORDS = {"姓名", "年龄", "性别", "名字", "叫做", "出生"}


def apply_hard_protection(candidates: list[dict]) -> list[dict]:
    """
    Remove candidates that must never be deleted:
    - identity type + contains name/age/gender keywords
    - importance_score >= settings.forget_importance_protect
    """
    result = []
    for c in candidates:
        mem_type = c.get("memory_type", "")
        importance = c.get("importance_score", 0.0)
        content = c.get("content", "")

        if importance >= settings.forget_importance_protect:
            continue  # protected

        if mem_type == "identity" and any(kw in content for kw in _HARD_PROTECT_KEYWORDS):
            continue  # protected

        result.append(c)
    return result


async def forget_scan_job(pg, redis) -> None:
    """
    Daily job: dual-track candidate selection → hard protection → ForgetJudge → delete.

    Track 1: decay score < threshold
    Track 2: per-person per-type quota overflow
    """
    from llm.skills.forget_judge import ForgetJudge, ForgetJudgeInput
    import datetime

    judge = ForgetJudge()
    _task_registry: set = set()

    # In production: iterate over all active persons
    # For MVP: pg.get_all_person_ids() → iterate
    # This skeleton shows the logic for one person
    async def process_person(person_id: str) -> None:
        rows = await pg.get_candidates_for_forget(
            person_id,
            forget_score_threshold=settings.forget_score_threshold * 5,  # wider net
            limit=2000,
        )

        now = datetime.datetime.utcnow()
        candidates = []
        for row in rows:
            last = row.get("last_accessed_at") or row.get("created_at")
            days = (now - last).days if last else 0
            mem_type = row.get("memory_type", "preference")
            lam = settings.forget_lambda.get(mem_type, 0.010)
            score = compute_forget_score(row["importance_score"], lam, days)
            if score < settings.forget_score_threshold:
                candidates.append({**row, "days_old": days, "forget_score": score})

        if not candidates:
            return

        candidates = apply_hard_protection(candidates)

        # Batch into chunks of forget_batch_size, with concurrency limit
        sem = _asyncio.Semaphore(settings.forget_judge_concurrency)
        chunks = [
            candidates[i:i + settings.forget_batch_size]
            for i in range(0, len(candidates), settings.forget_batch_size)
        ]

        async def judge_chunk(chunk: list[dict]) -> None:
            async with sem:
                try:
                    result = await judge.decide(ForgetJudgeInput(candidates=chunk))
                    if result.delete_ids:
                        await pg.soft_delete(result.delete_ids)
                        await redis.remove_from_sorted_sets(result.delete_ids)
                        task = _asyncio.create_task(
                            pg.hard_delete_later(
                                result.delete_ids,
                                delay=settings.forget_hard_delete_delay,
                            )
                        )
                        _task_registry.add(task)
                        task.add_done_callback(_task_registry.discard)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception(
                        "forget_scan_job chunk failed, skipping"
                    )

        await _asyncio.gather(*[judge_chunk(chunk) for chunk in chunks])
```

Also add `forget_scan_job` to the `create_scheduler()` function:

```python
    scheduler.add_job(
        lambda: asyncio.create_task(forget_scan_job(pg, redis)),
        "cron",
        hour=3,
        minute=0,
        id="forget_scan",
    )
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/integration/test_forget_scan.py -v 2>&1 | tail -15
```

Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add scheduler/jobs.py tests/integration/test_forget_scan.py
git commit -m "feat: add forget_scan_job with decay formula, hard protection, ForgetJudge batching"
```

---

## Task 11: Wire ExtractionGate + MergeWorker into `pipeline/worker.py`

**Files:**
- Modify: `pipeline/worker.py`
- Modify: `pipeline/extractor.py`

- [ ] **Step 1: Replace `anthropic.AsyncAnthropic` in `pipeline/extractor.py`**

Find the line:
```python
_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
```

Replace with:
```python
from llm.client import get_llm_client as _get_llm_client
```

Then update wherever `_client.messages.create(...)` is called to use `_get_llm_client()._client.messages.create(...)`.

Or simpler: keep the existing `_client` variable but initialize via:
```python
import anthropic
from config import settings
_anthropic = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
```
This is already the pattern — no change needed here. The LLMClient wraps it for Skills; `extractor.py` can stay as-is for now (it has its own retry logic already).

- [ ] **Step 2: Add ExtractionGate check in `pipeline/worker.py`**

Find the section where extraction is triggered (after consuming a Kafka message). Add before the `extract_memories()` call:

```python
from llm.skills.extraction_gate import ExtractionGate, ExtractionGateInput

# Gate check — skip low-value messages early
_gate = ExtractionGate()
gate_result = await _gate.check(ExtractionGateInput(
    message=msg_data["message"],
    role=msg_data.get("role", "user"),
    recent_context="",
))
if not gate_result.should_extract:
    logger.debug("extraction_gate blocked message: %s", gate_result.reason)
    await self._mark_task_done(task_id, "skipped_by_gate")
    return
```

- [ ] **Step 3: Route writes through MergeWorker**

After `extract_memories()` returns a list of `MemoryEntryCreate`, instead of writing directly:

```python
from pipeline.merge_worker import get_merge_worker, MergeTask

merge_worker = get_merge_worker()
for mem in extracted_memories:
    task = MergeTask(
        person_id=person_id,
        memory_entry=mem.dict(),
        session_id=session_id,
        turn_index=turn_index,
    )
    accepted = await merge_worker.enqueue(task)
    if not accepted:
        # Queue full fallback: write directly without merge
        await self._direct_write(mem, person_id)
```

- [ ] **Step 4: Start MergeWorker in lifespan (`api/main.py`)**

In the FastAPI lifespan startup:

```python
from pipeline.merge_worker import get_merge_worker
worker = get_merge_worker()
await worker.start()
```

In shutdown:

```python
await worker.stop()
```

- [ ] **Step 5: Run existing tests to verify no regression**

```bash
cd /home/zihai/workspace/Agent_server_design/memory_system
python -m pytest tests/ -v --ignore=tests/unit/test_merge_judge.py -k "not real_llm" 2>&1 | tail -20
```

Expected: all previously passing tests still PASS

- [ ] **Step 6: Commit**

```bash
git add pipeline/worker.py pipeline/extractor.py api/main.py
git commit -m "feat: wire ExtractionGate and MergeWorker into extraction pipeline"
```

---

## Task 12: Full Regression + Accuracy Tests

- [ ] **Step 1: Run all unit tests (mocked)**

```bash
python -m pytest tests/unit/ tests/integration/ tests/e2e/ -v -k "not real_llm" 2>&1 | tail -30
```

Expected: all PASS

- [ ] **Step 2: Run MergeJudge real LLM accuracy (requires API key)**

```bash
python -m pytest tests/unit/test_merge_judge.py -k real -v -s 2>&1 | grep -E "accuracy|PASSED|FAILED"
```

Expected: `MergeJudge accuracy >= 85%`

- [ ] **Step 3: Run UsageGate real LLM accuracy**

```bash
python -m pytest tests/unit/test_usage_gate.py -k real -v -s 2>&1 | grep -E "precision|recall|PASSED|FAILED"
```

Expected: precision >= 90%, recall >= 85%

- [ ] **Step 4: Run existing Phase 0–3 tests to confirm no regression**

```bash
python -m pytest tests/simulate_test.py tests/phase1_test.py tests/event_test.py tests/phase3_test.py -v 2>&1 | tail -20
```

Expected: all previously passing metrics still pass

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat: Phase 4 complete — LLM Skill architecture with merge, gate, and forgetting"
```

---

## Quick Reference

### Run all mocked tests
```bash
python -m pytest tests/unit/ tests/integration/ tests/e2e/ -k "not real_llm" -q
```

### Run real LLM accuracy suite
```bash
python -m pytest tests/ -k real_llm -v -s
```

### Check a single skill
```bash
python -m pytest tests/unit/test_merge_judge.py -v -s
```

### Key thresholds to verify
| Test | Target |
|---|---|
| MergeJudge action accuracy | ≥ 85% |
| MergeJudge UPDATE content quality | ≥ 80% |
| UsageGate precision | ≥ 90% |
| UsageGate recall | ≥ 85% |
| ExtractionGate recall (valid memories pass) | ≥ 90% |
| Concurrency peak ≤ merge_concurrency | 100% |
| Semaphore fully released after tasks | 100% |
| Decay formula error | < 0.001 |
| Hard protection (identity names) | 100% |
