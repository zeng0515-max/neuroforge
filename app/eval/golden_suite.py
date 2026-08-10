"""Offline golden evaluation harness for code-generation agents.

The suite is deliberately deterministic: it checks parseability, expected
symbols, expected keywords, and minimum artifact size. It is not a substitute
for execution-based evaluation, but it gives CI a stable regression gate that
does not depend on an LLM.
"""

from __future__ import annotations

import ast
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.eval.metrics import GOLDEN_PASS_THRESHOLD, GOLDEN_WARN_THRESHOLD, evaluate_gate


@dataclass(frozen=True)
class GoldenCase:
    id: str
    task: str
    expected_symbols: tuple[str, ...]
    expected_keywords: tuple[str, ...] = ()
    expected_symbol_groups: tuple[tuple[str, ...], ...] = ()
    expected_keyword_groups: tuple[tuple[str, ...], ...] = ()
    min_files: int = 1
    min_lines: int = 5
    tags: tuple[str, ...] = ()
    reference_code: str = ""


@dataclass
class GoldenCaseResult:
    case_id: str
    passed: bool
    checks: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass
class GoldenReport:
    total: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate: float = 0.0
    gate: str = "SKIP"
    mode: str = "offline"
    model: str = ""
    command: str = ""
    results: List[GoldenCaseResult] = field(default_factory=list)
    generated_at: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": round(self.pass_rate, 4),
            "gate": self.gate,
            "mode": self.mode,
            "model": self.model,
            "command": self.command,
            "generated_at": self.generated_at,
            "results": [
                {
                    "case_id": r.case_id,
                    "passed": r.passed,
                    "checks": r.checks,
                    "reason": r.reason,
                }
                for r in self.results
            ],
        }


