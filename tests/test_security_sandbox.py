from __future__ import annotations

import asyncio
import os
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, WebSocketException
from starlette.requests import Request

from app.core import auth
from app.core.events import Event, EventType
from app.core.memory_system import EpisodicMemory, WorkingMemory
from app.graph import pipeline as pipeline_module
from app.main import _resolve_cors_config
from app.workspace.sandbox import SandboxConfig, SandboxExecutor, SandboxMode


def _http_request(path: str = "/api/v1/tasks") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 1234),
        }
    )


def test_production_missing_api_key_rejects_rest(monkeypatch) -> None:
    monkeypatch.setattr(auth.settings.app, "env", "production")
    monkeypatch.setattr(auth.settings.security, "api_key", "")

    async def _run() -> None:
        with pytest.raises(HTTPException) as exc:
            await auth.verify_api_key(_http_request())
        assert exc.value.status_code == 503

    asyncio.run(_run())


def test_dev_missing_api_key_still_allows_rest(monkeypatch) -> None:
    monkeypatch.setattr(auth.settings.app, "env", "dev")
    monkeypatch.setattr(auth.settings.security, "api_key", "")

    async def _run() -> None:
        await auth.verify_api_key(_http_request())

    asyncio.run(_run())


def test_websocket_missing_api_key_rejects_in_production(monkeypatch) -> None:
    monkeypatch.setattr(auth.settings.app, "env", "production")
    monkeypatch.setattr(auth.settings.security, "api_key", "")
    fake_ws = SimpleNamespace(url=SimpleNamespace(path="/api/v1/ws/pipeline/t1"))

    async def _run() -> None:
        with pytest.raises(WebSocketException) as exc:
            await auth.verify_api_key_ws(fake_ws)  # type: ignore[arg-type]
        assert exc.value.code == 1008

    asyncio.run(_run())


def test_cors_never_combines_wildcard_with_credentials(monkeypatch) -> None:
    monkeypatch.setattr(auth.settings.security, "cors_origins", ["*"])
    origins, allow_credentials = _resolve_cors_config()
    assert origins == ["*"]
    assert allow_credentials is False

    monkeypatch.setattr(
        auth.settings.security,
        "cors_origins",
        ["https://app.example.com", "http://localhost:3000"],
    )
    origins, allow_credentials = _resolve_cors_config()
    assert origins == ["https://app.example.com", "http://localhost:3000"]
    assert allow_credentials is True


def test_sandbox_env_does_not_expose_llm_key(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "super-secret")
    executor = SandboxExecutor(SandboxConfig(mode=SandboxMode.LOCAL))
    safe = executor._build_safe_env({"LLM_API_KEY": "caller-secret", "TMP": "/tmp"})
    assert "LLM_API_KEY" not in safe
    assert "super-secret" not in safe.values()
    assert safe.get("TMP") == "/tmp"


def test_generated_code_cannot_read_llm_key(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "super-secret")
    executor = SandboxExecutor(SandboxConfig(mode=SandboxMode.LOCAL, timeout_seconds=10))
    result = asyncio.run(
        executor.aexecute("import os\nprint(repr(os.environ.get('LLM_API_KEY')))")
    )
    assert result.success
    assert "super-secret" not in result.stdout


def test_docker_default_and_isolation_flags() -> None:
    assert SandboxConfig().mode == SandboxMode.DOCKER
    config = SandboxConfig(
        mode=SandboxMode.DOCKER,
        max_memory_mb=256,
        max_processes=17,
        network_enabled=False,
    )
    cmd = SandboxExecutor(config)._docker_cmd(Path("/tmp/main.py"), "sid", None)
    assert "--memory=256m" in cmd
    assert "--cpus=1.0" in cmd
    assert "--pids-limit=17" in cmd
    assert "--network=none" in cmd


def test_working_memory_thread_safety() -> None:
    memory = WorkingMemory(capacity=200)

    def write(i: int) -> None:
        memory.add_item(f"k{i}", i)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(1000)))

    assert len(memory) <= 200
    assert memory.get_item("k999") is None or memory.get_item("k999") == 999


def test_episodic_memory_thread_safety() -> None:
    memory = EpisodicMemory(max_events=100)

    def record(i: int) -> None:
        memory.record_event("decision", {"i": i})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record, range(500)))

    assert len(memory) <= 100
    assert all(event.data.get("i") is not None for event in memory.query_events())


def test_delayed_cleanup_removes_stream_events(monkeypatch) -> None:
    from app.api.v1 import tasks as tasks_module

    monkeypatch.setattr(tasks_module, "_stream_events", {"t1": [{}], "keep": [{}]})
    monkeypatch.setattr(tasks_module, "_final_states", {"t1": {}, "keep": {}})
    monkeypatch.setattr(tasks_module, "_done_events", {"t1": asyncio.Event(), "keep": asyncio.Event()})
    monkeypatch.setattr(tasks_module, "_running", set())
    monkeypatch.setattr(tasks_module, "_lock", asyncio.Lock())

    asyncio.run(tasks_module._delayed_cleanup("t1", delay=0))

    assert "t1" not in tasks_module._stream_events
    assert "keep" in tasks_module._stream_events


