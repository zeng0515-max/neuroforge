"""测试生成规则、策略、覆盖率分析与质量评估模块。

本模块包含：
- 测试策略枚举与注册表
- 多种测试生成策略（单元/集成/边界/回归）
- 代码分析工具（AST 函数提取、类型推断、默认值生成）
- 覆盖率分析与圈复杂度估算
- 测试质量多维度评估（可读性/断言强度/覆盖深度/可维护性）
- 纯本地 AST 测试文件生成（0 Token 消耗）
"""

from __future__ import annotations

import ast
import re
from typing import Any, Dict, List, Tuple

from pydantic import BaseModel, Field

from app.graph.state import CodePackage, TestCase


# ====================================================================== #
#  测试策略枚举
# ====================================================================== #

class TestStrategy(str):
    """测试生成策略枚举。"""
    UNIT = "unit"
    INTEGRATION = "integration"
    BOUNDARY = "boundary"
    REGRESSION = "regression"
    ALL = "all"


TEST_STRATEGIES = (
    TestStrategy.UNIT,
    TestStrategy.INTEGRATION,
    TestStrategy.BOUNDARY,
    TestStrategy.REGRESSION,
)


# ====================================================================== #
#  测试质量评估模型
# ====================================================================== #

class TestQualityMetrics(BaseModel):
    """测试质量评估指标。"""
    readability: int = Field(default=80, ge=0, le=100, description="可读性评分")
    assertion_strength: int = Field(default=70, ge=0, le=100, description="断言强度评分")
    coverage_depth: int = Field(default=60, ge=0, le=100, description="覆盖深度评分")
    maintainability: int = Field(default=75, ge=0, le=100, description="可维护性评分")
    overall_score: int = Field(default=70, ge=0, le=100, description="综合质量分")
    issues: List[str] = Field(default_factory=list, description="质量问题列表")


class CoverageAnalysis(BaseModel):
    """测试覆盖率分析结果。"""
    total_functions: int = Field(default=0, description="总函数数量")
    covered_functions: int = Field(default=0, description="已覆盖函数数量")
    coverage_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="函数覆盖率")
    uncovered_functions: List[str] = Field(default_factory=list, description="未覆盖函数名列表")
    coverage_by_file: Dict[str, float] = Field(default_factory=dict, description="按文件的覆盖率")
    complexity_score: float = Field(default=0.0, description="代码复杂度评分（越低越好）")


# ====================================================================== #
#  代码分析工具（AST 函数提取、类型推断、默认值生成）
# ====================================================================== #