def _defined_symbols(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
    return symbols


class GoldenEvaluator:
    """Evaluate generated file packages against a golden case."""

    def evaluate(self, case: GoldenCase, files: List[Dict[str, Any]]) -> GoldenCaseResult:
        checks: Dict[str, Any] = {}
        reasons: List[str] = []

        py_files = [f for f in files if str(f.get("path", "")).endswith(".py")]
        content_by_path = {str(f.get("path", "")): str(f.get("content", "")) for f in py_files}

        checks["min_files"] = len(py_files) >= case.min_files
        if not checks["min_files"]:
            reasons.append(f"expected >= {case.min_files} python file(s), got {len(py_files)}")

        combined = "\n".join(content_by_path.values())
        total_lines = combined.count("\n") + 1 if combined else 0
        checks["min_lines"] = total_lines >= case.min_lines
        if not checks["min_lines"]:
            reasons.append(f"expected >= {case.min_lines} lines, got {total_lines}")

        syntax_ok = True
        for path, content in content_by_path.items():
            try:
                ast.parse(content)
            except SyntaxError as exc:
                syntax_ok = False
                reasons.append(f"{path} has syntax error: {exc.msg}")
                break
        checks["syntax"] = syntax_ok

        symbols = set()
        for content in content_by_path.values():
            symbols |= _defined_symbols(content)
        symbol_groups = case.expected_symbol_groups or tuple((s,) for s in case.expected_symbols)
        missing_symbol_groups = [
            group for group in symbol_groups if not any(s in symbols for s in group)
        ]
        checks["expected_symbols"] = not missing_symbol_groups
        if missing_symbol_groups:
            flat = ", ".join(sorted({s for group in missing_symbol_groups for s in group}))
            reasons.append(f"missing symbol group: {flat}")

        lower = combined.lower()
        keyword_groups = case.expected_keyword_groups or tuple(
            (k,) for k in case.expected_keywords
        )
        missing_keyword_groups = [
            group for group in keyword_groups if not any(k.lower() in lower for k in group)
        ]
        checks["expected_keywords"] = not missing_keyword_groups
        if missing_keyword_groups:
            flat = ", ".join(sorted({k for group in missing_keyword_groups for k in group}))
            reasons.append(f"missing keyword group: {flat}")

        passed = all(checks.values())
        return GoldenCaseResult(
            case_id=case.id,
            passed=passed,
            checks=checks,
            reason="; ".join(reasons) if reasons else "all deterministic checks passed",
        )

    def evaluate_suite(
        self,
        generated_files: Dict[str, List[Dict[str, Any]]],
        cases: Optional[List[GoldenCase]] = None,
    ) -> GoldenReport:
        cases = cases or GOLDEN_CASES
        results: List[GoldenCaseResult] = []
        for case in cases:
            files = generated_files.get(case.id, [])
            results.append(self.evaluate(case, files))
        return build_report(results)


def build_report(results: List[GoldenCaseResult]) -> GoldenReport:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    rate = passed / max(total, 1)
    return GoldenReport(
        total=total,
        passed=passed,
        failed=total - passed,
        pass_rate=rate,
        gate=evaluate_gate(rate),
        results=results,
    )


def write_report(report: GoldenReport, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "golden-report.json"
    json_path.write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path = output_dir / "golden-report.md"
    lines = [
        "# Golden Evaluation Report",
        "",
        f"- Mode: {report.mode}",
        f"- Model: {report.model or 'N/A'}",
        f"- Command: `{report.command or 'N/A'}`",
        f"- Total: {report.total}",
        f"- Passed: {report.passed}",
        f"- Failed: {report.failed}",
        f"- Pass rate: {report.pass_rate:.1%}",
        f"- Gate: {report.gate}",
        "",
        "| Case | Passed | Reason |",
        "| --- | --- | --- |",
    ]
    for result in report.results:
        lines.append(
            f"| {result.case_id} | {'PASS' if result.passed else 'FAIL'} | {result.reason} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path


GOLDEN_CASES: List[GoldenCase] = [
    GoldenCase(
        id="lru_cache",
        task="Implement a thread-safe LRU cache class with get and put methods.",
        expected_symbols=("LRUCache", "get", "put"),
        expected_keywords=("collections", "OrderedDict"),
        min_lines=12,
        tags=("algorithm", "concurrency"),
        reference_code="""
from collections import OrderedDict

class LRUCache:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._data = OrderedDict()

    def get(self, key):
        if key not in self._data:
            return -1
        self._data.move_to_end(key)
        return self._data[key]

    def put(self, key, value):
        self._data[key] = value
        self._data.move_to_end(key)
        if len(self._data) > self.capacity:
            self._data.popitem(last=False)
""",
    ),
    GoldenCase(
        id="rate_limiter",
        task="Implement a token-bucket rate limiter with an allow method.",
        expected_symbols=(),
        expected_symbol_groups=(("RateLimiter", "TokenBucketRateLimiter", "TokenBucket"), ("allow",)),
        expected_keywords=("time",),
        min_lines=10,
        tags=("system-design",),
        reference_code="""
import time

class RateLimiter:
    def __init__(self, rate: float, burst: int) -> None:
        self.rate = rate
        self.burst = burst
        self.tokens = burst
        self.updated_at = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.updated_at) * self.rate)
        self.updated_at = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True
""",
    ),
    GoldenCase(
        id="csv_parser",
        task="Implement a CSV parser that returns a list of rows from raw text.",
        expected_symbols=(),
        expected_symbol_groups=(("parse_csv", "load_csv", "parse"),),
        expected_keywords=("csv",),
        min_lines=6,
        tags=("data-processing",),
        reference_code="""
import csv
from io import StringIO

def parse_csv(text: str):
    return list(csv.reader(StringIO(text)))
""",
    ),
    GoldenCase(
        id="thread_safe_counter",
        task="Implement a thread-safe counter with increment and get_value methods.",
        expected_symbols=(),
        expected_symbol_groups=(
            ("Counter", "ThreadSafeCounter"),
            ("increment",),
            ("get_value",),
        ),
        expected_keywords=("threading", "Lock"),
        min_lines=10,
        tags=("concurrency",),
        reference_code="""
import threading

class Counter:
    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    def increment(self) -> None:
        with self._lock:
            self._value += 1

    def get_value(self) -> int:
        with self._lock:
            return self._value
""",
    ),
    GoldenCase(
        id="simple_queue",
        task="Implement a FIFO queue using collections.deque with enqueue and dequeue methods.",
        expected_symbols=(),
        expected_symbol_groups=(
            ("Queue", "FIFOQueue", "SimpleQueue"),
            ("enqueue",),
            ("dequeue",),
        ),
        expected_keywords=("deque",),
        min_lines=8,
        tags=("data-structure",),
        reference_code="""
from collections import deque

class Queue:
    def __init__(self) -> None:
        self._items = deque()

    def enqueue(self, item) -> None:
        self._items.append(item)

    def dequeue(self):
        return self._items.popleft()
""",
    ),
    GoldenCase(
        id="fizzbuzz",
        task="Implement fizzbuzz(n) that returns a list with Fizz, Buzz, or FizzBuzz replacements.",
        expected_symbols=(),
        expected_symbol_groups=(("fizzbuzz", "fizz_buzz"),),
        expected_keywords=("FizzBuzz",),
        min_lines=5,
        tags=("algorithm",),
        reference_code="""
def fizzbuzz(n: int):
    out = []
    for i in range(1, n + 1):
        if i % 15 == 0:
            out.append("FizzBuzz")
        elif i % 3 == 0:
            out.append("Fizz")
        elif i % 5 == 0:
            out.append("Buzz")
        else:
            out.append(i)
    return out
""",
    ),
    GoldenCase(
        id="fibonacci",
        task="Implement fib(n) that returns the nth Fibonacci number efficiently.",
        expected_symbols=(),
        expected_symbol_groups=(("fib", "fibonacci"),),
        expected_keywords=(),
        expected_keyword_groups=(("memo", "cache", "dict", "iterative"),),
        min_lines=6,
        tags=("algorithm",),
        reference_code="""
def fib(n: int) -> int:
    memo = {0: 0, 1: 1}
    for i in range(2, n + 1):
        memo[i] = memo[i - 1] + memo[i - 2]
    return memo[n]
""",
    ),
    GoldenCase(
        id="data_cleaner",
        task="Implement clean_phone(phone) that removes non-digits from a phone string.",
        expected_symbols=(),
        expected_symbol_groups=(
            ("clean_phone", "clean_phone_number", "normalize_phone"),
        ),
        expected_keywords=("re",),
        min_lines=5,
        tags=("data-processing",),
        reference_code="""
import re

def clean_phone(phone: str) -> str:
    return re.sub(r"\\D", "", phone)
""",
    ),
]


def reference_files() -> Dict[str, List[Dict[str, str]]]:
    return {
        case.id: [{"path": f"{case.id}.py", "content": case.reference_code, "language": "python"}]
        for case in GOLDEN_CASES
    }


__all__ = [
    "GoldenCase",
    "GoldenCaseResult",
    "GoldenReport",
    "GoldenEvaluator",
    "GOLDEN_CASES",
    "build_report",
    "evaluate_gate",
    "GOLDEN_PASS_THRESHOLD",
    "GOLDEN_WARN_THRESHOLD",
    "reference_files",
    "write_report",
]
