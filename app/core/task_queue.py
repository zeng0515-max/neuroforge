"""SQLite-backed durable job queue with retries and worker leases."""

from __future__ import annotations

import logging
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional

from sqlalchemy.orm import Session

from app.db.models import QueueTask

logger = logging.getLogger(__name__)


class QueueStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class QueueStats:
    queued: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0
    total: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "queued": self.queued,
            "running": self.running,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "cancelled": self.cancelled,
            "total": self.total,
        }


class TaskQueue:
    """Thread-safe queue backed by the shared SQLite client.

    Leases prevent two workers from processing the same job: ``claim()``
    atomically transitions ``queued -> running`` and records a lock token.
    """

    def __init__(self, session_factory=None) -> None:
        self._lock = threading.RLock()
        self._session_factory = session_factory or self._default_session

    @staticmethod
    def _default_session() -> Session:
        from app.db.sqlite_client import db

        return db.fast_session()

    @contextmanager
    def _session(self) -> Iterator[Session]:
        s = self._session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def enqueue(
        self,
        task_id: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        priority: int = 5,
        max_attempts: int = 3,
    ) -> bool:
        """Insert a new job. Returns False when the task id already exists."""
        with self._lock, self._session() as s:
            exists = s.query(QueueTask).filter(QueueTask.task_id == task_id).first()
            if exists:
                return False
            s.add(
                QueueTask(
                    task_id=task_id,
                    queue_status=QueueStatus.QUEUED.value,
                    priority=max(1, min(priority, 10)),
                    max_attempts=max(1, max_attempts),
                    payload=dict(payload or {}),
                )
            )
            logger.info("Enqueued job %s priority=%s", task_id, priority)
            return True

    def claim(self, worker_id: str, limit: int = 1) -> List[Dict[str, Any]]:
        """Atomically claim up to ``limit`` queued jobs for a worker."""
        claimed: List[Dict[str, Any]] = []
        with self._lock, self._session() as s:
            rows = (
                s.query(QueueTask)
                .filter(QueueTask.queue_status == QueueStatus.QUEUED.value)
                .order_by(QueueTask.priority.desc(), QueueTask.id.asc())
                .limit(limit)
                .all()
            )
            for row in rows:
                row.queue_status = QueueStatus.RUNNING.value
                row.attempts += 1
                row.worker_id = worker_id
                row.lock_token = uuid.uuid4().hex
                row.started_at = datetime.now()
                row.updated_at = datetime.now()
                claimed.append(self._to_dict(row, include_lock=True))
        return claimed

    def complete(
        self,
        task_id: str,
        worker_id: str,
        result: Optional[Dict[str, Any]] = None,
        lock_token: str = "",
    ) -> bool:
        with self._lock, self._session() as s:
            row = self._find_owned(s, task_id, worker_id, lock_token)
            if row is None:
                return False
            row.queue_status = QueueStatus.SUCCEEDED.value
            row.result = dict(result or {})
            row.finished_at = datetime.now()
            row.updated_at = datetime.now()
            return True

    def fail(
        self,
        task_id: str,
        worker_id: str,
        error: str,
        lock_token: str = "",
    ) -> bool:
        """Fail a job; retry when attempts remain, otherwise mark failed."""
        with self._lock, self._session() as s:
            row = self._find_owned(s, task_id, worker_id, lock_token)
            if row is None:
                return False
            row.error = (error or "")[:2000]
            row.worker_id = ""
            row.lock_token = ""
            row.started_at = None
            row.updated_at = datetime.now()
            if row.attempts >= row.max_attempts:
                row.queue_status = QueueStatus.FAILED.value
                row.finished_at = datetime.now()
            else:
                row.queue_status = QueueStatus.QUEUED.value
            logger.info(
                "Job %s failed (attempt %d/%d), next=%s",
                task_id,
                row.attempts,
                row.max_attempts,
                row.queue_status,
            )
            return True

    def cancel(self, task_id: str) -> bool:
        with self._lock, self._session() as s:
            row = (
                s.query(QueueTask)
                .filter(
                    QueueTask.task_id == task_id,
                    QueueTask.queue_status.in_(
                        [QueueStatus.QUEUED.value, QueueStatus.RUNNING.value]
                    ),
                )
                .first()
            )
            if row is None:
                return False
            row.queue_status = QueueStatus.CANCELLED.value
            row.finished_at = datetime.now()
            row.updated_at = datetime.now()
            return True

    def requeue_stale(self, timeout_seconds: int = 600) -> int:
        """Requeue jobs whose worker lease expired (e.g. after a crash)."""
        cutoff = datetime.now() - timedelta(seconds=timeout_seconds)
        count = 0
        with self._lock, self._session() as s:
            rows = (
                s.query(QueueTask)
                .filter(
                    QueueTask.queue_status == QueueStatus.RUNNING.value,
                    QueueTask.started_at.is_not(None),
                    QueueTask.started_at < cutoff,
                )
                .all()
            )
            for row in rows:
                row.queue_status = QueueStatus.QUEUED.value
                row.worker_id = ""
                row.lock_token = ""
                row.started_at = None
                row.updated_at = datetime.now()
                count += 1
        if count:
            logger.warning("Requeued %d stale jobs after %ss lease timeout", count, timeout_seconds)
        return count

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._session() as s:
            row = s.query(QueueTask).filter(QueueTask.task_id == task_id).first()
            return self._to_dict(row) if row else None

    def list_tasks(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        with self._session() as s:
            rows = (
                s.query(QueueTask)
                .order_by(QueueTask.created_at.desc())
                .limit(limit)
                .offset(offset)
                .all()
            )
            return [self._to_dict(r) for r in rows]

    def stats(self) -> QueueStats:
        with self._session() as s:
            rows = s.query(QueueTask.queue_status).all()
        stats = QueueStats(total=len(rows))
        for (status,) in rows:
            if status == QueueStatus.QUEUED.value:
                stats.queued += 1
            elif status == QueueStatus.RUNNING.value:
                stats.running += 1
            elif status == QueueStatus.SUCCEEDED.value:
                stats.succeeded += 1
            elif status == QueueStatus.FAILED.value:
                stats.failed += 1
            elif status == QueueStatus.CANCELLED.value:
                stats.cancelled += 1
        return stats

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _find_owned(s: Session, task_id: str, worker_id: str, lock_token: str) -> Optional[QueueTask]:
        query = s.query(QueueTask).filter(
            QueueTask.task_id == task_id,
            QueueTask.worker_id == worker_id,
        )
        if lock_token:
            query = query.filter(QueueTask.lock_token == lock_token)
        return query.first()

    @staticmethod
    def _to_dict(row: QueueTask, include_lock: bool = False) -> Dict[str, Any]:
        data = {
            "task_id": row.task_id,
            "status": row.queue_status,
            "attempts": row.attempts,
            "max_attempts": row.max_attempts,
            "priority": row.priority,
            "payload": row.payload,
            "result": row.result,
            "error": row.error,
            "worker_id": row.worker_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        }
        if include_lock:
            data["lock_token"] = row.lock_token
        return data


task_queue = TaskQueue()

__all__ = [
    "QueueStatus",
    "QueueStats",
    "TaskQueue",
    "task_queue",
]
