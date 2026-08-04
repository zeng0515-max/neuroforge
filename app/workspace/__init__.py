"""Workspace isolation layer — Local / Docker sandbox code execution.

Design principle (借鉴 OpenHands V1):
  Optional isolation: Agents run locally by default, with transparent
  opt-in Docker sandboxing for safety. No mandatory container overhead.
"""

from .sandbox import SandboxConfig, SandboxExecutor, ExecutionResult, SandboxMode

__all__ = [
    "SandboxConfig",
    "SandboxExecutor",
    "ExecutionResult",
    "SandboxMode",
]
