from __future__ import annotations

import fnmatch
import inspect
import json
import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type, Union

from pydantic import BaseModel, Field, create_model

try:
    from app.core.observability import metrics_collector
except ImportError:
    metrics_collector = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  工具权限等级
# ------------------------------------------------------------------ #
class ToolPermissionLevel(str, Enum):
    """工具权限分级。

    FREE      - 只读、无副作用的工具，不限制使用
    OBSERVE   - 观察类操作，可读取文件/目录信息
    MODIFY    - 修改类操作，可创建/修改文件
    DANGEROUS - 危险操作，需要特殊授权
    """
    FREE = "free"
    OBSERVE = "observe"
    MODIFY = "modify"
    DANGEROUS = "dangerous"


# ------------------------------------------------------------------ #
#  /tools/call 敏感文件/目录黑名单
# ------------------------------------------------------------------ #
# read_file 拒绝读取的文件名/扩展名模式
_READ_FILE_BLOCKED_PATTERNS = [
    ".env*", "*.db", "*.sqlite*", "*.key", "*.pem", "id_rsa*", "*.log",
]
# read_file / list_dir 拒绝访问的目录前缀（按路径段匹配，相对项目根目录）
_READ_DIR_BLOCKED_PREFIXES = [["data"], ["app", "core"]]
# write_file 拒绝写入的扩展名/文件名模式
_WRITE_FILE_BLOCKED_PATTERNS = ["*.py", "*.pyw", "*.sh", "*.bat", "*.ps1", ".env*"]
# write_file 拒绝写入的目录前缀（按路径段匹配）
_WRITE_DIR_BLOCKED_PREFIXES = [["app"], ["tests"], ["scripts"], [".github"]]


def _match_name(name: str, patterns: List[str]) -> bool:
    """按 fnmatch 模式匹配文件名（大小写不敏感，跨平台一致）。"""
    lower_name = name.lower()
    return any(fnmatch.fnmatch(lower_name, p.lower()) for p in patterns)


def _is_under_blocked_dir(rel_path: str, prefixes: List[List[str]]) -> bool:
    """判断相对路径是否落在任一黑名单目录前缀下。

    例如 rel_path="app/core/config.py"，prefixes=[["app","core"]] → True
    """
    parts = Path(rel_path).parts
    if not parts:
        return False
    for prefix_parts in prefixes:
        n = len(prefix_parts)
        if len(parts) >= n and list(parts[:n]) == prefix_parts:
            return True
    return False


def _check_read_blacklist(rel_path: str) -> None:
    """读取敏感文件/目录校验：违规直接 raise ValueError。"""
    name = Path(rel_path).name
    if _match_name(name, _READ_FILE_BLOCKED_PATTERNS):
        raise ValueError(f"禁止读取敏感文件: {rel_path}")
    if _is_under_blocked_dir(rel_path, _READ_DIR_BLOCKED_PREFIXES):
        raise ValueError(f"禁止访问敏感目录下的文件: {rel_path}")


def _check_write_blacklist(rel_path: str) -> None:
    """写入敏感文件/目录校验：违规直接 raise ValueError。"""
    name = Path(rel_path).name
    if _match_name(name, _WRITE_FILE_BLOCKED_PATTERNS):
        raise ValueError(f"禁止写入敏感文件类型: {rel_path}")
    if _is_under_blocked_dir(rel_path, _WRITE_DIR_BLOCKED_PREFIXES):
        raise ValueError(f"禁止写入受保护目录: {rel_path}")


@dataclass
class ToolParamSpec:
    """单个参数规格。"""

    name: str
    type: str  # "string" / "integer" / "number" / "boolean" / "array" / "object"
    required: bool = True
    description: str = ""
    default: Any = None