def test_resume_rejects_stream_task_without_checkpoint(monkeypatch) -> None:
    from app.api.v1 import tasks as tasks_module

    task = MagicMock(status="running", user_request="x", phase="code_gen")
    monkeypatch.setattr(tasks_module, "has_checkpoint", lambda task_id: False)
    monkeypatch.setattr(tasks_module.db, "get_task", lambda task_id: task)

    async def _run() -> None:
        with pytest.raises(HTTPException) as exc:
            await tasks_module.resume_task("stream-task")
        assert exc.value.status_code == 409

    asyncio.run(_run())


def test_checkpointer_is_per_thread(monkeypatch) -> None:
    class FakeConnection:
        def close(self) -> None:
            pass

    class FakeSaver:
        def __init__(self, conn):
            self.conn = conn

    monkeypatch.setattr(pipeline_module, "_SQLITE_SAVER_AVAILABLE", True)
    monkeypatch.setattr(pipeline_module, "SqliteSaver", FakeSaver)
    monkeypatch.setattr(pipeline_module.sqlite3, "connect", lambda *args, **kwargs: FakeConnection())
    pipeline_module._checkpointer_local = threading.local()
    pipeline_module._checkpointer_connections.clear()

    results: dict[str, FakeSaver] = {}

    def get_saver() -> None:
        results[threading.current_thread().name] = pipeline_module.get_checkpointer()

    threads = [threading.Thread(target=get_saver) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 2
    savers = list(results.values())
    assert savers[0] is not savers[1]


def test_ws_bridge_without_loop_enqueues(monkeypatch) -> None:
    from app.api.v1 import websocket as ws_module

    pending: queue.Queue[dict] = queue.Queue()
    monkeypatch.setattr(ws_module, "_bridge_loop", None)
    monkeypatch.setattr(ws_module, "_pending_payloads", pending)

    ws_module._on_event(Event(type=EventType.NODE_COMPLETED, data={"task_id": "t1"}, task_id="t1"))

    assert pending.qsize() == 1
    assert pending.get_nowait()["task_id"] == "t1"


def test_ws_stats_http_endpoint_is_not_ws_protected(client) -> None:
    response = client.get("/api/v1/ws/stats")
    assert response.status_code == 200


def test_local_timeout_kills_child_process_tree(tmp_path) -> None:
    psutil = pytest.importorskip("psutil")
    pid_file = tmp_path / "child.pid"
    code = f"""
import pathlib
import subprocess
import sys
import time

child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))
time.sleep(30)
"""
    executor = SandboxExecutor(
        SandboxConfig(mode=SandboxMode.LOCAL, timeout_seconds=1, max_processes=8)
    )
    result = asyncio.run(executor.aexecute(code))

    assert not result.success
    assert "timed out" in result.error
    assert pid_file.exists()

    child_pid = int(pid_file.read_text())
    deadline = time.time() + 2
    while psutil.pid_exists(child_pid) and time.time() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(child_pid)


def test_stop_ws_bridge_cancels_worker(monkeypatch) -> None:
    from app.api.v1 import websocket as ws_module

    monkeypatch.setattr(ws_module, "_subscribe_bridge", lambda: None)

    async def _run() -> None:
        ws_module._bridge_loop = None
        ws_module._bridge_task = None
        ws_module._bridge_registered = False
        ws_module._pending_payloads = queue.Queue()

        ws_module.start_ws_event_bridge()
        assert ws_module._bridge_task is not None
        assert not ws_module._bridge_task.done()

        await ws_module.stop_ws_event_bridge()
        assert ws_module._bridge_task is None

    asyncio.run(_run())


def test_resume_pipeline_does_not_rerun_done_checkpoint(monkeypatch) -> None:
    class FakeCheckpoint:
        channel_values = {"state": {"status": {"phase": "done"}}}

    class FakeCheckpointer:
        def get_tuple(self, config):
            return FakeCheckpoint()

    class FakeApp:
        def invoke(self, *args, **kwargs):
            raise AssertionError("done checkpoint must not be replayed")

    monkeypatch.setattr(pipeline_module, "_LANGGRAPH_AVAILABLE", True)
    monkeypatch.setattr(pipeline_module, "_SQLITE_SAVER_AVAILABLE", True)
    monkeypatch.setattr(pipeline_module, "get_checkpointer", lambda: FakeCheckpointer())
    monkeypatch.setattr(pipeline_module, "build_graph", lambda checkpointer=None: FakeApp())

    assert pipeline_module.resume_pipeline(task_id="done-task") is None
