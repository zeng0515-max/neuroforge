from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base


# ====================================================================== #
#  pytest 配置：在所有测试启动前锁住环境变量，禁止真实网络调用
# ====================================================================== #
def pytest_configure(config: pytest.Config) -> None:
    """全局配置钩子：在测试收集前强制进入离线/DryRun 模式。

    设计要点：
    - 即使本地 .env 配置了真实 API Key，测试也不会真的调用 LLM / DashScope
    - pydantic-settings 中环境变量优先级高于 .env 文件，因此 setdefault 不够，必须强制赋值
    - sqlite/向量库目录指向临时目录，避免污染开发数据
    """
    # 必须在 import app 之前生效：但 conftest 顶部已经 import，所以这里主要影响
    # 后续重新构造的 settings 对象，以及通过 os.environ 读取的代码路径
    os.environ["LLM_API_KEY"] = ""
    os.environ["CODE_LLM_API_KEY"] = ""
    os.environ["DASHSCOPE_API_KEY"] = ""
    os.environ["DEEPSEEK_API_KEY"] = ""
    os.environ["ARK_API_KEY"] = ""
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["EMBEDDING_MODEL"] = "hash"
    os.environ["DB_PATH"] = str(Path(tempfile.gettempdir()) / "mac_test_app.db")
    os.environ["CHROMA_PERSIST_DIR"] = str(Path(tempfile.gettempdir()) / "mac_test_chroma")
    os.environ["ENABLE_TEST_GEN"] = "true"
    os.environ["APP_ENV"] = "test"

    # 直接把全局 llm 单例打到 DryRun，避免已初始化的真实 client 被测试触发
    try:
        from app.core.llm_adapter import llm
        llm._main = None
        llm._code = None
        llm.dry_run = True
        llm.init_error = ""
    except Exception:
        pass


# ====================================================================== #
#  autouse：每个测试前再次确认 DryRun / 离线状态，避免被某个用例改回
# ====================================================================== #
@pytest.fixture(autouse=True)
def _force_dry_run() -> Generator[None, None, None]:
    """强制全局 LLM 走 DryRun，禁用真实 API 调用。"""
    from app.core.llm_adapter import llm
    saved = (llm._main, llm._code, llm.dry_run)
    llm._main = None
    llm._code = None
    llm.dry_run = True
    yield
    llm._main, llm._code, llm.dry_run = saved


# ====================================================================== #
#  FastAPI TestClient
# ====================================================================== #
@pytest.fixture(scope="module")
def client() -> Generator[TestClient, None, None]:
    """FastAPI TestClient，用于集成测试。"""
    from app.main import app
    with TestClient(app) as c:
        yield c


# ====================================================================== #
#  临时数据库
# ====================================================================== #
@pytest.fixture(scope="function")
def temp_db() -> Generator[Session, None, None]:
    """内存 SQLite 数据库，每次测试独立建表/销毁。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        Base.metadata.drop_all(engine)


# ====================================================================== #
#  Mock LLM 响应
# ====================================================================== #
@pytest.fixture(scope="function")
def mock_llm_response() -> Generator[MagicMock, None, None]:
    """Mock LLM 响应，返回一个可配置的 MagicMock。

    使用方式：
        def test_xxx(mock_llm_response):
            mock_llm_response.return_value.content = "mock response"
            with patch("app.core.llm_adapter.llm.chat", mock_llm_response):
                ...
    """
    mock = MagicMock()
    from app.core.llm_adapter import ChatResponse, LLMUsage
    mock.return_value = ChatResponse(
        content="mock llm response",
        usage=LLMUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        model="mock-model",
    )
    yield mock


@pytest.fixture(scope="function")
def mock_llm_achat() -> Generator[AsyncMock, None, None]:
    """Mock LLM 异步响应。"""
    from app.core.llm_adapter import ChatResponse, LLMUsage
    mock = AsyncMock()
    mock.return_value = ChatResponse(
        content="mock async llm response",
        usage=LLMUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        model="mock-model",
    )
    yield mock
