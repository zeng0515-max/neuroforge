"""LLM 驱动的行为测试生成模块。

本模块包含：
- 行为测试系统提示词（BEHAVIORAL_TEST_SYSTEM_PROMPT）
- LLM 测试 prompt 构建（_build_behavioral_prompt）
- LLM 输出解析与测试方法提取（_extract_test_methods）
- 完整的行为测试文件生成（_generate_behavioral_test_file）

行为测试区别于纯 AST 模板测试：LLM 基于函数源码生成带真实断言的测试，
验证函数输入→输出的实际行为，而非仅检查存在性或类型。
"""

from __future__ import annotations

import ast
from typing import Any, Dict, List, Optional, Tuple

from app.core.llm_adapter import LLMAdapter, LLMUsage
from app.graph.state import CodePackage, GeneratedFile, TestCase
from app.tools.ast_validator import extract_first_python_code_block

# Import _extract_functions from test_gen_rules for backward compatibility
from app.agents.test_gen_rules import (  # noqa: F401
    _extract_functions,
)


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
    """为单个 Python 文件构造行为测试 prompt。

    Args:
        file: 被测生成文件
        module_name: 模块名
        funcs: 函数签名信息列表

    Returns:
        构造好的 prompt 字符串
    """
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
    """从 LLM 输出中提取所有 test_* 方法源码。

    Args:
        code: LLM 返回的 Python 代码字符串

    Returns:
        测试方法源码列表
    """
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
    """用 LLM 为每个顶层函数生成带真实断言的行为测试。

    遍历代码包中的所有 Python 文件，对每个文件提取顶层公共函数，
    通过 LLM 生成带真实断言的 unittest 测试方法。

    Args:
        pkg: 代码包
        llm_adapter: LLM 适配器实例
        use_code_model: 是否使用代码模型
        model: 指定模型名称

    Returns:
        (测试文件内容, 测试用例列表, LLM 用量统计)
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


__all__ = [
    "BEHAVIORAL_TEST_SYSTEM_PROMPT",
    "_build_behavioral_prompt",
    "_extract_test_methods",
    "_generate_behavioral_test_file",
]
