from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """SQLAlchemy ORM 公共基类。"""


# ====================================================================== #
#  任务表
# ====================================================================== #

class TaskRecord(Base):
    """主任务表：记录每一次用户触发的流水线执行。"""

    __tablename__ = "task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    user_request: Mapped[str] = mapped_column(Text, default="", nullable=False)
    extra_args_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)

    phase: Mapped[str] = mapped_column(String(32), default="pending", index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)

    # Token 用量
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # 时间戳
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, index=True,
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    duration_seconds: Mapped[float] = mapped_column(Integer, default=0)

    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # 产物摘要（JSON 字符串，便于列表页展示）
    summary_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)

    # --- ORM 关系 ---
    files: Mapped[List["FileRecord"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )

    # ------------------------------------------------------------------ #
    #  工具属性
    # ------------------------------------------------------------------ #
    @property
    def extra_args(self) -> Dict[str, Any]:
        try:
            return json.loads(self.extra_args_json) if self.extra_args_json else {}
        except Exception:
            return {}

    @extra_args.setter
    def extra_args(self, value: Dict[str, Any]) -> None:
        self.extra_args_json = json.dumps(value, ensure_ascii=False)

    @property
    def summary(self) -> Dict[str, Any]:
        try:
            return json.loads(self.summary_json) if self.summary_json else {}
        except Exception:
            return {}

    @summary.setter
    def summary(self, value: Dict[str, Any]) -> None:
        self.summary_json = json.dumps(value, ensure_ascii=False)


# ====================================================================== #
#  文件产物表
# ====================================================================== #

class FileRecord(Base):
    """文件产物：每次任务生成的每个文件单独一行，方便按路径检索。"""

    __tablename__ = "file"
    __table_args__ = (
        Index("ix_file_task_path", "task_id", "path", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("task.task_id", ondelete="CASCADE"), nullable=False, index=True,
    )

    path: Mapped[str] = mapped_column(String(512), nullable=False)
    language: Mapped[str] = mapped_column(String(32), default="python", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)

    source_stage: Mapped[str] = mapped_column(
        String(32), default="code_gen", nullable=False,
    )

    line_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)

    task: Mapped[TaskRecord] = relationship(back_populates="files")


# ====================================================================== #
#  阶段执行日志
# ====================================================================== #

class StageLog(Base):
    """每个 Agent / 阶段的执行日志，便于问题排查。"""

    __tablename__ = "stage_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    stage: Mapped[str] = mapped_column(String(32), index=True, nullable=False)

    prompt_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    llm_output: Mapped[str] = mapped_column(Text, default="", nullable=False)

    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


class QueueTask(Base):
    """Durable job queue row used by the background worker.

    Kept separate from :class:`TaskRecord` so the task lifecycle (queued,
    running, retry, failed) is observable without blocking pipeline reads.
    """

    __tablename__ = "task_queue"
    __table_args__ = (
        Index("ix_queue_status_priority", "queue_status", "priority"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    queue_status: Mapped[str] = mapped_column(
        String(16), default="queued", index=True, nullable=False,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=5, nullable=False)

    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    result_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)

    worker_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    lock_token: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, index=True,
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # ------------------------------------------------------------------ #
    #  JSON helpers
    # ------------------------------------------------------------------ #
    @property
    def payload(self) -> Dict[str, Any]:
        try:
            return json.loads(self.payload_json) if self.payload_json else {}
        except Exception:
            return {}

    @payload.setter
    def payload(self, value: Dict[str, Any]) -> None:
        self.payload_json = json.dumps(value, ensure_ascii=False)

    @property
    def result(self) -> Dict[str, Any]:
        try:
            return json.loads(self.result_json) if self.result_json else {}
        except Exception:
            return {}

    @result.setter
    def result(self, value: Dict[str, Any]) -> None:
        self.result_json = json.dumps(value, ensure_ascii=False)


__all__ = [
    "Base",
    "TaskRecord",
    "FileRecord",
    "StageLog",
    "QueueTask",
]
