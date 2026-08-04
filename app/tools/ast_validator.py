from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  数据结构
# ------------------------------------------------------------------ #

@dataclass
class ASTViolation:
    """一次 AST 校验违规。"""

    severity: str  # warning / error
    node_type: str
    rule: str
    message: str
    line: int = 0
    col: int = 0


@dataclass
class ASTValidationResult:
    """完整校验结果。"""

    passed: bool = False
    source: str = ""
    violations: List[ASTViolation] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    top_level_symbols: List[str] = field(default_factory=list)
    line_count: int = 0
    ast_tree: Optional[ast.AST] = None

    @property
    def errors(self) -> List[ASTViolation]:
        return [v for v in self.violations if v.severity == "error"]

    @property
    def warnings(self) -> List[ASTViolation]:
        return [v for v in self.violations if v.severity == "warning"]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "line_count": self.line_count,
            "imports": self.imports,
            "top_level_symbols": self.top_level_symbols,
            "violations": [
                {
                    "severity": v.severity,
                    "node_type": v.node_type,
                    "rule": v.rule,
                    "message": v.message,
                    "line": v.line,
                    "col": v.col,
                }
                for v in self.violations
            ],
        }


# ------------------------------------------------------------------ #
#  核心 Walkers
# ------------------------------------------------------------------ #

