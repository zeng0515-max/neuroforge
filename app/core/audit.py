"""全链路审计日志：记录 Agent 系统中所有关键操作的完整审计轨迹。

Design Decisions:
- 环形缓冲区 (deque maxlen=10000) 做内存快速查询，避免频繁 DB 查询
- 可选 SQLite 持久化用于长期存储和历史回溯
- 与 EventBus 深度集成，自动捕获所有生命周期事件
- 审计事件类型独立于 EventBus 的 EventType，支持更细粒度追踪
- 线程安全：所有公共方法使用 threading.Lock 保护
"""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Deque, Dict, List, Optional

from sqlalchemy import text

from app.core.events import Event, EventType, event_bus
from app.db.sqlite_client import db

logger = logging.getLogger(__name__)


class AuditEventType(str, Enum):
    """审计事件类型：覆盖 Agent 全生命周期的关键操作。"""
    AGENT_EXECUTE = "agent_execute"
    TOOL_CALL = "tool_call"
    LLM_CALL = "llm_call"
    STATE_TRANSITION = "state_transition"
    HUMAN_APPROVAL = "human_approval"
    PIPELINE_START = "pipeline_start"
    PIPELINE_END = "pipeline_end"
    POLICY_DENIED = "policy_denied"
    POLICY_KILL_SWITCH = "policy_kill_switch"


class ActorType(str, Enum):
    """操作者类型：区分操作的发起主体。"""
    AGENT = "agent"
    USER = "user"
    SYSTEM = "system"
    TOOL = "tool"


@dataclass
class AuditEntry:
    """单条审计记录。

    Design Decisions:
    - dataclass 提供类型安全和默认值
    - input/output_data 用 Dict 存储任意结构化数据
    - duration_ms 用 float 支持毫秒级精度
    """
    event_type: AuditEventType
    actor_type: ActorType
    actor_id: str
    action: str
    timestamp: datetime = field(default_factory=datetime.now)
    input_data: Dict[str, Any] = field(default_factory=dict)
    output_data: Dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    task_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.event_type, str):
            self.event_type = AuditEventType(self.event_type)
        if isinstance(self.actor_type, str):
            self.actor_type = ActorType(self.actor_type)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "actor_type": self.actor_type.value,
            "actor_id": self.actor_id,
            "action": self.action,
            "timestamp": self.timestamp.isoformat(),
            "input_data": self.input_data,
            "output_data": self.output_data,
            "duration_ms": self.duration_ms,
            "task_id": self.task_id,
            "metadata": self.metadata,
        }


