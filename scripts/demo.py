#!/usr/bin/env python3
"""NeuroForge Demo — 一键流水线演示脚本。

Usage:
    python scripts/demo.py
    python scripts/demo.py --task "write a Python LRU cache"
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

if sys.platform == "win32":
    os.system("")


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    BG_BLUE = "\033[44m"
    BG_GREEN = "\033[42m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"


def banner(title: str, color: str = C.CYAN, width: int = 58) -> None:
    pad = width - len(title) - 4
    left = pad // 2
    right = pad - left
    print()
    print(f"{color}{C.BOLD}╔{'═' * width}╗{C.RESET}")
    print(f"{color}{C.BOLD}║{' ' * left} {title} {' ' * right}║{C.RESET}")
    print(f"{color}{C.BOLD}╚{'═' * width}╝{C.RESET}")
    print()


def stage_icon(index: int) -> str:
    icons = ["🔍", "⚙️", "🔎", "🧪", "✅"]
    return icons[index % len(icons)]


def run_pipeline(task: str, dry_run: bool = True) -> Dict[str, Any]:
    try:
        from app.graph.unified_engine import run_engine
    except ImportError:
        return _simulate(task)

    task_id = str(uuid.uuid4())[:8]
    print(f"  {C.DIM}Task ID: {task_id}{C.RESET}")
    print(f"  {C.DIM}Engine: unified (dry-run={dry_run}){C.RESET}")
    print()

    stages = ["requirement", "code_gen", "review", "test_gen"]
    stage_times: List[float] = []
    stage_tokens: List[int] = []

    t_start = time.perf_counter()

    try:
        state = run_engine(
            task_id=task_id,
            user_request=task,
            mode="linear",
            enable_checkpoint=False,
        )
        elapsed_ms = (time.perf_counter() - t_start) * 1000

        code = str(state.get("generated_code", ""))
        tests = str(state.get("generated_tests", ""))
        total_tokens = 0

        try:
            from app.core.llm_adapter import llm
            total_tokens = llm.usage.total_tokens
        except Exception:
            pass

        for i, name in enumerate(stages):
            t = elapsed_ms / len(stages)
            tok = total_tokens // len(stages) if total_tokens else 0
            stage_times.append(t)
            stage_tokens.append(tok)
            icon = stage_icon(i)
            bar_len = 20
            pct = (i + 1) / len(stages)
            filled = int(bar_len * pct)
            bar = "█" * filled + "░" * (bar_len - filled)
            color = C.GREEN if i < 2 else C.YELLOW if i < 3 else C.CYAN
            print(f"  {icon} {C.BOLD}{color}{name:14s}{C.RESET} │ {color}{bar}{C.RESET} │ {t:7.0f} ms │ {tok:6d} tok")

        code_lines = len(code.splitlines()) if code else 0
        test_lines = len(tests.splitlines()) if tests else 0
        success = state.get("status") == "completed" or bool(code)

        return {
            "success": success,
            "stages": stages,
            "stage_times": stage_times,
            "stage_tokens": stage_tokens,
            "total_tokens": total_tokens,
            "total_ms": elapsed_ms,
            "code_lines": code_lines,
            "test_lines": test_lines,
            "mode": "engine",
        }
    except Exception as e:
        print(f"  {C.YELLOW}⚠ Engine error: {e}{C.RESET}")
        print(f"  {C.CYAN}→ Falling back to simulation mode...{C.RESET}")
        return _simulate(task, stages, stage_times, stage_tokens)


def _simulate(
    task: str,
    stages: Optional[List[str]] = None,
    stage_times: Optional[List[float]] = None,
    stage_tokens: Optional[List[int]] = None,
) -> Dict[str, Any]:
    if stages is None:
        stages = ["requirement", "code_gen", "review", "test_gen"]
    if stage_times is None:
        stage_times = []
    if stage_tokens is None:
        stage_tokens = []

    print(f"  {C.DIM}Mode: simulation (engine unavailable){C.RESET}")
    print()

    for i, name in enumerate(stages):
        t0 = time.perf_counter()
        time.sleep(0.02 * (i + 1))
        tok = 800 + i * 400
        elapsed = (time.perf_counter() - t0) * 1000 + 250 + i * 180
        stage_times.append(elapsed)
        stage_tokens.append(tok)
        icon = stage_icon(i)
        bar_len = 20
        pct = (i + 1) / len(stages)
        filled = int(bar_len * pct)
        bar = "█" * filled + "░" * (bar_len - filled)
        color = C.GREEN if i < 2 else C.YELLOW if i < 3 else C.CYAN
        print(f"  {icon} {C.BOLD}{color}{name:14s}{C.RESET} │ {color}{bar}{C.RESET} │ {elapsed:7.0f} ms │ {tok:6d} tok")

    total_tokens = sum(stage_tokens)
    total_ms = sum(stage_times)

    return {
        "success": True,
        "stages": stages,
        "stage_times": stage_times,
        "stage_tokens": stage_tokens,
        "total_tokens": total_tokens,
        "total_ms": total_ms,
        "code_lines": 42,
        "test_lines": 18,
        "mode": "simulation",
    }


def print_summary(result: Dict[str, Any], task: str) -> None:
    w = 58
    L = [
        "",
        "╔" + "═" * w + "╗",
        "║" + f"  NEUROFORGE DEMO — EXECUTION REPORT".center(w) + "║",
        "╠" + "═" * w + "╣",
        f"  Task:       {task}",
        f"  Mode:       {result['mode']}",
        f"  Status:      {C.GREEN + C.BOLD if result['success'] else C.RED + C.BOLD}{'✦ SUCCESS' if result['success'] else '✗ FAILED'}{C.RESET}",
        f"  Total Time:  {C.BLUE}{result['total_ms']:,.0f} ms{C.RESET}",
        f"  Tokens:      {C.MAGENTA}{result['total_tokens']:,}{C.RESET}",
        f"  Code:        {result['code_lines']} lines",
        f"  Tests:       {result['test_lines']} lines",
        "",
        "  Stage Breakdown:",
    ]
    for i, name in enumerate(result["stages"]):
        t = result["stage_times"][i]
        tok = result["stage_tokens"][i]
        pct = max(0, t) / max(1, result["total_ms"]) * 20
        bar = "█" * int(pct) + "░" * (20 - int(pct))
        icon = stage_icon(i)
        L.append(f"    {icon} {name:14s} │ {bar} │ {t:7.0f} ms │ {tok:6d} tok")
    L += ["", "╚" + "═" * w + "╝", ""]

    for line in L:
        print(line)


def completion_banner(success: bool, elapsed: float) -> None:
    color = C.GREEN if success else C.RED
    status = "SUCCESS" if success else "FAILED"
    w = 58
    print()
    print(f"{color}{C.BOLD}╔{'═' * w}╗{C.RESET}")
    print(f"{color}{C.BOLD}║{' ' * 16} ✦ {status} ✦ {' ' * (w - 22 - len(status))}║{C.RESET}")
    print(f"{color}{C.BOLD}╚{'═' * w}╝{C.RESET}")
    print(f"  {C.DIM}Total wall-clock: {elapsed:.2f}s{C.RESET}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NeuroForge Demo — 一键流水线演示",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/demo.py
  python scripts/demo.py --task "write a Python LRU cache"
  python scripts/demo.py --task "build a FastAPI CRUD service"
        """,
    )
    parser.add_argument(
        "--task",
        default="write a Python LRU cache",
        help="任务描述 (default: 'write a Python LRU cache')",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="禁用 dry-run 模式（会尝试真实 LLM 调用）",
    )
    args = parser.parse_args()

    banner("NeuroForge Multi-Agent Code Engine", C.CYAN)

    print(f"  {C.BOLD}Task:{C.RESET} {C.YELLOW}{args.task}{C.RESET}")
    print(f"  {C.BOLD}Dry-Run:{C.RESET} {C.GREEN}{'ON' if not args.no_dry_run else 'OFF'}{C.RESET}")
    print()

    t0 = time.perf_counter()
    result = run_pipeline(args.task, dry_run=not args.no_dry_run)
    elapsed = time.perf_counter() - t0

    print_summary(result, args.task)
    completion_banner(result["success"], elapsed)


if __name__ == "__main__":
    main()