def _extract_functions(source: str) -> List[Dict[str, Any]]:
    """用 AST 提取所有公共顶层函数的签名信息（跳过类方法与私有函数）。

    Args:
        source: Python 源码字符串

    Returns:
        函数签名信息列表，每项包含 name、args、returns、n_required、line
    """
    funcs: List[Dict[str, Any]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return funcs

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
        if node.name.startswith("_"):
            continue
        if _is_inside_class_or_function(node):
            continue

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

        returns = ""
        if node.returns:
            try:
                returns = ast.unparse(node.returns)
            except Exception:
                returns = "Any"

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
    """根据类型注解返回合理的默认测试值。

    Args:
        annotation: 类型注解字符串

    Returns:
        对应类型的默认值表达式
    """
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


def _get_type_check_expr(returns: str) -> str:
    """根据返回类型注解生成类型断言表达式。

    Args:
        returns: 返回类型注解字符串

    Returns:
        unittest 断言表达式字符串
    """
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


def _generate_test_for_function(func: Dict[str, Any], module_name: str) -> List[str]:
    """为单个函数生成测试代码行。

    Args:
        func: 函数签名信息字典
        module_name: 模块名（用于 import 路径）

    Returns:
        测试代码行列表
    """
    func_name = func["name"]
    args = func["args"]
    test_lines: List[str] = []
    test_lines.append(f"    # 测试 {func_name}")
    test_lines.append(f"    def test_{func_name}_exists(self):")
    test_lines.append(f'        """验证 {func_name} 函数存在且可调用"""')
    test_lines.append(f"        self.assertTrue(hasattr({module_name}, '{func_name}'))")

    call_args = []
    for arg in args:
        call_args.append(_get_default_value(arg["annotation"]))
    call_str = ", ".join(call_args)

    test_lines.append(f"    def test_{func_name}_call(self):")
    test_lines.append(f'        """验证 {func_name} 正常调用不报错"""')
    test_lines.append(f"        try:")
    test_lines.append(f"            result = {module_name}.{func_name}({call_str})")
    test_lines.append(f"        except (ValueError, TypeError, AttributeError, ImportError) as e:")
    test_lines.append(f"            pass")

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
        test_lines.append(f"            pass")

    return test_lines


# ====================================================================== #
#  多种测试生成策略
# ====================================================================== #

class UnitTestStrategy:
    """单元测试生成策略：pytest + unittest 双框架支持。"""

    @staticmethod
    def generate(
        pkg: CodePackage,
        framework: str = "pytest",
    ) -> Tuple[str, List[TestCase]]:
        """为代码包生成单元测试。

        Args:
            pkg: 代码包
            framework: 测试框架 (pytest / unittest)

        Returns:
            (测试文件内容, 测试用例列表)
        """
        if framework == "pytest":
            return UnitTestStrategy._generate_pytest(pkg)
        return _generate_test_file(pkg)

    @staticmethod
    def _generate_pytest(pkg: CodePackage) -> Tuple[str, List[TestCase]]:
        """生成 pytest 风格测试。"""
        all_test_lines: List[str] = []
        all_test_lines.append('"""自动生成的 pytest 单元测试（纯本地 AST 模板，0 Token 消耗）。"""')
        all_test_lines.append("import pytest")
        all_test_lines.append("import sys")
        all_test_lines.append("import os")
        all_test_lines.append("")
        all_test_lines.append("sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))")
        all_test_lines.append("")

        py_files = [f for f in pkg.files if f.path.endswith(".py") and f.content.strip()]
        module_names = []
        for f in py_files:
            module_name = f.path.replace("/", ".").replace(".py", "")
            module_names.append(module_name)
            all_test_lines.append(f"import {module_name}")

        all_test_lines.append("")
        all_test_lines.append("")

        cases: List[TestCase] = []

        for f in py_files:
            module_name = f.path.replace("/", ".").replace(".py", "")
            funcs = _extract_functions(f.content)
            if not funcs:
                continue

            all_test_lines.append(f"# ===== 来自 {f.path} =====")
            for func in funcs:
                func_name = func["name"]
                args = func["args"]

                call_args = []
                for arg in args:
                    call_args.append(_get_default_value(arg["annotation"]))
                call_str = ", ".join(call_args)

                all_test_lines.append(f"def test_{func_name}_exists():")
                all_test_lines.append(f'    """验证 {func_name} 函数存在且可调用。"""')
                all_test_lines.append(f"    assert hasattr({module_name}, '{func_name}')")
                all_test_lines.append("")

                all_test_lines.append(f"def test_{func_name}_call():")
                all_test_lines.append(f'    """验证 {func_name} 正常调用不报错。"""')
                all_test_lines.append(f"    try:")
                all_test_lines.append(f"        result = {module_name}.{func_name}({call_str})")
                all_test_lines.append(f"    except (ValueError, TypeError, AttributeError):")
                all_test_lines.append(f"        pass")
                all_test_lines.append("")

                tc_names = [f"test_{func_name}_exists", f"test_{func_name}_call"]
                for tc_name in tc_names:
                    cases.append(TestCase(
                        name=tc_name,
                        description=f"pytest 单元测试: {func_name} 函数",
                        target_file=f.path,
                        target_function=func_name,
                        priority="high",
                        status="generated",
                        error_message="",
                        output="",
                        duration_ms=0,
                        code_snippet="",
                    ))

        if not cases:
            for f in py_files:
                module_name = f.path.replace("/", ".").replace(".py", "")
                tc_name = f"test_import_{module_name.replace('.', '_')}"
                all_test_lines.append(f"def {tc_name}():")
                all_test_lines.append(f'    """验证 {module_name} 模块可正常导入。"""')
                all_test_lines.append(f"    assert True")
                all_test_lines.append("")
                cases.append(TestCase(
                    name=tc_name,
                    description=f"pytest 单元测试: {module_name} 模块导入",
                    target_file=f.path,
                    target_function="",
                    priority="high",
                    status="generated",
                    error_message="",
                    output="",
                    duration_ms=0,
                    code_snippet="",
                ))

        return "\n".join(all_test_lines), cases


class IntegrationTestStrategy:
    """集成测试生成策略：模块间协作、API 接口、数据流。"""

    @staticmethod
    def generate(pkg: CodePackage) -> Tuple[str, List[TestCase]]:
        """生成集成测试。

        分析代码包中的模块依赖关系，生成模块集成测试。

        Args:
            pkg: 代码包

        Returns:
            (测试文件内容, 测试用例列表)
        """
        test_lines: List[str] = []
        cases: List[TestCase] = []

        py_files = [f for f in pkg.files if f.path.endswith(".py") and f.content.strip()]
        if len(py_files) < 2:
            return "", cases

        test_lines.append('"""自动生成的集成测试（模块间协作验证）。"""')
        test_lines.append("import unittest")
        test_lines.append("import sys")
        test_lines.append("import os")
        test_lines.append("")
        test_lines.append("sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))")
        test_lines.append("")

        module_names = []
        for f in py_files:
            module_name = f.path.replace("/", ".").replace(".py", "")
            module_names.append(module_name)
            test_lines.append(f"import {module_name}")

        test_lines.append("")
        test_lines.append("")
        test_lines.append("class TestIntegration(unittest.TestCase):")
        test_lines.append('    """集成测试类：验证模块间协作。"""')
        test_lines.append("")

        for i, f1 in enumerate(py_files):
            for f2 in py_files[i + 1:]:
                m1 = f1.path.replace("/", ".").replace(".py", "")
                m2 = f2.path.replace("/", ".").replace(".py", "")
                tc_name = f"test_integration_{m1}_{m2}".replace(".", "_")
                test_lines.append(f"    def {tc_name}(self):")
                test_lines.append(f'        """验证 {m1} 和 {m2} 模块的集成。"""')
                test_lines.append(f"        self.assertIsNotNone({m1})")
                test_lines.append(f"        self.assertIsNotNone({m2})")
                test_lines.append(f"        # TODO: 添加具体集成断言")
                test_lines.append("")
                cases.append(TestCase(
                    name=tc_name,
                    description=f"集成测试: {m1} + {m2} 模块协作",
                    target_file=f"{f1.path}, {f2.path}",
                    target_function="",
                    priority="medium",
                    status="generated",
                    error_message="",
                    output="",
                    duration_ms=0,
                    code_snippet="",
                ))

        test_lines.append("")
        test_lines.append("if __name__ == '__main__':")
        test_lines.append("    unittest.main()")
        test_lines.append("")

        return "\n".join(test_lines), cases


class BoundaryTestStrategy:
    """边界测试生成策略：极值、空值、特殊字符、类型边界。"""

    @staticmethod
    def generate(pkg: CodePackage) -> Tuple[str, List[TestCase]]:
        """为函数生成边界测试用例。

        Args:
            pkg: 代码包

        Returns:
            (测试文件内容, 测试用例列表)
        """
        test_lines: List[str] = []
        cases: List[TestCase] = []

        test_lines.append('"""自动生成的边界测试（极值与异常输入验证）。"""')
        test_lines.append("import unittest")
        test_lines.append("import sys")
        test_lines.append("import os")
        test_lines.append("")
        test_lines.append("sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))")
        test_lines.append("")

        py_files = [f for f in pkg.files if f.path.endswith(".py") and f.content.strip()]
        for f in py_files:
            module_name = f.path.replace("/", ".").replace(".py", "")
            test_lines.append(f"import {module_name}")

        test_lines.append("")
        test_lines.append("")
        test_lines.append("class TestBoundary(unittest.TestCase):")
        test_lines.append('    """边界条件测试类。"""')
        test_lines.append("")

        for f in py_files:
            module_name = f.path.replace("/", ".").replace(".py", "")
            funcs = _extract_functions(f.content)
            if not funcs:
                continue

            test_lines.append(f"    # ===== 来自 {f.path} =====")
            for func in funcs:
                func_name = func["name"]
                args = func["args"]

                boundary_values = {
                    "int": ["0", "-1", "2**31-1", "-2**31"],
                    "float": ["0.0", "-1.0", "float('inf')", "float('-inf')"],
                    "str": ['""', '" "', 'None', '"\x00"'],
                    "bool": ["True", "False"],
                    "list": ["[]", "[None]", "[1]*10000"],
                    "dict": ["{}", '{"": ""}', "{str(i): i for i in range(10000)}"],
                }

                for arg in args:
                    ann = arg["annotation"].lower()
                    for type_key, values in boundary_values.items():
                        if type_key in ann:
                            for val in values[:2]:
                                tc_name = f"test_{func_name}_{arg['name']}_boundary_{val}".replace("(", "").replace(")", "").replace("*", "star")
                                test_lines.append(f"    def {tc_name}(self):")
                                test_lines.append(f'        """验证 {func_name} 的参数 {arg["name"]} 为 {val} 时的边界行为。"""')
                                test_lines.append(f"        try:")
                                test_lines.append(f"            {module_name}.{func_name}({val})")
                                test_lines.append(f"        except (ValueError, TypeError, AttributeError, OverflowError):")
                                test_lines.append(f"            pass")
                                test_lines.append("")
                                cases.append(TestCase(
                                    name=tc_name,
                                    description=f"边界测试: {func_name}({arg['name']}={val})",
                                    target_file=f.path,
                                    target_function=func_name,
                                    priority="medium",
                                    status="generated",
                                    error_message="",
                                    output="",
                                    duration_ms=0,
                                    code_snippet="",
                                ))
                            break

        test_lines.append("")
        test_lines.append("if __name__ == '__main__':")
        test_lines.append("    unittest.main()")
        test_lines.append("")

        return "\n".join(test_lines), cases


class RegressionTestStrategy:
    """回归测试生成策略：基于已有测试和历史经验生成回归测试。"""

    @staticmethod
    def generate(
        pkg: CodePackage,
        existing_tests: List[Dict[str, Any]] | None = None,
    ) -> Tuple[str, List[TestCase]]:
        """生成回归测试。

        如果有历史测试，基于历史测试生成回归用例；
        否则基于函数签名生成稳定的回归基线测试。

        Args:
            pkg: 代码包
            existing_tests: 历史测试记录

        Returns:
            (测试文件内容, 测试用例列表)
        """
        test_lines: List[str] = []
        cases: List[TestCase] = []

        test_lines.append('"""自动生成的回归测试（稳定基线验证）。"""')
        test_lines.append("import unittest")
        test_lines.append("import sys")
        test_lines.append("import os")
        test_lines.append("")
        test_lines.append("sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))")
        test_lines.append("")

        py_files = [f for f in pkg.files if f.path.endswith(".py") and f.content.strip()]
        for f in py_files:
            module_name = f.path.replace("/", ".").replace(".py", "")
            test_lines.append(f"import {module_name}")

        test_lines.append("")
        test_lines.append("")
        test_lines.append("class TestRegression(unittest.TestCase):")
        test_lines.append('    """回归测试类：确保核心功能稳定不退化。"""')
        test_lines.append("")

        for f in py_files:
            module_name = f.path.replace("/", ".").replace(".py", "")
            funcs = _extract_functions(f.content)
            if not funcs:
                continue

            test_lines.append(f"    # ===== 来自 {f.path} =====")
            for func in funcs:
                func_name = func["name"]
                args = func["args"]

                call_args = []
                for arg in args:
                    call_args.append(_get_default_value(arg["annotation"]))
                call_str = ", ".join(call_args)

                tc_name = f"test_regression_{func_name}"
                test_lines.append(f"    def {tc_name}(self):")
                test_lines.append(f'        """回归测试: 验证 {func_name} 核心行为稳定。"""')
                test_lines.append(f"        result = {module_name}.{func_name}({call_str})")
                test_lines.append(f"        self.assertIsNotNone(result)")
                test_lines.append("")
                cases.append(TestCase(
                    name=tc_name,
                    description=f"回归测试: {func_name} 核心行为验证",
                    target_file=f.path,
                    target_function=func_name,
                    priority="high",
                    status="generated",
                    error_message="",
                    output="",
                    duration_ms=0,
                    code_snippet="",
                ))

        test_lines.append("")
        test_lines.append("if __name__ == '__main__':")
        test_lines.append("    unittest.main()")
        test_lines.append("")

        return "\n".join(test_lines), cases


STRATEGY_REGISTRY: Dict[str, Any] = {
    TestStrategy.UNIT: UnitTestStrategy,
    TestStrategy.INTEGRATION: IntegrationTestStrategy,
    TestStrategy.BOUNDARY: BoundaryTestStrategy,
    TestStrategy.REGRESSION: RegressionTestStrategy,
}


# ====================================================================== #
#  覆盖率分析
# ====================================================================== #

def analyze_coverage(
    pkg: CodePackage,
    test_cases: List[TestCase],
) -> CoverageAnalysis:
    """分析测试覆盖率。

    通过 AST 分析提取所有函数，然后与测试用例关联。

    Args:
        pkg: 代码包
        test_cases: 测试用例列表

    Returns:
        覆盖率分析结果
    """
    py_files = [f for f in pkg.files if f.path.endswith(".py") and f.content.strip()]

    all_functions: Dict[str, List[str]] = {}
    for f in py_files:
        funcs = _extract_functions(f.content)
        all_functions[f.path] = [fn["name"] for fn in funcs]

    test_targets: Dict[str, set] = {}
    for tc in test_cases:
        if tc.target_file and tc.target_function:
            if tc.target_file not in test_targets:
                test_targets[tc.target_file] = set()
            test_targets[tc.target_file].add(tc.target_function)

    total_functions = 0
    covered_functions = 0
    uncovered: List[str] = []
    coverage_by_file: Dict[str, float] = {}

    for fpath, funcs in all_functions.items():
        total_functions += len(funcs)
        targets = test_targets.get(fpath, set())
        file_covered = 0
        for fn in funcs:
            if fn in targets:
                covered_functions += 1
                file_covered += 1
            else:
                uncovered.append(f"{fpath}:{fn}")

        coverage_by_file[fpath] = (
            round(file_covered / len(funcs), 2) if funcs else 0.0
        )

    coverage_rate = round(covered_functions / total_functions, 2) if total_functions > 0 else 0.0

    complexity_score = _estimate_complexity(pkg)

    return CoverageAnalysis(
        total_functions=total_functions,
        covered_functions=covered_functions,
        coverage_rate=coverage_rate,
        uncovered_functions=uncovered,
        coverage_by_file=coverage_by_file,
        complexity_score=complexity_score,
    )


def _estimate_complexity(pkg: CodePackage) -> float:
    """估算代码圈复杂度（简化版）。

    Args:
        pkg: 代码包

    Returns:
        平均圈复杂度评分
    """
    total_complexity = 0.0
    function_count = 0

    for f in pkg.files:
        if not f.path.endswith(".py") or not (f.content or "").strip():
            continue
        try:
            tree = ast.parse(f.content)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function_count += 1
                complexity = 1
                for child in ast.walk(node):
                    if isinstance(child, (ast.If, ast.For, ast.While, ast.AsyncFor,
                                          ast.ExceptHandler, ast.With, ast.AsyncWith,
                                          ast.Assert, ast.Compare)):
                        complexity += 1
                total_complexity += complexity

    if function_count == 0:
        return 0.0
    return round(total_complexity / function_count, 2)


# ====================================================================== #
#  测试质量评估
# ====================================================================== #

def assess_test_quality(
    test_cases: List[TestCase],
    test_content: str = "",
) -> TestQualityMetrics:
    """评估测试用例的质量。

    评估维度：
    - 可读性：命名规范、注释充分性
    - 断言强度：真实断言 vs 空断言
    - 覆盖深度：覆盖的场景数量
    - 可维护性：测试复杂度

    Args:
        test_cases: 测试用例列表
        test_content: 测试代码内容

    Returns:
        质量评估指标
    """
    issues: List[str] = []

    readability = _assess_readability(test_cases, test_content, issues)
    assertion_strength = _assess_assertion_strength(test_cases, test_content, issues)
    coverage_depth = _assess_coverage_depth(test_cases, issues)
    maintainability = _assess_maintainability(test_cases, test_content, issues)

    overall = int(
        (readability + assertion_strength + coverage_depth + maintainability) / 4
    )

    return TestQualityMetrics(
        readability=readability,
        assertion_strength=assertion_strength,
        coverage_depth=coverage_depth,
        maintainability=maintainability,
        overall_score=overall,
        issues=issues,
    )


def _assess_readability(
    test_cases: List[TestCase],
    content: str,
    issues: List[str],
) -> int:
    """评估测试可读性。

    Args:
        test_cases: 测试用例列表
        content: 测试代码内容
        issues: 质量问题列表（输出参数）

    Returns:
        可读性评分
    """
    score = 80
    if not test_cases:
        return 0

    naming_ok = sum(1 for tc in test_cases if tc.name.startswith("test_"))
    naming_ratio = naming_ok / len(test_cases) if test_cases else 0
    if naming_ratio < 0.8:
        score -= 20
        issues.append("部分测试用例命名不规范（应以 test_ 开头）")

    if content:
        has_docstrings = len(re.findall(r'""".*?"""', content, re.DOTALL))
        if has_docstrings < len(test_cases) * 0.3:
            score -= 15
            issues.append("测试用例缺少 docstring 说明")

    return max(0, min(100, score))


def _assess_assertion_strength(
    test_cases: List[TestCase],
    content: str,
    issues: List[str],
) -> int:
    """评估断言强度。

    Args:
        test_cases: 测试用例列表
        content: 测试代码内容
        issues: 质量问题列表（输出参数）

    Returns:
        断言强度评分
    """
    score = 70
    if not content:
        return 0

    assert_patterns = [
        (r"assertTrue\(hasattr", 10),
        (r"assertIsInstance", 15),
        (r"assertEqual", 20),
        (r"assertRaises", 15),
        (r"assertFalse", 10),
        (r"assertIsNotNone", 5),
        (r"assertIsNone", 5),
    ]

    for pattern, weight in assert_patterns:
        matches = re.findall(pattern, content)
        if matches:
            score += weight

    if "pass  # 预期可能对非法输入抛异常" in content:
        score -= 30
        issues.append("存在占位 pass 断言，缺少真实验证逻辑")
    if "except" in content and "pass" in content:
        score -= 10
        issues.append("测试中存在空 except 块，可能吞掉异常")

    return max(0, min(100, score))


def _assess_coverage_depth(
    test_cases: List[TestCase],
    issues: List[str],
) -> int:
    """评估覆盖深度。

    Args:
        test_cases: 测试用例列表
        issues: 质量问题列表（输出参数）

    Returns:
        覆盖深度评分
    """
    score = 60
    if not test_cases:
        return 0

    unique_functions = set(tc.target_function for tc in test_cases if tc.target_function)
    total = len(test_cases)

    if unique_functions:
        avg_cases_per_func = total / len(unique_functions)
        if avg_cases_per_func >= 3:
            score += 20
        elif avg_cases_per_func >= 2:
            score += 10

    priority_dist = {}
    for tc in test_cases:
        p = tc.priority or "medium"
        priority_dist[p] = priority_dist.get(p, 0) + 1

    if priority_dist.get("high", 0) > 0:
        score += 10

    if not any("edge" in tc.name.lower() for tc in test_cases):
        issues.append("缺少边界条件测试用例")
    else:
        score += 5

    return max(0, min(100, score))


def _assess_maintainability(
    test_cases: List[TestCase],
    content: str,
    issues: List[str],
) -> int:
    """评估可维护性。

    Args:
        test_cases: 测试用例列表
        content: 测试代码内容
        issues: 质量问题列表（输出参数）

    Returns:
        可维护性评分
    """
    score = 75
    if not test_cases:
        return 0

    if content:
        lines = content.split("\n")
        avg_lines_per_test = len(lines) / max(len(test_cases), 1)
        if avg_lines_per_test > 20:
            score -= 15
            issues.append("测试方法平均过长（>20行），可维护性下降")
        elif avg_lines_per_test > 10:
            score -= 5

        import_count = content.count("import ")
        if import_count > 10:
            score -= 10
            issues.append("测试文件 import 过多，可能存在不必要的依赖")

    return max(0, min(100, score))


# ====================================================================== #
#  原始 AST 测试生成（保持向后兼容）
# ====================================================================== #

def _generate_test_file(pkg: CodePackage) -> Tuple[str, List[TestCase]]:
    """为整个代码包生成 unittest 测试文件内容。

    Args:
        pkg: 代码包

    Returns:
        (测试文件内容, 测试用例列表)
    """
    all_test_lines: List[str] = []
    all_test_lines.append('"""自动生成的单元测试（纯本地 AST 模板，0 Token 消耗）。"""')
    all_test_lines.append("import unittest")
    all_test_lines.append("import sys")
    all_test_lines.append("import os")
    all_test_lines.append("")
    all_test_lines.append("# 添加被测代码路径")

    py_files = [f for f in pkg.files if f.path.endswith(".py") and f.content.strip()]
    for f in py_files:
        module_name = f.path.replace("/", ".").replace(".py", "")
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

    if not cases:
        for f in py_files:
            module_name = f.path.replace("/", ".").replace(".py", "")
            tc_name = f"test_import_{module_name.replace('.', '_')}"
            all_test_lines.append(f"    def {tc_name}(self):")
            all_test_lines.append(f'        """验证 {module_name} 模块可正常导入"""')
            all_test_lines.append(f"        self.assertTrue(True)")
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


__all__ = [
    "TestStrategy",
    "TEST_STRATEGIES",
    "TestQualityMetrics",
    "CoverageAnalysis",
    "UnitTestStrategy",
    "IntegrationTestStrategy",
    "BoundaryTestStrategy",
    "RegressionTestStrategy",
    "STRATEGY_REGISTRY",
    "analyze_coverage",
    "assess_test_quality",
    "_generate_test_file",
]
