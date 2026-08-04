"""代码执行验证工具：把生成的代码写入临时目录，执行语法检查 + 导入测试 + 运行验证。

设计要点：
- 隔离：tempfile.mkdtemp 创建临时目录，跑完 shutil.rmtree
- 安全：subprocess 不传 shell=True；超时 15s 防死循环；环境变量白名单；路径穿越防御
- 容错：单文件失败不阻断整体，收集所有结果
"""
from __future__ import annotations

import ast
import keyword
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 环境变量白名单：仅保留运行所必需的最小集合，避免泄露本机敏感环境变量
SAFE_ENV_VARS = {"PATH", "SYSTEMROOT", "TEMP", "TMP", "LANG", "LC_ALL", "PYTHONIOENCODING"}

# 第三方依赖缺失时不算验证失败——模型生成的代码引用第三方库是正常的
_THIRD_PARTY_MISSING_RE = re.compile(
    r"(ModuleNotFoundError|ImportError).*No module named\s+['\"](\w+)['\"]"
)


def _run_subprocess_with_pgroup(
    args: List[str],
    *,
    timeout: int,
    cwd: str,
    env: Dict[str, str],
) -> Tuple[int, str, str, bool]:
    """Bug P6 修复：在独立进程组中启动子进程，超时时杀整个进程组避免孤儿进程。

    返回 (returncode, stdout, stderr, timed_out)。
    """
    # 启动时创建新 session（Unix）或新进程组（Windows），使超时时可杀整组
    kwargs: Dict[str, Any] = {}
    if sys.platform != "win32":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=env,
        **kwargs,
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # 杀整个进程组，避免孙子进程变孤儿
        if sys.platform != "win32":
            import signal
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        else:
            proc.kill()
        stdout, stderr = proc.communicate(timeout=5)
        timed_out = True
    return proc.returncode, stdout or "", stderr or "", timed_out


@dataclass
class FileExecResult:
    """单文件执行结果。"""
    path: str = ""
    syntax_ok: bool = False
    syntax_error: str = ""
    import_ok: bool = False
    import_error: str = ""
    import_is_third_party: bool = False  # True 表示是第三方库缺失，非代码质量问题
    run_ok: bool = False
    run_stdout: str = ""
    run_stderr: str = ""
    duration_ms: int = 0


@dataclass
class ExecResult:
    """整体执行验证结果。"""
    success: bool = False
    total_files: int = 0
    passed_files: int = 0
    failed_files: int = 0
    results: List[FileExecResult] = field(default_factory=list)
    duration_ms: int = 0
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "total_files": self.total_files,
            "passed_files": self.passed_files,
            "failed_files": self.failed_files,
            "results": [
                {
                    "path": r.path,
                    "syntax_ok": r.syntax_ok,
                    "syntax_error": r.syntax_error,
                    "import_ok": r.import_ok,
                    "import_error": r.import_error,
                    "run_ok": r.run_ok,
                    "run_stdout": r.run_stdout[:2000],
                    "run_stderr": r.run_stderr[:2000],
                    "duration_ms": r.duration_ms,
                }
                for r in self.results
            ],
            "duration_ms": self.duration_ms,
            "summary": self.summary,
        }


