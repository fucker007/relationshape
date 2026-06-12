"""R1 并发压测 (P0 全清后性能基线)

方案: S3 混合读写 + C1 梯度(1/5/10/20/50) + O2 多 owner + 60s 每档
  - 80% S1 纯 recall (2 轮对话不触发 extract)
  - 20% S2 recall+extract (5 轮触发 outbox)
  - owner 池: 20 个, 轮流使用
"""
from __future__ import annotations
import asyncio, httpx, uuid, time, json, random, statistics, subprocess, os

URL = "http://localhost:8010/api/v1/memory/chat"
DUR = 60  # 每档持续秒
GRADIENTS = [1, 5, 10, 20, 50]
OWNER_POOL_SIZE = 20

RECALL_TURNS = [
    ("我叫小明", "你好小明"),
    ("我喜欢打篮球", "不错的爱好"),
]
EXTRACT_TURNS = [
    ("我叫小明", "你好小明"),
    ("我今年10岁", "好的"),
    ("我喜欢数学", "很棒"),
    ("我有个弟弟叫小强", "记住了"),
    ("昨天和小强打了一架", "听起来很生气"),
]

# 预生成 owner 池
OWNERS = [str(uuid.uuid4()) for _ in range(OWNER_POOL_SIZE)]


async def one_session(client: httpx.AsyncClient, is_extract: bool):
    owner = random.choice(OWNERS)
    sid = str(uuid.uuid4())
    turns = EXTRACT_TURNS if is_extract else RECALL_TURNS
    latencies = []
    errors = 0
    for u, a in turns:
        t0 = time.monotonic()
        try:
            r = await client.post(URL, json={
                "owner_id": owner,
                "user_name": "张伟",
                "user_message": u,
                "assistant_message": a,
                "session_id": sid,
            }, timeout=15)
            dt = (time.monotonic() - t0) * 1000
            if r.status_code != 200:
                errors += 1
            else:
                latencies.append(dt)
        except Exception:
            errors += 1
    return latencies, errors


async def worker(client, stop_event, stats):
    while not stop_event.is_set():
        is_extract = random.random() < 0.2
        lats, errs = await one_session(client, is_extract)
        stats["latencies"].extend(lats)
        stats["errors"] += errs
        stats["sessions"] += 1


async def run_gradient(concurrency: int, duration: int):
    stats = {"latencies": [], "errors": 0, "sessions": 0}
    stop = asyncio.Event()
    async with httpx.AsyncClient() as client:
        tasks = [asyncio.create_task(worker(client, stop, stats))
                 for _ in range(concurrency)]
        t0 = time.monotonic()
        await asyncio.sleep(duration)
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        wall = time.monotonic() - t0

    lats = sorted(stats["latencies"])
    n = len(lats)
    if n == 0:
        return {"concurrency": concurrency, "requests": 0, "error": "no data"}
    p50 = lats[n // 2]
    p95 = lats[min(int(n * 0.95), n - 1)]
    p99 = lats[min(int(n * 0.99), n - 1)]
    rps = n / wall
    return {
        "concurrency": concurrency,
        "wall_sec": round(wall, 1),
        "sessions": stats["sessions"],
        "requests": n,
        "errors": stats["errors"],
        "error_rate": round(stats["errors"] / max(1, n + stats["errors"]) * 100, 2),
        "rps": round(rps, 1),
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "p99_ms": round(p99, 1),
        "avg_ms": round(statistics.mean(lats), 1),
    }


def pg(sql: str) -> str:
    r = subprocess.run(
        ["env", "PGPASSWORD=memory", "psql", "-h", "127.0.0.1", "-p", "5434",
         "-U", "memory", "-d", "memory", "-tAc", sql],
        capture_output=True, text=True)
    return r.stdout.strip()


def snapshot_db():
    return {
        "pg_active": pg("SELECT count(*) FROM pg_stat_activity WHERE datname='memory' AND state='active';"),
        "pg_total": pg("SELECT count(*) FROM pg_stat_activity WHERE datname='memory';"),
        "tasks_pending": pg("SELECT count(*) FROM extraction_tasks WHERE status='pending';"),
        "tasks_processing": pg("SELECT count(*) FROM extraction_tasks WHERE status='processing';"),
        "tasks_failed": pg("SELECT count(*) FROM extraction_tasks WHERE status='failed';"),
        "tasks_done": pg("SELECT count(*) FROM extraction_tasks WHERE status='done';"),
        "events_total": pg("SELECT count(*) FROM events;"),
    }


def container_stats():
    r = subprocess.run(
        ["docker", "stats", "--no-stream", "--format",
         "{{.CPUPerc}}|{{.MemUsage}}", "memory-allinone"],
        capture_output=True, text=True)
    return r.stdout.strip()


async def main():
    print(f"=== R1 并发压测 ===  mode=S3 (80% recall + 20% extract)  "
          f"owners={OWNER_POOL_SIZE}  duration={DUR}s per step\n")
    results = []
    print(f"[pre] {snapshot_db()}")
    print(f"[pre] container: {container_stats()}\n")

    for c in GRADIENTS:
        print(f"--- concurrency={c} ---")
        db_before = snapshot_db()
        r = await run_gradient(c, DUR)
        db_after = snapshot_db()
        cs = container_stats()
        r["db_delta_events"] = int(db_after["events_total"]) - int(db_before["events_total"])
        r["db_tasks_pending_end"] = db_after["tasks_pending"]
        r["db_tasks_failed_end"] = db_after["tasks_failed"]
        r["container_end"] = cs
        print(json.dumps(r, ensure_ascii=False))
        print(f"    db_after: active={db_after['pg_active']}/{db_after['pg_total']} "
              f"pending={db_after['tasks_pending']} failed={db_after['tasks_failed']} "
              f"done={db_after['tasks_done']}")
        print(f"    container: {cs}\n")
        results.append(r)
        # 档间冷却 5s 让 extract 消化
        await asyncio.sleep(5)

    out = "/home/zihai/workspace/Agent_server_design/memory_system/reports/r1_bench_results.json"
    with open(out, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nJSON → {out}")

    # 汇总表
    print("\n=== 汇总 ===")
    print(f"{'conc':>6} {'reqs':>6} {'rps':>8} {'err%':>6} "
          f"{'p50':>7} {'p95':>7} {'p99':>7} {'avg':>7}")
    for r in results:
        if r.get("requests", 0) == 0:
            continue
        print(f"{r['concurrency']:>6} {r['requests']:>6} {r['rps']:>8} "
              f"{r['error_rate']:>6} {r['p50_ms']:>7} {r['p95_ms']:>7} "
              f"{r['p99_ms']:>7} {r['avg_ms']:>7}")


if __name__ == "__main__":
    asyncio.run(main())
