"""记忆系统测试总览：把散落各处的记忆测试/压测/模糊/快照/持久化一次性跑齐，出一张总表。

涵盖：
  · 单元测试（pytest）：test_memory / test_memory_port / test_llm_extract / test_pg_store
  · 抽取召回压测（memory_stress：recall / mixed / update / qualified）—— 留出泛化集
  · 精确率注入压测（memory_stress --precision）—— 生产日志5类污染，强不变量
  · 独立多轮对话模糊（convo_fuzz）—— 纯规则精确率 + 召回；理想抽取器召回上界
  · 指令快照（directive_snapshot）—— 54 轮确定性回归
  · PostgreSQL 持久化（pg_smoke）—— 设 RELATIONSHAPE_PG_DSN 时才跑

用法：
  python eval/run_memory_suite.py            # 中等规模（快，约1分钟）
  python eval/run_memory_suite.py --full     # 大规模（留出5万/精确率10万/模糊3万）
  python eval/run_memory_suite.py --quick     # 冒烟（秒级）
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

# 规模档位：(memory_stress每场景条数, precision条数, convo_fuzz组数)
SIZES = {"quick": (300, 800, 400), "moderate": (4000, 6000, 3000), "full": (50000, 100000, 30000)}


def sh(cmd: list[str], env: dict | None = None) -> tuple[int, str]:
    e = dict(os.environ)
    if env:
        e.update(env)
    p = subprocess.run(cmd, cwd=str(ROOT), env=e, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def _last_total(out: str) -> tuple[int, int] | None:
    """取最后一个"总计 X/Y"或"总召回率：X/Y"。"""
    ms = re.findall(r"(?:总计|总召回率)\D*?(\d+)\s*/\s*(\d+)", out)
    if not ms:
        return None
    return int(ms[-1][0]), int(ms[-1][1])


def _pct(hits: int, total: int) -> float:
    return 100.0 * hits / max(total, 1)


def _fuzz_metrics(out: str) -> dict[str, tuple[int, int]]:
    """从 convo_fuzz 输出抓每行指标：'名称  X/Y  Z%'。"""
    d = {}
    for name, h, t in re.findall(r"([一-鿿（）()A-Za-z]+)\s+(\d+)/(\d+)\s+[\d.]+%", out):
        d[name] = (int(h), int(t))
    return d


class Result:
    def __init__(self, name, kind, ok, summary, detail=""):
        self.name, self.kind, self.ok, self.summary, self.detail = name, kind, ok, summary, detail


def run_suite(label: str, sizes, verbose: bool) -> list[Result]:
    ms_per, prec_per, fuzz_n = sizes
    results: list[Result] = []

    def note(name, kind, ok, summary, out=""):
        results.append(Result(name, kind, ok, summary, out))
        flag = "✓" if ok else ("•" if kind == "soft" else "✗")
        print(f"  [{flag}] {name:<24} {summary}")
        if verbose and out:
            print("\n".join("        " + ln for ln in out.strip().splitlines()[-30:]))

    pg_dsn = os.environ.get("RELATIONSHAPE_PG_DSN") or os.environ.get("MEMORY_PG_DSN")

    # 1. 单元测试（记忆相关）
    files = ["tests/test_memory.py", "tests/test_memory_port.py",
             "tests/test_llm_extract.py", "tests/test_pg_store.py"]
    rc, out = sh([PY, "-m", "pytest", "-q", *files])
    m = re.search(r"(\d+) passed(?:, (\d+) skipped)?(?:, (\d+) failed)?", out)
    failed = re.search(r"(\d+) failed", out)
    passed = m.group(1) if m else "?"
    skipped = (m.group(2) if m and m.group(2) else "0")
    note("单元测试 pytest", "hard", rc == 0 and not failed,
         f"{passed} passed, {skipped} skipped" + (" ✗有失败" if failed else ""), out)

    # 2. 抽取召回（留出 test 池）
    rc, out = sh([PY, "eval/memory_stress.py", "--pool", "test", "--per", str(ms_per)])
    tot = _last_total(out)
    p = _pct(*tot) if tot else 0
    note("抽取召回压测", "soft", p >= 95, f"{p:.1f}%  ({tot[0]}/{tot[1]})" if tot else "解析失败", out)

    # 3. 混合负载 torture
    rc, out = sh([PY, "eval/memory_stress.py", "--pool", "test", "--mixed", "--per", str(max(ms_per // 8, 200))])
    tot = _last_total(out)
    p = _pct(*tot) if tot else 0
    note("混合负载 torture", "soft", p >= 85, f"{p:.1f}%  ({tot[0]}/{tot[1]})" if tot else "解析失败", out)

    # 4. 冲突更新
    rc, out = sh([PY, "eval/memory_stress.py", "--pool", "test", "--update", "--per", str(ms_per)])
    tot = _last_total(out)
    p = _pct(*tot) if tot else 0
    note("冲突更新健壮性", "soft", p >= 95, f"{p:.1f}%  ({tot[0]}/{tot[1]})" if tot else "解析失败", out)

    # 5. 带属性多实体
    rc, out = sh([PY, "eval/memory_stress.py", "--pool", "test", "--qualified", "--per", str(ms_per)])
    tot = _last_total(out)
    p = _pct(*tot) if tot else 0
    note("带属性多实体", "soft", p >= 95, f"{p:.1f}%  ({tot[0]}/{tot[1]})" if tot else "解析失败", out)

    # 6. 精确率注入（强不变量：必须 100%）
    rc, out = sh([PY, "eval/memory_stress.py", "--pool", "test", "--precision", "--per", str(prec_per)])
    tot = _last_total(out)
    p = _pct(*tot) if tot else 0
    note("精确率注入(5类污染)", "hard", tot is not None and tot[0] == tot[1],
         f"{p:.1f}%  ({tot[0]}/{tot[1]})" if tot else "解析失败", out)

    # 7. 独立多轮模糊（纯规则）：精确率 5 项必须 100%；召回软报
    rc, out = sh([PY, "eval/convo_fuzz.py", "-n", str(fuzz_n)])
    fm = _fuzz_metrics(out)
    prec_keys = ["无幻觉偏好", "无幻觉厌恶", "第三方不入偏好", "撤回已生效", "提问闲聊不污染"]
    prec_ok = all(k in fm and fm[k][0] == fm[k][1] for k in prec_keys)
    rec_keys = ["名字召回", "喜好召回", "厌恶召回", "朋友召回"]
    rec_str = " ".join(f"{k[:2]}{_pct(*fm[k]):.0f}%" for k in rec_keys if k in fm)
    note("独立多轮·纯规则", "hard", prec_ok, f"精确率{'全100%' if prec_ok else '✗有破口'} ｜ 召回 {rec_str}", out)

    # 8. 独立多轮模糊（理想抽取器 = 召回上界）
    rc, out = sh([PY, "eval/convo_fuzz.py", "-n", str(fuzz_n), "--oracle", "--llm-mode", "always"])
    fm = _fuzz_metrics(out)
    prec_ok = all(k in fm and fm[k][0] == fm[k][1] for k in prec_keys)
    rec_str = " ".join(f"{k[:2]}{_pct(*fm[k]):.0f}%" for k in rec_keys if k in fm)
    note("独立多轮·理想上界", "soft", prec_ok, f"召回上界 {rec_str}", out)

    # 9. 指令快照
    rc, out = sh([PY, "eval/directive_snapshot.py"])
    note("指令快照回归", "hard", rc == 0 and "零漂移" in out,
         "54轮零漂移" if rc == 0 else "✗发生漂移", out)

    # 10. PostgreSQL 持久化（有 DSN 才跑）
    if pg_dsn:
        rc, out = sh([PY, "eval/pg_smoke.py"])
        note("PostgreSQL 持久化", "hard", rc == 0 and "✓" in out,
             "往返+精确率保持" if rc == 0 else "✗失败", out)
    else:
        note("PostgreSQL 持久化", "soft", True, "跳过（未设 RELATIONSHAPE_PG_DSN）")

    return results


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--quick", action="store_true", help="冒烟规模（秒级）")
    g.add_argument("--full", action="store_true", help="大规模（留出5万/精确率10万/模糊3万）")
    ap.add_argument("--verbose", action="store_true", help="打印每个子套件的尾部输出")
    a = ap.parse_args()
    label = "quick" if a.quick else "full" if a.full else "moderate"

    t0 = time.time()
    print("═" * 70)
    print(f"  记忆系统 · 测试总览  ·  规模={label}  ·  {SIZES[label]}（压测条数/精确率条数/模糊组数）")
    print("═" * 70)
    results = run_suite(label, SIZES[label], a.verbose)
    dt = time.time() - t0

    hard = [r for r in results if r.kind == "hard"]
    hard_fail = [r for r in hard if not r.ok]
    soft_warn = [r for r in results if r.kind == "soft" and not r.ok]
    print("─" * 70)
    verdict = "PASS ✓" if not hard_fail else f"FAIL ✗（{len(hard_fail)} 个强校验未过）"
    print(f"  总判定：{verdict}   ·   强校验 {len(hard)-len(hard_fail)}/{len(hard)} 通过"
          f"   ·   用时 {dt:.0f}s")
    if hard_fail:
        print("  未过：" + "、".join(r.name for r in hard_fail))
    if soft_warn:
        print("  软指标偏低（不计入判定）：" + "、".join(f"{r.name}({r.summary})" for r in soft_warn))
    print("═" * 70)
    sys.exit(1 if hard_fail else 0)


if __name__ == "__main__":
    main()
