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
from app.core.config import settings


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

    # conftest 顶部已触发 settings 创建，env 覆盖不会自动同步到旧实例，
    # 这里直接改对象，保证测试期间不读 .env 中的真实密钥/模型配置。
    settings.app.env = "test"
    settings.llm.api_key = ""
    settings.code_llm.api_key = ""
    settings.chroma.embedding_model = "hash"
    settings.db.path = str(Path(tempfile.gettempdir()) / "mac_test_app.db")
    settings.chroma.persist_dir = str(Path(tempfile.gettempdir()) / "mac_test_chroma")
    settings.tracing.enabled = False
    settings.semantic_cache.enabled = False

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


# ====================================================================== #
#  编排引擎 fixtures
# ====================================================================== #


@pytest.fixture(scope="function")
def task_graph():
    """空 TaskGraph 实例。"""
    from app.graph.orchestrator import TaskGraph
    return TaskGraph()


@pytest.fixture(scope="function")
def linear_task_graph():
    """线性链路任务图: a→b→c→d。"""
    from app.graph.orchestrator import AgentTask, TaskGraph
    g = TaskGraph()
    g.add_task("a", AgentTask(task_id="a", agent_type="requirement"))
    g.add_task("b", AgentTask(task_id="b", agent_type="code_gen", dependencies=["a"]))
    g.add_task("c", AgentTask(task_id="c", agent_type="review", dependencies=["b"]))
    g.add_task("d", AgentTask(task_id="d", agent_type="test_gen", dependencies=["c"]))
    return g


@pytest.fixture(scope="function")
def parallel_task_graph():
    """并行任务图: a→(b,c)→d。"""
    from app.graph.orchestrator import AgentTask, TaskGraph
    g = TaskGraph()
    g.add_task("a", AgentTask(task_id="a", agent_type="requirement"))
    g.add_task("b", AgentTask(task_id="b", agent_type="code_gen", dependencies=["a"]))
    g.add_task("c", AgentTask(task_id="c", agent_type="code_gen", dependencies=["a"]))
    g.add_task("d", AgentTask(task_id="d", agent_type="test_gen", dependencies=["b", "c"]))
    return g


@pytest.fixture(scope="function")
def orchestrator():
    """Orchestrator 实例。"""
    from app.graph.orchestrator import Orchestrator
    return Orchestrator()


# ====================================================================== #
#  记忆系统 fixtures
# ====================================================================== #


@pytest.fixture(scope="function")
def working_memory():
    """独立 WorkingMemory 实例。"""
    from app.core.memory_system import WorkingMemory
    return WorkingMemory(capacity=50)


@pytest.fixture(scope="function")
def episodic_memory():
    """独立 EpisodicMemory 实例。"""
    from app.core.memory_system import EpisodicMemory
    return EpisodicMemory(max_events=100)


@pytest.fixture(scope="function")
def memory_facade():
    """独立 MemoryFacade 实例（三层记忆）。"""
    from app.core.memory_system import MemoryFacade
    return MemoryFacade(working_capacity=50, episodic_max=100)


# ====================================================================== #
#  Agent 协议 fixtures
# ====================================================================== #


@pytest.fixture(scope="function")
def agent_protocol():
    """已注册标准 Agent 的 AgentInteractionProtocol。"""
    from app.agents.agent_protocol import (
        AgentCapability,
        AgentInteractionProtocol,
        AgentProfile,
        AgentRole,
    )
    proto = AgentInteractionProtocol()
    proto.register_agent(AgentProfile(
        name="coder", role=AgentRole.CODER,
        capabilities=[AgentCapability.CODE_GENERATION],
    ))
    proto.register_agent(AgentProfile(
        name="reviewer", role=AgentRole.REVIEWER,
        capabilities=[AgentCapability.CODE_REVIEW],
    ))
    proto.register_agent(AgentProfile(
        name="tester", role=AgentRole.TESTER,
        capabilities=[AgentCapability.TESTING],
    ))
    proto.register_agent(AgentProfile(
        name="architect", role=AgentRole.ARCHITECT,
        capabilities=[AgentCapability.PLANNING, AgentCapability.ANALYSIS],
    ))
    return proto


# ====================================================================== #
#  任务拆解 fixtures
# ====================================================================== #


@pytest.fixture(scope="function")
def task_decomposer():
    """TaskDecomposer 实例。"""
    from app.graph.task_decomposer import TaskDecomposer
    return TaskDecomposer()


@pytest.fixture(scope="function")
def decision_engine():
    """DecisionEngine 实例。"""
    from app.graph.task_decomposer import DecisionEngine
    return DecisionEngine()


# ====================================================================== #
#  统一编排 fixtures
# ====================================================================== #


@pytest.fixture(scope="function")
def unified_orchestrator():
    """UnifiedOrchestrator 实例。"""
    from app.unified_orchestrator import UnifiedOrchestrator
    return UnifiedOrchestrator()
