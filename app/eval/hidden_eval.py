"""隐藏测试执行器：把生成代码与"黄金预期输出"用例一起在隔离临时目录执行。
计算真实的 golden_pass_rate（真实通过率），而不是只依赖 review_verdict / AST 模板测试。

设计要点：
- 复用 app/tools/code_executor.py 的隔离机制：临时目录 + 进程组超时杀 + 环境变量白名单
- 复用 app/tools/ast_validator.py 的 AST 校验与路径穿越防御，保证生成代码安全
- 隐藏用例由我方（可信）编写，通过 eval 在模块命名空间执行；harness 为项目内可信代码，
  不经过 AST 校验（避免误报 eval），但生成代码本身仍严格校验
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from app.tools.code_executor import SAFE_ENV_VARS, _run_subprocess_with_pgroup

logger = logging.getLogger(__name__)


# ====================================================================== #
#  结果数据结构
# ====================================================================== #
@dataclass
class HiddenEvalResult:
    """隐藏测试执行结果。"""

    golden_total: int = 0
    golden_passed: int = 0
    golden_pass_rate: float = 0.0
    passed_cases: List[str] = field(default_factory=list)
    failed_cases: List[Dict[str, Any]] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "golden_total": self.golden_total,
            "golden_passed": self.golden_passed,
            "golden_pass_rate": round(self.golden_pass_rate, 4),
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "error": self.error,
        }


# ====================================================================== #
#  Harness 模板（注入 import 语句与用例，逐条断言并输出 JSON + SUMMARY）
# ====================================================================== #
_HARNESS_TEMPLATE = r"""# -*- coding: utf-8 -*-
import json
import sys


def main():
    ns = {}
    exec(__IMPORT_STMT__, ns)
    cases = __CASES__
    passed = 0
    for case in cases:
        call = case["call"]
        exc = None
        result = None
        try:
            result = eval(call, ns)
        except Exception as e:  # noqa: BLE001
            exc = e
        expected = case.get("expected", "__MISSING__")
        raises = case.get("raises")
        if exc is not None:
            ok = (raises is not None and type(exc).__name__ == raises)
        elif raises is not None:
            ok = False
        else:
            ok = (result == expected)
        entry = {
            "call": call,
            "ok": ok,
            "expected": expected if "expected" in case else None,
            "raises": raises,
            "actual": None if exc is None else type(exc).__name__,
            "error": str(exc) if exc is not None else None,
        }
        print(json.dumps(entry, ensure_ascii=False))
        if ok:
            passed += 1
    print("SUMMARY %d %d" % (passed, len(cases)))
    sys.exit(0)


main()
"""


def _build_harness(golden: Dict[str, Any]) -> str:
    """根据 golden 规格生成 harness 源码。"""
    import_stmt = golden.get("import", "")
    cases = golden.get("cases", [])
    return (
        _HARNESS_TEMPLATE
        .replace("__IMPORT_STMT__", json.dumps(import_stmt))
        .replace("__CASES__", json.dumps(cases, ensure_ascii=False))
    )


def _parse_output(stdout: str, result: HiddenEvalResult) -> None:
    """解析 harness 输出：逐行 JSON 用例 + 末尾 SUMMARY 行。"""
    entries: List[Dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("SUMMARY"):
            parts = line.split()
            try:
                result.golden_passed = int(parts[1])
                result.golden_total = int(parts[2])
            except (ValueError, IndexError):
                pass
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    result.passed_cases = [e["call"] for e in entries if e.get("ok")]
    result.failed_cases = [e for e in entries if not e.get("ok")]
    if result.golden_total == 0:
        result.golden_total = len(entries)
    result.golden_pass_rate = result.golden_passed / max(result.golden_total, 1)


# ====================================================================== #
#  主入口
# ====================================================================== #
def run_hidden_eval(
    files: List[Dict[str, str]],
    *,
    golden: Dict[str, Any],
    timeout: int = 15,
) -> HiddenEvalResult:
    """执行隐藏测试。

    参数：
        files: [{"path": "main.py", "content": "...", "language": "python"}, ...]
        golden: {"import": "from main import to_snake_case",
                 "cases": [{"call": "to_snake_case('x')", "expected": "x"}, ...]}
        timeout: 整体执行超时秒数

    返回：HiddenEvalResult
    """
    result = HiddenEvalResult()
    py_files = [f for f in files if (f.get("path", "") or "").endswith(".py")]
    if not py_files:
        result.error = "无 Python 文件可验证"
        return result

    tmpdir = tempfile.mkdtemp(prefix="hidden_eval_")
    try:
        tmp_root = Path(tmpdir).resolve()
        from app.tools.ast_validator import validate_python_source

        # 1. 写入生成代码（AST 校验 + 路径穿越防御，与 code_executor 保持一致）
        written: Dict[str, Path] = {}
        for f in py_files:
            rel = (f.get("path") or "").strip().lstrip("/")
            if not rel:
                continue
            rel_path = Path(rel)
            if ".." in rel_path.parts:
                continue
            target = (Path(tmpdir) / rel).resolve()
            try:
                target.relative_to(tmp_root)
            except ValueError:
                continue  # 越界
            content = f.get("content") or ""
            ast_result = validate_python_source(content)
            if not ast_result.passed:
                logger.warning(
                    "隐藏测试跳过文件 %s（AST 校验未通过）", rel
                )
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written[rel] = target

        if not written:
            result.error = "生成代码未通过 AST 校验，无文件可执行"
            return result

        # 2. 写入 harness 并执行
        harness_path = tmp_root / "_hidden_harness.py"
        harness_path.write_text(_build_harness(golden), encoding="utf-8")

        env = {k: v for k, v in os.environ.items() if k in SAFE_ENV_VARS}
        env["PYTHONPATH"] = str(tmpdir)
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        rc, stdout, stderr, timed_out = _run_subprocess_with_pgroup(
            [sys.executable, str(harness_path)],
            timeout=timeout,
            cwd=tmpdir,
            env=env,
        )
        if timed_out:
            result.error = f"隐藏测试执行超时（{timeout}s）"
            return result
        if rc != 0:
            result.error = (stderr or stdout or "")[:500]
            return result
        _parse_output(stdout, result)
        return result
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


__all__ = ["HiddenEvalResult", "run_hidden_eval"]