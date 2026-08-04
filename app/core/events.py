"""事件驱动架构：EventBus 单例，支持订阅/发布模式。

内置事件类型覆盖流水线全生命周期：
- TASK_STARTED：任务启动
- NODE_COMPLETED：节点执行完成
- NODE_FAILED：节点执行失败
- HITL_WAITING：进入人工审批等待
- HITL_APPROVED：人工审批通过
- HITL_REJECTED：人工审批被拒
- PIPELINE_DONE：流水线正常完成
- PIPELINE_FAILED：流水线执行失败
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """预定义事件类型。"""

    TASK_STARTED = "task_started"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"
    HITL_WAITING = "hitl_waiting"
    HITL_APPROVED = "hitl_approved"
    HITL_REJECTED = "hitl_rejected"
    PIPELINE_DONE = "pipeline_done"
    PIPELINE_FAILED = "pipeline_failed"


@dataclass
class Event:
    """事件数据容器。"""

    type: EventType
    data: Dict[str, Any] = field(default_factory=dict)
    task_id: str = ""


# 事件处理器类型签名
EventHandler = Callable[[Event], None]


class EventBus:
    """线程安全的事件总线，支持订阅/发布。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: Dict[EventType, List[EventHandler]] = {}

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """订阅指定类型的事件。

        Args:
            event_type: 事件类型
            handler: 事件处理器，接收 Event 对象
        """
        with self._lock:
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            self._handlers[event_type].append(handler)
            logger.debug("事件订阅: %s (handler=%s)", event_type.value, handler.__name__)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """取消订阅。"""
        with self._lock:
            if event_type in self._handlers:
                self._handlers[event_type] = [
                    h for h in self._handlers[event_type] if h is not handler
                ]

    def emit(self, event_type: EventType, data: Optional[Dict[str, Any]] = None, task_id: str = "") -> None:
        """发布事件，同步调用所有已注册的处理器。

        Args:
            event_type: 事件类型
            data: 事件携带的数据
            task_id: 关联的任务 ID
        """
        event = Event(type=event_type, data=data or {}, task_id=task_id)
        handlers = list(self._handlers.get(event_type, []))
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                logger.exception("事件处理器 %s 执行失败: %s", handler.__name__, e)
        logger.debug("事件发布: %s (task_id=%s, handlers=%d)", event_type.value, task_id, len(handlers))

    def clear(self) -> None:
        """清空所有订阅。"""
        with self._lock:
            self._handlers.clear()


# ====================================================================== #
#  全局单例
# ====================================================================== #

event_bus = EventBus()


__all__ = [
    "EventType",
    "Event",
    "EventBus",
    "event_bus",
]