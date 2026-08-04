"""WebSocket streaming for real-time pipeline visualization.

Provides a duplex WebSocket endpoint that pushes pipeline state updates
in real-time — each Agent node transition, event, and output streamed
to the connected frontend dashboard.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.events import event_bus, EventType, Event
from app.core.config import settings
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
    asyncio.ensure_future(ws_manager.broadcast(tid, payload))


# Register bridge (idempotent — subscribe to all event types)
for _et in EventType:
    event_bus.subscribe(_et, _on_event)

# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/pipeline/{task_id}")
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