class _SecurityVisitor(ast.NodeVisitor):
    """检查危险节点与导入模块。"""

    # 常见危险调用：subprocess / eval / os.system / shell=True / getattr 系反射
    DANGER_CALLS: Dict[str, str] = {
        "eval": "禁止调用 eval()，存在任意代码执行风险",
        "exec": "禁止调用 exec()，存在任意代码执行风险",
        "compile": "禁止动态 compile()",
        "globals": "禁止通过 globals() 暴露/修改全局命名空间",
        "locals": "禁止通过 locals() 暴露/修改命名空间（警告级）",
        # 反射类调用：可绕过静态分析访问敏感属性
        "getattr": "禁止调用 getattr()，可绕过静态分析访问敏感属性",
        "setattr": "禁止调用 setattr()，可动态修改对象属性绕过保护",
        "delattr": "禁止调用 delattr()，可动态删除对象属性",
        "hasattr": "禁止调用 hasattr()，可能触发属性访问副作用",
        "__getattribute__": "禁止调用 __getattribute__()，可绕过属性访问保护",
    }

    DANGER_SUBSCRIPT: Dict[str, str] = {
        "globals": "禁止通过下标访问 globals",
        "builtins": "禁止访问 __builtins__",
    }

    # 危险属性访问：可触发原型链污染、逃逸沙箱、获取字节码等
    DANGER_ATTRS: Dict[str, str] = {
        "__import__": "禁止使用 __import__ 动态导入",
        "__dict__": "访问/修改 __dict__ 可绕过保护机制",
        "__class__": "通过 __class__ 访问对象类型可触发原型污染",
        "__bases__": "禁止访问 __bases__，可用于类继承链逃逸",
        "__subclasses__": "禁止访问 __subclasses__()，可用于沙箱逃逸",
        "__mro__": "禁止访问 __mro__，可用于方法解析链逃逸",
        "__globals__": "禁止访问 __globals__，可泄露全局命名空间",
        "__code__": "禁止访问 __code__，可获取/替换函数字节码",
        "__builtins__": "禁止访问 __builtins__，可获取内建危险对象",
        "__loader__": "禁止访问 __loader__，可被用于加载任意模块",
        "__spec__": "禁止访问 __spec__，可泄露模块加载器信息",
        "__func__": "禁止访问 __func__，可获取绑定的原始函数对象",
        "__self__": "禁止访问 __self__，可获取绑定方法的实例",
        "__wrapped__": "禁止访问 __wrapped__，可绕过装饰器保护",
        "f_globals": "禁止访问帧对象的 f_globals，可泄露全局命名空间",
        "f_locals": "禁止访问帧对象的 f_locals，可泄露局部命名空间",
        "f_builtins": "禁止访问帧对象的 f_builtins，可获取内建危险对象",
    }

    # 危险属性调用：(模块名, 方法名) 形如 os.system / subprocess.run
    DANGER_ATTR_CALLS: Set[Tuple[str, str]] = {
        ("os", "system"), ("os", "popen"), ("os", "execv"), ("os", "execve"),
        ("os", "spawnl"), ("os", "spawnv"), ("os", "fork"), ("os", "kill"),
        ("subprocess", "run"), ("subprocess", "Popen"), ("subprocess", "call"),
        ("subprocess", "check_call"), ("subprocess", "check_output"),
        ("pty", "spawn"),
    }

    # from <module> import <name> 的危险名字映射
    DANGER_FROM_IMPORTS: Dict[str, Set[str]] = {
        "os": {"system", "popen", "execv", "execve", "spawnl", "spawnv", "fork", "kill"},
        "subprocess": {"run", "Popen", "call", "check_call", "check_output"},
        "pty": {"spawn"},
    }

    def __init__(
        self,
        *,
        allowed_modules: Set[str],
        forbidden_nodes: Set[str],
        project_root: Path,
    ) -> None:
        self.allowed = set(allowed_modules)
        self.forbidden = set(forbidden_nodes)
        self.project_root = project_root
        self.violations: List[ASTViolation] = []
        self.imports: List[str] = []
        # 允许本地模块：所有不在白名单中的模块名，只要对应目录
        # 在 project_root 下实际存在，就视为本地模块放行（warning 级）
        self._local_cache: Dict[str, bool] = {}

    def _is_local_module(self, top: str) -> bool:
        """判断 top 是否为 project_root 下的本地可导入模块。"""
        if top in self._local_cache:
            return self._local_cache[top]
        root = self.project_root.resolve()
        # 根目录下是否存在同名包/模块
        pkg_dir = root / top
        mod_file = root / f"{top}.py"
        is_local = pkg_dir.is_dir() or mod_file.is_file()
        self._local_cache[top] = is_local
        return is_local

    def _is_module_allowed(self, top: str) -> bool:
        """白名单匹配：支持 * 全放行、精确匹配、前缀.* 通配、以及 *xxx* 子串通配。"""
        if "*" in self.allowed:
            return True
        if top in self.allowed:
            return True
        for rule in self.allowed:
            if rule.endswith(".*") and top == rule[:-2]:
                return True
            if rule.startswith("*.") and top.endswith(rule[2:]):
                return True
            # 子串通配：如 *test* 匹配 pytest / unittest
            if rule.startswith("*") and rule.endswith("*") and len(rule) > 2:
                needle = rule[1:-1]
                if needle and needle in top:
                    return True
        return False

    # -------------------------------------------------------------- #
    #  导入检查
    # -------------------------------------------------------------- #
    def visit_Import(self, node: ast.Import) -> Any:
        for alias in node.names:
            top = alias.name.split(".")[0]
            self.imports.append(alias.name)
            if not self._is_module_allowed(top):
                if self._is_local_module(top):
                    # 本地模块放行但给 warning，便于开发者注意
                    self.violations.append(
                        ASTViolation(
                            severity="warning",
                            node_type="Import",
                            rule="local_module",
                            message=f"本地模块 '{alias.name}' 未在白名单中，但在项目根目录下存在，已放行",
                            line=node.lineno,
                            col=node.col_offset,
                        )
                    )
                else:
                    self.violations.append(
                        ASTViolation(
                            severity="error",
                            node_type="Import",
                            rule="import_whitelist",
                            message=f"模块 '{alias.name}' 不在白名单中",
                            line=node.lineno,
                            col=node.col_offset,
                        )
                    )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        module = node.module or ""
        top = module.split(".")[0]
        if node.level > 0:
            # 相对导入：检查必须位于 project_root 内
            self.violations.append(
                ASTViolation(
                    severity="warning",
                    node_type="ImportFrom",
                    rule="relative_import",
                    message=f"相对导入 '{module}'，请确保路径在项目根目录内",
                    line=node.lineno,
                    col=node.col_offset,
                )
            )
        else:
            self.imports.append(module)
            if not self._is_module_allowed(top):
                if self._is_local_module(top):
                    self.violations.append(
                        ASTViolation(
                            severity="warning",
                            node_type="ImportFrom",
                            rule="local_module",
                            message=f"本地模块 '{module}' 未在白名单中，但在项目根目录下存在，已放行",
                            line=node.lineno,
                            col=node.col_offset,
                        )
                    )
                else:
                    self.violations.append(
                        ASTViolation(
                            severity="error",
                            node_type="ImportFrom",
                            rule="import_whitelist",
                            message=f"模块 '{module}' 不在白名单中",
                            line=node.lineno,
                            col=node.col_offset,
                        )
                    )
        # 阻断 from <危险模块> import <危险名>（深度防御：即使模块在白名单也禁）
        danger_names = self.DANGER_FROM_IMPORTS.get(top, set())
        if danger_names:
            for alias in node.names:
                if alias.name in danger_names:
                    self.violations.append(
                        ASTViolation(
                            severity="error",
                            node_type="ImportFrom",
                            rule="danger_from_import",
                            message=f"禁止从 '{module}' 导入 '{alias.name}'，存在命令/进程执行风险",
                            line=node.lineno,
                            col=node.col_offset,
                        )
                    )
        self.generic_visit(node)

    # -------------------------------------------------------------- #
    #  黑名单节点类型
    # -------------------------------------------------------------- #
    def _check_forbidden(self, node: ast.AST) -> None:
        cls = type(node).__name__
        if cls in self.forbidden:
            self.violations.append(
                ASTViolation(
                    severity="error",
                    node_type=cls,
                    rule="forbidden_node",
                    message=f"禁止使用 AST 节点 '{cls}'",
                    line=getattr(node, "lineno", 0),
                    col=getattr(node, "col_offset", 0),
                )
            )

    def visit_Eval(self, node: ast.AST) -> Any:
        self._check_forbidden(node)
        self.generic_visit(node)

    def visit_Exec(self, node: ast.AST) -> Any:
        self._check_forbidden(node)
        self.generic_visit(node)

    def visit_Breakpoint(self, node: ast.AST) -> Any:
        self._check_forbidden(node)
        self.generic_visit(node)

    # -------------------------------------------------------------- #
    #  调用/属性检查
    # -------------------------------------------------------------- #
    def visit_Call(self, node: ast.Call) -> Any:
        # Name(func) 直接调用：eval() / exec() / open()
        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name in self.DANGER_CALLS:
                sev = "warning" if name == "locals" else "error"
                self.violations.append(
                    ASTViolation(
                        severity=sev,
                        node_type="Call",
                        rule=f"danger_call:{name}",
                        message=self.DANGER_CALLS[name],
                        line=node.lineno,
                        col=node.col_offset,
                    )
                )
            if name == "open":
                self._check_open_path(node)
            if name == "subprocess" or name == "Popen":
                self.violations.append(
                    ASTViolation(
                        severity="error",
                        node_type="Call",
                        rule="subprocess_blocked",
                        message="禁止使用 subprocess 启动子进程",
                        line=node.lineno,
                        col=node.col_offset,
                    )
                )
        # Attribute 调用：os.system / subprocess.run / pty.spawn 等
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            pair = (node.func.value.id, node.func.attr)
            if pair in self.DANGER_ATTR_CALLS:
                self.violations.append(
                    ASTViolation(
                        severity="error",
                        node_type="Call",
                        rule=f"danger_attr_call:{pair[0]}.{pair[1]}",
                        message=f"禁止调用 {pair[0]}.{pair[1]}()，存在命令/进程执行风险",
                        line=node.lineno,
                        col=node.col_offset,
                    )
                )
        # 关键字参数 shell=True
        for kw in node.keywords:
            if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                self.violations.append(
                    ASTViolation(
                        severity="error",
                        node_type="Call",
                        rule="shell_true",
                        message="禁止 shell=True 调用",
                        line=node.lineno,
                        col=node.col_offset,
                    )
                )
        self.generic_visit(node)

    def _check_open_path(self, node: ast.Call) -> None:
        if not node.args:
            return
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            target = Path(first.value).resolve() if not Path(first.value).is_absolute() else Path(first.value)
            try:
                root = self.project_root.resolve()
                target.relative_to(root)
            except ValueError:
                self.violations.append(
                    ASTViolation(
                        severity="error",
                        node_type="Call",
                        rule="path_traversal",
                        message=f"open() 路径 '{first.value}' 越出项目根目录",
                        line=node.lineno,
                        col=node.col_offset,
                    )
                )

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        attr = node.attr
        if attr in self.DANGER_ATTRS:
            # 所有危险属性访问统一 error 级（含 __class__ / __dict__）
            self.violations.append(
                ASTViolation(
                    severity="error",
                    node_type="Attribute",
                    rule=f"danger_attr:{attr}",
                    message=self.DANGER_ATTRS[attr],
                    line=getattr(node, "lineno", 0),
                    col=getattr(node, "col_offset", 0),
                )
            )
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> Any:
        if isinstance(node.value, ast.Name) and node.value.id in self.DANGER_SUBSCRIPT:
            self.violations.append(
                ASTViolation(
                    severity="error",
                    node_type="Subscript",
                    rule="danger_subscript",
                    message=self.DANGER_SUBSCRIPT[node.value.id],
                    line=getattr(node, "lineno", 0),
                    col=getattr(node, "col_offset", 0),
                )
            )
        self.generic_visit(node)


