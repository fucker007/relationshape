"""
LoCoMo-Plus Memory Evaluator — 召回记忆 + 生成答案 + 输出官方格式.

流程:
  1. 对每个 QA pair，用 question 调 /memory/chat recall 获取记忆上下文
  2. 用记忆上下文 + question 调 LLM 生成答案 (prediction)
  3. 输出对齐官方 evaluate_qa.py 格式的 JSON，可直接喂给 llm_judge

双视角策略:
  - 每个 QA 的 question 可能涉及 A 或 B 的记忆
  - 默认用 speaker_a 视角召回；如果 question 中提到 speaker_b 的名字，也从 B 视角召回
  - 合并两个视角的记忆作为 context
"""

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI

from locomo_loader import (
    LocomoSample, CognitiveSample, QAPair,
    load_locomo10, load_locomo_plus, evidence_to_text,
    LOCOMO_CATEGORY_NAMES,
)
from memory_client import MemoryClient, device_to_owner_uuid

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────
API_BASE_URL = "http://localhost:8010"
API_TIMEOUT  = 60.0

# LLM for answer generation (same qwen-plus as system)
LLM_BASE_URL = os.environ.get(
    "OPENAI_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)
LLM_API_KEY = os.environ.get("MEMORY_QWEN_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
LLM_MODEL = os.environ.get("EVAL_MODEL", "qwen-plus")
LLM_TEMPERATURE = 0.3
LLM_MAX_TOKENS = 1024


# ── Answer Generation Prompts ────────────────────────

ANSWER_SYSTEM_PROMPT = """You are a helpful assistant with access to memory about people and past conversations.
Use the provided memory context to answer the question accurately and concisely.
If the memory context does not contain relevant information, say so honestly.
Do not make up information that is not supported by the memory context."""

ANSWER_USER_TEMPLATE = """Memory Context:
{context}

Question: {question}

Answer the question based on the memory context above. Be specific and concise."""

ADVERSARIAL_SYSTEM_PROMPT = """You are a helpful assistant with access to memory about people and past conversations.
Use the provided memory context to answer the question.
IMPORTANT: If the information asked about was never mentioned or discussed in any conversation you have memory of, you MUST clearly state that this information was not mentioned in the conversation. Do not guess or make up an answer."""

COGNITIVE_SYSTEM_PROMPT = """You are continuing a conversation with someone you've spoken to before.
Use your memory of past conversations to respond naturally.
Your response should show awareness of relevant past context and memories.
Respond as a natural conversational partner, not as an AI assistant."""

COGNITIVE_USER_TEMPLATE = """Memory Context:
{context}

The other person says: {trigger}

Respond naturally, showing awareness of your shared history and past conversations."""


@dataclass
class EvalRecord:
    """一条评测记录，对齐官方 evaluate_qa.py 输出格式."""
    question_input: str = ""
    evidence: str = ""
    category: str = ""
    ground_truth: str = ""
    prediction: str = ""
    model: str = ""
    # 额外字段（不影响 judge 兼容性）
    sample_id: str = ""
    recall_profile: str = ""
    recall_events_count: int = 0
    recall_source: str = ""
    time_gap: str = ""


async def _get_llm_client() -> AsyncOpenAI:
    """获取 LLM 客户端."""
    return AsyncOpenAI(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
    )


async def _generate_answer(
    llm: AsyncOpenAI,
    context: str,
    question: str,
    category: str,
    trigger: str = "",
    model: str = "",
) -> str:
    """用 LLM 基于记忆上下文生成答案."""
    model = model or LLM_MODEL

    if category == "adversarial":
        system = ADVERSARIAL_SYSTEM_PROMPT
        user_msg = ANSWER_USER_TEMPLATE.format(context=context or "(no memory found)", question=question)
    elif category == "Cognitive":
        system = COGNITIVE_SYSTEM_PROMPT
        user_msg = COGNITIVE_USER_TEMPLATE.format(
            context=context or "(no memory found)",
            trigger=trigger or question,
        )
    else:
        system = ANSWER_SYSTEM_PROMPT
        user_msg = ANSWER_USER_TEMPLATE.format(context=context or "(no memory found)", question=question)

    try:
        resp = await llm.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error(f"LLM generation failed: {e}")
        return f"(generation error: {e})"


async def _recall_for_question(
    client: MemoryClient,
    sample: LocomoSample,
    question: str,
) -> dict:
    """
    双视角召回：用 question 分别从 A 和 B 视角召回记忆，合并结果.

    Returns:
        {profile: str, events: list, events_count: int, source: str, context_string: str}
    """
    device_a = f"locomo_{sample.sample_id}_{sample.speaker_a}"
    device_b = f"locomo_{sample.sample_id}_{sample.speaker_b}"

    # 从 A 视角召回
    recall_a = await client.recall(device_a, sample.speaker_a, question)
    # 从 B 视角召回
    recall_b = await client.recall(device_b, sample.speaker_b, question)

    # 合并
    profile_parts = []
    if recall_a.get("profile_summary"):
        profile_parts.append(f"{sample.speaker_a}: {recall_a['profile_summary']}")
    if recall_b.get("profile_summary"):
        profile_parts.append(f"{sample.speaker_b}: {recall_b['profile_summary']}")

    events_a = recall_a.get("events") or []
    events_b = recall_b.get("events") or []
    all_events = events_a + events_b

    # 构建 context string
    parts = []
    if profile_parts:
        parts.append("Profiles:\n" + "\n".join(profile_parts))

    if all_events:
        event_lines = []
        seen = set()
        for e in all_events:
            summary = (e.get("summary") or "").strip()
            if not summary or summary in seen:
                continue
            seen.add(summary)
            date_str = ""
            t = e.get("event_time") or ""
            if t and len(str(t)) >= 10:
                date_str = f"[{str(t)[:10]}] "
            event_lines.append(f"- {date_str}{summary}")
        if event_lines:
            parts.append("Related memories:\n" + "\n".join(event_lines))

    context_string = "\n\n".join(parts) if parts else ""

    return {
        "profile": "\n".join(profile_parts),
        "events": all_events,
        "events_count": len(all_events),
        "source": f"A={recall_a.get('source','')} B={recall_b.get('source','')}",
        "context_string": context_string,
    }


async def evaluate_qa_pairs(
    client: MemoryClient,
    llm: AsyncOpenAI,
    sample: LocomoSample,
    *,
    model: str = "",
    concurrency: int = 3,
) -> list[EvalRecord]:
    """
    评测一个样本的所有 QA pairs.

    对每个 QA:
      1. recall 记忆
      2. 用记忆 + question 生成 prediction
      3. 构建 EvalRecord
    """
    model = model or LLM_MODEL
    records = []
    sem = asyncio.Semaphore(concurrency)

    async def _eval_one(qa: QAPair) -> EvalRecord:
        async with sem:
            # 召回
            recall_data = await _recall_for_question(client, sample, qa.question)

            # 生成答案
            prediction = await _generate_answer(
                llm,
                context=recall_data["context_string"],
                question=qa.question,
                category=qa.category,
            )

            # 构建 evidence 文本（用于 judge）
            evidence_text = evidence_to_text(sample, qa.evidence)

            return EvalRecord(
                question_input=qa.question,
                evidence=evidence_text,
                category=qa.category,
                ground_truth=qa.answer,
                prediction=prediction,
                model=model,
                sample_id=sample.sample_id,
                recall_profile=recall_data["profile"],
                recall_events_count=recall_data["events_count"],
                recall_source=recall_data["source"],
            )

    tasks = [_eval_one(qa) for qa in sample.qa_pairs]
    records = await asyncio.gather(*tasks)
    return list(records)


async def evaluate_cognitive_pairs(
    client: MemoryClient,
    llm: AsyncOpenAI,
    cognitive_samples: list[CognitiveSample],
    locomo_samples: list[LocomoSample],
    *,
    model: str = "",
    concurrency: int = 3,
) -> list[EvalRecord]:
    """
    评测 Cognitive 类 QA pairs.

    Cognitive 特殊：
      - 没有标准答案 (ground_truth="")
      - trigger_query 是触发对话
      - evidence 是 cue_dialogue
      - judge 检查 prediction 是否关联到 evidence
    """
    model = model or LLM_MODEL
    records = []
    sem = asyncio.Semaphore(concurrency)

    # Cognitive samples 需要关联到某个 locomo 样本
    # 官方 build_conv.py 是随机关联的，我们按顺序轮转
    n_locomo = len(locomo_samples)

    async def _eval_one(idx: int, cog: CognitiveSample) -> EvalRecord:
        async with sem:
            # 关联到一个 locomo 样本
            linked = locomo_samples[idx % n_locomo]

            # 解析 trigger_query 中的最后一句作为 query
            trigger_lines = [
                l.strip() for l in cog.trigger_query.strip().split("\n")
                if l.strip() and (l.strip().startswith("A:") or l.strip().startswith("B:"))
            ]
            trigger_text = trigger_lines[-1] if trigger_lines else cog.trigger_query
            # 去掉 "A: " 或 "B: " 前缀
            if trigger_text[:2] in ("A:", "B:"):
                trigger_text = trigger_text[2:].strip()

            # 召回
            recall_data = await _recall_for_question(client, linked, trigger_text)

            # 生成回复
            prediction = await _generate_answer(
                llm,
                context=recall_data["context_string"],
                question=trigger_text,
                category="Cognitive",
                trigger=trigger_text,
            )

            return EvalRecord(
                question_input=trigger_text,
                evidence=cog.cue_dialogue,
                category="Cognitive",
                ground_truth="",  # Cognitive 无标准答案
                prediction=prediction,
                model=model,
                sample_id=linked.sample_id,
                recall_profile=recall_data["profile"],
                recall_events_count=recall_data["events_count"],
                recall_source=recall_data["source"],
                time_gap=cog.time_gap,
            )

    tasks = [_eval_one(i, cog) for i, cog in enumerate(cognitive_samples)]
    records = await asyncio.gather(*tasks)
    return list(records)


async def run_evaluation(
    locomo_samples: list[LocomoSample],
    cognitive_samples: Optional[list[CognitiveSample]] = None,
    *,
    base_url: str = API_BASE_URL,
    timeout: float = API_TIMEOUT,
    model: str = "",
    concurrency: int = 3,
    categories: Optional[set[str]] = None,
) -> list[dict]:
    """
    运行完整评测.

    Args:
        locomo_samples: LoCoMo 样本列表
        cognitive_samples: Cognitive 样本列表（可选）
        categories: 要评测的类别集合（None = 全部）
        concurrency: 并发数

    Returns:
        list of dicts (EvalRecord 格式)，可直接喂给 llm_judge
    """
    model = model or LLM_MODEL
    all_records = []

    llm = await _get_llm_client()

    async with MemoryClient(base_url=base_url, timeout=timeout) as client:
        healthy = await client.health_check()
        if not healthy:
            logger.error("Memory system health check failed!")
            return []

        # 评测 5 类 QA
        for i, sample in enumerate(locomo_samples):
            logger.info(
                f"\n{'='*60}\n"
                f"Evaluating {i+1}/{len(locomo_samples)}: {sample.sample_id} "
                f"({len(sample.qa_pairs)} QA pairs)\n"
                f"{'='*60}"
            )
            t0 = time.time()
            records = await evaluate_qa_pairs(
                client, llm, sample,
                model=model, concurrency=concurrency,
            )

            # 按类别过滤
            if categories:
                records = [r for r in records if r.category in categories]

            all_records.extend(records)
            elapsed = time.time() - t0
            logger.info(
                f"[{sample.sample_id}] {len(records)} records, {elapsed:.1f}s"
            )

        # 评测 Cognitive 类
        if cognitive_samples and (categories is None or "Cognitive" in categories):
            logger.info(
                f"\n{'='*60}\n"
                f"Evaluating Cognitive: {len(cognitive_samples)} pairs\n"
                f"{'='*60}"
            )
            t0 = time.time()
            cog_records = await evaluate_cognitive_pairs(
                client, llm, cognitive_samples, locomo_samples,
                model=model, concurrency=concurrency,
            )
            all_records.extend(cog_records)
            elapsed = time.time() - t0
            logger.info(f"Cognitive: {len(cog_records)} records, {elapsed:.1f}s")

    return [asdict(r) for r in all_records]


def save_predictions(records: list[dict], path: str):
    """保存预测结果到 JSON（对齐官方格式）."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(records)} prediction records to {path}")


def print_eval_summary(records: list[dict]):
    """打印评测概要."""
    from collections import Counter
    cats = Counter(r["category"] for r in records)
    has_recall = sum(1 for r in records if r.get("recall_events_count", 0) > 0)

    print("\n" + "=" * 60)
    print("  Evaluation Summary (predictions generated)")
    print("=" * 60)
    print(f"  Total records: {len(records)}")
    print(f"  With memory recall: {has_recall}/{len(records)} ({100*has_recall/len(records):.1f}%)")
    print(f"  By category:")
    for cat, cnt in sorted(cats.items()):
        print(f"    {cat}: {cnt}")
    print("=" * 60)


# ── CLI ───────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Evaluate LoCoMo-Plus QA with memory system")
    parser.add_argument("--sample-ids", type=str, default="",
                        help="Comma-separated sample IDs. Empty = all.")
    parser.add_argument("--categories", type=str, default="",
                        help="Comma-separated categories to evaluate. Empty = all.")
    parser.add_argument("--no-cognitive", action="store_true",
                        help="Skip Cognitive category")
    parser.add_argument("--base-url", type=str, default=API_BASE_URL)
    parser.add_argument("--model", type=str, default="")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--out-file", type=str, default="results/predictions.json")
    args = parser.parse_args()

    samples = load_locomo10()
    if args.sample_ids:
        ids = {s.strip() for s in args.sample_ids.split(",")}
        samples = [s for s in samples if s.sample_id in ids]

    cog_samples = None if args.no_cognitive else load_locomo_plus()

    cats = None
    if args.categories:
        cats = {c.strip() for c in args.categories.split(",")}

    records = asyncio.run(run_evaluation(
        samples, cog_samples,
        base_url=args.base_url,
        model=args.model,
        concurrency=args.concurrency,
        categories=cats,
    ))

    print_eval_summary(records)
    save_predictions(records, args.out_file)