@dataclass
class ToolSpec:
    """工具描述（OpenAI Function Calling 兼容格式可直接导出）。"""

    name: str
    description: str
    params: List[ToolParamSpec] = field(default_factory=list)
    permission_level: ToolPermissionLevel = ToolPermissionLevel.FREE

    def to_openai_function(self) -> Dict[str, Any]:
        props: Dict[str, Any] = {}
        required: List[str] = []
        for p in self.params:
            props[p.name] = {
                "type": p.type,
                "description": p.description,
            }
            if p.default is not None:
                props[p.name]["default"] = p.default
            if p.required:
                required.append(p.name)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
                "permission_level": self.permission_level.value,
            },
        }


@dataclass
class ToolEntry:
    spec: ToolSpec
    handler: Callable[..., Any]
    pydantic_model: Optional[Type[BaseModel]] = None


class ToolRegistry:
    """
    工具注册表：
    - 装饰器 @register 注册 Python 函数为 MCP/Function Calling 工具
    - 自动从 type hints 生成参数描述
    - 提供统一调用：call(name, kwargs)
    - 提供导出列表：to_openai_tools() — 直接喂给 LLM bind_tools
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tools: Dict[str, ToolEntry] = {}

    # ------------------------------------------------------------------ #
    #  注册
    # ------------------------------------------------------------------ #
    def register(
        self,
        func: Optional[Callable[..., Any]] = None,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        permission_level: ToolPermissionLevel = ToolPermissionLevel.FREE,
    ):
        """
        装饰器用法：
            @tool_registry.register
            def my_tool(x: int, y: str = "") -> str: ...

            @tool_registry.register(name="rename_it", description="说明")
            def my_tool(...): ...

            @tool_registry.register(permission_level=ToolPermissionLevel.MODIFY)
            def my_tool(...): ...
        """

        def _wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._register_function(fn, name_override=name, desc_override=description,
                                    permission_level=permission_level)
            return fn

        if func is not None and callable(func):
            # @tool_registry.register 无括号
            return _wrap(func)
        # @tool_registry.register(...) 有括号
        return _wrap

    def _register_function(
        self,
        fn: Callable[..., Any],
        *,
        name_override: Optional[str],
        desc_override: Optional[str],
        permission_level: ToolPermissionLevel = ToolPermissionLevel.FREE,
    ) -> None:
        tool_name = name_override or fn.__name__
        doc = (desc_override or fn.__doc__ or "").strip()
        if not doc:
            logger.warning("工具 %s 没有 docstring，将无法告诉 LLM 它的用途", tool_name)
        params = _extract_params(fn)
        pydantic_cls = _build_pydantic_model(tool_name, params)

        spec = ToolSpec(name=tool_name, description=doc, params=params, permission_level=permission_level)
        entry = ToolEntry(spec=spec, handler=fn, pydantic_model=pydantic_cls)
        with self._lock:
            self._tools[tool_name] = entry
        logger.info("已注册工具：%s（%d 参数, 权限: %s）", tool_name, len(params), permission_level.value)

    # ------------------------------------------------------------------ #
    #  查询 / 调用
    # ------------------------------------------------------------------ #
    def has(self, name: str) -> bool:
        return name in self._tools

    def list_names(self) -> List[str]:
        return sorted(self._tools.keys())

    def list_specs(self) -> List[ToolSpec]:
        """返回所有工具规格列表，含权限信息。"""
        return [e.spec for e in self._tools.values()]

    def get_spec(self, name: str) -> Optional[ToolSpec]:
        e = self._tools.get(name)
        return e.spec if e else None

    def to_openai_tools(self, *, names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        tools = self._tools.values()
        if names:
            tools = [t for t in tools if t.spec.name in names]
        return [t.spec.to_openai_function() for t in tools]

    # ------------------------------------------------------------------ #
    #  权限管理
    # ------------------------------------------------------------------ #
    # 当前调用上下文中的权限等级（可通过 require_permission 设置）
    _current_permission_level: ToolPermissionLevel = ToolPermissionLevel.OBSERVE

    def set_permission(self, level: ToolPermissionLevel) -> None:
        """设置当前调用上下文的权限等级。"""
        self._current_permission_level = level
        logger.info("工具调用权限已设为: %s", level.value)

    def get_current_permission(self) -> ToolPermissionLevel:
        """获取当前调用上下文的权限等级。"""
        return self._current_permission_level

    def require_permission(self, required_level: ToolPermissionLevel):
        """装饰器/上下文管理器：要求调用方具备指定权限等级。

        用法：
            @tool_registry.require_permission(ToolPermissionLevel.MODIFY)
            def my_tool(...): ...
        """
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                current = self._current_permission_level
                # 权限等级顺序: FREE < OBSERVE < MODIFY < DANGEROUS
                level_order = {
                    ToolPermissionLevel.FREE: 0,
                    ToolPermissionLevel.OBSERVE: 1,
                    ToolPermissionLevel.MODIFY: 2,
                    ToolPermissionLevel.DANGEROUS: 3,
                }
                if level_order.get(current, 0) < level_order.get(required_level, 0):
                    raise PermissionError(
                        f"权限不足: 需要 {required_level.value}，当前 {current.value}"
                    )
                return func(*args, **kwargs)
            return wrapper
        return decorator

    def call(self, name: str, kwargs: Optional[Dict[str, Any]] = None) -> Any:
        entry = self._tools.get(name)
        if not entry:
            raise KeyError(f"未注册的工具: {name}")

        # 权限检查：当前上下文权限必须 >= 工具所需权限
        level_order = {
            ToolPermissionLevel.FREE: 0,
            ToolPermissionLevel.OBSERVE: 1,
            ToolPermissionLevel.MODIFY: 2,
            ToolPermissionLevel.DANGEROUS: 3,
        }
        current_level = level_order.get(self._current_permission_level, 0)
        required_level = level_order.get(entry.spec.permission_level, 0)
        if current_level < required_level:
            raise PermissionError(
                f"权限不足: 工具 '{name}' 需要 {entry.spec.permission_level.value} 权限，"
                f"当前上下文权限为 {self._current_permission_level.value}"
            )

        kw = dict(kwargs or {})
        # 用 pydantic 校验参数
        if entry.pydantic_model is not None:
            try:
                validated = entry.pydantic_model(**kw)
                kw = validated.model_dump()
            except Exception as e:
                raise ValueError(f"工具 {name} 参数错误: {e}") from e
        try:
            result = entry.handler(**kw)
            # 指标埋点
            if metrics_collector is not None:
                metrics_collector.record_tool_call(tool_name=name, success=True)
            return result
        except Exception as e:
            # 指标埋点（失败）
            if metrics_collector is not None:
                metrics_collector.record_tool_call(tool_name=name, success=False)
            logger.exception("调用工具 %s 失败：%s", name, e)
            raise


# ====================================================================== #
#  内部：从函数签名推导参数
# ====================================================================== #

_PY_TYPE_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
    Any: "object",
}


def _pytype_to_json(annotation: Any) -> str:
    if annotation is inspect.Parameter.empty:
        return "string"
    # 处理 Optional/Union[str, None]
    origin = getattr(annotation, "__origin__", None)
    if origin is Optional or (origin is Union and type(None) in getattr(annotation, "__args__", ())):
        args = [a for a in getattr(annotation, "__args__", ()) if a is not type(None)]
        if len(args) == 1:
            return _pytype_to_json(args[0])
    if annotation in _PY_TYPE_TO_JSON:
        return _PY_TYPE_TO_JSON[annotation]
    if origin in (list, List):
        return "array"
    if origin in (dict, Dict):
        return "object"
    return "string"


def _extract_params(fn: Callable[..., Any]) -> List[ToolParamSpec]:
    params: List[ToolParamSpec] = []
    sig = inspect.signature(fn)
    for pname, p in sig.parameters.items():
        if pname in ("self", "cls"):
            continue
        ann = p.annotation
        json_type = _pytype_to_json(ann)
        required = p.default is inspect.Parameter.empty
        description = f"参数 {pname}"
        if isinstance(ann, type) and hasattr(ann, "__name__"):
            description = f"{ann.__name__} 类型的 {pname}"
        params.append(
            ToolParamSpec(
                name=pname,
                type=json_type,
                required=required,
                description=description,
                default=None if required else p.default,
            )
        )
    return params


def _build_pydantic_model(tool_name: str, params: List[ToolParamSpec]) -> Type[BaseModel]:
    fields: Dict[str, Any] = {}
    for p in params:
        ftype: Any = str
        if p.type == "integer":
            ftype = int
        elif p.type == "number":
            ftype = float
        elif p.type == "boolean":
            ftype = bool
        elif p.type == "array":
            ftype = list
        elif p.type == "object":
            ftype = dict
        if not p.required:
            ftype = Optional[ftype]
            fields[pname_clean(p.name)] = (ftype, Field(default=p.default, description=p.description))
        else:
            fields[pname_clean(p.name)] = (ftype, Field(..., description=p.description))
    model_name = f"{tool_name}_args".replace("-", "_")
    return create_model(model_name, **fields)  # type: ignore


def pname_clean(name: str) -> str:
    # pydantic 字段名必须是合法 python 标识符
    if name.isidentifier():
        return name
    clean = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    if not clean or not clean[0].isalpha():
        clean = "f_" + clean
    return clean


# ====================================================================== #
#  全局单例
# ====================================================================== #

tool_registry = ToolRegistry()


# ------------------------------------------------------------------ #
#  内置示例工具（注册几个通用工具，便于测试使用）
# ------------------------------------------------------------------ #

@tool_registry.register(description="对 Python 代码做 AST 安全校验，返回结构化结果。", permission_level=ToolPermissionLevel.FREE)
def validate_code(source: str, allowed_modules: str = "") -> str:
    from app.tools.ast_validator import validate_python_source
    result = validate_python_source(source)
    return json.dumps(result.as_dict(), ensure_ascii=False, indent=2)


@tool_registry.register(description="在项目根目录下安全地创建/覆盖文件，禁止路径穿越。", permission_level=ToolPermissionLevel.MODIFY)
def write_file(path: str, content: str) -> str:
    # 敏感文件/目录黑名单校验：违规直接 raise ValueError（不被下方 try 捕获）
    _check_write_blacklist(path)
    from app.tools.ast_validator import write_file_safely
    try:
        p = write_file_safely(path, content)
        return f"OK: wrote {len(content.encode('utf-8'))} bytes to {p}"
    except Exception as e:
        return f"ERROR: {e}"


@tool_registry.register(description="读取指定相对路径文件的内容。", permission_level=ToolPermissionLevel.OBSERVE)
def read_file(path: str) -> str:
    # 敏感文件/目录黑名单校验：违规直接 raise ValueError（不被下方 try 捕获）
    _check_read_blacklist(path)
    from app.tools.ast_validator import safe_resolve_path
    try:
        target = safe_resolve_path(path)
        if not target.exists():
            return f"ERROR: file not found: {path}"
        return target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"ERROR: {e}"


@tool_registry.register(description="列出目录下的文件和子目录。", permission_level=ToolPermissionLevel.OBSERVE)
def list_dir(path: str = ".") -> str:
    # 敏感目录黑名单校验：违规直接 raise ValueError（避免列目录泄露敏感信息）
    _check_read_blacklist(path)
    from app.tools.ast_validator import safe_resolve_path
    try:
        target = safe_resolve_path(path)
    except Exception as e:
        return f"ERROR: {e}"
    if not target.exists() or not target.is_dir():
        return f"ERROR: not a directory: {path}"
    lines = []
    for item in sorted(target.iterdir()):
        kind = "D" if item.is_dir() else "F"
        size = item.stat().st_size if item.is_file() else 0
        lines.append(f"[{kind}] {item.name:<40s}  {size:>10d}")
    return "\n".join(lines)


__all__ = [
    "ToolParamSpec",
    "ToolSpec",
    "ToolEntry",
    "ToolRegistry",
    "ToolPermissionLevel",
    "tool_registry",
]