class _SymbolCollector(ast.NodeVisitor):
    """收集顶层定义的函数/类名。"""

    def __init__(self) -> None:
        self.symbols: List[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self.symbols.append(f"def {node.name}")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        self.symbols.append(f"async def {node.name}")

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        self.symbols.append(f"class {node.name}")


# ------------------------------------------------------------------ #
#  工具函数 / 安全校验
# ------------------------------------------------------------------ #

# 常见 Markdown 代码块包裹
_CODE_BLOCK_RE = re.compile(
    r"```(?:python|py)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE
)


def extract_first_python_code_block(text: str) -> str:
    """从 LLM 输出中提取第一个 Python 代码块，找不到就返回原文本 strip。"""
    if not text:
        return ""
    m = _CODE_BLOCK_RE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def safe_resolve_path(rel_path: str, project_root: Optional[Path] = None) -> Path:
    """
    安全解析相对路径：确保结果在 project_root / 允许的白名单目录内。
    禁止 .. / 绝对路径。
    """
    root = Path(project_root or settings.security.project_root).resolve()
    # 禁止 Windows 绝对路径 / Unix 绝对路径
    if Path(rel_path).is_absolute():
        raise ValueError(f"路径 '{rel_path}' 必须是相对路径")
    if ".." in Path(rel_path).parts:
        raise ValueError(f"路径 '{rel_path}' 包含 '..'，禁止路径穿越")
    target = (root / rel_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise ValueError(f"路径 '{rel_path}' 越出项目根目录") from e
    return target


# ------------------------------------------------------------------ #
#  校验入口
# ------------------------------------------------------------------ #

def validate_python_source(
    source: str,
    *,
    allowed_modules: Optional[Set[str]] = None,
    forbidden_nodes: Optional[Set[str]] = None,
    project_root: Optional[Path] = None,
) -> ASTValidationResult:
    """
    主入口：校验一段 Python 源码字符串。
    仅当没有任何 error 级违规且能成功解析为 AST 时 passed=True。
    """
    cfg = settings.security
    mods = allowed_modules or set(cfg.allowed_modules)
    forbidden = forbidden_nodes or set(cfg.forbidden_nodes)
    root = Path(project_root or cfg.project_root)

    result = ASTValidationResult(source=source, line_count=source.count("\n") + 1)

    # 1. 语法解析
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        result.violations.append(
            ASTViolation(
                severity="error",
                node_type="SyntaxError",
                rule="syntax_error",
                message=f"语法错误：{e.msg}（偏移 {e.offset}）",
                line=e.lineno or 0,
                col=e.offset or 0,
            )
        )
        result.passed = False
        return result
    result.ast_tree = tree

    # 2. 安全 Walk
    sec = _SecurityVisitor(
        allowed_modules=mods,
        forbidden_nodes=forbidden,
        project_root=root,
    )
    sec.visit(tree)
    result.violations.extend(sec.violations)
    result.imports = sec.imports

    # 3. 顶层符号收集
    col = _SymbolCollector()
    col.visit(tree)
    result.top_level_symbols = col.symbols

    # 4. 结论
    result.passed = not any(v.severity == "error" for v in result.violations)
    return result


def write_file_safely(rel_path: str, content: str) -> Path:
    """
    安全写文件：路径穿越检查 + 文件大小限制（默认 2MB）+ 父目录自动创建。
    返回最终绝对路径。
    """
    MAX_BYTES = 2 * 1024 * 1024  # 2MB
    if len(content.encode("utf-8")) > MAX_BYTES:
        raise ValueError(f"写入内容过大（>{MAX_BYTES}）")
    target = safe_resolve_path(rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    logger.info("安全写入文件：%s (%d 字节)", target, len(content.encode("utf-8")))
    return target


__all__ = [
    "ASTViolation",
    "ASTValidationResult",
    "validate_python_source",
    "extract_first_python_code_block",
    "safe_resolve_path",
    "write_file_safely",
]
