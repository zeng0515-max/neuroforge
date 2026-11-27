from __future__ import annotations

import contextvars
import fnmatch
import importlib
import inspect
import json
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Type, Union

from pydantic import BaseModel, Field, create_model

try:
    from app.core.observability import metrics_collector
except ImportError:
    metrics_collector = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class ToolPermissionLevel(str, Enum):
    """工具权限分级。FREE < OBSERVE < MODIFY < DANGEROUS。"""

    FREE = "free"
    OBSERVE = "observe"
    MODIFY = "modify"
    DANGEROUS = "dangerous"


_READ_FILE_BLOCKED_PATTERNS = [".env*", "*.db", "*.sqlite*", "*.key", "*.pem", "id_rsa*", "*.log"]
_READ_DIR_BLOCKED_PREFIXES = [["data"], ["app", "core"]]
_WRITE_FILE_BLOCKED_PATTERNS = [
    "*.pyw", "*.sh", "*.bat", "*.ps1", ".env*",
    "main.py", "start.py", "generated.py", "check_db.py",
    "requirements.txt", "pyproject.toml", "README.md",
    "Dockerfile", "docker-compose.yml", "Makefile", "LICENSE",
    "benchmark_results.json", "BENCHMARK_V2.md", "nul",
]
_WRITE_DIR_BLOCKED_PREFIXES = [
    ["app"], ["tests"], ["scripts"], [".github"],
    ["web"], ["docs"], ["data"], ["semantic_cache"], ["runtime"],
]


def _match_name(name: str, patterns: List[str]) -> bool:
    lower_name = name.lower()
    return any(fnmatch.fnmatch(lower_name, p.lower()) for p in patterns)


def _is_under_blocked_dir(rel_path: str, prefixes: List[List[str]]) -> bool:
    parts = Path(rel_path).parts
    if not parts:
        return False
    for prefix_parts in prefixes:
        n = len(prefix_parts)
        if len(parts) >= n and list(parts[:n]) == prefix_parts:
            return True
    return False


def _check_read_blacklist(rel_path: str) -> None:
    name = Path(rel_path).name
    if _match_name(name, _READ_FILE_BLOCKED_PATTERNS):
        raise ValueError(f"禁止读取敏感文件: {rel_path}")
    if _is_under_blocked_dir(rel_path, _READ_DIR_BLOCKED_PREFIXES):
        raise ValueError(f"禁止访问敏感目录下的文件: {rel_path}")


def _check_write_blacklist(rel_path: str) -> None:
    name = Path(rel_path).name
    if _match_name(name, _WRITE_FILE_BLOCKED_PATTERNS):
        raise ValueError(f"禁止写入敏感文件类型: {rel_path}")
    if _is_under_blocked_dir(rel_path, _WRITE_DIR_BLOCKED_PREFIXES):
        raise ValueError(f"禁止写入受保护目录: {rel_path}")


@dataclass
class ToolParamSpec:
    name: str
    type: str
    required: bool = True
    description: str = ""
    default: Any = None


@dataclass
class ToolSpec:
    name: str
    description: str
    params: List[ToolParamSpec] = field(default_factory=list)
    permission_level: ToolPermissionLevel = ToolPermissionLevel.FREE
    require_approval: bool = False

    def to_openai_function(self) -> Dict[str, Any]:
        props: Dict[str, Any] = {}
        required: List[str] = []
        for p in self.params:
            props[p.name] = {"type": p.type, "description": p.description}
            if p.default is not None:
                props[p.name]["default"] = p.default
            if p.required:
                required.append(p.name)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": props, "required": required},
                "permission_level": self.permission_level.value,
                "require_approval": self.require_approval,
            },
        }


@dataclass
class ToolEntry:
    spec: ToolSpec
    handler: Callable[..., Any]
    pydantic_model: Optional[Type[BaseModel]] = None