def execute_code(
    files: List[Dict[str, str]],
    *,
    timeout: int = 15,
    run_entry: bool = True,
) -> ExecResult:
    """执行代码验证：语法检查 → 导入测试 → 运行入口文件。

    参数：
        files: [{"path": "main.py", "content": "...", "language": "python"}, ...]
        timeout: 单步执行超时秒数
        run_entry: 是否尝试运行入口文件（main.py 或 __main__.py）

    返回：ExecResult
    """
    result = ExecResult(total_files=len(files))
    t0 = time.perf_counter()

    py_files = [f for f in files if (f.get("path", "") or "").endswith(".py")]
    if not py_files:
        result.summary = "无 Python 文件可验证"
        result.duration_ms = int((time.perf_counter() - t0) * 1000)
        return result

    tmpdir = tempfile.mkdtemp(prefix="code_exec_verify_")
    try:
        # 1. 写入所有文件（含 AST 校验 + 路径穿越防御）
        written_paths: Dict[str, Path] = {}
        tmp_root = Path(tmpdir).resolve()
        from app.tools.ast_validator import validate_python_source
        for f in py_files:
            rel = (f.get("path") or "").strip().lstrip("/")
            if not rel:
                continue
            # Bug 10: 路径穿越防御——禁止 .. 越界，resolve 后必须仍在 tmpdir 内
            rel_path = Path(rel)
            if ".." in rel_path.parts:
                continue
            target = (Path(tmpdir) / rel).resolve()
            try:
                target.relative_to(tmp_root)
            except ValueError:
                continue  # 越界
            content = f.get("content") or ""
            # Bug 4: 写入前 AST 校验，passed=False 则跳过该文件不执行
            ast_result = validate_python_source(content)
            if not ast_result.passed:
                # Bug 13: AST 校验失败也记录结果，避免统计偏差
                fr = FileExecResult(path=rel, syntax_ok=False,
                                    syntax_error="; ".join(v.message for v in ast_result.errors[:3]))
                result.results.append(fr)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written_paths[rel] = target

        # 2. 逐文件验证
        for rel, target in written_paths.items():
            fr = FileExecResult(path=rel)
            content = target.read_text(encoding="utf-8")

            # 2a. 语法检查（AST 解析）
            try:
                ast.parse(content)
                fr.syntax_ok = True
            except SyntaxError as e:
                fr.syntax_ok = False
                fr.syntax_error = f"第{e.lineno}行: {e.msg}"
                result.results.append(fr)
                continue

            # 2b. 导入测试（python -c "import module"）
            module_name = rel.replace("/", ".").replace(".py", "")
            # Bug 12: 模块名合法性校验，防止命令注入
            parts = module_name.split(".")
            if not all(p.isidentifier() and not keyword.iskeyword(p) for p in parts if p):
                fr.import_ok = False
                fr.import_error = f"非法模块名: {module_name}"
                result.results.append(fr)
                continue
            # Bug 11: 环境变量白名单，仅保留运行必需的最小集合
            env = {k: v for k, v in os.environ.items() if k in SAFE_ENV_VARS}
            env["PYTHONPATH"] = str(tmpdir)
            env["PYTHONDONTWRITEBYTECODE"] = "1"

            t_imp = time.perf_counter()
            try:
                # Bug P6：用 Popen + 进程组替代 subprocess.run，超时时杀整组避免孤儿
                rc, _stdout, stderr, timed_out = _run_subprocess_with_pgroup(
                    [sys.executable, "-c", f"import {module_name}"],
                    timeout=timeout, cwd=tmpdir, env=env,
                )
                if timed_out:
                    fr.import_ok = False
                    fr.import_error = f"导入超时（{timeout}s）"
                else:
                    fr.import_ok = (rc == 0)
                    if not fr.import_ok:
                        err_text = (stderr or _stdout or "")[:500]
                        fr.import_error = err_text
                        # Bug 14: 区分第三方库缺失（非代码质量问题）vs 真实语法/逻辑错误
                        m = _THIRD_PARTY_MISSING_RE.search(err_text)
                        if m:
                            fr.import_is_third_party = True
                            fr.import_ok = True  # 第三方库缺失不算代码质量失败
                fr.duration_ms = int((time.perf_counter() - t_imp) * 1000)
            except Exception as e:
                fr.import_ok = False
                fr.import_error = str(e)[:500]
                fr.duration_ms = int((time.perf_counter() - t_imp) * 1000)

            # 2c. 运行入口文件（仅对 main.py / __main__.py / app.py）
            if run_entry and rel in ("main.py", "__main__.py", "app.py", "run.py"):
                t_run = time.perf_counter()
                try:
                    # Bug P6：用 Popen + 进程组替代 subprocess.run，超时时杀整组避免孤儿
                    rc, stdout, stderr, timed_out = _run_subprocess_with_pgroup(
                        [sys.executable, str(target)],
                        timeout=timeout, cwd=tmpdir, env=env,
                    )
                    if timed_out:
                        fr.run_ok = False
                        fr.run_stderr = f"运行超时（{timeout}s）"
                    else:
                        fr.run_ok = (rc == 0)
                        fr.run_stdout = stdout[-2000:]
                        fr.run_stderr = stderr[-2000:]
                    fr.duration_ms += int((time.perf_counter() - t_run) * 1000)
                except Exception as e:
                    fr.run_ok = False
                    fr.run_stderr = str(e)[:500]
                    fr.duration_ms += int((time.perf_counter() - t_run) * 1000)

            result.results.append(fr)

        # 3. 汇总
        result.passed_files = sum(
            1 for r in result.results if r.syntax_ok and r.import_ok
        )
        result.failed_files = result.total_files - result.passed_files
        result.success = (result.failed_files == 0)
        result.duration_ms = int((time.perf_counter() - t0) * 1000)

        if result.success:
            result.summary = f"验证通过：{result.passed_files} 个文件全部语法正确且可导入"
        else:
            failed_names = [
                r.path for r in result.results if not (r.syntax_ok and r.import_ok)
            ]
            result.summary = f"验证失败：{result.failed_files} 个文件有问题（{', '.join(failed_names[:5])}）"

        return result

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


__all__ = ["execute_code", "ExecResult", "FileExecResult"]
