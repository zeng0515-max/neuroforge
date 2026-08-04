from __future__ import annotations

import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from sqlalchemy import create_engine, desc, event, func, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.models import Base, FileRecord, StageLog, TaskRecord

logger = logging.getLogger(__name__)


class SqliteClient:
    """
    SQLite 客户端单例。
    - 通过 settings.db 配置路径
    - 提供上下文会话管理
    - 封装常用 CRUD：Task / File / StageLog
    """

    _instance: Optional["SqliteClient"] = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "SqliteClient":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    # ------------------------------------------------------------------ #
    #  初始化
    # ------------------------------------------------------------------ #
    def _init(self) -> None:
        db_path = Path(settings.db.path).resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite:///{db_path}"
        # SQLite + 多线程安全：check_same_thread=False + 默认 QueuePool
        # Bug P1 修复：移除 StaticPool（单连接串行化），改用默认 QueuePool；
        # 通过 event listener 在每个新连接上设置 WAL / busy_timeout 等 PRAGMA，
        # 让 FastAPI 异步路由下的并发读写不再 "database is locked"。
        self.engine: Engine = create_engine(
            db_url,
            echo=settings.db.echo,
            future=True,
            connect_args={"check_same_thread": False},
        )

        # 每个新连接都执行 PRAGMA：WAL 模式 + 忙等待 + 外键约束
        @event.listens_for(self.engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, conn_record):  # noqa: ANN001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        self._session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
            future=True,
        )
        self._create_tables()
        logger.info("SQLite 连接就绪: %s", db_url)

    def _create_tables(self) -> None:
        try:
            Base.metadata.create_all(self.engine)
        except Exception as e:
            logger.exception("建表失败：%s", e)
            raise

    # ------------------------------------------------------------------ #
    #  Session 管理
    # ------------------------------------------------------------------ #
    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        s = self._session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def fast_session(self) -> Session:
        """返回一个原始 session，调用方自己负责 commit/close。"""
        return self._session_factory()

    # ================================================================== #
    #  Task 相关
    # ================================================================== #
    def create_task(
        self,
        *,
        task_id: str,
        user_request: str,
        extra_args: Optional[Dict[str, Any]] = None,
    ) -> TaskRecord:
        with self.session() as s:
            existing = s.query(TaskRecord).filter(TaskRecord.task_id == task_id).first()
            if existing:
                # task 已由 upsert_files 等方法隐式创建，更新 user_request
                existing.user_request = user_request or existing.user_request
                existing.phase = "pending"
                s.flush()
                return self._detached_copy(existing)

            task = TaskRecord(
                task_id=task_id,
                user_request=user_request,
                phase="pending",
                status="running",
            )
            if extra_args:
                task.extra_args = extra_args
            s.add(task)
            s.flush()
            created = s.get(TaskRecord, task.id)
            return self._detached_copy(created) if created else task

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        with self.session() as s:
            t = s.query(TaskRecord).filter(TaskRecord.task_id == task_id).first()
            return self._detached_copy(t) if t else None

    def list_tasks(self, *, limit: int = 50, offset: int = 0) -> List[TaskRecord]:
        with self.session() as s:
            rows = (
                s.query(TaskRecord)
                .order_by(desc(TaskRecord.started_at))
                .limit(limit)
                .offset(offset)
                .all()
            )
            return [self._detached_copy(r) for r in rows]

    def update_task_phase(
        self,
        task_id: str,
        *,
        phase: Optional[str] = None,
        status: Optional[str] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        error_message: Optional[str] = None,
        summary: Optional[Dict[str, Any]] = None,
        finished: bool = False,
        duration_seconds: Optional[float] = None,
    ) -> None:
        with self.session() as s:
            t = s.query(TaskRecord).filter(TaskRecord.task_id == task_id).first()
            if not t:
                return
            if phase is not None:
                t.phase = phase
            if status is not None:
                t.status = status
            if prompt_tokens is not None:
                t.prompt_tokens += prompt_tokens
            if completion_tokens is not None:
                t.completion_tokens += completion_tokens
            t.total_tokens = t.prompt_tokens + t.completion_tokens
            if error_message:
                t.error_message = error_message
            if summary:
                prev = t.summary or {}
                prev.update(summary)
                t.summary = prev
            if finished:
                t.finished_at = datetime.now()
                if t.started_at and duration_seconds is None:
                    duration_seconds = (t.finished_at - t.started_at).total_seconds()
            if duration_seconds is not None:
                t.duration_seconds = duration_seconds
            t.updated_at = datetime.now()

    def delete_task(self, task_id: str) -> bool:
        with self.session() as s:
            t = s.query(TaskRecord).filter(TaskRecord.task_id == task_id).first()
            if not t:
                return False
            # 级联删除关联的文件与阶段日志
            s.query(FileRecord).filter(FileRecord.task_id == task_id).delete()
            s.query(StageLog).filter(StageLog.task_id == task_id).delete()
            s.delete(t)
            return True

    def clear_all_tasks(self) -> int:
        """清空所有任务及其关联日志/文件，返回删除的任务数。"""
        with self.session() as s:
            count = s.query(TaskRecord).count()
            s.query(FileRecord).delete()
            s.query(StageLog).delete()
            s.query(TaskRecord).delete()
            logger.info("已清空历史任务，共 %d 条", count)
            return count

    def mark_task_done(self, task_id: str, *, status: str = "done",
                       summary: Optional[Dict[str, Any]] = None) -> None:
        """快捷：把一个任务标记为完成（写 finished_at / final status）。"""
        extra: Dict[str, Any] = {"finished": True, "status": status}
        if summary:
            extra["summary"] = {**(summary or {}), "final_status": status}
        self.update_task_phase(task_id, phase=status, **extra)

    def mark_task_failed(self, task_id: str, error: str = "",
                         *, summary: Optional[Dict[str, Any]] = None,
                         failed_stage: Optional[str] = None) -> None:
        """快捷：把一个任务标记为失败。

        Bug D5：若传入 failed_stage，则清理该阶段之后写入的中间产物文件，
        保留失败阶段本身写入的文件供调试，避免给用户返回半成品代码。
        """
        extra: Dict[str, Any] = {"finished": True, "status": "failed",
                                 "error_message": error[:500]}
        if summary:
            extra["summary"] = {**summary, "final_status": "failed"}
        self.update_task_phase(task_id, phase="failed", **extra)
        # 清理失败阶段之后的中间产物（保留失败阶段本身写入的文件供调试）
        if failed_stage:
            stage_order = ["requirement", "code_gen", "review",
                           "test_gen", "retry", "chat_modify"]
            try:
                failed_idx = stage_order.index(failed_stage)
                later_stages = stage_order[failed_idx + 1:]
                if later_stages:
                    with self.session() as s:
                        s.query(FileRecord).filter(
                            FileRecord.task_id == task_id,
                            FileRecord.source_stage.in_(later_stages)
                        ).delete(synchronize_session=False)
            except ValueError:
                pass  # 未知 stage，不清理

    # ================================================================== #
    #  FileRecord 相关
    # ================================================================== #
    def upsert_files(
        self,
        *,
        task_id: str,
        files: List[Dict[str, Any]],
        source_stage: str = "code_gen",
    ) -> None:
        if not files:
            return
        with self.session() as s:
            # 确保 task 记录存在，否则 FileRecord 外键约束会失败
            task_exists = s.query(TaskRecord).filter(
                TaskRecord.task_id == task_id
            ).first()
            if task_exists is None:
                s.add(TaskRecord(task_id=task_id, user_request=""))
            for f in files:
                path = str(f.get("path", "")).strip()
                if not path:
                    continue
                existing = (
                    s.query(FileRecord)
                    .filter(FileRecord.task_id == task_id, FileRecord.path == path)
                    .first()
                )
                content = str(f.get("content", ""))
                line_count = content.count("\n") + 1 if content else 0
                if existing:
                    existing.language = f.get("language", existing.language)
                    existing.description = f.get("description", existing.description)
                    existing.content = content
                    existing.source_stage = f.get("source_stage", source_stage)
                    existing.line_count = line_count
                else:
                    s.add(
                        FileRecord(
                            task_id=task_id,
                            path=path,
                            language=f.get("language", "python"),
                            description=f.get("description", ""),
                            content=content,
                            source_stage=f.get("source_stage", source_stage),
                            line_count=line_count,
                        )
                    )

    def list_files(self, task_id: str) -> List[FileRecord]:
        with self.session() as s:
            rows = (
                s.query(FileRecord)
                .filter(FileRecord.task_id == task_id)
                .order_by(FileRecord.path)
                .all()
            )
            return [self._detached_copy(r) for r in rows]

    # ================================================================== #
    #  StageLog 相关
    # ================================================================== #
    def add_stage_log(
        self,
        *,
        task_id: str,
        stage: str,
        prompt_text: str = "",
        llm_output: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        duration_ms: int = 0,
        error: str = "",
    ) -> None:
        with self.session() as s:
            s.add(
                StageLog(
                    task_id=task_id,
                    stage=stage,
                    prompt_text=prompt_text,
                    llm_output=llm_output,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    duration_ms=duration_ms,
                    error=error,
                )
            )

    def list_stage_logs(self, task_id: str) -> List[StageLog]:
        with self.session() as s:
            rows = (
                s.query(StageLog)
                .filter(StageLog.task_id == task_id)
                .order_by(StageLog.created_at.asc())
                .all()
            )
            return [self._detached_copy(r) for r in rows]

    # ================================================================== #
    #  统计
    # ================================================================== #
    def stat(self) -> Dict[str, Any]:
        with self.session() as s:
            total_tasks = s.query(func.count(TaskRecord.id)).scalar() or 0
            total_tokens = s.query(func.coalesce(func.sum(TaskRecord.total_tokens), 0)).scalar() or 0
            total_files = s.query(func.count(FileRecord.id)).scalar() or 0
        return {
            "total_tasks": int(total_tasks),
            "total_tokens": int(total_tokens),
            "total_files": int(total_files),
        }

    # ------------------------------------------------------------------ #
    #  内部工具：将 ORM 对象脱离 session（复制字段）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _detached_copy(obj: Any) -> Any:
        """对已查询对象做最小化复制，避免在 session 外访问时出现 DetachedInstanceError。"""
        if obj is None:
            return None
        # 简单方案：用 __dict__ 重新构造对象所需字段；这里直接返回并确保 session 关闭后
        # 没有触发 lazy load。依赖 ORM 已经在 create_all 时 eagerly 加载了标量字段。
        try:
            # 触发一次访问，确保字段加载
            if isinstance(obj, TaskRecord):
                _ = (obj.id, obj.task_id, obj.phase, obj.status, len(obj.files or []))
            elif isinstance(obj, FileRecord):
                _ = (obj.id, obj.task_id, obj.path, obj.content[:0], obj.line_count)
            elif isinstance(obj, StageLog):
                _ = (obj.id, obj.task_id, obj.stage, obj.prompt_tokens, obj.total_tokens)
        except Exception:
            pass
        return obj


db = SqliteClient()

__all__ = ["db", "SqliteClient"]