class ToolAudit:
    """工具调用审计日志，线程安全，支持按工具名筛选。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: List[Dict[str, Any]] = []
        self._max_records = 1000

    def log(self, tool_name: str, args: Dict[str, Any], result: Any, success: bool, caller: str = "system") -> None:
        record: Dict[str, Any] = {
            "timestamp": time.time(),
            "tool_name": tool_name,
            "args": args,
            "success": success,
            "caller": caller,
        }
        if not success:
            record["error"] = str(result)[:200]
        with self._lock:
            self._records.append(record)
            if len(self._records) > self._max_records:
                self._records = self._max_records  # type: ignore[assignment]

    def get_records(self, tool_name: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            records = list(self._records)
        if tool_name:
            records = [r for r in records if r["tool_name"] == tool_name]
        return records[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


class ToolApprovalManager:
    """DANGEROUS 工具审批工作流：请求→审批/拒绝→执行。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._approved: Set[str] = set()

    def request_approval(self, tool_name: str, caller: str = "system", reason: str = "") -> str:
        approval_id = str(uuid.uuid4())[:8]
        self._pending[approval_id] = {
            "tool_name": tool_name,
            "caller": caller,
            "reason": reason,
            "timestamp": time.time(),
            "status": "pending",
        }
        logger.warning("工具审批请求: %s -> %s (ID: %s)", caller, tool_name, approval_id)
        return approval_id

    def approve(self, approval_id: str) -> bool:
        with self._lock:
            if approval_id not in self._pending:
                return False
            self._pending[approval_id]["status"] = "approved"
            self._approved.add(approval_id)
            logger.info("审批已通过: %s", approval_id)
            return True

    def reject(self, approval_id: str) -> bool:
        with self._lock:
            if approval_id not in self._pending:
                return False
            self._pending[approval_id]["status"] = "rejected"
            logger.info("审批已拒绝: %s", approval_id)
            return True

    def is_approved(self, approval_id: str) -> bool:
        return approval_id in self._approved

    def get_pending(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [{**v, "id": k} for k, v in self._pending.items() if v["status"] == "pending"]

    def clear(self, approval_id: str) -> None:
        self._pending.pop(approval_id, None)
        self._approved.discard(approval_id)


class ToolRegistry:
    """工具注册表：注册、调用、权限管理、审计日志、审批工作流。

    权限等级存放在 ``ContextVar`` 里而不是实例属性上：每个请求（asyncio 任务
    或线程）拿到自己的上下文副本，一个请求临时提升到 MODIFY 不会污染并发中的
    其他请求。进程级默认等级仍可通过 ``set_default_permission`` 调整（运维开关）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tools: Dict[str, ToolEntry] = {}
        self._audit = ToolAudit()
        self._approval_mgr = ToolApprovalManager()
        self._default_permission_level: ToolPermissionLevel = ToolPermissionLevel.OBSERVE
        self._permission_var: contextvars.ContextVar[Optional[ToolPermissionLevel]] = (
            contextvars.ContextVar("neuroforge_tool_permission", default=None)
        )

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
        require_approval: bool = False,
    ):
        def _wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._register_function(
                fn,
                name_override=name,
                desc_override=description,
                permission_level=permission_level,
                require_approval=require_approval,
            )
            return fn

        if func is not None and callable(func):
            return _wrap(func)
        return _wrap

    def _register_function(
        self,
        fn: Callable[..., Any],
        *,
        name_override: Optional[str],
        desc_override: Optional[str],
        permission_level: ToolPermissionLevel = ToolPermissionLevel.FREE,
        require_approval: bool = False,
    ) -> None:
        tool_name = name_override or fn.__name__
        doc = (desc_override or fn.__doc__ or "").strip()
        if not doc:
            logger.warning("工具 %s 没有 docstring", tool_name)
        params = _extract_params(fn)
        pydantic_cls = _build_pydantic_model(tool_name, params)
        need_approval = require_approval or permission_level == ToolPermissionLevel.DANGEROUS
        spec = ToolSpec(
            name=tool_name,
            description=doc,
            params=params,
            permission_level=permission_level,
            require_approval=need_approval,
        )
        entry = ToolEntry(spec=spec, handler=fn, pydantic_model=pydantic_cls)
        with self._lock:
            self._tools[tool_name] = entry
        logger.info(
            "已注册工具：%s（%d 参数, 权限: %s, 需审批: %s）",
            tool_name,
            len(params),
            permission_level.value,
            need_approval,
        )

    # ------------------------------------------------------------------ #
    #  查询
    # ------------------------------------------------------------------ #
    def has(self, name: str) -> bool:
        return name in self._tools

    def list_names(self) -> List[str]:
        return sorted(self._tools.keys())

    def list_specs(self) -> List[ToolSpec]:
        return [e.spec for e in self._tools.values()]

    def get_spec(self, name: str) -> Optional[ToolSpec]:
        e = self._tools.get(name)
        return e.spec if e else None

    def get_tools_by_permission(self, level: ToolPermissionLevel) -> List[ToolSpec]:
        """按权限等级筛选工具列表。"""
        return [e.spec for e in self._tools.values() if e.spec.permission_level == level]

    def to_openai_tools(self, *, names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        tools = self._tools.values()
        if names:
            tools = [t for t in tools if t.spec.name in names]
        return [t.spec.to_openai_function() for t in tools]

    # ------------------------------------------------------------------ #
    #  动态发现
    # ------------------------------------------------------------------ #
    def dynamic_discover_tools(self, package: str = "app.tools") -> List[str]:
        """扫描指定包，发现含 @register 装饰器的模块。"""
        discovered: List[str] = []
        try:
            pkg = importlib.import_module(package)
            pkg_path = getattr(pkg, "__path__", None)
            if not pkg_path:
                return discovered
            for _, module_name, _ in importlib.walk_packages(pkg_path, prefix=package + "."):
                try:
                    mod = importlib.import_module(module_name)
                    for _, obj in inspect.getmembers(mod, inspect.isfunction):
                        if hasattr(obj, "__wrapped__") and self.has(obj.__wrapped__.__name__):
                            continue
                    discovered.append(module_name)
                except Exception as e:
                    logger.debug("扫描模块 %s 失败: %s", module_name, e)
        except Exception as e:
            logger.warning("动态发现工具失败: %s", e)
        return discovered

    # ------------------------------------------------------------------ #
    #  权限管理
    # ------------------------------------------------------------------ #
    _LEVEL_ORDER = {
        ToolPermissionLevel.FREE: 0,
        ToolPermissionLevel.OBSERVE: 1,
        ToolPermissionLevel.MODIFY: 2,
        ToolPermissionLevel.DANGEROUS: 3,
    }

    def set_permission(self, level: ToolPermissionLevel) -> contextvars.Token:
        """Set the permission level for the *current* context only.

        Returns a token so the previous level can be restored with
        ``reset_permission`` (or use :meth:`permission_scope`).
        """
        logger.info("工具调用权限已设为: %s（当前上下文）", level.value)
        return self._permission_var.set(level)

    def reset_permission(self, token: contextvars.Token) -> None:
        """Restore the level captured by a previous ``set_permission`` call."""
        try:
            self._permission_var.reset(token)
        except (ValueError, RuntimeError):  # token from another context
            self._permission_var.set(None)

    @contextmanager
    def permission_scope(self, level: ToolPermissionLevel) -> Iterator[None]:
        """Temporarily raise/lower the permission level inside this context."""
        token = self._permission_var.set(level)
        try:
            yield
        finally:
            self.reset_permission(token)

    def set_default_permission(self, level: ToolPermissionLevel) -> None:
        """Process-wide default used when the current context sets nothing."""
        self._default_permission_level = level
        logger.info("工具调用默认权限已设为: %s", level.value)

    def get_default_permission(self) -> ToolPermissionLevel:
        return self._default_permission_level

    def get_current_permission(self) -> ToolPermissionLevel:
        level = self._permission_var.get()
        return level if level is not None else self._default_permission_level

    def require_permission(self, required_level: ToolPermissionLevel):
        """装饰器：要求调用方具备指定权限等级。"""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                current = self.get_current_permission()
                if self._LEVEL_ORDER.get(current, 0) < self._LEVEL_ORDER.get(required_level, 0):
                    raise PermissionError(f"权限不足: 需要 {required_level.value}，当前 {current.value}")
                return func(*args, **kwargs)

            return wrapper

        return decorator

    def call(
        self,
        name: str,
        kwargs: Optional[Dict[str, Any]] = None,
        caller: str = "system",
        approval_id: Optional[str] = None,
    ) -> Any:
        """调用工具，含权限检查、审批验证、参数校验和审计日志。"""
        entry = self._tools.get(name)
        if not entry:
            raise KeyError(f"未注册的工具: {name}")

        try:
            from app.core.runtime_policy import ActionKind, runtime_policy

            decision = runtime_policy.allow(
                ActionKind.TOOL_CALL,
                actor=caller,
                context={
                    "tool": name,
                    "permission": entry.spec.permission_level.value,
                    "requires_approval": entry.spec.require_approval,
                },
            )
            if not decision.allowed:
                self._audit.log(name, kwargs or {}, None, False, caller)
                raise PermissionError(f"Tool call blocked by runtime policy: {decision.reason}")
        except ImportError:
            pass

        current_permission = self.get_current_permission()
        current_level = self._LEVEL_ORDER.get(current_permission, 0)
        required_level = self._LEVEL_ORDER.get(entry.spec.permission_level, 0)
        if current_level < required_level:
            self._audit.log(name, kwargs or {}, None, False, caller)
            raise PermissionError(
                f"权限不足: 工具 '{name}' 需要 {entry.spec.permission_level.value} 权限，"
                f"当前上下文权限为 {current_permission.value}"
            )

        if entry.spec.require_approval:
            if approval_id is None:
                raise PermissionError(f"工具 '{name}' 需要审批，必须提供 approval_id")
            if not self._approval_mgr.is_approved(approval_id):
                self._audit.log(name, kwargs or {}, None, False, caller)
                raise PermissionError(f"审批未通过: {approval_id}")

        kw = dict(kwargs or {})
        if entry.pydantic_model is not None:
            try:
                validated = entry.pydantic_model(**kw)
                kw = validated.model_dump()
            except Exception as e:
                self._audit.log(name, kwargs or {}, None, False, caller)
                raise ValueError(f"工具 {name} 参数错误: {e}") from e
        try:
            result = entry.handler(**kw)
            if metrics_collector is not None:
                metrics_collector.record_tool_call(tool_name=name, success=True)
            self._audit.log(name, kwargs or {}, result, True, caller)
            return result
        except Exception as e:
            if metrics_collector is not None:
                metrics_collector.record_tool_call(tool_name=name, success=False)
            self._audit.log(name, kwargs or {}, e, False, caller)
            logger.exception("调用工具 %s 失败：%s", name, e)
            raise

    # ------------------------------------------------------------------ #
    #  审计 & 审批访问
    # ------------------------------------------------------------------ #
    def get_audit_records(self, tool_name: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        return self._audit.get_records(tool_name, limit)

    @property
    def approval_manager(self) -> ToolApprovalManager:
        return self._approval_mgr


# ====================================================================== #
#  内部辅助函数
# ====================================================================== #

_PY_TYPE_TO_JSON: Dict[Any, str] = {
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
        fname = pname_clean(p.name)
        if not p.required:
            ftype = Optional[ftype]
            fields[fname] = (ftype, Field(default=p.default, description=p.description))
        else:
            fields[fname] = (ftype, Field(..., description=p.description))
    model_name = f"{tool_name}_args".replace("-", "_")
    return create_model(model_name, **fields)


def pname_clean(name: str) -> str:
    if name.isidentifier():
        return name
    clean = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    if not clean or not clean[0].isalpha():
        clean = "f_" + clean
    return clean


# ====================================================================== #
#  全局单例 & 内置工具
# ====================================================================== #

tool_registry = ToolRegistry()


@tool_registry.register(
    description="对 Python 代码做 AST 安全校验，返回结构化结果。", permission_level=ToolPermissionLevel.FREE
)
def validate_code(source: str, allowed_modules: str = "") -> str:
    from app.tools.ast_validator import validate_python_source

    result = validate_python_source(source)
    return json.dumps(result.as_dict(), ensure_ascii=False, indent=2)


@tool_registry.register(
    description="在项目根目录下安全地创建/覆盖文件，禁止路径穿越。", permission_level=ToolPermissionLevel.MODIFY
)
def write_file(path: str, content: str) -> str:
    _check_write_blacklist(path)
    from app.tools.ast_validator import write_file_safely

    try:
        p = write_file_safely(path, content)
        return f"OK: wrote {len(content.encode('utf-8'))} bytes to {p}"
    except Exception as e:
        return f"ERROR: {e}"


@tool_registry.register(description="读取指定相对路径文件的内容。", permission_level=ToolPermissionLevel.OBSERVE)
def read_file(path: str) -> str:
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
    "ToolAudit",
    "ToolApprovalManager",
    "tool_registry",
]
