"""审查维度枚举、扫描器、评分规则与修复建议模板。

本模块定义多维度代码审查的规则引擎，包括：
- 审查维度枚举（质量/安全/性能/兼容性）
- 4 类 AST 静态扫描器
- 维度评分与问题推断
- 自动修复建议生成
"""

from __future__ import annotations

import ast
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from app.graph.state import CodePackage, GeneratedFile, ReviewIssue, ReviewSeverity

logger = logging.getLogger(__name__)


# ====================================================================== #
#  审查维度枚举
# ====================================================================== #

class ReviewDimension(str):
    """审查维度枚举。"""

    QUALITY = "quality"
    SECURITY = "security"
    PERFORMANCE = "performance"
    COMPATIBILITY = "compatibility"
    ALL = "all"


REVIEW_DIMENSIONS = (
    ReviewDimension.QUALITY,
    ReviewDimension.SECURITY,
    ReviewDimension.PERFORMANCE,
    ReviewDimension.COMPATIBILITY,
)


# ====================================================================== #
#  扫描器基类与工具函数
# ====================================================================== #

class BaseReviewScanner:
    """审查扫描器基类，提供公共方法。"""

    dimension: str = "base"

    @staticmethod
    def _get_source_tree(content: str) -> Optional[ast.AST]:
        """安全解析 Python 源码为 AST。"""
        if not content or not content.strip():
            return None
        try:
            return ast.parse(content)
        except SyntaxError:
            return None


def _get_attr_chain(node: ast.AST) -> str:
    """获取属性访问链的完整名称。"""
    parts: List[str] = []
    obj = node
    while isinstance(obj, ast.Attribute):
        parts.insert(0, obj.attr)
        obj = obj.value
    if isinstance(obj, ast.Name):
        parts.insert(0, obj.id)
    return ".".join(parts)


def _node_contains(parent: ast.AST, target: ast.AST) -> bool:
    """判断 parent 是否包含 target 节点。"""
    if parent is target:
        return True
    for child in ast.iter_child_nodes(parent):
        if _node_contains(child, target):
            return True
    return False


# ====================================================================== #
#  代码质量扫描器
# ====================================================================== #

