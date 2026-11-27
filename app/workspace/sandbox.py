"""Sandbox for safe code execution (Docker-first, hardened local fallback).

Isolation boundary, stated honestly:

- ``docker`` mode is the only mode with OS-level isolation (namespaces, read-only
  root, memory/pids limits, no network). Use it in production.
- ``local`` mode is *process-level soft isolation*: trimmed environment, blocked
  network via an in-process socket monkeypatch, resource limits and a hard
  timeout. It is **not** a security boundary — code that uses ``ctypes``,
  re-imports ``socket`` or spawns a subprocess can escape the network block.
  Treat it as a development convenience, never as a jail.

Every execution also passes ``runtime_policy`` (kill switch, default rules) and
policy denials are persisted to the audit log by ``RuntimePolicy._persist_denial``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


SANDBOX_SAFE_ENV_VARS = {
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "PYTHONIOENCODING",
    "PYTHONUNBUFFERED",
}

_LOCAL_NETWORK_GUARD = (
    "import socket, runpy, sys\n"
    "class _SandboxNetworkDisabled(PermissionError):\n"
    "    pass\n"
    "def _blocked_socket(*args, **kwargs):\n"
    "    raise _SandboxNetworkDisabled('network access is disabled in local sandbox')\n"
    "socket.socket = _blocked_socket\n"
    "sys.argv = [sys.argv[1], *sys.argv[2:]]\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n"
)


class SandboxMode(str, Enum):
    LOCAL = "local"
    DOCKER = "docker"


@dataclass
class SandboxConfig:
    mode: SandboxMode = SandboxMode.DOCKER
    image: str = "python:3.11-slim"
    timeout_seconds: int = 30
    max_memory_mb: int = 512
    max_processes: int = 64
    max_output_bytes: int = 1024 * 1024
    network_enabled: bool = False
    read_only_root: bool = True
    work_dir: str = "/sandbox"
    allowed_modules: tuple[str, ...] = ()
    env_vars: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_app_config(cls) -> "SandboxConfig":
        try:
            from app.core.config import settings as s

            sandbox = getattr(s, "sandbox", s)
            return cls(
                mode=SandboxMode(getattr(sandbox, "mode", "docker")),
                image=getattr(sandbox, "image", "python:3.11-slim"),
                timeout_seconds=getattr(sandbox, "timeout", 30),
                max_memory_mb=getattr(sandbox, "max_memory_mb", 512),
                max_processes=getattr(sandbox, "max_processes", 64),
                network_enabled=getattr(sandbox, "network_enabled", False),
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


class SandboxExecutor:
    """Unified executor with a Docker-first policy and hardened local fallback."""

    def __init__(self, config: Optional[SandboxConfig] = None):
        self.config = config or SandboxConfig.from_app_config()

    def execute(self, code: str, *, filename: str = "main.py", env: Optional[dict] = None) -> ExecutionResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.aexecute(code, filename=filename, env=env))
        raise RuntimeError("Use aexecute() inside async context; cannot nest event loops.")

    async def aexecute(
        self, code: str, *, filename: str = "main.py", env: Optional[dict] = None
    ) -> ExecutionResult:
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
            result = await self._docker_execute(code, filename, env)
            if result.error and "Docker is not installed" in result.error:
                logger.warning("Docker is unavailable; falling back to hardened local sandbox")
                return await self._local_execute(code, filename, env)
            return result
        return await self._local_execute(code, filename, env)

    async def _local_execute(self, code: str, filename: str, env: Optional[dict]) -> ExecutionResult:
        sid = _sandbox_id()
        tmp = tempfile.mkdtemp(prefix="neuroforge_sb_")
        code_path = Path(tmp) / filename
        proc = None
        try:
            code_path.write_text(code, encoding="utf-8")
            t0 = time.perf_counter()
            safe_env = self._build_safe_env(env)

            proc = await asyncio.create_subprocess_exec(
                *self._local_command(code_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(code_path.parent),
                env=safe_env,
                **self._local_process_kwargs(),
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=self.config.timeout_seconds
            )
            elapsed = (time.perf_counter() - t0) * 1000

            stdout = self._safe_decode(stdout_bytes)
            stderr = self._safe_decode(stderr_bytes)
            stdout, stdout_truncated = self._truncate(stdout)
            stderr, stderr_truncated = self._truncate(stderr)

            return_code = proc.returncode or 0
            error = None
            if return_code < 0:
                # POSIX: the child died to a signal (SIGXCPU from RLIMIT_CPU,
                # SIGKILL from a resource limit, ...). Without this the caller
                # only saw success=False with no explanation.
                error = f"process terminated by signal {-return_code}"
            elif return_code != 0:
                # A non-zero exit with no explanation is useless to the caller;
                # surface the tail of stderr as the reason.
                detail = (stderr or "").strip().splitlines()
                reason = detail[-1] if detail else ""
                error = f"process exited with code {return_code}" + (f": {reason}" if reason else "")

            return ExecutionResult(
                success=return_code == 0,
                stdout=stdout,
                stderr=stderr,
                return_code=return_code,
                elapsed_ms=round(elapsed, 2),
                truncated=stdout_truncated or stderr_truncated,
                error=error,
                sandbox_id=sid,
            )
        except asyncio.TimeoutError:
            if proc is not None:
                await self._terminate_process(proc)
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

    async def _docker_execute(self, code: str, filename: str, env: Optional[dict]) -> ExecutionResult:
        sid = _sandbox_id()
        tmp = tempfile.mkdtemp(prefix="neuroforge_docker_")
        code_path = Path(tmp) / filename
        try:
            code_path.write_text(code, encoding="utf-8")
            cmd = self._docker_cmd(code_path, sid, env)
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
            stdout, stdout_truncated = self._truncate(stdout)
            stderr, stderr_truncated = self._truncate(stderr)

            return ExecutionResult(
                success=proc.returncode == 0,
                stdout=stdout,
                stderr=stderr,
                return_code=proc.returncode or 0,
                elapsed_ms=round(elapsed, 2),
                truncated=stdout_truncated or stderr_truncated,
                sandbox_id=sid,
            )
        except asyncio.TimeoutError:
            try:
                kill_proc = await asyncio.create_subprocess_exec(
                    "docker", "kill", f"nf-sandbox-{sid}"
                )
                await kill_proc.communicate()
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

    def _docker_cmd(self, code_path: Path, sid: str, env: Optional[dict]) -> list[str]:
        network = "none" if not self.config.network_enabled else "bridge"
        return [
            "docker", "run", "--rm",
            f"--name=nf-sandbox-{sid}",
            f"--memory={self.config.max_memory_mb}m",
            "--cpus=1.0",
            f"--pids-limit={self.config.max_processes}",
            f"--network={network}",
            f"--read-only={str(self.config.read_only_root).lower()}",
            "--tmpfs=/tmp:exec,size=256m",
            "-v", f"{code_path.parent}:{self.config.work_dir}:ro",
            "-w", self.config.work_dir,
            *self._docker_env_args(env),
            self.config.image,
            "python", str(Path(self.config.work_dir) / code_path.name),
        ]

    def _build_safe_env(self, env: Optional[dict]) -> dict[str, str]:
        safe = {k: v for k, v in os.environ.items() if k in SANDBOX_SAFE_ENV_VARS}
        for key, value in (env or {}).items():
            if key in SANDBOX_SAFE_ENV_VARS:
                safe[key] = str(value)
        safe["PYTHONDONTWRITEBYTECODE"] = "1"
        safe["PYTHONUNBUFFERED"] = "1"
        return safe

    def _docker_env_args(self, env: Optional[dict]) -> list[str]:
        args = []
        merged: dict[str, str] = {}
        for key, value in self.config.env_vars.items():
            if key in SANDBOX_SAFE_ENV_VARS:
                merged[key] = value
        for key, value in (env or {}).items():
            if key in SANDBOX_SAFE_ENV_VARS:
                merged[key] = value
        for key, value in merged.items():
            args.extend(["-e", f"{key}={value}"])
        return args

    def _local_process_kwargs(self) -> dict[str, Any]:
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
            return {"creationflags": creationflags}
        return {
            "preexec_fn": self._posix_limit_resources,
            "start_new_session": True,
        }

    def _local_command(self, code_path: Path) -> list[str]:
        return [sys.executable, "-c", _LOCAL_NETWORK_GUARD, str(code_path)]

    async def _terminate_process(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        if sys.platform == "win32":
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/PID", str(proc.pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.communicate()
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:
            pass

    def _posix_limit_resources(self) -> None:
        try:
            import resource

            memory_limit = self.config.max_memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
        except Exception:
            pass
        try:
            import resource

            # CPU-time backstop *above* the wall-clock timeout: a sleeping or
            # blocked child accumulates no CPU time, so this must never be the
            # thing that fires first (that would kill the child with SIGXCPU at
            # the same second as asyncio.wait_for and hide the real timeout).
            cpu_limit = max(1, self.config.timeout_seconds) + 5
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 1))
        except Exception:
            pass
        # RLIMIT_NPROC is deliberately NOT set here: it counts processes for the
        # whole UID, not for this process tree. On a shared host (e.g. a CI
        # runner user that already runs dozens of processes) a small value makes
        # every fork fail immediately instead of limiting the sandbox. The
        # process-count limit is enforced in docker mode via --pids-limit.

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


def _sandbox_id() -> str:
    return uuid.uuid4().hex[:8]


def _rmtree(path: str) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


__all__ = [
    "ExecutionResult",
    "SandboxConfig",
    "SandboxExecutor",
    "SandboxMode",
    "SANDBOX_SAFE_ENV_VARS",
]
