#!/usr/bin/env python3
"""
LoCoMo-Plus 完整评测流水线 — 注入 → 评测 → Judge → 汇总.

用法:
  # 单样本验证全链路
  python run_eval.py --sample-ids conv-26 --categories single-hop

  # 单样本全类别（不含 Cognitive）
  python run_eval.py --sample-ids conv-26 --no-cognitive

  # 全量评测
  python run_eval.py

  # 跳过注入（已注入过的）
  python run_eval.py --skip-inject

  # 只注入不评测
  python run_eval.py --inject-only
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# 确保可以 import 同目录模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

from locomo_loader import load_locomo10, load_locomo_plus
from memory_injector import (
    inject_all_samples, print_injection_summary, save_injection_stats,
)
from memory_evaluator import (
    run_evaluation, save_predictions, print_eval_summary,
)
from llm_judge import (
    judge_batch_sync, compute_summary, print_summary,
)


def setup_logging(log_file: str = ""):
    """配置日志."""
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def clear_sample_data(sample_ids: list[str], samples):
    """清理指定样本在数据库中的数据."""
    import subprocess

    logger = logging.getLogger(__name__)

    for sample in samples:
        if sample_ids and sample.sample_id not in sample_ids:
            continue

        from memory_client import device_to_owner_uuid
        device_a = f"locomo_{sample.sample_id}_{sample.speaker_a}"
        device_b = f"locomo_{sample.sample_id}_{sample.speaker_b}"
        owner_a = device_to_owner_uuid(device_a)
        owner_b = device_to_owner_uuid(device_b)

        for owner_id, name in [(owner_a, sample.speaker_a), (owner_b, sample.speaker_b)]:
            sql = f"DELETE FROM events WHERE owner_id = '{owner_id}'"
            cmd = (
                f"docker exec memory-allinone psql "
                f"-h /var/run/postgresql -p 5434 -U memory -d memory "
                f"-tAc \"{sql}\""
            )
            try:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
                deleted = result.stdout.strip()
                if deleted:
                    logger.info(f"Cleared {deleted} events for {name} ({owner_id[:8]}...)")
            except Exception as e:
                logger.warning(f"Failed to clear data for {name}: {e}")

            # 也清理 person_nodes
            sql2 = f"DELETE FROM person_nodes WHERE owner_id = '{owner_id}'"
            cmd2 = (
                f"docker exec memory-allinone psql "
                f"-h /var/run/postgresql -p 5434 -U memory -d memory "
                f"-tAc \"{sql2}\""
            )
            try:
                subprocess.run(cmd2, shell=True, capture_output=True, text=True, timeout=10)
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(
        description="LoCoMo-Plus Full Evaluation Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--sample-ids", type=str, default="",
                        help="Comma-separated sample IDs (e.g. conv-26). Empty = all 10.")
    parser.add_argument("--categories", type=str, default="",
                        help="Comma-separated categories. Empty = all 6.")
    parser.add_argument("--no-cognitive", action="store_true",
                        help="Skip Cognitive category evaluation")

    # 阶段控制
    parser.add_argument("--skip-inject", action="store_true",
                        help="Skip injection (use existing data)")
    parser.add_argument("--inject-only", action="store_true",
                        help="Only inject, skip evaluation and judging")
    parser.add_argument("--skip-judge", action="store_true",
                        help="Skip judging (only generate predictions)")
    parser.add_argument("--judge-only", type=str, default="",
                        help="Only judge from existing predictions file")
    parser.add_argument("--clear-data", action="store_true",
                        help="Clear existing memory data before injection")

    # 配置
    parser.add_argument("--base-url", type=str, default="http://localhost:8010")
    parser.add_argument("--model", type=str, default="",
                        help="Model for answer generation (default: qwen-plus)")
    parser.add_argument("--judge-model", type=str, default="",
                        help="Model for judging (default: qwen-plus)")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--judge-concurrency", type=int, default=5)
    parser.add_argument("--sleep-session", type=float, default=2.0)
    parser.add_argument("--sleep-final", type=float, default=15.0)

    # 输出
    parser.add_argument("--out-dir", type=str, default="results",
                        help="Output directory for all result files")
    parser.add_argument("--run-name", type=str, default="",
                        help="Run name prefix for output files")

    args = parser.parse_args()

    # 生成 run name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = args.run_name or f"locomo_{timestamp}"
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log_file = str(out_dir / f"{run_name}.log")
    setup_logging(log_file)
    logger = logging.getLogger(__name__)

    logger.info(f"Run: {run_name}")
    logger.info(f"Args: {vars(args)}")

    # ── 加载数据 ──────────────────────────────────────
    t_total = time.time()

    samples = load_locomo10()
    sample_ids = []
    if args.sample_ids:
        sample_ids = [s.strip() for s in args.sample_ids.split(",")]
        samples = [s for s in samples if s.sample_id in set(sample_ids)]
        if not samples:
            logger.error(f"No samples found for: {args.sample_ids}")
            sys.exit(1)

    cog_samples = None
    if not args.no_cognitive:
        cog_samples = load_locomo_plus()

    cats = None
    if args.categories:
        cats = {c.strip() for c in args.categories.split(",")}

    logger.info(f"Samples: {[s.sample_id for s in samples]}")
    logger.info(f"Categories: {cats or 'all'}")
    logger.info(f"Cognitive: {'yes' if cog_samples else 'no'} ({len(cog_samples or [])} pairs)")

    # ── Judge-only 模式 ───────────────────────────────
    if args.judge_only:
        logger.info(f"Judge-only mode: loading {args.judge_only}")
        with open(args.judge_only, "r", encoding="utf-8") as f:
            records = json.load(f)
        logger.info(f"Loaded {len(records)} prediction records")

        judged = judge_batch_sync(
            records,
            concurrency=args.judge_concurrency,
            model=args.judge_model or None,
        )
        summary = compute_summary(judged)
        print_summary(summary)

        judge_file = str(out_dir / f"{run_name}_judged.json")
        summary_file = str(out_dir / f"{run_name}_summary.json")
        with open(judge_file, "w", encoding="utf-8") as f:
            json.dump(judged, f, ensure_ascii=False, indent=2)
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        logger.info(f"Judged: {judge_file}")
        logger.info(f"Summary: {summary_file}")
        return

    # ── Phase 1: 注入 ────────────────────────────────
    if not args.skip_inject:
        if args.clear_data:
            logger.info("Clearing existing memory data...")
            clear_sample_data(sample_ids, samples)

        logger.info("\n" + "=" * 70)
        logger.info("  PHASE 1: INJECTION")
        logger.info("=" * 70)

        inject_stats = asyncio.run(inject_all_samples(
            samples,
            base_url=args.base_url,
            sleep_after_session=args.sleep_session,
            sleep_after_all=args.sleep_final,
        ))
        print_injection_summary(inject_stats)
        save_injection_stats(inject_stats, str(out_dir / f"{run_name}_inject.json"))

        if args.inject_only:
            logger.info("Inject-only mode: done.")
            return
    else:
        logger.info("Skipping injection (--skip-inject)")

    # ── Phase 2: 评测 ────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info("  PHASE 2: EVALUATION (recall + generate)")
    logger.info("=" * 70)

    records = asyncio.run(run_evaluation(
        samples, cog_samples,
        base_url=args.base_url,
        model=args.model,
        concurrency=args.concurrency,
        categories=cats,
    ))

    pred_file = str(out_dir / f"{run_name}_predictions.json")
    save_predictions(records, pred_file)
    print_eval_summary(records)

    if args.skip_judge:
        logger.info("Skipping judge (--skip-judge)")
        total_elapsed = time.time() - t_total
        logger.info(f"\nTotal time: {total_elapsed:.1f}s")
        return

    # ── Phase 3: Judge ────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info("  PHASE 3: LLM-AS-JUDGE")
    logger.info("=" * 70)

    judged = judge_batch_sync(
        records,
        concurrency=args.judge_concurrency,
        model=args.judge_model or None,
    )

    summary = compute_summary(judged)
    print_summary(summary)

    # 保存结果
    judge_file = str(out_dir / f"{run_name}_judged.json")
    summary_file = str(out_dir / f"{run_name}_summary.json")

    with open(judge_file, "w", encoding="utf-8") as f:
        json.dump(judged, f, ensure_ascii=False, indent=2)
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    logger.info(f"\nResults saved:")
    logger.info(f"  Predictions: {pred_file}")
    logger.info(f"  Judged:      {judge_file}")
    logger.info(f"  Summary:     {summary_file}")

    total_elapsed = time.time() - t_total
    logger.info(f"\nTotal time: {total_elapsed:.1f}s")

    # 最终汇总
    print("\n" + "=" * 70)
    print(f"  LoCoMo-Plus Evaluation Complete: {run_name}")
    print("=" * 70)
    print(f"  Samples:    {len(samples)}")
    print(f"  QA pairs:   {len(records)}")
    print(f"  Overall:    {summary['overall_avg']:.4f}")
    for cat, v in summary["by_category"].items():
        print(f"    {cat:15s}: {v['avg']:.4f} ({v['score']:.1f}/{v['count']})")
    print(f"  Time:       {total_elapsed:.1f}s")
    print("=" * 70)


if __name__ == "__main__":
    main()
