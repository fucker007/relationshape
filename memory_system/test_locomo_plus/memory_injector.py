"""
LoCoMo-Plus Memory Injector — 逐 session 注入对话到记忆系统.

设计：两人对话 → 双视角注入
  - speaker_a 视角：owner_id=A, user_message=A的话, assistant_message=B的话
  - speaker_b 视角：owner_id=B, user_message=B的话, assistant_message=A的话
  每轮对话同时从两个视角注入，各自积累独立记忆。

系统每 5 轮 user_message 自动触发 background extract，无需手动调用。
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from locomo_loader import (
    LocomoSample, CognitiveSample, SessionData, Turn,
    load_locomo10, load_locomo_plus,
)
from memory_client import MemoryClient, device_to_owner_uuid

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────
EXTRACT_TRIGGER_EVERY = 5          # 系统每 5 轮触发 extract
SLEEP_AFTER_SESSION   = 2.0        # session 间等待 (秒)
SLEEP_AFTER_ALL       = 15.0       # 全部注入后等最后一批 extract 完成
API_BASE_URL          = "http://localhost:8010"
API_TIMEOUT           = 60.0


@dataclass
class InjectionStats:
    """注入统计."""
    sample_id: str = ""
    speaker_a: str = ""
    speaker_b: str = ""
    total_sessions: int = 0
    total_turns: int = 0
    turns_injected_a: int = 0    # A 视角注入的轮数
    turns_injected_b: int = 0    # B 视角注入的轮数
    errors: int = 0
    elapsed_sec: float = 0.0


def _build_dual_pairs(turns: list[Turn], speaker_a: str, speaker_b: str):
    """
    把一个 session 的 turns 配对成双视角注入对.

    LoCoMo 对话是 A/B 交替说话。对于连续的 (A说, B说) 对：
      - A 视角: user_message=A的话, assistant_message=B的话
      - B 视角: user_message=B的话, assistant_message=A的话

    单独的尾轮（没有配对）也要注入（assistant_message=""）。

    Returns:
        list of dicts: [{
            'a_user_msg': str, 'a_asst_msg': str,
            'b_user_msg': str, 'b_asst_msg': str,
        }]
    """
    pairs = []
    i = 0
    while i < len(turns):
        t = turns[i]
        # 看下一轮是否是对方说的
        next_t = turns[i + 1] if i + 1 < len(turns) else None

        if t.speaker == speaker_a:
            a_user = t.text
            if next_t and next_t.speaker == speaker_b:
                a_asst = next_t.text
                b_user = next_t.text
                b_asst = t.text
                i += 2
            else:
                # A 说了但 B 没回（尾轮）
                a_asst = ""
                b_user = ""
                b_asst = ""
                i += 1
            pairs.append({
                'a_user_msg': a_user, 'a_asst_msg': a_asst,
                'b_user_msg': b_user, 'b_asst_msg': b_asst,
            })
        elif t.speaker == speaker_b:
            b_user = t.text
            if next_t and next_t.speaker == speaker_a:
                b_asst = next_t.text
                a_user = next_t.text
                a_asst = t.text
                i += 2
            else:
                b_asst = ""
                a_user = ""
                a_asst = ""
                i += 1
            pairs.append({
                'a_user_msg': a_user, 'a_asst_msg': a_asst,
                'b_user_msg': b_user, 'b_asst_msg': b_asst,
            })
        else:
            logger.warning(f"Unknown speaker: {t.speaker}")
            i += 1

    return pairs


async def inject_sample(
    client: MemoryClient,
    sample: LocomoSample,
    *,
    session_prefix: str = "",
    sleep_after_session: float = SLEEP_AFTER_SESSION,
) -> InjectionStats:
    """
    注入一个 LoCoMo 样本的所有 session.

    每个 session 使用独立 session_id（让系统正确划分会话窗口）。
    双视角注入：A 和 B 各自作为 user 发送自己说的话。

    Args:
        client: MemoryClient 实例
        sample: LocomoSample 数据
        session_prefix: session_id 前缀（用于区分不同实验）
        sleep_after_session: session 间等待秒数
    """
    stats = InjectionStats(
        sample_id=sample.sample_id,
        speaker_a=sample.speaker_a,
        speaker_b=sample.speaker_b,
        total_sessions=len(sample.sessions),
    )
    t0 = time.time()

    # device_id 用 sample_id + speaker 保证唯一
    device_a = f"locomo_{sample.sample_id}_{sample.speaker_a}"
    device_b = f"locomo_{sample.sample_id}_{sample.speaker_b}"
    owner_a = device_to_owner_uuid(device_a)
    owner_b = device_to_owner_uuid(device_b)

    prefix = session_prefix or sample.sample_id

    for sess in sample.sessions:
        sess_id_a = f"{prefix}_s{sess.session_idx}_{sample.speaker_a}"
        sess_id_b = f"{prefix}_s{sess.session_idx}_{sample.speaker_b}"

        pairs = _build_dual_pairs(sess.turns, sample.speaker_a, sample.speaker_b)
        stats.total_turns += len(sess.turns)

        for pair in pairs:
            # A 视角注入
            if pair['a_user_msg']:
                try:
                    await client.send_turn(
                        owner_id=owner_a,
                        user_name=sample.speaker_a,
                        user_message=pair['a_user_msg'],
                        assistant_message=pair['a_asst_msg'],
                        session_id=sess_id_a,
                    )
                    stats.turns_injected_a += 1
                except Exception as e:
                    logger.warning(f"[{sample.sample_id}] A inject error: {e}")
                    stats.errors += 1

            # B 视角注入
            if pair['b_user_msg']:
                try:
                    await client.send_turn(
                        owner_id=owner_b,
                        user_name=sample.speaker_b,
                        user_message=pair['b_user_msg'],
                        assistant_message=pair['b_asst_msg'],
                        session_id=sess_id_b,
                    )
                    stats.turns_injected_b += 1
                except Exception as e:
                    logger.warning(f"[{sample.sample_id}] B inject error: {e}")
                    stats.errors += 1

        if sleep_after_session > 0:
            await asyncio.sleep(sleep_after_session)

        logger.info(
            f"[{sample.sample_id}] session {sess.session_idx}/{len(sample.sessions)} done, "
            f"A={stats.turns_injected_a} B={stats.turns_injected_b}"
        )

    stats.elapsed_sec = time.time() - t0
    return stats


async def inject_all_samples(
    samples: list[LocomoSample],
    *,
    base_url: str = API_BASE_URL,
    timeout: float = API_TIMEOUT,
    sleep_after_session: float = SLEEP_AFTER_SESSION,
    sleep_after_all: float = SLEEP_AFTER_ALL,
    session_prefix: str = "",
) -> list[InjectionStats]:
    """注入所有样本，返回统计列表."""
    all_stats = []

    async with MemoryClient(base_url=base_url, timeout=timeout) as client:
        healthy = await client.health_check()
        if not healthy:
            logger.error("Memory system health check failed!")
            return all_stats

        for i, sample in enumerate(samples):
            logger.info(
                f"\n{'='*60}\n"
                f"Injecting sample {i+1}/{len(samples)}: {sample.sample_id} "
                f"({sample.speaker_a} & {sample.speaker_b}, "
                f"{len(sample.sessions)} sessions, "
                f"{sum(len(s.turns) for s in sample.sessions)} turns)\n"
                f"{'='*60}"
            )
            stats = await inject_sample(
                client, sample,
                session_prefix=session_prefix or sample.sample_id,
                sleep_after_session=sleep_after_session,
            )
            all_stats.append(stats)

            logger.info(
                f"[{sample.sample_id}] DONE: "
                f"A={stats.turns_injected_a} B={stats.turns_injected_b} "
                f"errors={stats.errors} time={stats.elapsed_sec:.1f}s"
            )

        # 等待最后一批 extract 完成
        if sleep_after_all > 0:
            logger.info(f"Waiting {sleep_after_all}s for final extraction to complete...")
            await asyncio.sleep(sleep_after_all)

    return all_stats


def print_injection_summary(all_stats: list[InjectionStats]):
    """打印注入汇总."""
    print("\n" + "=" * 70)
    print("  Injection Summary")
    print("=" * 70)
    total_a = sum(s.turns_injected_a for s in all_stats)
    total_b = sum(s.turns_injected_b for s in all_stats)
    total_err = sum(s.errors for s in all_stats)
    total_time = sum(s.elapsed_sec for s in all_stats)

    for s in all_stats:
        print(
            f"  {s.sample_id:10s}  {s.speaker_a:12s} & {s.speaker_b:12s}  "
            f"sessions={s.total_sessions:2d}  turns={s.total_turns:4d}  "
            f"injected(A={s.turns_injected_a:3d} B={s.turns_injected_b:3d})  "
            f"err={s.errors}  time={s.elapsed_sec:.1f}s"
        )

    print("-" * 70)
    print(
        f"  TOTAL: {len(all_stats)} samples, "
        f"injected A={total_a} B={total_b}, "
        f"errors={total_err}, "
        f"time={total_time:.1f}s"
    )
    print("=" * 70)


def save_injection_stats(all_stats: list[InjectionStats], path: str):
    """保存注入统计到 JSON."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(s) for s in all_stats], f, indent=2, ensure_ascii=False)
    print(f"Injection stats saved to {path}")