class CodeQualityScanner(BaseReviewScanner):
    """代码质量审查：命名规范、复杂度、注释、类型注解。"""

    dimension = ReviewDimension.QUALITY

    NAMING_CONVENTIONS = {
        "function": (re.compile(r"^[a-z_][a-z0-9_]*$"), "函数应使用 snake_case 命名"),
        "class": (re.compile(r"^[A-Z][a-zA-Z0-9]*$"), "类应使用 PascalCase 命名"),
        "variable": (re.compile(r"^[a-z_][a-z0-9_]*$"), "变量应使用 snake_case 命名"),
        "constant": (re.compile(r"^[A-Z_][A-Z0-9_]*$"), "常量应使用 UPPER_SNAKE_CASE 命名"),
    }

    MAX_FUNCTION_LENGTH = 50
    MAX_PARAMETER_COUNT = 5
    MAX_NESTING_DEPTH = 4

    @classmethod
    def scan(cls, pkg: CodePackage) -> List[ReviewIssue]:
        """执行代码质量扫描。"""
        issues: List[ReviewIssue] = []
        for f in pkg.files:
            if not f.path.endswith(".py") or not (f.content or "").strip():
                continue
            issues.extend(cls._scan_file(f))
        return issues

    @classmethod
    def _scan_file(cls, f: GeneratedFile) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        tree = cls._get_source_tree(f.content)
        if tree is None:
            return issues

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                issues.extend(cls._check_function(f, node))
            elif isinstance(node, ast.ClassDef):
                issues.extend(cls._check_class(f, node))

        issues.extend(cls._check_naming(f, tree))
        return issues

    @classmethod
    def _check_function(
        cls, f: GeneratedFile, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        name = node.name

        docstring = ast.get_docstring(node)
        if not docstring:
            issues.append(ReviewIssue(
                file_path=f.path,
                line_start=node.lineno,
                severity=ReviewSeverity.LOW,
                rule_id="NO-DOCSTRING",
                message=f"函数 {name} 缺少 docstring",
                suggestion=f'为 {name} 添加文档字符串，说明功能、参数和返回值。',
            ))

        end_line = getattr(node, "end_lineno", node.lineno) or node.lineno
        func_lines = end_line - node.lineno
        if func_lines > cls.MAX_FUNCTION_LENGTH:
            issues.append(ReviewIssue(
                file_path=f.path,
                line_start=node.lineno,
                severity=ReviewSeverity.MEDIUM,
                rule_id="LONG-FUNCTION",
                message=f"函数 {name} 过长（{func_lines} 行，上限 {cls.MAX_FUNCTION_LENGTH} 行）",
                suggestion="将长函数拆分为多个职责单一的小函数。",
            ))

        args = node.args
        unannotated = [
            a.arg for a in args.args
            if a.annotation is None and a.arg != "self"
        ]
        if unannotated:
            issues.append(ReviewIssue(
                file_path=f.path,
                line_start=node.lineno,
                severity=ReviewSeverity.LOW,
                rule_id="NO-TYPE-HINT",
                message=f"函数 {name} 参数缺少类型注解: {', '.join(unannotated)}",
                suggestion="为参数添加类型注解以提升代码可读性。",
            ))

        if len(args.args) > cls.MAX_PARAMETER_COUNT:
            issues.append(ReviewIssue(
                file_path=f.path,
                line_start=node.lineno,
                severity=ReviewSeverity.MEDIUM,
                rule_id="TOO-MANY-PARAMS",
                message=f"函数 {name} 参数过多（{len(args.args)} 个，上限 {cls.MAX_PARAMETER_COUNT} 个）",
                suggestion="考虑使用数据类或配置对象封装相关参数。",
            ))

        nesting = cls._count_nesting(node)
        if nesting > cls.MAX_NESTING_DEPTH:
            issues.append(ReviewIssue(
                file_path=f.path,
                line_start=node.lineno,
                severity=ReviewSeverity.MEDIUM,
                rule_id="DEEP-NESTING",
                message=f"函数 {name} 嵌套过深（{nesting} 层，上限 {cls.MAX_NESTING_DEPTH} 层）",
                suggestion="使用提前返回或提取辅助函数降低嵌套深度。",
            ))

        return issues

    @classmethod
    def _check_class(cls, f: GeneratedFile, node: ast.ClassDef) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        docstring = ast.get_docstring(node)
        if not docstring:
            issues.append(ReviewIssue(
                file_path=f.path,
                line_start=node.lineno,
                severity=ReviewSeverity.LOW,
                rule_id="NO-DOCSTRING",
                message=f"类 {node.name} 缺少 docstring",
                suggestion=f'为类 {node.name} 添加文档字符串，说明其用途和设计意图。',
            ))
        return issues

    @classmethod
    def _check_naming(cls, f: GeneratedFile, tree: ast.AST) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                pattern, msg = cls.NAMING_CONVENTIONS["function"]
                if not node.name.startswith("_") and not pattern.match(node.name):
                    issues.append(ReviewIssue(
                        file_path=f.path,
                        line_start=node.lineno,
                        severity=ReviewSeverity.LOW,
                        rule_id="BAD-NAMING",
                        message=f"函数 '{node.name}' 命名不规范: {msg}",
                        suggestion=f"将函数重命名为符合 snake_case 规范的名称。",
                    ))
            elif isinstance(node, ast.ClassDef):
                pattern, msg = cls.NAMING_CONVENTIONS["class"]
                if not pattern.match(node.name):
                    issues.append(ReviewIssue(
                        file_path=f.path,
                        line_start=node.lineno,
                        severity=ReviewSeverity.LOW,
                        rule_id="BAD-NAMING",
                        message=f"类 '{node.name}' 命名不规范: {msg}",
                        suggestion=f"将类重命名为符合 PascalCase 规范的名称。",
                    ))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        is_const = name.isupper() or "_" in name
                        if is_const:
                            pattern, msg = cls.NAMING_CONVENTIONS["constant"]
                            if not pattern.match(name):
                                issues.append(ReviewIssue(
                                    file_path=f.path,
                                    line_start=node.lineno,
                                    severity=ReviewSeverity.LOW,
                                    rule_id="BAD-NAMING",
                                    message=f"常量 '{name}' 命名不规范: {msg}",
                                    suggestion="将常量重命名为符合 UPPER_SNAKE_CASE 规范的名称。",
                                ))
        return issues

    @classmethod
    def _count_nesting(cls, node: ast.AST, depth: int = 0) -> int:
        """计算 AST 节点的最大控制流嵌套深度。"""
        max_depth = depth
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.If, ast.For, ast.While, ast.With, ast.Try,
                                  ast.AsyncFor, ast.AsyncWith)):
                child_depth = cls._count_nesting(child, depth + 1)
                max_depth = max(max_depth, child_depth)
            else:
                child_depth = cls._count_nesting(child, depth)
                max_depth = max(max_depth, child_depth)
        return max_depth


