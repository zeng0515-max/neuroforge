from __future__ import annotations

import ast
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.llm_adapter import LLMAdapter, LLMUsage, llm
from app.db.sqlite_client import db
from app.graph.state import (
    CodePackage,
    GeneratedFile,
    PipelinePhase,
    PipelineState,
    RequirementSpec,
    TestCase,
    TestReport,
    TestStatus,
    ensure_status,
)
from app.tools.ast_validator import extract_first_python_code_block

logger = logging.getLogger(__name__)


# ====================================================================== #
#  纯本地测试生成（0 Token）
#  原理：用 AST 解析代码文件，提取函数签名，生成模板测试
# ====================================================================== #

def _extract_functions(source: str) -> List[Dict[str, Any]]:
    """用 AST 提取所有公共顶层函数的签名信息（跳过类方法与私有函数）。"""
    funcs: List[Dict[str, Any]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return funcs

    # 为每个节点设置 parent，便于判断函数是否位于类定义内部
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            setattr(child, "_parent", parent)

    def _is_inside_class_or_function(node: ast.AST) -> bool:
        current: ast.AST = node
        while hasattr(current, "_parent"):
            current = current._parent  # type: ignore[attr-defined]
            if isinstance(current, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                return True
        return False

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # 跳过私有函数（_开头）
        if node.name.startswith("_"):
            continue
        # 跳过类方法与嵌套函数：只生成顶层函数测试
        if _is_inside_class_or_function(node):
            continue

        # 提取参数
        args_info: List[Dict[str, str]] = []
        for arg in node.args.args:
            arg_name = arg.arg
            annotation = ""
            if arg.annotation:
                try:
                    annotation = ast.unparse(arg.annotation)
                except Exception:
                    annotation = "Any"
            args_info.append({"name": arg_name, "annotation": annotation})

        # 返回类型
        returns = ""
        if node.returns:
            try:
                returns = ast.unparse(node.returns)
            except Exception:
                returns = "Any"

        # 提取默认值
        defaults = node.args.defaults
        n_defaults = len(defaults)
        n_args = len(args_info)

        funcs.append({
            "name": node.name,
            "args": args_info,
            "returns": returns,
            "n_required": n_args - n_defaults,
            "line": node.lineno,
        })
    return funcs


def _get_default_value(annotation: str) -> str:
    """根据类型注解返回合理的默认测试值。"""
    annotation_lower = (annotation or "").lower()
    if "int" in annotation_lower:
        return "42"
    if "float" in annotation_lower:
        return "3.14"
    if "str" in annotation_lower:
        return '"hello"'
    if "bool" in annotation_lower:
        return "True"
    if "list" in annotation_lower:
        return "[1, 2, 3]"
    if "dict" in annotation_lower:
        return '{"key": "value"}'
    if "tuple" in annotation_lower:
        return "(1, 2)"
    if "set" in annotation_lower:
        return "{1, 2, 3}"
    return "None"


def _generate_test_for_function(func: Dict[str, Any], module_name: str) -> List[str]:
    """为单个函数生成测试代码行。"""
    func_name = func["name"]
    args = func["args"]
    test_lines: List[str] = []
    test_lines.append(f"    # 测试 {func_name}")
    test_lines.append(f"    def test_{func_name}_exists(self):")
    test_lines.append(f'        """验证 {func_name} 函数存在且可调用"""')
    test_lines.append(f"        self.assertTrue(hasattr({module_name}, '{func_name}'))")

    # 构造参数调用
    call_args = []
    for arg in args:
        call_args.append(_get_default_value(arg["annotation"]))
    call_str = ", ".join(call_args)

    # 正常调用测试
    test_lines.append(f"    def test_{func_name}_call(self):")
    test_lines.append(f'        """验证 {func_name} 正常调用不报错"""')
    test_lines.append(f"        try:")
    test_lines.append(f"            result = {module_name}.{func_name}({call_str})")
    test_lines.append(f"        except (ValueError, TypeError, AttributeError, ImportError) as e:")
    test_lines.append(f"            pass  # 预期可能对非法输入抛异常")

    # 返回类型测试（如果有注解）
    returns = func.get("returns", "")
    if returns and returns != "Any" and returns != "None":
        type_check = _get_type_check_expr(returns)
        if type_check:
            test_lines.append(f"    def test_{func_name}_return_type(self):")
            test_lines.append(f'        """验证 {func_name} 返回类型正确"""')
            test_lines.append(f"        try:")
            test_lines.append(f"            result = {module_name}.{func_name}({call_str})")
            test_lines.append(f"            {type_check}")
            test_lines.append(f"        except (ValueError, TypeError, AttributeError, ImportError):")
            test_lines.append(f"            pass")

    # 边界值测试（空参数）
    if args:
        edge_args = []
        for arg in args:
            ann = arg["annotation"].lower()
            if "int" in ann or "float" in ann:
                edge_args.append("0")
            elif "str" in ann:
                edge_args.append('""')
            elif "list" in ann:
                edge_args.append("[]")
            elif "dict" in ann:
                edge_args.append("{}")
            else:
                edge_args.append("None")
        edge_str = ", ".join(edge_args)
        test_lines.append(f"    def test_{func_name}_edge_case(self):")
        test_lines.append(f'        """验证 {func_name} 边界值处理"""')
        test_lines.append(f"        try:")
        test_lines.append(f"            {module_name}.{func_name}({edge_str})")
        test_lines.append(f"        except (ValueError, TypeError, AttributeError, ImportError) as e:")
        test_lines.append(f"            pass  # 边界值可能预期抛异常")

    return test_lines


def _get_type_check_expr(returns: str) -> str:
    """根据返回类型注解生成类型断言表达式。"""
    r = returns.lower()
    if "int" in r:
        return "self.assertIsInstance(result, int)"
    if "float" in r:
        return "self.assertIsInstance(result, (int, float))"
    if "str" in r:
        return "self.assertIsInstance(result, str)"
    if "bool" in r:
        return "self.assertIsInstance(result, bool)"
    if "list" in r:
        return "self.assertIsInstance(result, list)"
    if "dict" in r:
        return "self.assertIsInstance(result, dict)"
    if "tuple" in r:
        return "self.assertIsInstance(result, tuple)"
    return ""


def _generate_test_file(pkg: CodePackage) -> Tuple[str, List[TestCase]]:
    """为整个代码包生成测试文件内容。

    返回：(测试文件内容, 测试用例列表)
    """
    all_test_lines: List[str] = []
    all_test_lines.append('"""自动生成的单元测试（纯本地 AST 模板，0 Token 消耗）。"""')
    all_test_lines.append("import unittest")
    all_test_lines.append("import sys")
    all_test_lines.append("import os")
    all_test_lines.append("")
    all_test_lines.append("# 添加被测代码路径")

    # 为每个 Python 文件生成测试
    py_files = [f for f in pkg.files if f.path.endswith(".py") and f.content.strip()]
    # 收集模块名
    module_names = []
    for f in py_files:
        module_name = f.path.replace("/", ".").replace(".py", "")
        module_names.append(module_name)

    # 添加路径：测试文件位于 tests/ 目录下，被测代码在项目根目录
    all_test_lines.append("sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))")
    all_test_lines.append("")

    # import 所有模块
    for f in py_files:
        module_name = f.path.replace("/", ".").replace(".py", "")
        # 模块名不能有特殊字符
        safe_name = module_name.replace(".", "_")
        all_test_lines.append(f"import {module_name}")

    all_test_lines.append("")
    all_test_lines.append("")
    all_test_lines.append("class TestAutoGenerated(unittest.TestCase):")
    all_test_lines.append('    """自动生成的测试类。"""')
    all_test_lines.append("")

    cases: List[TestCase] = []

    for f in py_files:
        module_name = f.path.replace("/", ".").replace(".py", "")
        funcs = _extract_functions(f.content)
        if not funcs:
            continue

        all_test_lines.append(f"    # ===== 来自 {f.path} =====")
        for func in funcs:
            test_lines = _generate_test_for_function(func, module_name)
            all_test_lines.extend(test_lines)
            all_test_lines.append("")

            # 记录测试用例：与 _generate_test_for_function 实际生成的测试方法保持同步
            tc_names = [
                f"test_{func['name']}_exists",
                f"test_{func['name']}_call",
            ]
            returns = func.get("returns", "")
            if returns and returns != "Any" and returns != "None":
                if _get_type_check_expr(returns):
                    tc_names.append(f"test_{func['name']}_return_type")
            if func.get("args"):
                tc_names.append(f"test_{func['name']}_edge_case")

            for tc_name in tc_names:
                cases.append(TestCase(
                    name=tc_name,
                    description=f"自动生成: {func['name']} 函数测试",
                    target_file=f.path,
                    target_function=func["name"],
                    priority="high" if "exists" in tc_name else "medium",
                    status="generated",
                    error_message="",
                    output="",
                    duration_ms=0,
                    code_snippet="",
                ))

    # 如果没有任何函数，生成一个基本的导入测试
    if not cases:
        for f in py_files:
            module_name = f.path.replace("/", ".").replace(".py", "")
            tc_name = f"test_import_{module_name.replace('.', '_')}"
            all_test_lines.append(f"    def {tc_name}(self):")
            all_test_lines.append(f'        """验证 {module_name} 模块可正常导入"""')
            all_test_lines.append(f"        self.assertTrue(True)  # 导入成功即通过")
            all_test_lines.append("")
            cases.append(TestCase(
                name=tc_name,
                description=f"自动生成: {module_name} 模块导入测试",
                target_file=f.path,
                target_function="",
                priority="high",
                status="generated",
                error_message="",
                output="",
                duration_ms=0,
                code_snippet="",
            ))

    all_test_lines.append("")
    all_test_lines.append("if __name__ == '__main__':")
    all_test_lines.append("    unittest.main()")
    all_test_lines.append("")

    return "\n".join(all_test_lines), cases


# ====================================================================== #
#  行为测试生成（LLM，带真实断言）
#  在纯本地 AST 模板之上，用代码模型为每个顶层函数生成验证输入→输出
#  实际行为（而非仅存在性）的 unittest 测试。失败时自动降级到 AST 模板。
# ====================================================================== #

BEHAVIORAL_TEST_SYSTEM_PROMPT = """你是资深 Python 测试工程师。请为给定模块的顶层函数生成 unittest 风格的行为测试。
要求：
- 只为顶层公共函数生成测试（跳过 _ 开头、类方法、嵌套函数）。
- 每个测试方法必须带真实断言，验证函数输入→输出的实际行为（例如 to_snake_case('camelCase') == 'camel_case'），
  而不仅是"存在/可调用/返回类型"。
- 使用 self.assertEqual / self.assertRaises / self.assertIsInstance 等，断言失败能被 junitxml 正确捕获。
- 方法名唯一且描述场景，形如 test_<函数名>_<场景>。
- 只 import 被测模块，不要修改被测代码。
- 只输出合法 Python 代码，不要 markdown 代码块包裹，不要解释。"""


def _build_behavioral_prompt(
    file: GeneratedFile,
    module_name: str,
    funcs: List[Dict[str, Any]],
) -> str:
    """为单个 Python 文件构造行为测试 prompt（注入函数签名 + 源码）。"""
    func_lines = []
    for func in funcs:
        args = ", ".join(f"{a['name']}: {a['annotation'] or 'Any'}" for a in func["args"])
        ret = func.get("returns") or "Any"
        func_lines.append(f"- {func['name']}({args}) -> {ret}")
    return (
        f"模块名: {module_name}\n\n"
        f"需要测试的顶层函数:\n" + "\n".join(func_lines) + "\n\n"
        f"模块源码:\n```python\n{file.content}\n```\n\n"
        f"请为每个函数生成一个带真实断言的 unittest 测试方法（验证输入→输出的实际行为）。"
        f"直接输出测试代码，不要解释。"
    )


def _extract_test_methods(code: str) -> List[str]:
    """从 LLM 输出中提取所有 test_* 方法源码（缩进为 4 空格，便于并入测试类）。"""
    methods: List[str] = []
    if not code or not code.strip():
        return methods
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return methods
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            try:
                src = ast.unparse(node)
            except Exception:
                continue
            indented = "\n".join(
                f"    {line}" if line else line for line in src.split("\n")
            )
            methods.append(indented)
    return methods


def _generate_behavioral_test_file(
    pkg: CodePackage,
    llm_adapter: LLMAdapter,
    *,
    use_code_model: bool = True,
    model: Optional[str] = None,
) -> Tuple[str, List[TestCase], LLMUsage]:
    """用 LLM 为每个顶层函数生成带真实断言的行为测试（tests/test_behavioral.py）。

    返回：(测试文件内容, 测试用例列表, 本次 LLM 用量)
    """
    py_files = [f for f in pkg.files if f.path.endswith(".py") and f.content.strip()]
    if not py_files:
        return "", [], LLMUsage()

    all_test_lines: List[str] = []
    all_test_lines.append('"""LLM 行为测试（带真实断言，验证函数行为正确性）。"""')
    all_test_lines.append("import unittest")
    all_test_lines.append("import sys")
    all_test_lines.append("import os")
    all_test_lines.append("")
    all_test_lines.append("sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))")
    all_test_lines.append("")
    for f in py_files:
        module_name = f.path.replace("/", ".").replace(".py", "")
        all_test_lines.append(f"import {module_name}")
    all_test_lines.append("")
    all_test_lines.append("")
    all_test_lines.append("class TestBehavioral(unittest.TestCase):")
    all_test_lines.append('    """LLM 生成的行为测试类。"""')
    all_test_lines.append("")

    cases: List[TestCase] = []
    usage = LLMUsage()

    for f in py_files:
        module_name = f.path.replace("/", ".").replace(".py", "")
        funcs = _extract_functions(f.content)
        if not funcs:
            continue

        all_test_lines.append(f"    # ===== 来自 {f.path} =====")
        prompt = _build_behavioral_prompt(f, module_name, funcs)
        messages = llm_adapter.to_messages(
            system=BEHAVIORAL_TEST_SYSTEM_PROMPT,
            user=prompt,
        )
        resp = llm_adapter.chat(messages, use_code_model=use_code_model, model=model)
        if resp.usage:
            usage.add(resp.usage)

        code = extract_first_python_code_block(resp.content or "")
        methods = _extract_test_methods(code)
        for m in methods:
            all_test_lines.append(m)
            method_name = m.strip().split("(", 1)[0].replace("def ", "") or ""
            # 尝试从方法名匹配目标函数（test_<func>_<场景>）
            target_func = ""
            for func in funcs:
                if method_name.startswith(f"test_{func['name']}_"):
                    target_func = func["name"]
                    break
            cases.append(TestCase(
                name=method_name,
                description=f"LLM 行为测试: {target_func or f.path} 函数行为验证",
                target_file=f.path,
                target_function=target_func,
                priority="medium",
                status="generated",
                error_message="",
                output="",
                duration_ms=0,
                code_snippet="",
            ))
        all_test_lines.append("")

    all_test_lines.append("")
    all_test_lines.append("if __name__ == '__main__':")
    all_test_lines.append("    unittest.main()")
    all_test_lines.append("")

    return "\n".join(all_test_lines), cases, usage


class TestGenAgent:
    """测试生成 Agent：默认纯本地 AST 模板（0 Token）；启用行为测试时为每个
    顶层函数生成带真实断言的 LLM 测试，失败自动降级到 AST 模板。"""

    def run(self, state: PipelineState) -> TestReport:
        task_id = state.get("task_id", "")
        from app.graph.state import normalize_state
        state = normalize_state(state)
        req: RequirementSpec = state.get("requirement") or RequirementSpec()
        pkg: CodePackage = state.get("code_package") or CodePackage()

        if not pkg.files:
            return TestReport(
                status=TestStatus.FAILED,
                summary="无代码可生成测试。",
                passed=0, total=0, failed=0, skipped=0,
            )

        t0 = time.perf_counter()

        # 1. 纯本地 AST 生成测试代码（0 Token，作为离线/fallback 基线，始终保留）
        ast_content, ast_cases = _generate_test_file(pkg)
        test_files = [{
            "path": "tests/test_auto_generated.py",
            "language": "python",
            "content": ast_content,
            "description": "纯本地 AST 自动生成测试（0 Token）",
        }]
        cases = list(ast_cases)
        behavioral_used = False
        behavioral_usage = LLMUsage()

        # 2. 行为测试生成（LLM，可选）：仅当启用且 LLM 可用时尝试
        if (settings.agent.enable_test_gen
                and settings.agent.behavioral_test_enabled
                and not llm.dry_run):
            try:
                beh_content, beh_cases, beh_usage = _generate_behavioral_test_file(
                    pkg, llm, use_code_model=True, model=state.get("model"),
                )
                if beh_content.strip():
                    test_files.append({
                        "path": "tests/test_behavioral.py",
                        "language": "python",
                        "content": beh_content,
                        "description": "LLM 行为测试（带真实断言）",
                    })
                    cases.extend(beh_cases)
                    behavioral_usage = beh_usage
                    behavioral_used = True
            except Exception as e:
                # 行为测试生成失败 → 自动降级到原生 AST 模板，不阻断流水线
                logger.warning("行为测试生成失败，降级到原生 AST 模板：%s", e)

        duration_ms = int((time.perf_counter() - t0) * 1000)

        # 3. 记录日志
        mode_desc = "行为测试(LLM)+本地AST" if behavioral_used else "纯本地AST"
        db.add_stage_log(
            task_id=task_id,
            stage=PipelinePhase.TEST_GEN.value,
            prompt_text=req.title or req.goal,
            llm_output=f"[{mode_desc}] {len(cases)} 条测试用例",
            prompt_tokens=behavioral_usage.prompt_tokens,
            completion_tokens=behavioral_usage.completion_tokens,
            total_tokens=behavioral_usage.total_tokens,
            duration_ms=duration_ms,
        )
        db.update_task_phase(
            task_id,
            phase=PipelinePhase.TEST_GEN.value,
        )

        total = len(cases)
        run_command = (
            "python -m pytest tests/test_auto_generated.py tests/test_behavioral.py -v"
            if behavioral_used
            else "python -m pytest tests/test_auto_generated.py -v"
        )
        report = TestReport(
            status=TestStatus.GENERATED,
            summary=f"{mode_desc}生成 {len(test_files)} 个测试文件，{total} 条用例"
                    f"（{'行为测试消耗 ' + str(behavioral_usage.total_tokens) + ' Token' if behavioral_used else '0 Token'}）",
            total=total,
            passed=0,
            failed=0,
            skipped=0,
            error_count=0,
            duration_ms=duration_ms,
            test_cases=cases,
            test_files=test_files,
            run_command=run_command,
        )

        # 3. 执行测试
        try:
            exec_result = _execute_tests(
                code_files=pkg.files,
                test_files=test_files,
                timeout=30,
            )
            report = _apply_execution_result(report, exec_result, cases)
        except Exception as e:
            logger.exception("测试执行失败，保留 GENERATED 状态：%s", e)
            report.summary = f"{report.summary}（执行异常：{e}）"

        logger.info(
            "测试阶段完成（%s）：用例=%d  pass=%d  fail=%d  error=%d  status=%s",
            mode_desc, report.total_tests_count, report.passed, report.failed,
            report.error_count, report.status.value,
        )
        return report


# ------------------------------------------------------------------ #
#  LangGraph 节点函数
# ------------------------------------------------------------------ #

def test_gen_node(state: PipelineState) -> Dict[str, Any]:
    """LangGraph 节点：为当前代码包生成测试。"""
    task_id = state.get("task_id", "")
    agent = TestGenAgent()
    report = agent.run(state)

    # 将测试文件也写入 DB 的 file_records
    if report.test_files:
        db.upsert_files(
            task_id=task_id,
            files=[
                {"path": f["path"], "content": f["content"],
                 "language": f.get("language", "python"),
                 "description": f.get("description", "test"),
                 "source_stage": "test_gen"}
                for f in report.test_files
            ],
            source_stage="test_gen",
        )
    db.update_task_phase(
        task_id,
        phase=PipelinePhase.TEST_GEN.value,
        summary={"cases": report.total, "files": len(report.test_files)},
    )
    status = ensure_status(state)
    status.phase = PipelinePhase.TEST_GEN
    status.touch()
    return {
        "test_report": report,
        "status": status,
    }


__all__ = [
    "TestGenAgent",
    "test_gen_node",
    "TestReport",
    "TestCase",
    "TestStatus",
]


# ====================================================================== #
#  测试执行器：在隔离临时目录跑 pytest，解析 junitxml 回填结果
# ====================================================================== #
#  设计要点：
#  - 隔离：每次执行用 tempfile.mkdtemp 创建临时目录，跑完 shutil.rmtree
#    防止测试代码污染项目根目录或访问项目源码（强制测试只通过 import 测被测代码）
#  - 安全：subprocess 不传 shell=True；超时 30s 防死循环；不继承当前 venv 的
#    PYTHONPATH（清空环境变量）防止测试越权 import 项目内部模块
#  - 可靠：用 pytest --junitxml 输出 XML，标准库 xml.etree 解析，
#    不依赖 pytest-json-report 等额外插件
#  - 容错：测试执行失败不阻断流水线，回退为 GENERATED 状态
# ====================================================================== #


def _execute_tests(
    *,
    code_files: List[GeneratedFile],
    test_files: List[Dict[str, str]],
    timeout: int = 30,
) -> Dict[str, Any]:
    """把代码与测试文件写入临时目录，执行 pytest，返回结构化结果。

    返回字段：
        status: passed / failed / error / skipped / timeout
        total / passed / failed / skipped / error
        duration_ms
        case_results: {test_name: {status, message, duration_ms}}
        stdout / stderr (截断)
    """
    result: Dict[str, Any] = {
        "status": "skipped",
        "total": 0, "passed": 0, "failed": 0, "skipped": 0, "error": 0,
        "duration_ms": 0,
        "case_results": {},
        "stdout": "", "stderr": "",
    }

    # 只执行 Python 测试文件
    py_tests = [f for f in test_files if (f.get("path", "") or "").endswith(".py")]
    if not py_tests or not code_files:
        result["status"] = "skipped"
        return result

    tmpdir = tempfile.mkdtemp(prefix="multi_agent_test_")
    try:
        # 1. 写入被测代码文件（按相对路径还原目录结构）
        tmp_root = Path(tmpdir).resolve()
        for f in code_files:
            rel = (f.path or "").strip().lstrip("/")
            if not rel:
                continue
            # 路径穿越防御：禁止 .. 越界，resolve 后必须仍在 tmpdir 内
            rel_path = Path(rel)
            if ".." in rel_path.parts:
                continue
            target = (Path(tmpdir) / rel).resolve()
            try:
                target.relative_to(tmp_root)
            except ValueError:
                continue  # 越界
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f.content or "", encoding="utf-8")

        # 2. 写入测试文件，记录测试文件路径
        test_paths: List[str] = []
        for f in py_tests:
            rel = (f.get("path") or "").strip().lstrip("/")
            if not rel:
                continue
            # 路径穿越防御
            rel_path = Path(rel)
            if ".." in rel_path.parts:
                continue
            target = (Path(tmpdir) / rel).resolve()
            try:
                target.relative_to(tmp_root)
            except ValueError:
                continue  # 越界
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f.get("content") or "", encoding="utf-8")
            test_paths.append(str(target))

        if not test_paths:
            result["status"] = "skipped"
            return result

        # 3. 跑 pytest，输出 junitxml
        junit_path = Path(tmpdir) / "report.xml"
        cmd = [
            sys.executable, "-m", "pytest",
            *test_paths,
            "-v", "--tb=short",
            f"--junitxml={junit_path}",
            "--no-header", "-rN",
        ]
        # 清空 PYTHONPATH / PYTHONHOME 防止测试越权 import 项目源码
        env = {k: v for k, v in __import__("os").environ.items()
               if not k.startswith(("PYTHONPATH", "PYTHONHOME"))}
        # 让临时目录成为 sys.path[0]，使测试能 import 同目录的被测代码
        env["PYTHONPATH"] = str(tmpdir)

        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, cwd=tmpdir, env=env,
            )
            result["stdout"] = (proc.stdout or "")[-4000:]
            result["stderr"] = (proc.stderr or "")[-4000:]
        except subprocess.TimeoutExpired as e:
            result["status"] = "timeout"
            result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
            result["stderr"] = f"测试执行超时（{timeout}s）：{e}"
            return result
        result["duration_ms"] = int((time.perf_counter() - t0) * 1000)

        # 4. 解析 junitxml
        case_results: Dict[str, Dict[str, Any]] = {}
        if junit_path.exists():
            try:
                tree = ET.parse(str(junit_path))
                root = tree.getroot()
                # 全局统计：tests 属性可能在 <testsuites> 或 <testsuite> 节点上
                # 用 set 记录已处理的节点 id，防止 root.iter 重复遍历 root 自身
                totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
                seen_node_ids: set = set()

                def _accumulate_testsuite(node: Any) -> None:
                    nid = id(node)
                    if nid in seen_node_ids:
                        return
                    seen_node_ids.add(nid)
                    for k in totals:
                        totals[k] += int(node.get(k) or 0)

                # 兼容：root 本身就是 testsuite（单文件场景）
                if root.tag == "testsuite":
                    _accumulate_testsuite(root)
                # 遍历所有 testsuite 子节点累加（多文件场景会有多个 testsuite）
                for ts in root.iter("testsuite"):
                    _accumulate_testsuite(ts)
                # 每个 testcase 节点
                for tc in root.iter("testcase"):
                    name = tc.get("name") or tc.get("classname", "") or "unknown"
                    duration_ms_tc = int(float(tc.get("time") or 0) * 1000)
                    child_tags = [c.tag for c in tc]
                    if "failure" in child_tags:
                        st = "failed"
                        msg = next((c.text or c.get("message", "") for c in tc if c.tag == "failure"), "")
                    elif "error" in child_tags:
                        st = "error"
                        msg = next((c.text or c.get("message", "") for c in tc if c.tag == "error"), "")
                    elif "skipped" in child_tags:
                        st = "skipped"
                        msg = next((c.get("message", "") for c in tc if c.tag == "skipped"), "")
                    else:
                        st = "passed"
                        msg = ""
                    case_results[name] = {
                        "status": st,
                        "message": (msg or "")[:500],
                        "duration_ms": duration_ms_tc,
                    }
                # 如果 totals["tests"] 仍为 0 但 case_results 非空，用 case_results 数量兜底
                if totals["tests"] == 0 and case_results:
                    totals["tests"] = len(case_results)
                    # 重新统计 passed
                    passed_count = sum(1 for v in case_results.values() if v["status"] == "passed")
                    totals["failures"] = sum(1 for v in case_results.values() if v["status"] == "failed")
                    totals["errors"] = sum(1 for v in case_results.values() if v["status"] == "error")
                    totals["skipped"] = sum(1 for v in case_results.values() if v["status"] == "skipped")
                    # 修正 passed（case_results 已精确分类）
                    result["passed"] = passed_count
                else:
                    result["passed"] = totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]
                result["total"] = totals["tests"]
                result["failed"] = totals["failures"]
                result["error"] = totals["errors"]
                result["skipped"] = totals["skipped"]
            except ET.ParseError as e:
                logger.warning("junitxml 解析失败：%s", e)
        else:
            # 没有 junitxml，从 stdout 提取统计
            result["total"] = _count_pattern(r"(\d+) passed", result["stdout"])
            result["failed"] = _count_pattern(r"(\d+) failed", result["stdout"])
            result["error"] = _count_pattern(r"(\d+) error", result["stdout"])
            result["skipped"] = _count_pattern(r"(\d+) skipped", result["stdout"])

        result["case_results"] = case_results

        # 5. 判定总体状态
        if result["error"] > 0:
            result["status"] = "error"
        elif result["failed"] > 0:
            result["status"] = "failed"
        elif result["total"] > 0 and result["passed"] == result["total"]:
            result["status"] = "passed"
        elif result["total"] == 0:
            result["status"] = "skipped"
        else:
            result["status"] = "partial"
        return result
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _count_pattern(pattern: str, text: str) -> int:
    m = re.search(pattern, text or "")
    return int(m.group(1)) if m else 0


