"""API 接口冒烟测试。

策略：
- 同步用例使用 conftest 提供的 TestClient fixture
- 异步用例使用 httpx.AsyncClient + ASGITransport，验证 pytest-asyncio 链路
- 后台流水线通过 patch run_pipeline 拦截为 noop，避免真实执行 LLM 节点
- 全部接口在 DryRun / 离线模式下可跑通
"""
from __future__ import annotations

from typing import AsyncIterator

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport

from app.graph.state import (
    PipelinePhase,
    PipelineStatus,
    ReviewReport,
    ReviewVerdict,
)


# ------------------------------------------------------------------ #
#  同步冒烟
# ------------------------------------------------------------------ #
def test_health_endpoint(client: TestClient) -> None:
    """GET /api/v1/health 应返回 200 + status=ok，DryRun 下 llm=dry-run。"""
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # DryRun 模式下 llm 字段应为 "dry-run"
    assert body["llm"] == "dry-run"
    assert "debug" in body


def test_models_endpoint(client: TestClient) -> None:
    """GET /api/v1/models 应返回当前模型与可用模型列表。"""
    resp = client.get("/api/v1/models")
    assert resp.status_code == 200
    body = resp.json()
    assert "current_main" in body
    assert "current_code" in body
    assert "available" in body
    assert isinstance(body["available"], list)
    assert len(body["available"]) > 0


def test_list_tasks_empty_or_list(client: TestClient) -> None:
    """GET /api/v1/tasks 应返回 200 + items 列表（可能为空）。"""
    resp = client.get("/api/v1/tasks")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "total" in body
    assert isinstance(body["items"], list)


def test_get_nonexistent_task_returns_404(client: TestClient) -> None:
    """查询不存在的 task_id 应返回 404。"""
    resp = client.get("/api/v1/tasks/nonexistent-xyz-123")
    assert resp.status_code == 404


def test_config_llm_status(client: TestClient) -> None:
    """GET /api/v1/config/llm 应返回配置状态与厂商列表。"""
    resp = client.get("/api/v1/config/llm")
    assert resp.status_code == 200
    body = resp.json()
    # DryRun 环境（conftest 清空 key）下应为未配置
    assert "configured" in body
    assert "dry_run" in body
    assert "providers" in body
    assert isinstance(body["providers"], list)
    # 厂商列表应包含常见的厂商
    env_keys = {p["env_key"] for p in body["providers"]}
    assert "DASHSCOPE_API_KEY" in env_keys
    assert "DEEPSEEK_API_KEY" in env_keys


def test_config_llm_update_validation(client: TestClient) -> None:
    """POST /api/v1/config/llm 空字符串应被 Pydantic 校验拒绝（422）。"""
    resp = client.post("/api/v1/config/llm", json={
        "base_url": "", "api_key": "", "model": "",
    })
    assert resp.status_code == 422


def test_create_task_with_mocked_pipeline(client: TestClient) -> None:
    """POST /api/v1/tasks 应在 mock 流水线下立即返回 task_id 与 pending 状态。"""
    def _fake_run(**kwargs):
        # 返回最小 state，不执行任何 LLM 调用
        from app.graph.state import new_state
        state = new_state(task_id=kwargs["task_id"], user_request=kwargs["user_request"])
        state["status"] = PipelineStatus(phase=PipelinePhase.DONE)
        state["review_report"] = ReviewReport(verdict=ReviewVerdict.APPROVED, score=100)
        return state

    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "app.api.v1.tasks.run_pipeline", side_effect=_fake_run,
    ):
        resp = client.post("/api/v1/tasks", json={"user_request": "写个加法函数"})
    assert resp.status_code == 200
    body = resp.json()
    assert "task_id" in body
    assert body["status"]["phase"] == "pending"


def test_delete_task(client: TestClient) -> None:
    """DELETE /api/v1/tasks/{task_id} 应能删除任务（不存在时返回 404）。"""
    # 先创建一个任务（mock 流水线）
    def _fake_run(**kwargs):
        from app.graph.state import new_state
        state = new_state(task_id=kwargs["task_id"], user_request=kwargs["user_request"])
        state["status"] = PipelineStatus(phase=PipelinePhase.DONE)
        return state

    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "app.api.v1.tasks.run_pipeline", side_effect=_fake_run,
    ):
        resp = client.post("/api/v1/tasks", json={"user_request": "to be deleted"})
        task_id = resp.json()["task_id"]

    # 删除
    del_resp = client.delete(f"/api/v1/tasks/{task_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "ok"

    # 再查应 404
    assert client.get(f"/api/v1/tasks/{task_id}").status_code == 404


# ------------------------------------------------------------------ #
#  异步冒烟（httpx.AsyncClient + ASGITransport + pytest-asyncio）
# ------------------------------------------------------------------ #
@pytest.fixture
async def async_client() -> AsyncIterator[httpx.AsyncClient]:
    """异步 HTTP 客户端，直接对话 ASGI app。"""
    from app.main import app
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.mark.asyncio
async def test_async_health_endpoint(async_client: httpx.AsyncClient) -> None:
    """异步客户端应能命中 /api/v1/health。"""
    resp = await async_client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_async_models_endpoint(async_client: httpx.AsyncClient) -> None:
    """异步客户端应能命中 /api/v1/models。"""
    resp = await async_client.get("/api/v1/models")
    assert resp.status_code == 200
    body = resp.json()
    assert "available" in body
    assert isinstance(body["available"], list)


@pytest.mark.asyncio
async def test_async_create_and_get_task(async_client: httpx.AsyncClient) -> None:
    """异步客户端应能 POST 创建任务并 GET 查询。"""
    from unittest.mock import patch

    def _fake_run(**kwargs):
        from app.graph.state import new_state
        state = new_state(task_id=kwargs["task_id"], user_request=kwargs["user_request"])
        state["status"] = PipelineStatus(phase=PipelinePhase.DONE)
        state["review_report"] = ReviewReport(verdict=ReviewVerdict.APPROVED, score=100)
        return state

    with patch("app.api.v1.tasks.run_pipeline", side_effect=_fake_run):
        resp = await async_client.post(
            "/api/v1/tasks", json={"user_request": "异步冒烟"},
        )
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    # 查询任务详情
    detail = await async_client.get(f"/api/v1/tasks/{task_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["task"]["task_id"] == task_id
