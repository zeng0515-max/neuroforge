"""Request-level isolation for tool permission levels.

The registry used to keep the level on the singleton, so a request that raised
its own permission to MODIFY leaked that level into every concurrent request.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from app.tools.tool_registry import ToolPermissionLevel, ToolRegistry


def _registry_with_tool() -> ToolRegistry:
    registry = ToolRegistry()

    @registry.register(
        description="写文件（需要 MODIFY 权限）",
        permission_level=ToolPermissionLevel.MODIFY,
    )
    def guarded_write(path: str) -> str:
        return f"wrote {path}"

    return registry


def test_permission_scope_does_not_leak_across_threads() -> None:
    registry = _registry_with_tool()
    inside_scope = threading.Event()
    other_checked = threading.Event()
    outcome: dict[str, object] = {}

    def high_privilege_worker() -> None:
        with registry.permission_scope(ToolPermissionLevel.MODIFY):
            inside_scope.set()
            other_checked.wait(timeout=10)
            outcome["high"] = registry.call("guarded_write", {"path": "a.txt"})

    def low_privilege_worker() -> None:
        inside_scope.wait(timeout=10)
        try:
            registry.call("guarded_write", {"path": "b.txt"})
        except PermissionError as exc:
            outcome["low"] = exc
        finally:
            other_checked.set()

    threads = [
        threading.Thread(target=high_privilege_worker),
        threading.Thread(target=low_privilege_worker),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    assert outcome["high"] == "wrote a.txt"
    assert isinstance(outcome["low"], PermissionError)
    # The caller's own context is untouched by a scope that lived elsewhere.
    assert registry.get_current_permission() == ToolPermissionLevel.OBSERVE


def test_permission_scope_is_restored_after_exception() -> None:
    registry = _registry_with_tool()
    assert registry.get_current_permission() == ToolPermissionLevel.OBSERVE
    with pytest.raises(RuntimeError):
        with registry.permission_scope(ToolPermissionLevel.DANGEROUS):
            assert registry.get_current_permission() == ToolPermissionLevel.DANGEROUS
            raise RuntimeError("boom")
    assert registry.get_current_permission() == ToolPermissionLevel.OBSERVE


def test_permission_scope_is_isolated_between_asyncio_tasks() -> None:
    registry = _registry_with_tool()
    results: dict[str, object] = {}

    async def privileged() -> None:
        with registry.permission_scope(ToolPermissionLevel.MODIFY):
            await asyncio.sleep(0.05)
            results["privileged"] = registry.call("guarded_write", {"path": "x"})

    async def unprivileged() -> None:
        await asyncio.sleep(0.01)
        try:
            registry.call("guarded_write", {"path": "y"})
        except PermissionError as exc:
            results["unprivileged"] = exc

    async def main() -> None:
        await asyncio.gather(privileged(), unprivileged())

    asyncio.run(main())

    assert results["privileged"] == "wrote x"
    assert isinstance(results["unprivileged"], PermissionError)


def test_default_permission_is_the_fallback_only() -> None:
    registry = _registry_with_tool()
    registry.set_default_permission(ToolPermissionLevel.MODIFY)
    assert registry.get_current_permission() == ToolPermissionLevel.MODIFY
    with registry.permission_scope(ToolPermissionLevel.OBSERVE):
        assert registry.get_current_permission() == ToolPermissionLevel.OBSERVE
        with pytest.raises(PermissionError):
            registry.call("guarded_write", {"path": "c"})
    assert registry.get_current_permission() == ToolPermissionLevel.MODIFY
    registry.set_default_permission(ToolPermissionLevel.OBSERVE)