def _apply_execution_result(
    report: TestReport,
    exec_result: Dict[str, Any],
    cases: List[TestCase],
) -> TestReport:
    """把执行结果回填到 TestReport 与每个 TestCase。

    匹配策略：先按 test_cases 中的 name 精确匹配 junitxml 中的 testcase name，
    匹配不到则按 name 子串匹配（pytest 输出的 name 可能带参数化后缀）。
    """
    report.passed = int(exec_result.get("passed", 0))
    report.failed = int(exec_result.get("failed", 0))
    report.skipped = int(exec_result.get("skipped", 0))
    report.error_count = int(exec_result.get("error", 0))
    report.duration_ms = int(report.duration_ms or 0) + int(exec_result.get("duration_ms", 0))

    case_results: Dict[str, Dict[str, Any]] = exec_result.get("case_results", {}) or {}

    # 给每个 TestCase 回填状态
    for c in cases:
        matched: Optional[Dict[str, Any]] = None
        # 精确匹配
        if c.name in case_results:
            matched = case_results[c.name]
        else:
            # 子串匹配（pytest 参数化测试 name 可能形如 test_xxx[1-2]）
            for k, v in case_results.items():
                if c.name and (c.name in k or k in c.name):
                    matched = v
                    break
        if matched:
            c.status = matched.get("status", "generated")
            c.error_message = matched.get("message", "")
            c.duration_ms = int(matched.get("duration_ms", 0) or 0)
            c.output = (exec_result.get("stdout", "") or "")[:500]
        else:
            # 没匹配到，标记为 skipped（可能是 import 失败导致整个文件未收集）
            c.status = "skipped"
            c.error_message = "测试未被执行（可能未匹配到 pytest 用例名）"

    # 判定 TestReport 总状态
    status_str = str(exec_result.get("status", ""))
    try:
        if status_str == "passed":
            report.status = TestStatus.PASSED
        elif status_str == "failed":
            report.status = TestStatus.FAILED
        elif status_str == "timeout":
            report.status = TestStatus.FAILED
            report.summary = f"{report.summary}（执行超时）"
        elif status_str == "skipped":
            report.status = TestStatus.NOT_RUN
        else:
            report.status = TestStatus.PARTIAL
    except Exception:
        report.status = TestStatus.GENERATED

    # 更新 summary，包含执行结果
    report.summary = (
        f"执行完成：pass={report.passed} fail={report.failed} "
        f"error={report.error_count} skipped={report.skipped} "
        f"（耗时 {report.duration_ms}ms，状态 {report.status.value}）"
    )
    return report