class AuditLogger:
    """生产级审计日志记录器。

    Design Decisions:
    - 内存环形缓冲区 (deque, maxlen=10000) 提供快速查询
    - 可选 SQLite 持久化用于历史回溯
    - 自动订阅 EventBus 事件转为审计条目，支持手动 log()
    - 线程安全：threading.Lock 保护所有共享状态
    - 单例模式：模块级 audit_logger 全局共享
    """

    MAX_ENTRIES = 10000

    def __init__(self, persist_to_db: bool = True) -> None:
        self._lock = threading.Lock()
        self._buffer: Deque[AuditEntry] = deque(maxlen=self.MAX_ENTRIES)
        self._persist_to_db = persist_to_db
        self._event_handlers_registered = False
        self._setup_db()
        self._register_event_handlers()
        logger.info("AuditLogger 初始化 (持久化=%s, 缓冲区=%d)", persist_to_db, self.MAX_ENTRIES)

    def _setup_db(self) -> None:
        """创建 SQLite 审计表及索引。

        Design Decisions:
        - raw SQL 避免与现有 models.py 耦合
        - event_type / task_id 双索引加速筛选
        - 失败时降级为纯内存模式
        """
        if not self._persist_to_db:
            return
        try:
            with db.engine.begin() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS audit_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_type TEXT NOT NULL, actor_type TEXT NOT NULL,
                        actor_id TEXT NOT NULL, action TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        input_data TEXT DEFAULT '{}',
                        output_data TEXT DEFAULT '{}',
                        duration_ms REAL DEFAULT 0,
                        task_id TEXT DEFAULT '',
                        metadata TEXT DEFAULT '{}'
                    )
                """))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_audit_event_type ON audit_log(event_type)"
                ))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_audit_task_id ON audit_log(task_id)"
                ))
            logger.debug("审计日志 SQLite 表初始化完成")
        except Exception as e:
            logger.warning("审计日志 SQLite 初始化失败，降级为纯内存模式: %s", e)
            self._persist_to_db = False

    def _register_event_handlers(self) -> None:
        """订阅 EventBus 事件自动转换为审计条目。

        Design Decisions:
        - lambda 默认参数 (at=audit_type) 避免闭包捕获陷阱
        - 仅注册一次防止重复订阅
        """
        if self._event_handlers_registered:
            return
        self._event_handlers_registered = True

        mapping: Dict[EventType, AuditEventType] = {
            EventType.TASK_STARTED: AuditEventType.PIPELINE_START,
            EventType.PIPELINE_DONE: AuditEventType.PIPELINE_END,
            EventType.PIPELINE_FAILED: AuditEventType.PIPELINE_END,
            EventType.NODE_COMPLETED: AuditEventType.STATE_TRANSITION,
            EventType.NODE_FAILED: AuditEventType.STATE_TRANSITION,
            EventType.HITL_WAITING: AuditEventType.HUMAN_APPROVAL,
            EventType.HITL_APPROVED: AuditEventType.HUMAN_APPROVAL,
            EventType.HITL_REJECTED: AuditEventType.HUMAN_APPROVAL,
        }
        for event_type, audit_type in mapping.items():
            event_bus.subscribe(
                event_type,
                lambda event, at=audit_type: self._record_from_event(event, at),
            )

    def log(
        self,
        event_type: AuditEventType,
        actor_type: ActorType,
        actor_id: str,
        action: str,
        *,
        input_data: Optional[Dict[str, Any]] = None,
        output_data: Optional[Dict[str, Any]] = None,
        duration_ms: float = 0.0,
        task_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditEntry:
        """记录一条审计日志，返回已创建的 AuditEntry。"""
        entry = AuditEntry(
            event_type=event_type, actor_type=actor_type,
            actor_id=actor_id, action=action,
            input_data=input_data or {}, output_data=output_data or {},
            duration_ms=duration_ms, task_id=task_id,
            metadata=metadata or {},
        )
        self._add_entry(entry)
        return entry

    def _add_entry(self, entry: AuditEntry) -> None:
        with self._lock:
            self._buffer.append(entry)
        if self._persist_to_db:
            self._persist(entry)

    def _persist(self, entry: AuditEntry) -> None:
        """写入 SQLite（在锁外执行，避免阻塞）。"""
        try:
            with db.engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO audit_log
                        (event_type, actor_type, actor_id, action,
                         timestamp, input_data, output_data,
                         duration_ms, task_id, metadata)
                    VALUES
                        (:event_type, :actor_type, :actor_id, :action,
                         :timestamp, :input_data, :output_data,
                         :duration_ms, :task_id, :metadata)
                """), {
                    "event_type": entry.event_type.value,
                    "actor_type": entry.actor_type.value,
                    "actor_id": entry.actor_id[:256],
                    "action": entry.action[:512],
                    "timestamp": entry.timestamp.isoformat(),
                    "input_data": json.dumps(entry.input_data, ensure_ascii=False)[:50000],
                    "output_data": json.dumps(entry.output_data, ensure_ascii=False)[:50000],
                    "duration_ms": entry.duration_ms,
                    "task_id": entry.task_id[:128],
                    "metadata": json.dumps(entry.metadata, ensure_ascii=False)[:50000],
                })
        except Exception as e:
            logger.warning("审计日志 SQLite 写入失败: %s", e)

    def _record_from_event(self, event: Event, audit_type: AuditEventType) -> None:
        entry = AuditEntry(
            event_type=audit_type,
            actor_type=ActorType.SYSTEM,
            actor_id=f"event_bus:{event.type.value}",
            action=event.type.value,
            input_data=event.data,
            task_id=event.task_id,
        )
        self._add_entry(entry)

    def get_recent(
        self,
        audit_type: AuditEventType,
        limit: int = 20,
    ) -> List[AuditEntry]:
        """获取指定审计类型的最近 N 条记录（时间倒序）。"""
        with self._lock:
            matches = [e for e in reversed(self._buffer) if e.event_type == audit_type]
        return matches[:limit]

    def query(
        self,
        *,
        actor_type: Optional[ActorType] = None,
        action: Optional[str] = None,
        event_type: Optional[AuditEventType] = None,
        task_id: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 50,
    ) -> List[AuditEntry]:
        """按条件筛选审计日志（内存缓冲区），支持操作者/动作/事件类型/时间范围。"""
        with self._lock:
            entries = list(reversed(self._buffer))
        result: List[AuditEntry] = []
        for e in entries:
            if actor_type is not None and e.actor_type != actor_type:
                continue
            if event_type is not None and e.event_type != event_type:
                continue
            if action is not None and e.action != action:
                continue
            if task_id is not None and e.task_id != task_id:
                continue
            if since is not None and e.timestamp < since:
                continue
            if until is not None and e.timestamp > until:
                continue
            result.append(e)
            if len(result) >= limit:
                break
        return result

    def count(self, audit_type: Optional[AuditEventType] = None) -> int:
        """统计内存缓冲区中的审计条目数量。"""
        with self._lock:
            if audit_type is None:
                return len(self._buffer)
            return sum(1 for e in self._buffer if e.event_type == audit_type)

    def clear(self) -> None:
        """清空内存缓冲区（不影响 SQLite 持久化数据）。"""
        with self._lock:
            self._buffer.clear()
        logger.info("审计日志内存缓冲区已清空")


audit_logger = AuditLogger()

__all__ = ["AuditLogger", "AuditEntry", "AuditEventType", "ActorType", "audit_logger"]