# ====================================================================== #
#  安全扫描器
# ====================================================================== #

class SecurityScanner(BaseReviewScanner):
    """安全审查：注入攻击、路径穿越、敏感信息泄露、危险函数。"""

    dimension = ReviewDimension.SECURITY

    DANGEROUS_IMPORTS = {
        "os": ["system", "popen", "execv", "execvp", "execvpe"],
        "subprocess": ["call", "run", "Popen", "check_call", "check_output"],
        "pickle": ["load", "loads"],
        "marshal": ["loads"],
        "eval": [],
        "exec": [],
        "base64": [],
    }

    SENSITIVE_PATTERNS = [
        (re.compile(r"""(?i)(password|passwd|pwd)\s*[:=]\s*['"][^'"]+['"]"""), "疑似硬编码密码"),
        (re.compile(r"""(?i)(api_key|apikey|api_secret|secret_key)\s*[:=]\s*['"][^'"]+['"]"""), "疑似硬编码 API 密钥"),
        (re.compile(r"""(?i)(token|auth_token|access_token)\s*[:=]\s*['"][^'"]+['"]"""), "疑似硬编码 Token"),
        (re.compile(r"""(?i)(db_password|database_password)\s*[:=]\s*['"][^'"]+['"]"""), "疑似硬编码数据库密码"),
        (re.compile(r"""-----BEGIN\s+(RSA |DSA |EC |OPENSSH |PRIVATE KEY)"""), "疑似硬编码私钥"),
    ]

    SQL_INJECTION_PATTERNS = [
        re.compile(r"""(?:execute|exec|raw)\s*\(\s*(?:f['"]|['"].*%s|['"].*\{|['"].*\+\s*str\()"""),
        re.compile(r"""f['"]\s*(?:SELECT|INSERT|UPDATE|DELETE|DROP|CREATE)\s""", re.IGNORECASE),
        re.compile(r"""(?:SELECT|INSERT|UPDATE|DELETE)\s+.*(?:FROM|INTO|TABLE)\s+.*\+\s*""", re.IGNORECASE),
    ]

    @classmethod
    def scan(cls, pkg: CodePackage) -> List[ReviewIssue]:
        """执行安全扫描。"""
        issues: List[ReviewIssue] = []
        for f in pkg.files:
            if not f.path.endswith(".py") or not (f.content or "").strip():
                continue
            issues.extend(cls._scan_file(f))
        return issues

    @classmethod
    def _scan_file(cls, f: GeneratedFile) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        content = f.content or ""
        tree = cls._get_source_tree(content)
        if tree is None:
            return issues

        issues.extend(cls._check_dangerous_calls(f, tree))
        issues.extend(cls._check_path_traversal(f, tree, content))
        issues.extend(cls._check_sensitive_data(f, tree, content))
        issues.extend(cls._check_sql_injection(f, tree, content))
        issues.extend(cls._check_insecure_deserialization(f, tree))
        return issues

    @classmethod
    def _check_dangerous_calls(
        cls, f: GeneratedFile, tree: ast.AST
    ) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        dangerous_funcs = {
            "os.system", "os.popen", "os.execv", "os.execvp", "os.execvpe",
            "subprocess.call", "subprocess.run", "subprocess.Popen",
            "subprocess.check_call", "subprocess.check_output",
            "pickle.load", "pickle.loads", "marshal.loads",
            "eval", "exec",
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_str = ""
                if isinstance(node.func, ast.Attribute):
                    parts = []
                    obj = node.func
                    while isinstance(obj, ast.Attribute):
                        parts.insert(0, obj.attr)
                        obj = obj.value
                    if isinstance(obj, ast.Name):
                        parts.insert(0, obj.id)
                    func_str = ".".join(parts)
                elif isinstance(node.func, ast.Name):
                    func_str = node.func.id

                if func_str in dangerous_funcs or (
                    func_str and any(func_str.endswith(f".{d.split('.')[-1]}") for d in dangerous_funcs if "." in d)
                ):
                    is_allowed = False
                    for imp_node in ast.walk(tree):
                        if isinstance(imp_node, ast.Import):
                            for alias in imp_node.names:
                                if alias.asname and func_str.startswith(alias.asname):
                                    is_allowed = True
                        elif isinstance(imp_node, ast.ImportFrom):
                            for alias in imp_node.names:
                                if alias.asname and func_str == alias.asname:
                                    is_allowed = True
                    if not is_allowed:
                        issues.append(ReviewIssue(
                            file_path=f.path,
                            line_start=getattr(node, "lineno", 0),
                            severity=ReviewSeverity.HIGH,
                            rule_id="DANGEROUS-CALL",
                            message=f"调用了潜在危险的函数: {func_str}",
                            suggestion="避免使用该函数或添加严格的输入验证和沙箱隔离。",
                        ))
        return issues

    @classmethod
    def _check_path_traversal(
        cls, f: GeneratedFile, tree: ast.AST, content: str
    ) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr in ("open", "read", "write"):
                        issues.append(ReviewIssue(
                            file_path=f.path,
                            line_start=getattr(node, "lineno", 0),
                            severity=ReviewSeverity.HIGH,
                            rule_id="PATH-TRAVERSAL",
                            message=f"文件操作可能存在路径穿越风险: {node.func.attr}",
                            suggestion="使用白名单校验文件路径，禁止 '..' 序列，使用 os.path.realpath 解析后再校验。",
                        ))
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if ".." in node.value and any(
                    kw in content[max(0, node.col_offset - 20):node.col_offset].lower()
                    for kw in ["open(", "path", "file", "read", "write"]
                ):
                    issues.append(ReviewIssue(
                        file_path=f.path,
                        line_start=getattr(node, "lineno", 0),
                        severity=ReviewSeverity.MEDIUM,
                        rule_id="PATH-TRAVERSAL",
                        message=f"路径中包含 '..' 可能导致目录穿越: {node.value}",
                        suggestion="使用 os.path.abspath 或 Path.resolve() 规范化路径后验证。",
                    ))
        return issues

    @classmethod
    def _check_sensitive_data(
        cls, f: GeneratedFile, tree: ast.AST, content: str
    ) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            for pattern, desc in cls.SENSITIVE_PATTERNS:
                if pattern.search(line):
                    issues.append(ReviewIssue(
                        file_path=f.path,
                        line_start=i,
                        severity=ReviewSeverity.CRITICAL,
                        rule_id="SENSITIVE-DATA",
                        message=f"{desc}",
                        suggestion="将敏感信息移至环境变量或密钥管理服务，禁止硬编码。",
                    ))
        return issues

    @classmethod
    def _check_sql_injection(
        cls, f: GeneratedFile, tree: ast.AST, content: str
    ) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            for pattern in cls.SQL_INJECTION_PATTERNS:
                if pattern.search(line):
                    issues.append(ReviewIssue(
                        file_path=f.path,
                        line_start=i,
                        severity=ReviewSeverity.CRITICAL,
                        rule_id="SQL-INJECTION",
                        message="疑似 SQL 注入风险: 字符串拼接构造 SQL 查询",
                        suggestion="使用参数化查询（占位符 %s/?），禁止字符串拼接 SQL。",
                    ))
                    break
        return issues

    @classmethod
    def _check_insecure_deserialization(
        cls, f: GeneratedFile, tree: ast.AST
    ) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in ("pickle", "marshal", "yaml"):
                        issues.append(ReviewIssue(
                            file_path=f.path,
                            line_start=getattr(node, "lineno", 0),
                            severity=ReviewSeverity.HIGH,
                            rule_id="INSECURE-DESERIALIZATION",
                            message=f"使用了不安全的反序列化库: {alias.name}",
                            suggestion="使用 json 等安全格式替代，或确保反序列化数据来自可信来源。",
                        ))
        return issues


# ====================================================================== #
#  性能扫描器
# ====================================================================== #

class PerformanceScanner(BaseReviewScanner):
    """性能审查：算法复杂度、资源管理、并发安全。"""

    dimension = ReviewDimension.PERFORMANCE

    TIME_COMPLEXITY_PATTERNS = [
        (re.compile(r"""for\s+\w+\s+in\s+.*for\s+\w+\s+in\s+"""), "嵌套循环可能导致 O(n²) 复杂度"),
        (re.compile(r"""\.sort\(\).*\.sort\(\)"""), "多次排序可能不必要"),
        (re.compile(r"""while\s+True"""), "无限循环风险，需确保有退出条件"),
    ]

    @classmethod
    def scan(cls, pkg: CodePackage) -> List[ReviewIssue]:
        """执行性能扫描。"""
        issues: List[ReviewIssue] = []
        for f in pkg.files:
            if not f.path.endswith(".py") or not (f.content or "").strip():
                continue
            issues.extend(cls._scan_file(f))
        return issues

    @classmethod
    def _scan_file(cls, f: GeneratedFile) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        content = f.content or ""
        tree = cls._get_source_tree(content)
        if tree is None:
            return issues

        issues.extend(cls._check_algorithm_complexity(f, tree, content))
        issues.extend(cls._check_resource_management(f, tree))
        issues.extend(cls._check_concurrency_safety(f, tree, content))
        return issues

    @classmethod
    def _check_algorithm_complexity(
        cls, f: GeneratedFile, tree: ast.AST, content: str
    ) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                loop_depth = cls._count_loops(node)
                if loop_depth >= 2:
                    issues.append(ReviewIssue(
                        file_path=f.path,
                        line_start=node.lineno,
                        severity=ReviewSeverity.MEDIUM,
                        rule_id="HIGH-COMPLEXITY",
                        message=f"函数 {node.name} 存在 {loop_depth} 层嵌套循环，可能导致 O(n^{loop_depth}) 复杂度",
                        suggestion="考虑使用字典/集合优化查找，或采用分治/动态规划降低复杂度。",
                    ))

            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr in ("sort", "sorted"):
                        if isinstance(node.func.value, ast.Call):
                            if isinstance(node.func.value.func, ast.Attribute):
                                if node.func.value.func.attr in ("sort", "sorted"):
                                    issues.append(ReviewIssue(
                                        file_path=f.path,
                                        line_start=getattr(node, "lineno", 0),
                                        severity=ReviewSeverity.LOW,
                                        rule_id="DOUBLE-SORT",
                                        message="存在嵌套排序操作，可能不必要",
                                        suggestion="检查是否可在一次遍历中完成排序需求。",
                                    ))
        return issues

    @classmethod
    def _check_resource_management(
        cls, f: GeneratedFile, tree: ast.AST
    ) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.With):
                has_context = True
                for item in node.items:
                    call = item.context_expr
                    if isinstance(call, ast.Call):
                        func_str = ""
                        if isinstance(call.func, ast.Attribute):
                            func_str = f"{_get_attr_chain(call.func)}"
                        if func_str in ("open", "io.open"):
                            pass
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and target.attr == "close":
                        pass
            if isinstance(node, ast.Call):
                func_str = ""
                if isinstance(node.func, ast.Attribute):
                    func_str = _get_attr_chain(node.func)
                if func_str in ("open", "io.open"):
                    if not cls._is_in_with_block(node, tree):
                        issues.append(ReviewIssue(
                            file_path=f.path,
                            line_start=getattr(node, "lineno", 0),
                            severity=ReviewSeverity.MEDIUM,
                            rule_id="RESOURCE-LEAK",
                            message=f"文件打开操作未使用 with 上下文管理器: {func_str}",
                            suggestion="使用 with 语句确保文件句柄正确关闭，防止资源泄露。",
                        ))
        return issues

    @classmethod
    def _check_concurrency_safety(
        cls, f: GeneratedFile, tree: ast.AST, content: str
    ) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        unsafe_patterns = [
            (re.compile(r"(?i)\.threading\.Thread\("), "直接创建线程，考虑使用线程池"),
            (re.compile(r"(?i)global\s+(?!$)"), "使用了 global 变量，可能导致并发问题"),
            (re.compile(r"(?i)\.multiprocessing\."), "多进程使用，注意进程间通信"),
        ]
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            for pattern, desc in unsafe_patterns:
                if pattern.search(line):
                    issues.append(ReviewIssue(
                        file_path=f.path,
                        line_start=i,
                        severity=ReviewSeverity.LOW,
                        rule_id="CONCURRENCY-RISK",
                        message=desc,
                        suggestion="使用 concurrent.futures 或 asyncio 管理并发，确保线程安全。",
                    ))
        return issues

    @classmethod
    def _count_loops(cls, node: ast.AST) -> int:
        """计算函数体内的最大循环嵌套层数。"""
        max_depth = 0
        current = 0

        def _walk(n: ast.AST) -> None:
            nonlocal current, max_depth
            if isinstance(n, (ast.For, ast.While, ast.AsyncFor)):
                current += 1
                max_depth = max(max_depth, current)
            for child in ast.iter_child_nodes(n):
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _walk(child)
            if isinstance(n, (ast.For, ast.While, ast.AsyncFor)):
                current -= 1

        _walk(node)
        return max_depth

    @classmethod
    def _is_in_with_block(cls, target: ast.AST, tree: ast.AST) -> bool:
        """判断节点是否在 with 块内。"""
        for node in ast.walk(tree):
            if isinstance(node, ast.With):
                for item in node.items:
                    if _node_contains(item.context_expr, target):
                        return True
                for stmt in node.body:
                    if _node_contains(stmt, target):
                        return True
        return False


