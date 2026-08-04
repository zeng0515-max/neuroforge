"""数据模型与 SQLite CRUD 单元测试。

重点测（用内存 SQLite，不依赖文件）：
- TaskRecord 创建与字段默认值
- extra_args / summary 属性的 JSON 序列化
- FileRecord 与 TaskRecord 的关系
- StageLog 写入与查询
- Base.metadata 建表/销毁
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.db.models import Base, FileRecord, StageLog, TaskRecord


# ------------------------------------------------------------------ #
#  表结构
# ------------------------------------------------------------------ #
def test_base_metadata_has_required_tables(temp_db: Session) -> None:
    """建表后应存在 task / file / stage_log 三张表。"""
    inspector = inspect(temp_db.bind)
    tables = set(inspector.get_table_names())
    assert {"task", "file", "stage_log"}.issubset(tables)


# ------------------------------------------------------------------ #
#  TaskRecord 创建与字段
# ------------------------------------------------------------------ #
def test_task_record_defaults(temp_db: Session) -> None:
    """新建 TaskRecord 应有合理默认值：phase=pending, status=pending, tokens=0。"""
    t = TaskRecord(task_id="t-1", user_request="hello")
    temp_db.add(t)
    temp_db.flush()

    assert t.id is not None
    assert t.phase == "pending"
    assert t.status == "pending"
    assert t.prompt_tokens == 0
    assert t.completion_tokens == 0
    assert t.total_tokens == 0
    assert t.error_message == ""
    assert t.started_at is not None
    assert t.duration_seconds == 0


def test_task_record_extra_args_roundtrip(temp_db: Session) -> None:
    """extra_args 属性应支持 dict ↔ JSON 字符串互转。"""
    t = TaskRecord(task_id="t-2", user_request="x")
    t.extra_args = {"tech_stack": ["fastapi", "sqlite"], "limit": 10}
    temp_db.add(t)
    temp_db.flush()

    # 重新查出来验证
    temp_db.expire(t)
    fetched = temp_db.get(TaskRecord, t.id)
    assert fetched is not None
    args = fetched.extra_args
    assert args["tech_stack"] == ["fastapi", "sqlite"]
    assert args["limit"] == 10


def test_task_record_extra_args_invalid_json_returns_empty(temp_db: Session) -> None:
    """extra_args_json 是非法 JSON 时应返回空 dict，不抛异常。"""
    t = TaskRecord(task_id="t-bad", user_request="x", extra_args_json="{not json")
    temp_db.add(t)
    temp_db.flush()
    temp_db.expire(t)
    fetched = temp_db.get(TaskRecord, t.id)
    assert fetched is not None
    assert fetched.extra_args == {}


def test_task_record_summary_roundtrip(temp_db: Session) -> None:
    """summary 属性应支持 dict 序列化与反序列化。"""
    t = TaskRecord(task_id="t-sum", user_request="x")
    t.summary = {"review": {"score": 90, "verdict": "approved"}}
    temp_db.add(t)
    temp_db.flush()
    temp_db.expire(t)
    fetched = temp_db.get(TaskRecord, t.id)
    assert fetched is not None
    assert fetched.summary["review"]["score"] == 90


# ------------------------------------------------------------------ #
#  FileRecord 与 TaskRecord 关系
# ------------------------------------------------------------------ #
def test_file_record_belongs_to_task(temp_db: Session) -> None:
    """FileRecord 应能关联到 TaskRecord，且 task_id 外键正确。"""
    t = TaskRecord(task_id="t-file", user_request="x")
    temp_db.add(t)
    temp_db.flush()

    f1 = FileRecord(
        task_id="t-file", path="src/main.py", content="print(1)",
        language="python", line_count=1,
    )
    f2 = FileRecord(
        task_id="t-file", path="README.md", content="# hi",
        language="markdown", line_count=1,
    )
    temp_db.add_all([f1, f2])
    temp_db.flush()

    # 通过 relationship 反向访问
    temp_db.expire(t)
    fetched = temp_db.get(TaskRecord, t.id)
    assert fetched is not None
    assert len(fetched.files) == 2
    paths = {f.path for f in fetched.files}
    assert paths == {"src/main.py", "README.md"}


def test_file_record_cascade_delete(temp_db: Session) -> None:
    """删除 TaskRecord 应级联删除其 FileRecord。"""
    t = TaskRecord(task_id="t-cascade", user_request="x")
    temp_db.add(t)
    temp_db.flush()
    f = FileRecord(task_id="t-cascade", path="a.py", content="x")
    temp_db.add(f)
    temp_db.flush()
    file_id = f.id

    temp_db.delete(t)
    temp_db.flush()

    # FileRecord 应已被级联删除
    assert temp_db.get(FileRecord, file_id) is None


# ------------------------------------------------------------------ #
#  StageLog
# ------------------------------------------------------------------ #
def test_stage_log_create_and_query(temp_db: Session) -> None:
    """StageLog 应能按 task_id 写入并按时间顺序查回。"""
    t = TaskRecord(task_id="t-log", user_request="x")
    temp_db.add(t)
    temp_db.flush()

    log1 = StageLog(task_id="t-log", stage="requirement", prompt_tokens=10, completion_tokens=5, total_tokens=15)
    log2 = StageLog(task_id="t-log", stage="code_gen", prompt_tokens=20, completion_tokens=15, total_tokens=35)
    temp_db.add_all([log1, log2])
    temp_db.flush()

    from sqlalchemy import select
    rows = temp_db.execute(
        select(StageLog).where(StageLog.task_id == "t-log").order_by(StageLog.id)
    ).scalars().all()
    assert len(rows) == 2
    assert rows[0].stage == "requirement"
    assert rows[1].stage == "code_gen"
    assert rows[1].total_tokens == 35


# ------------------------------------------------------------------ #
#  SqliteClient 集成（用临时 db 路径，验证 create_task / get_task / list_tasks）
# ------------------------------------------------------------------ #
def test_sqlite_client_create_and_get_task(tmp_path) -> None:
    """SqliteClient 单例应能 create_task / get_task / list_tasks。"""
    # 用临时路径避免污染开发数据；构造一个独立 engine 而非全局单例
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # 直接构造 SqliteClient 但替换其 engine，避免触碰全局单例
    from app.db.sqlite_client import SqliteClient
    client = object.__new__(SqliteClient)
    db_path = tmp_path / "test_client.db"
    client.engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    client._session_factory = sessionmaker(
        bind=client.engine, autoflush=False, expire_on_commit=False, future=True,
    )
    Base.metadata.create_all(client.engine)

    # create_task
    created = client.create_task(task_id="client-t-1", user_request="hi")
    assert created.task_id == "client-t-1"

    # get_task
    fetched = client.get_task("client-t-1")
    assert fetched is not None
    assert fetched.user_request == "hi"

    # list_tasks
    client.create_task(task_id="client-t-2", user_request="hi2")
    rows = client.list_tasks(limit=10)
    assert len(rows) == 2
    task_ids = {r.task_id for r in rows}
    assert {"client-t-1", "client-t-2"} == task_ids

    # delete_task
    ok = client.delete_task("client-t-1")
    assert ok is True
    assert client.get_task("client-t-1") is None

    # 删除不存在的任务
    assert client.delete_task("not-exist") is False


def test_sqlite_client_update_phase_and_done(tmp_path) -> None:
    """update_task_phase / mark_task_done / mark_task_failed 应正确更新字段。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db.sqlite_client import SqliteClient
    client = object.__new__(SqliteClient)
    db_path = tmp_path / "test_phase.db"
    client.engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    client._session_factory = sessionmaker(
        bind=client.engine, autoflush=False, expire_on_commit=False, future=True,
    )
    Base.metadata.create_all(client.engine)

    client.create_task(task_id="phase-t", user_request="x")
    client.update_task_phase(
        "phase-t", phase="code_gen", status="running",
        prompt_tokens=100, completion_tokens=50,
    )
    t = client.get_task("phase-t")
    assert t is not None
    assert t.phase == "code_gen"
    assert t.prompt_tokens == 100
    assert t.completion_tokens == 50
    assert t.total_tokens == 150

    # mark_task_done
    client.mark_task_done("phase-t", summary={"review": {"score": 95}})
    t = client.get_task("phase-t")
    assert t is not None
    assert t.status == "done"
    assert t.finished_at is not None
    assert t.summary["review"]["score"] == 95

    # mark_task_failed
    client.create_task(task_id="fail-t", user_request="x")
    client.mark_task_failed("fail-t", "boom")
    t = client.get_task("fail-t")
    assert t is not None
    assert t.status == "failed"
    assert "boom" in t.error_message