# ── CLI ───────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Inject LoCoMo conversations into memory system")
    parser.add_argument("--sample-ids", type=str, default="",
                        help="Comma-separated sample IDs (e.g. conv-26,conv-30). Empty = all.")
    parser.add_argument("--base-url", type=str, default=API_BASE_URL)
    parser.add_argument("--sleep-session", type=float, default=SLEEP_AFTER_SESSION)
    parser.add_argument("--sleep-final", type=float, default=SLEEP_AFTER_ALL)
    parser.add_argument("--out-file", type=str, default="results/injection_stats.json")
    args = parser.parse_args()

    samples = load_locomo10()

    if args.sample_ids:
        ids = {s.strip() for s in args.sample_ids.split(",")}
        samples = [s for s in samples if s.sample_id in ids]
        if not samples:
            print(f"No samples found for IDs: {args.sample_ids}")
            exit(1)

    print(f"Will inject {len(samples)} samples")
    for s in samples:
        print(f"  {s.sample_id}: {s.speaker_a} & {s.speaker_b}, {len(s.sessions)} sessions")

    stats = asyncio.run(inject_all_samples(
        samples,
        base_url=args.base_url,
        sleep_after_session=args.sleep_session,
        sleep_after_all=args.sleep_final,
    ))

    print_injection_summary(stats)
    save_injection_stats(stats, args.out_file)
