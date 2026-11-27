"""WebSocket streaming for real-time pipeline visualization.

Provides a duplex WebSocket endpoint that pushes pipeline state updates
in real-time — each Agent node transition, event, and output streamed
to the connected frontend dashboard.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.core.events import event_bus, EventType, Event
from app.core.config import settings
from app.core.auth import verify_api_key_ws
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

# ---------------------------------------------------------------------------
# Connection registry
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Track active WebSocket connections. One task_id = one room."""

    def __init__(self):
        self._rooms: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def join(self, task_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._rooms.setdefault(task_id, set()).add(ws)
            logger.debug("WS joined room %s (total %d)", task_id, len(self._rooms[task_id]))

    async def leave(self, task_id: str, ws: WebSocket) -> None:
        async with self._lock:
            if task_id in self._rooms:
                self._rooms[task_id].discard(ws)
                if not self._rooms[task_id]:
                    del self._rooms[task_id]
            logger.debug("WS left room %s", task_id)

    async def broadcast(self, task_id: str, payload: dict) -> None:
        async with self._lock:
            sockets = list(self._rooms.get(task_id, set()))
        if not sockets:
            return
        text = json.dumps(payload, ensure_ascii=False, default=str)
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                if task_id in self._rooms:
                    self._rooms[task_id].difference_update(dead)

    @property
    def active_rooms(self) -> list[str]:
        return list(self._rooms.keys())

    @property
    def connection_count(self) -> int:
        return sum(len(s) for s in self._rooms.values())


ws_manager = ConnectionManager()

_bridge_loop: Optional[asyncio.AbstractEventLoop] = None
_bridge_task: Optional[asyncio.Task] = None
_bridge_lock = threading.Lock()
_pending_payloads: queue.Queue[dict] = queue.Queue()
_bridge_registered = False

# ---------------------------------------------------------------------------
# Event bridge — pipe pipeline events to WebSocket rooms
# ---------------------------------------------------------------------------

_EVENT_TO_STAGE: dict[str, str] = {
    EventType.TASK_STARTED: "task_started",
    EventType.NODE_COMPLETED: "node_completed",
    EventType.NODE_FAILED: "node_failed",
    EventType.HITL_WAITING: "hitl_waiting",
    EventType.HITL_APPROVED: "hitl_approved",
    EventType.HITL_REJECTED: "hitl_rejected",
    EventType.PIPELINE_DONE: "pipeline_done",
    EventType.PIPELINE_FAILED: "pipeline_failed",
}

# Subscribe to EventBus — forward to WebSocket rooms by task_id
def _on_event(event: Event) -> None:
    tid = event.task_id or event.data.get("task_id", "")
    if not tid:
        return
    event_type = event.type.value if isinstance(event.type, EventType) else event.type
    stage = _EVENT_TO_STAGE.get(event_type, "unknown")
    payload = {
        "type": "event",
        "event_type": event_type,
        "stage": stage,
        "task_id": tid,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": event.data,
    }
    _schedule_bridge_payload(payload)


# Register bridge (idempotent — subscribe to all event types)
def _schedule_bridge_payload(payload: dict) -> None:
    loop = _bridge_loop
    if loop is not None and not loop.is_closed():
        try:
            asyncio.run_coroutine_threadsafe(
                ws_manager.broadcast(payload.get("task_id", ""), payload),
                loop,
            )
            return
        except RuntimeError:
            pass
    _pending_payloads.put(payload)


def _subscribe_bridge() -> None:
    global _bridge_registered
    for _et in EventType:
        event_bus.subscribe(_et, _on_event)
    _bridge_registered = True


def _ensure_bridge(loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
    global _bridge_loop, _bridge_task, _bridge_registered
    loop = loop or asyncio.get_running_loop()
    with _bridge_lock:
        if _bridge_loop is None or _bridge_loop.is_closed():
            _bridge_loop = loop
            _bridge_task = loop.create_task(_bridge_worker())
    if not _bridge_registered:
        _subscribe_bridge()


def start_ws_event_bridge(*, force: bool = False) -> None:
    global _bridge_registered
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        return
    _ensure_bridge(loop)
    if force:
        _subscribe_bridge()


async def stop_ws_event_bridge() -> None:
    global _bridge_loop, _bridge_task
    task = _bridge_task
    with _bridge_lock:
        _bridge_loop = None
        _bridge_task = None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _bridge_worker() -> None:
    while True:
        try:
            while True:
                payload = _pending_payloads.get_nowait()
                await ws_manager.broadcast(payload.get("task_id", ""), payload)
        except queue.Empty:
            pass
        await asyncio.sleep(0.05)

# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/pipeline/{task_id}", dependencies=[Depends(verify_api_key_ws)])
async def pipeline_websocket(ws: WebSocket, task_id: str):
    """Stream pipeline execution events for a specific task.

    Connect with: ws://host:8001/api/v1/ws/pipeline/{task_id}

    Messages received from server:
      {"type":"event","stage":"node_running","task_id":"...","data":{...}}
      {"type":"ping","ts":"..."}

    Messages sent by client:
      {"type":"subscribe"}
      {"type":"pong"}
    """
    _ensure_bridge(asyncio.get_running_loop())
    await ws.accept()
    await ws_manager.join(task_id, ws)

    # Send initial connection confirmation
    await ws.send_json({
        "type": "connected",
        "task_id": task_id,
        "active_connections": ws_manager.connection_count,
    })

    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=30)
                data = json.loads(msg)

                if data.get("type") == "ping" or data.get("type") == "subscribe":
                    await ws.send_json({"type": "pong", "ts": str(uuid.uuid4())})
                elif data.get("type") == "history":
                    # Client requests execution history — could query DB here
                    await ws.send_json({"type": "history", "events": []})

            except asyncio.TimeoutError:
                # Send heartbeat
                try:
                    await ws.send_json({"type": "ping"})
                except Exception:
                    break

    except WebSocketDisconnect:
        logger.debug("WS disconnected from room %s", task_id)
    except Exception:
        logger.exception("WS error in room %s")
    finally:
        await ws_manager.leave(task_id, ws)


# ---------------------------------------------------------------------------
# REST endpoint for connection stats
# ---------------------------------------------------------------------------

@router.get("/ws/stats")
async def ws_stats():
    return {
        "active_rooms": ws_manager.active_rooms,
        "connection_count": ws_manager.connection_count,
    }
