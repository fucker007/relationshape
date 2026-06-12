#!/usr/bin/env python3
"""
dedupe_primary_persons.py
-------------------------

清理 person_nodes 中同一个 owner 下出现多个 role='primary' 的脏数据，
并修复 events.participant_ids / relationships.from_person_id / relationships.to_person_id
的引用，使它们指向保留下来的 keep_id。

策略
====
对每个 owner_id：
  1. 拉出该 owner 全部 role='primary' 的 person_node，按 created_at 升序。
  2. keep_id = 最早创建那条。
  3. drop_candidates = 其它 primary。
  4. 额外把 identity.name 命中占位词集合（"用户/小朋友/..."）的非 keep primary 也强制进 drop。
  5. 重映射:
       UPDATE events
         SET participant_ids = (
           SELECT array_agg(DISTINCT CASE WHEN x = ANY($drop_ids) THEN $keep_id ELSE x END)
           FROM unnest(participant_ids) x
         )
         WHERE participant_ids && $drop_ids;
       UPDATE relationships SET from_person_id = $keep_id WHERE from_person_id = ANY($drop_ids);
       UPDATE relationships SET to_person_id   = $keep_id WHERE to_person_id   = ANY($drop_ids);
       -- relationships 有 UNIQUE(from_person_id, to_person_id)，重映射后可能撞 unique，
       -- 所以先 DELETE 那些会撞车的 drop 行（保留 keep 的同向 relation）。
       DELETE FROM person_nodes WHERE person_id = ANY($drop_ids);

  整个 owner 在一个事务内完成。--dry-run（默认）只打印计划。

注意
====
- events 表结构: participant_ids UUID[]，没有直连 person_id 字段（已对照 storage/pg_store.py 确认）。
- relationships 表结构: from_person_id / to_person_id（不是 subject_id/object_id）。
- daily_emotions 也有 person_id 列；该脚本同样会重映射。

用法
====
  # dry-run，全 owner 扫描
  python scripts/dedupe_primary_persons.py

  # dry-run，只看一个 owner
  python scripts/dedupe_primary_persons.py --owner-id 7263aee7-a7bb-508f-9c2f-c487c6d8bf60

  # 真执行
  python scripts/dedupe_primary_persons.py --owner-id 7263aee7-... --execute

环境变量
========
  PG_DSN  默认 postgres://postgres@localhost:5434/memory
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import asyncpg


PLACEHOLDER_NAMES = {
    "用户", "小朋友", "朋友", "宝宝", "孩子", "小孩",
    "你", "我", "他", "她", "主人",
    "user", "User", "USER",
    "", None,
}

DEFAULT_DSN = os.environ.get(
    "PG_DSN", "postgres://postgres@localhost:5434/memory"
)


# -------------------------------------------------------------------- helpers

def _identity_name(identity_raw: Any) -> str | None:
    if identity_raw is None:
        return None
    if isinstance(identity_raw, dict):
        ident = identity_raw
    else:
        try:
            ident = json.loads(identity_raw) if identity_raw else {}
        except Exception:
            return None
    if not isinstance(ident, dict):
        return None
    v = ident.get("name")
    return v if isinstance(v, str) else None


async def _fetch_owner_groups(
    conn: asyncpg.Connection, owner_id: str | None
) -> list[tuple[str, list[asyncpg.Record]]]:
    """返回 [(owner_id, [primary_nodes asc by created_at]), ...]，仅含 count>1 的 owner。"""
    if owner_id:
        rows = await conn.fetch(
            """
            SELECT person_id, owner_id, name, role, identity, created_at
            FROM person_nodes
            WHERE owner_id = $1::uuid AND role = 'primary'
            ORDER BY created_at ASC
            """,
            owner_id,
        )
        return [(owner_id, list(rows))] if len(rows) > 1 else []

    rows = await conn.fetch(
        """
        SELECT person_id, owner_id, name, role, identity, created_at
        FROM person_nodes
        WHERE role = 'primary'
        ORDER BY owner_id, created_at ASC
        """
    )
    groups: dict[str, list[asyncpg.Record]] = {}
    for r in rows:
        groups.setdefault(str(r["owner_id"]), []).append(r)
    return [(oid, lst) for oid, lst in groups.items() if len(lst) > 1]


async def _count_event_refs(
    conn: asyncpg.Connection, drop_ids: list[str]
) -> int:
    if not drop_ids:
        return 0
    return await conn.fetchval(
        "SELECT COUNT(*) FROM events WHERE participant_ids && $1::uuid[]",
        drop_ids,
    )


async def _count_rel_refs(
    conn: asyncpg.Connection, drop_ids: list[str]
) -> tuple[int, int]:
    if not drop_ids:
        return 0, 0
    f = await conn.fetchval(
        "SELECT COUNT(*) FROM relationships WHERE from_person_id = ANY($1::uuid[])",
        drop_ids,
    )
    t = await conn.fetchval(
        "SELECT COUNT(*) FROM relationships WHERE to_person_id = ANY($1::uuid[])",
        drop_ids,
    )
    return f, t


async def _count_emotion_refs(
    conn: asyncpg.Connection, drop_ids: list[str]
) -> int:
    if not drop_ids:
        return 0
    return await conn.fetchval(
        "SELECT COUNT(*) FROM daily_emotions WHERE person_id = ANY($1::uuid[])",
        drop_ids,
    )


# -------------------------------------------------------------------- core

async def process_owner(
    conn: asyncpg.Connection,
    owner_id: str,
    primaries: list[asyncpg.Record],
    *,
    execute: bool,
) -> dict:
    """处理单个 owner 的清理/计划。返回报告 dict。"""
    keep = primaries[0]
    keep_id = str(keep["person_id"])
    drop_ids: list[str] = [str(r["person_id"]) for r in primaries[1:]]

    # 额外: identity.name 是占位词的非 keep primary，也算 drop（其实已包含在 drop_ids，
    # 这里只是单独标注出来便于报告）
    polluted: list[tuple[str, str | None]] = []
    for r in primaries[1:]:
        nm = _identity_name(r["identity"])
        if nm in PLACEHOLDER_NAMES or (r["name"] in PLACEHOLDER_NAMES):
            polluted.append((str(r["person_id"]), nm))

    ev_cnt = await _count_event_refs(conn, drop_ids)
    rel_from_cnt, rel_to_cnt = await _count_rel_refs(conn, drop_ids)
    emo_cnt = await _count_emotion_refs(conn, drop_ids)

    report = {
        "owner_id": owner_id,
        "keep_id": keep_id,
        "keep_name": keep["name"],
        "keep_created_at": str(keep["created_at"]),
        "drop_ids": drop_ids,
        "drop_detail": [
            {
                "person_id": str(r["person_id"]),
                "name": r["name"],
                "identity_name": _identity_name(r["identity"]),
                "created_at": str(r["created_at"]),
            }
            for r in primaries[1:]
        ],
        "polluted_drops": polluted,
        "events_to_remap": ev_cnt,
        "relationships_from_to_remap": rel_from_cnt,
        "relationships_to_to_remap": rel_to_cnt,
        "daily_emotions_to_remap": emo_cnt,
        "executed": False,
    }

    if not execute or not drop_ids:
        return report

    # 真执行：单事务
    async with conn.transaction():
        # 1) events.participant_ids 数组重映射
        await conn.execute(
            """
            UPDATE events
            SET participant_ids = (
                SELECT COALESCE(
                    array_agg(DISTINCT CASE WHEN x = ANY($1::uuid[]) THEN $2::uuid ELSE x END),
                    '{}'::uuid[]
                )
                FROM unnest(participant_ids) AS x
            )
            WHERE participant_ids && $1::uuid[]
            """,
            drop_ids, keep_id,
        )

        # 2) relationships: 先删掉重映射后会和 keep 冲突 unique 的 drop 行
        await conn.execute(
            """
            DELETE FROM relationships r
            WHERE r.from_person_id = ANY($1::uuid[])
              AND EXISTS (
                  SELECT 1 FROM relationships k
                  WHERE k.from_person_id = $2::uuid
                    AND k.to_person_id = r.to_person_id
              )
            """,
            drop_ids, keep_id,
        )
        await conn.execute(
            """
            DELETE FROM relationships r
            WHERE r.to_person_id = ANY($1::uuid[])
              AND EXISTS (
                  SELECT 1 FROM relationships k
                  WHERE k.to_person_id = $2::uuid
                    AND k.from_person_id = r.from_person_id
              )
            """,
            drop_ids, keep_id,
        )
        # 自指 (from=keep, to=keep) 也删，没意义
        await conn.execute(
            """
            UPDATE relationships
            SET from_person_id = $2::uuid
            WHERE from_person_id = ANY($1::uuid[])
            """,
            drop_ids, keep_id,
        )
        await conn.execute(
            """
            UPDATE relationships
            SET to_person_id = $2::uuid
            WHERE to_person_id = ANY($1::uuid[])
            """,
            drop_ids, keep_id,
        )
        await conn.execute(
            "DELETE FROM relationships WHERE from_person_id = to_person_id",
        )

        # 3) daily_emotions
        # 该表有 UNIQUE(person_id, date)，先删冲突行
        await conn.execute(
            """
            DELETE FROM daily_emotions d
            WHERE d.person_id = ANY($1::uuid[])
              AND EXISTS (
                  SELECT 1 FROM daily_emotions k
                  WHERE k.person_id = $2::uuid AND k.date = d.date
              )
            """,
            drop_ids, keep_id,
        )
        await conn.execute(
            """
            UPDATE daily_emotions
            SET person_id = $2::uuid
            WHERE person_id = ANY($1::uuid[])
            """,
            drop_ids, keep_id,
        )

        # 4) 最后删除重复 primary 节点
        await conn.execute(
            "DELETE FROM person_nodes WHERE person_id = ANY($1::uuid[])",
            drop_ids,
        )

    report["executed"] = True
    return report


# -------------------------------------------------------------------- cli

async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=DEFAULT_DSN, help="postgres DSN")
    parser.add_argument("--owner-id", default=None,
                        help="只处理指定 owner_id；不传则全库扫")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True,
                   help="(默认) 只打印计划，不写库")
    g.add_argument("--execute", action="store_true", default=False,
                   help="真改库，事务内提交")
    args = parser.parse_args()

    execute = bool(args.execute)
    mode = "EXECUTE" if execute else "DRY-RUN"
    print(f"[dedupe] mode={mode} dsn={args.dsn} owner_id={args.owner_id or 'ALL'}")

    conn = await asyncpg.connect(args.dsn)
    try:
        groups = await _fetch_owner_groups(conn, args.owner_id)
        if not groups:
            print("[dedupe] 没有发现重复 primary，无需处理。")
            return 0

        print(f"[dedupe] 发现 {len(groups)} 个 owner 存在重复 primary。")
        for owner_id, primaries in groups:
            print("\n" + "=" * 72)
            print(f"owner_id = {owner_id}  primary_count = {len(primaries)}")
            report = await process_owner(
                conn, owner_id, primaries, execute=execute
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))

        if not execute:
            print("\n[dedupe] DRY-RUN 完成。如确认无误，加 --execute 真跑。")
        else:
            print("\n[dedupe] EXECUTE 完成。请接着上线 add_primary_person_unique_index.sql。")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