# ====================================================================== #
#  兼容性扫描器
# ====================================================================== #

class CompatibilityScanner(BaseReviewScanner):
    """兼容性审查：依赖版本、API 兼容性、Python 版本兼容。"""

    dimension = ReviewDimension.COMPATIBILITY

    DEPRECATED_PATTERNS = [
        (re.compile(r"\.has_key\("), "dict.has_key() 在 Python 3 中已移除，使用 'in' 操作符"),
        (re.compile(r"raw_input\("), "raw_input() 在 Python 3 中已重命名为 input()"),
        (re.compile(r"unicode\("), "unicode() 在 Python 3 中已移除，所有字符串默认为 unicode"),
        (re.compile(r"xrange\("), "xrange() 在 Python 3 中已合并为 range()"),
        (re.compile(r"\.iteritems\("), "dict.iteritems() 在 Python 3 中已移除，使用 items()"),
        (re.compile(r"\.itervalues\("), "dict.itervalues() 在 Python 3 中已移除，使用 values()"),
        (re.compile(r"\.iterkeys\("), "dict.iterkeys() 在 Python 3 中已移除，使用 keys()"),
        (re.compile(r"\.has_key\("), "dict.has_key() 在 Python 3 中已移除，使用 'in' 操作符"),
        (re.compile(r"apply\("), "apply() 在 Python 3 中已移除，使用函数调用语法"),
        (re.compile(r"reduce\("), "reduce() 已移至 functools 模块"),
        (re.compile(r"\\.reduce\("), "reduce() 已移至 functools 模块"),
    ]

    @classmethod
    def scan(cls, pkg: CodePackage) -> List[ReviewIssue]:
        """执行兼容性扫描。"""
        issues: List[ReviewIssue] = []
        for f in pkg.files:
            if not f.path.endswith(".py") or not (f.content or "").strip():
                continue
            issues.extend(cls._scan_file(f))
        return issues

    @classmethod
    def _scan_file(cls, f: GeneratedFile) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        content = f.content or ""
        lines = content.split("\n")

        for i, line in enumerate(lines, 1):
            for pattern, msg in cls.DEPRECATED_PATTERNS:
                if pattern.search(line):
                    issues.append(ReviewIssue(
                        file_path=f.path,
                        line_start=i,
                        severity=ReviewSeverity.HIGH,
                        rule_id="PY2-INCOMPAT",
                        message=f"Python 2/3 不兼容: {msg}",
                        suggestion="改写代码以兼容 Python 3。",
                    ))

        tree = cls._get_source_tree(content)
        if tree:
            issues.extend(cls._check_import_compatibility(f, tree))

        return issues

    @classmethod
    def _check_import_compatibility(
        cls, f: GeneratedFile, tree: ast.AST
    ) -> List[ReviewIssue]:
        issues: List[ReviewIssue] = []
        deprecated_modules = {
            "ConfigParser": "configparser (Python 3)",
            "cPickle": "pickle (Python 3)",
            "StringIO": "io.StringIO (Python 3)",
            "BytesIO": "io.BytesIO (Python 3)",
            "collections.OrderedDict": "内置 dict (Python 3.7+)",
            "collections.abc": "直接使用 collections.abc (Python 3)",
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for old, new in deprecated_modules.items():
                        if alias.name == old or alias.name.startswith(old):
                            issues.append(ReviewIssue(
                                file_path=f.path,
                                line_start=getattr(node, "lineno", 0),
                                severity=ReviewSeverity.MEDIUM,
                                rule_id="DEPRECATED-IMPORT",
                                message=f"使用了已弃用的模块: {alias.name}，建议使用 {new}",
                                suggestion=f"迁移到推荐的替代方案: {new}",
                            ))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for old, new in deprecated_modules.items():
                        if node.module == old:
                            issues.append(ReviewIssue(
                                file_path=f.path,
                                line_start=getattr(node, "lineno", 0),
                                severity=ReviewSeverity.MEDIUM,
                                rule_id="DEPRECATED-IMPORT",
                                message=f"使用了已弃用的模块: {node.module}，建议使用 {new}",
                                suggestion=f"迁移到推荐的替代方案: {new}",
                            ))
        return issues


# ====================================================================== #
#  扫描器注册表
# ====================================================================== #

SCANNER_REGISTRY: Dict[str, type[BaseReviewScanner]] = {
    ReviewDimension.QUALITY: CodeQualityScanner,
    ReviewDimension.SECURITY: SecurityScanner,
    ReviewDimension.PERFORMANCE: PerformanceScanner,
    ReviewDimension.COMPATIBILITY: CompatibilityScanner,
}


# ====================================================================== #
#  维度评分与问题推断
# ====================================================================== #

def compute_dimension_scores(
    issues: List[ReviewIssue],
    total_files: int,
) -> Dict[str, int]:
    """计算各维度的质量评分（0-100）。

    基于问题数量和严重等级的加权扣分。
    """
    severity_weights = {
        ReviewSeverity.CRITICAL: 20,
        ReviewSeverity.HIGH: 10,
        ReviewSeverity.MEDIUM: 5,
        ReviewSeverity.LOW: 2,
    }

    default_score = 100
    scores: Dict[str, int] = {
        dim: default_score for dim in REVIEW_DIMENSIONS
    }

    for issue in issues:
        dim = getattr(issue, "dimension", None) or _infer_dimension(issue)
        if dim not in scores:
            continue
        weight = severity_weights.get(issue.severity, 2)
        scores[dim] = max(0, scores[dim] - weight)

    return scores


def _infer_dimension(issue: ReviewIssue) -> str:
    """根据 rule_id 推断审查维度。"""
    rule_id = (issue.rule_id or "").upper()
    security_rules = ("AST-FORBIDDEN", "AST-PATH", "DANGEROUS", "SENSITIVE",
                      "SQL-INJECTION", "PATH-TRAVERSAL", "INSECURE-DESERIALIZATION")
    performance_rules = ("LONG-FUNCTION", "HIGH-COMPLEXITY", "DOUBLE-SORT",
                         "RESOURCE-LEAK", "CONCURRENCY")
    compat_rules = ("PY2-", "DEPRECATED-IMPORT")

    if any(r in rule_id for r in security_rules):
        return ReviewDimension.SECURITY
    if any(r in rule_id for r in performance_rules):
        return ReviewDimension.PERFORMANCE
    if any(r in rule_id for r in compat_rules):
        return ReviewDimension.COMPATIBILITY
    return ReviewDimension.QUALITY


# ====================================================================== #
#  自动修复建议模型与模板
# ====================================================================== #

class FixSuggestion(BaseModel):
    """单条修复建议。"""

    issue_rule_id: str = Field(..., description="关联的问题规则 ID")
    file_path: str = Field(..., description="问题文件路径")
    line_start: int = Field(default=0, description="问题起始行号")
    original_code: str = Field(default="", description="原始代码片段")
    fixed_code: str = Field(default="", description="修复后代码片段")
    explanation: str = Field(default="", description="修复说明")


RULE_FIX_TEMPLATES: Dict[str, Callable[[ReviewIssue, str], Optional[FixSuggestion]]] = {}


def _register_fix(rule_id: str) -> Callable[..., Optional[FixSuggestion]]:
    """注册修复模板的装饰器。"""
    def decorator(fn: Callable[..., Optional[FixSuggestion]]) -> Callable[..., Optional[FixSuggestion]]:
        RULE_FIX_TEMPLATES[rule_id] = fn
        return fn
    return decorator


@_register_fix("NO-DOCSTRING")
def _fix_no_docstring(issue: ReviewIssue, content: str) -> Optional[FixSuggestion]:
    """为缺失 docstring 的函数添加模板。"""
    return FixSuggestion(
        issue_rule_id=issue.rule_id,
        file_path=issue.file_path,
        line_start=issue.line_start,
        original_code=content[:200],
        fixed_code='"""\nTODO: 添加函数文档字符串。\n\n    Args:\n        ...\n\n    Returns:\n        ...\n    """',
        explanation="为函数添加 docstring，说明功能、参数和返回值。",
    )


@_register_fix("NO-TYPE-HINT")
def _fix_no_type_hint(issue: ReviewIssue, content: str) -> Optional[FixSuggestion]:
    """为缺失类型注解的参数添加注解。"""
    return FixSuggestion(
        issue_rule_id=issue.rule_id,
        file_path=issue.file_path,
        line_start=issue.line_start,
        original_code=content[:200],
        fixed_code="def example(param: str, count: int = 0) -> None:",
        explanation="为参数和返回值添加类型注解。",
    )


@_register_fix("LONG-FUNCTION")
def _fix_long_function(issue: ReviewIssue, content: str) -> Optional[FixSuggestion]:
    """建议拆分过长函数。"""
    return FixSuggestion(
        issue_rule_id=issue.rule_id,
        file_path=issue.file_path,
        line_start=issue.line_start,
        original_code=content[:200],
        fixed_code="# 建议将函数拆分为多个小函数\n# def _step1(...):\n# def _step2(...):\n# def main(...):\n#     _step1(...)\n#     _step2(...)",
        explanation="将长函数按职责拆分为多个小函数，每个函数单一职责。",
    )


@_register_fix("RESOURCE-LEAK")
def _fix_resource_leak(issue: ReviewIssue, content: str) -> Optional[FixSuggestion]:
    """建议使用 with 语句管理资源。"""
    return FixSuggestion(
        issue_rule_id=issue.rule_id,
        file_path=issue.file_path,
        line_start=issue.line_start,
        original_code=content[:200],
        fixed_code="with open(filepath, 'r') as f:\n    data = f.read()",
        explanation="使用 with 上下文管理器确保资源正确释放。",
    )


@_register_fix("SQL-INJECTION")
def _fix_sql_injection(issue: ReviewIssue, content: str) -> Optional[FixSuggestion]:
    """建议使用参数化查询。"""
    return FixSuggestion(
        issue_rule_id=issue.rule_id,
        file_path=issue.file_path,
        line_start=issue.line_start,
        original_code=content[:200],
        fixed_code="cursor.execute('SELECT * FROM table WHERE id = %s', (user_id,))",
        explanation="使用参数化查询替代字符串拼接 SQL。",
    )


@_register_fix("SENSITIVE-DATA")
def _fix_sensitive_data(issue: ReviewIssue, content: str) -> Optional[FixSuggestion]:
    """建议使用环境变量。"""
    return FixSuggestion(
        issue_rule_id=issue.rule_id,
        file_path=issue.file_path,
        line_start=issue.line_start,
        original_code=content[:200],
        fixed_code='import os\npassword = os.environ.get("DB_PASSWORD", "")',
        explanation="从环境变量读取敏感信息，禁止硬编码。",
    )


@_register_fix("BAD-NAMING")
def _fix_bad_naming(issue: ReviewIssue, content: str) -> Optional[FixSuggestion]:
    """建议规范命名。"""
    return FixSuggestion(
        issue_rule_id=issue.rule_id,
        file_path=issue.file_path,
        line_start=issue.line_start,
        original_code=content[:200],
        fixed_code="# 按规范重命名\n# 函数: snake_case\n# 类: PascalCase\n# 常量: UPPER_SNAKE_CASE",
        explanation="按命名规范重命名标识符。",
    )


def generate_fix_suggestions(
    issues: List[ReviewIssue],
    pkg: CodePackage,
) -> List[FixSuggestion]:
    """根据审查问题生成自动修复建议。

    Args:
        issues: 审查问题列表
        pkg: 代码包

    Returns:
        修复建议列表
    """
    suggestions: List[FixSuggestion] = []
    content_cache: Dict[str, str] = {}

    for f in pkg.files:
        content_cache[f.path] = f.content or ""

    for issue in issues:
        rule_id = issue.rule_id or ""
        handler = RULE_FIX_TEMPLATES.get(rule_id)
        if handler:
            content = content_cache.get(issue.file_path, "")
            suggestion = handler(issue, content)
            if suggestion:
                suggestions.append(suggestion)

    return suggestions


__all__ = [
    "ReviewDimension",
    "REVIEW_DIMENSIONS",
    "BaseReviewScanner",
    "CodeQualityScanner",
    "SecurityScanner",
    "PerformanceScanner",
    "CompatibilityScanner",
    "SCANNER_REGISTRY",
    "compute_dimension_scores",
    "FixSuggestion",
    "generate_fix_suggestions",
]