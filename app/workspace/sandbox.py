"""Docker / Local sandbox for safe code execution.

借鉴 OpenHands V1 workspace 设计：
  - Local mode：直接在子进程中运行（快速开发）
  - Docker mode：在隔离容器中运行（生产安全）
  - 统一 ExecutionResult 接口，上层无需感知模式
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & Data Classes
# ---------------------------------------------------------------------------

class SandboxMode(str, Enum):
    LOCAL = "local"
    DOCKER = "docker"


@dataclass
class SandboxConfig:
    mode: SandboxMode = SandboxMode.LOCAL
    image: str = "python:3.11-slim"
    timeout_seconds: int = 30
    max_memory_mb: int = 512
    max_output_bytes: int = 1024 * 1024
    network_enabled: bool = False
    read_only_root: bool = True
    work_dir: str = "/sandbox"
    allowed_modules: tuple[str, ...] = ()
    env_vars: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_app_config(cls) -> "SandboxConfig":
        """Build from global app settings (app.core.config.settings)."""
        try:
            from app.core.config import settings as s
            return cls(
                mode=SandboxMode(s.sandbox_mode) if getattr(s, "sandbox_mode", None) else SandboxMode.LOCAL,
                image=getattr(s, "sandbox_image", "python:3.11-slim"),
                timeout_seconds=getattr(s, "sandbox_timeout", 30),
                max_memory_mb=getattr(s, "sandbox_max_memory_mb", 512),
                network_enabled=getattr(s, "sandbox_network_enabled", False),
            )
        except Exception:
            return cls()


@dataclass
class ExecutionResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    return_code: int = -1
    elapsed_ms: float = 0.0
    truncated: bool = False
    sandbox_id: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Sandbox Executor
# ---------------------------------------------------------------------------

class SandboxExecutor:
    """Unified executor — local subprocess or Docker container."""

    def __init__(self, config: Optional[SandboxConfig] = None):
        self.config = config or SandboxConfig.from_app_config()

    # -- public API ----------------------------------------------------------

    def execute(self, code: str, *, filename: str = "main.py", env: Optional[dict] = None) -> ExecutionResult:
        """Synchronous execute (wraps async for convenience)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.aexecute(code, filename=filename, env=env))
        raise RuntimeError("Use aexecute() inside async context — cannot nest event loops.")

    async def aexecute(
        self, code: str, *, filename: str = "main.py", env: Optional[dict] = None
    ) -> ExecutionResult:
        """Asynchronous execute — primary API."""
        from app.core.runtime_policy import ActionKind, runtime_policy

        decision = runtime_policy.allow(
            ActionKind.EXECUTE,
            actor="sandbox",
            context={"mode": self.config.mode.value, "filename": filename, "bytes": len(code)},
        )
        if not decision.allowed:
            return ExecutionResult(
                success=False,
                error=f"Sandbox execution blocked by policy: {decision.reason}",
                sandbox_id=_sandbox_id(),
            )

        if self.config.mode == SandboxMode.DOCKER:
            return await self._docker_execute(code, filename, env)
        return await self._local_execute(code, filename, env)

    # -- local ---------------------------------------------------------------

    async def _local_execute(self, code: str, filename: str, env: Optional[dict]) -> ExecutionResult:
        sid = _sandbox_id()
        tmp = tempfile.mkdtemp(prefix="neuroforge_sb_")
        code_path = Path(tmp) / filename
        try:
            code_path.write_text(code, encoding="utf-8")
            t0 = time.perf_counter()

            proc = await asyncio.create_subprocess_exec(
                "python", str(code_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(code_path.parent),
                env={**os.environ, **(env or {})},
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=self.config.timeout_seconds
            )
            elapsed = (time.perf_counter() - t0) * 1000

            stdout = self._safe_decode(stdout_bytes)
            stderr = self._safe_decode(stderr_bytes)
            stdout, truncated = self._truncate(stdout)

            return ExecutionResult(
                success=proc.returncode == 0,
                stdout=stdout,
                stderr=stderr,
                return_code=proc.returncode or 0,
                elapsed_ms=round(elapsed, 2),
                truncated=truncated,
                sandbox_id=sid,
            )
        except asyncio.TimeoutError:
            return ExecutionResult(
                success=False,
                error=f"Execution timed out after {self.config.timeout_seconds}s",
                sandbox_id=sid,
            )
        except Exception as exc:
            logger.exception("Local sandbox execution failed")
            return ExecutionResult(success=False, error=str(exc), sandbox_id=sid)
        finally:
            _rmtree(tmp)

    # -- docker --------------------------------------------------------------

    async def _docker_execute(self, code: str, filename: str, env: Optional[dict]) -> ExecutionResult:
        sid = _sandbox_id()
        tmp = tempfile.mkdtemp(prefix="neuroforge_docker_")
        code_path = Path(tmp) / filename
        try:
            code_path.write_text(code, encoding="utf-8")

            # Build docker run command
            cmd = [
                "docker", "run", "--rm",
                f"--name=nf-sandbox-{sid}",
                f"--memory={self.config.max_memory_mb}m",
                f"--cpus=1",
                f"--network={'none' if not self.config.network_enabled else 'bridge'}",
                f"--read-only={str(self.config.read_only_root).lower()}",
                f"--tmpfs=/tmp:exec,size=256m",
                f"-v", f"{code_path.parent}:{self.config.work_dir}:ro",
                f"-w", self.config.work_dir,
                *self._docker_env_args(env),
                self.config.image,
                "python", str(Path(self.config.work_dir) / filename),
            ]

            t0 = time.perf_counter()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=self.config.timeout_seconds
            )
            elapsed = (time.perf_counter() - t0) * 1000

            stdout = self._safe_decode(stdout_bytes)
            stderr = self._safe_decode(stderr_bytes)
            stdout, truncated = self._truncate(stdout)

            return ExecutionResult(
                success=proc.returncode == 0,
                stdout=stdout,
                stderr=stderr,
                return_code=proc.returncode or 0,
                elapsed_ms=round(elapsed, 2),
                truncated=truncated,
                sandbox_id=sid,
            )
        except asyncio.TimeoutError:
            # Force kill the container
            try:
                await asyncio.create_subprocess_exec("docker", "kill", f"nf-sandbox-{sid}")
            except Exception:
                pass
            return ExecutionResult(
                success=False,
                error=f"Docker execution timed out after {self.config.timeout_seconds}s",
                sandbox_id=sid,
            )
        except FileNotFoundError:
            return ExecutionResult(
                success=False,
                error="Docker is not installed or not in PATH. Use SandboxMode.LOCAL instead.",
                sandbox_id=sid,
            )
        except Exception as exc:
            logger.exception("Docker sandbox execution failed")
            return ExecutionResult(success=False, error=str(exc), sandbox_id=sid)
        finally:
            _rmtree(tmp)

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _safe_decode(data: bytes) -> str:
        try:
            return data.decode("utf-8", errors="replace")
        except Exception:
            return ""

    def _truncate(self, text: str) -> tuple[str, bool]:
        if len(text) > self.config.max_output_bytes:
            return text[: self.config.max_output_bytes] + "\n... [OUTPUT TRUNCATED]", True
        return text, False

    def _docker_env_args(self, env: Optional[dict]) -> list[str]:
        args = []
        merged = {**self.config.env_vars, **(env or {})}
        for k, v in merged.items():
            args.extend(["-e", f"{k}={v}"])
        return args


# ---------------------------------------------------------------------------
# utilities
# ---------------------------------------------------------------------------

def _sandbox_id() -> str:
    return uuid.uuid4().hex[:8]


def _rmtree(path: str) -> None:
    import shutil
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass
