"""Agent 协议层单元测试。

覆盖:
- AgentProfile 创建与能力匹配
- AgentMessage 序列化 / 反序列化
- AgentInteractionProtocol 四种交互模式（direct / broadcast / delegation / result_sharing）
- AgentTask 状态流转
- 枚举类型约束
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.agents.agent_protocol import (
    AgentCapability,
    AgentInteractionProtocol,
    AgentMessage,
    AgentProfile,
    AgentRole,
    AgentStatus,
    AgentTask,
    MessageType,
    TaskPriority,
)


# ====================================================================== #
#  AgentProfile —— 创建与能力匹配
# ====================================================================== #


class TestAgentProfile:
    """AgentProfile 创建与能力匹配。"""

    def test_create_minimal_profile(self):
        """最小化 Profile 创建。"""
        profile = AgentProfile(name="coder", role=AgentRole.CODER)
        assert profile.name == "coder"
        assert profile.role == AgentRole.CODER
        assert profile.capabilities == []
        assert profile.system_prompt == ""

    def test_create_full_profile(self):
        """完整 Profile 创建。"""
        profile = AgentProfile(
            name="reviewer",
            role=AgentRole.REVIEWER,
            capabilities=[AgentCapability.CODE_REVIEW, AgentCapability.ANALYSIS],
            system_prompt="You are a code reviewer.",
            description="Reviews code for quality.",
            model="gpt-4",
            metadata={"team": "backend"},
        )
        assert profile.name == "reviewer"
        assert len(profile.capabilities) == 2
        assert profile.model == "gpt-4"
        assert profile.metadata["team"] == "backend"

    def test_has_capability(self):
        """has_capability 应正确判断能力。"""
        profile = AgentProfile(
            name="coder",
            role=AgentRole.CODER,
            capabilities=[AgentCapability.CODE_GENERATION, AgentCapability.TESTING],
        )
        assert profile.has_capability(AgentCapability.CODE_GENERATION) is True
        assert profile.has_capability(AgentCapability.TESTING) is True
        assert profile.has_capability(AgentCapability.CODE_REVIEW) is False

    def test_role_values(self):
        """AgentRole 枚举值应正确。"""
        assert AgentRole.ARCHITECT == "architect"
        assert AgentRole.CODER == "coder"
        assert AgentRole.REVIEWER == "reviewer"
        assert AgentRole.TESTER == "tester"
        assert AgentRole.ORCHESTRATOR == "orchestrator"
        assert AgentRole.ANALYST == "analyst"
        assert AgentRole.DEPLOYER == "deployer"

    def test_capability_values(self):
        """AgentCapability 枚举值应正确。"""
        expected = {
            "code_generation", "code_review", "testing", "analysis",
            "planning", "deployment", "communication", "memory_access",
        }
        actual = {c.value for c in AgentCapability}
        assert actual == expected


# ====================================================================== #
#  AgentMessage —— 序列化
# ====================================================================== #


class TestAgentMessage:
    """AgentMessage 创建与序列化。"""

    def test_create_message_defaults(self):
        """默认消息创建。"""
        msg = AgentMessage(sender="a", receiver="b", content="hello")
        assert msg.sender == "a"
        assert msg.receiver == "b"
        assert msg.content == "hello"
        assert msg.msg_type == MessageType.DIRECT
        assert msg.payload == {}
        assert msg.message_id is not None
        assert msg.timestamp is not None
        assert msg.correlation_id is None

    def test_message_serialization(self):
        """to_json 序列化与反序列化。"""
        msg = AgentMessage(
            sender="orch",
            receiver="coder",
            content="Please implement login",
            msg_type=MessageType.TASK_ASSIGN,
            payload={"task_type": "auth", "priority": "high"},
            correlation_id="corr_123",
        )
        json_str = msg.model_dump_json()
        data = json.loads(json_str)
        assert data["sender"] == "orch"
        assert data["receiver"] == "coder"
        assert data["content"] == "Please implement login"
        assert data["msg_type"] == "task_assign"
        assert data["payload"]["task_type"] == "auth"
        assert data["correlation_id"] == "corr_123"

    def test_message_deserialization(self):
        """从字典反序列化。"""
        data = {
            "sender": "a",
            "receiver": "b",
            "content": "test",
            "msg_type": "broadcast",
            "message_id": "msg_001",
        }
        msg = AgentMessage.model_validate(data)
        assert msg.sender == "a"
        assert msg.msg_type == MessageType.BROADCAST
        assert msg.message_id == "msg_001"

    def test_message_unique_ids(self):
        """每条消息应有唯一 message_id。"""
        msg1 = AgentMessage(sender="a", receiver="b", content="m1")
        msg2 = AgentMessage(sender="a", receiver="b", content="m2")
        assert msg1.message_id != msg2.message_id

    def test_message_types(self):
        """MessageType 枚举值应正确。"""
        assert MessageType.DIRECT == "direct"
        assert MessageType.BROADCAST == "broadcast"
        assert MessageType.TASK_ASSIGN == "task_assign"
        assert MessageType.TASK_RESULT == "task_result"
        assert MessageType.REQUEST == "request"
        assert MessageType.RESPONSE == "response"
        assert MessageType.NOTIFICATION == "notification"
        assert MessageType.ERROR == "error"


# ====================================================================== #
#  AgentTask —— 状态流转
# ====================================================================== #


class TestAgentTask:
    """AgentTask 创建与状态流转。"""

    def test_create_task_defaults(self):
        """默认任务创建。"""
        task = AgentTask(description="实现登录")
        assert task.description == "实现登录"
        assert task.status == AgentStatus.IDLE
        assert task.priority == TaskPriority.MEDIUM
        assert task.progress == 0
        assert task.retry_count == 0
        assert task.result is None

    def test_advance_progress(self):
        """推进进度。"""
        task = AgentTask(description="test")
        task.advance_progress(25)
        assert task.progress == 25
        task.advance_progress(25)
        assert task.progress == 50

    def test_progress_capped_at_100(self):
        """进度上限为 100。"""
        task = AgentTask(description="test")
        task.advance_progress(200)
        assert task.progress == 100

    def test_mark_completed(self):
        """标记完成。"""
        task = AgentTask(description="test")
        task.mark_completed(result={"output": "ok"})
        assert task.status == AgentStatus.COMPLETED
        assert task.progress == 100
        assert task.result == {"output": "ok"}

    def test_mark_failed(self):
        """标记失败。"""
        task = AgentTask(description="test")
        task.mark_failed("something broke")
        assert task.status == AgentStatus.FAILED
        assert task.error_message == "something broke"

    def test_mark_waiting(self):
        """标记等待中。"""
        task = AgentTask(description="test", dependencies=["prev"])
        task.mark_waiting()
        assert task.status == AgentStatus.WAITING

    def test_task_priority_values(self):
        """TaskPriority 枚举值应正确。"""
        assert TaskPriority.LOW == "low"
        assert TaskPriority.MEDIUM == "medium"
        assert TaskPriority.HIGH == "high"
        assert TaskPriority.CRITICAL == "critical"

    def test_agent_status_values(self):
        """AgentStatus 枚举值应正确。"""
        expected = {"idle", "working", "waiting", "completed", "failed", "suspended"}
        actual = {s.value for s in AgentStatus}
        assert actual == expected

    def test_task_touch_updates_timestamp(self):
        """touch() 应更新 updated_at。"""
        task = AgentTask(description="test")
        old_ts = task.updated_at
        task.touch()
        assert task.updated_at >= old_ts


# ====================================================================== #
#  AgentInteractionProtocol —— 交互
# ====================================================================== #


class TestAgentInteractionProtocol:
    """AgentInteractionProtocol 四种交互模式。"""

    @pytest.fixture
    def protocol(self):
        """创建已注册 Agent 的协议实例。"""
        proto = AgentInteractionProtocol()
        proto.register_agent(AgentProfile(
            name="coder",
            role=AgentRole.CODER,
            capabilities=[AgentCapability.CODE_GENERATION],
        ))
        proto.register_agent(AgentProfile(
            name="reviewer",
            role=AgentRole.REVIEWER,
            capabilities=[AgentCapability.CODE_REVIEW],
        ))
        proto.register_agent(AgentProfile(
            name="tester",
            role=AgentRole.TESTER,
            capabilities=[AgentCapability.TESTING],
        ))
        return proto

    def test_register_and_get_agent(self, protocol):
        """注册与获取 Agent。"""
        agent = protocol.get_agent("coder")
        assert agent is not None
        assert agent.name == "coder"
        assert protocol.get_agent("nonexistent") is None

    def test_unregister_agent(self, protocol):
        """注销 Agent。"""
        protocol.unregister_agent("coder")
        assert protocol.get_agent("coder") is None

    def test_direct_message(self, protocol):
        """点对点直接消息。"""
        msg = protocol.direct_message(
            sender="coder",
            receiver="reviewer",
            content="Code is ready for review.",
            payload={"code_id": "c_001"},
        )
        assert msg.msg_type == MessageType.DIRECT
        assert msg.sender == "coder"
        assert msg.receiver == "reviewer"
        assert len(protocol.message_log) == 1

    def test_broadcast(self, protocol):
        """广播消息。"""
        messages = protocol.broadcast(
            sender="coder",
            content="System will restart briefly.",
        )
        assert len(messages) == 2
        receivers = {m.receiver for m in messages}
        assert "reviewer" in receivers
        assert "tester" in receivers
        assert "coder" not in receivers
        assert all(m.msg_type == MessageType.BROADCAST for m in messages)

    def test_broadcast_with_exclude(self, protocol):
        """排除指定 Agent 的广播。"""
        messages = protocol.broadcast(
            sender="coder",
            content="Hello",
            exclude=["reviewer"],
        )
        assert len(messages) == 1
        assert messages[0].receiver == "tester"

    def test_task_delegation(self, protocol):
        """任务委派。"""
        task = protocol.task_delegation(
            sender="orchestrator",
            receiver="coder",
            description="实现用户登录模块",
            priority=TaskPriority.HIGH,
        )
        assert task.assigned_to == "coder"
        assert task.status == AgentStatus.IDLE
        assert task.priority == TaskPriority.HIGH
        assert len(protocol.task_log) == 1
        assert len(protocol.message_log) == 1
        assert protocol.message_log[0].msg_type == MessageType.TASK_ASSIGN

    def test_task_delegation_with_dependencies(self, protocol):
        """带依赖的任务委派应标记为 WAITING。"""
        task = protocol.task_delegation(
            sender="orch",
            receiver="coder",
            description="实现功能",
            dependencies=["prev_task"],
        )
        assert task.status == AgentStatus.WAITING

    def test_result_sharing(self, protocol):
        """结果共享。"""
        task = protocol.task_delegation(
            sender="orch",
            receiver="coder",
            description="实现功能",
        )
        msg = protocol.result_sharing(
            sender="coder",
            receiver="orch",
            task_id=task.task_id,
            result={"code": "def login(): pass"},
        )
        assert msg.msg_type == MessageType.TASK_RESULT
        assert msg.payload["status"] == "completed"
        updated = protocol.find_task(task.task_id)
        assert updated is not None
        assert updated.status == AgentStatus.COMPLETED

    def test_find_task(self, protocol):
        """按 ID 查找任务。"""
        task = protocol.task_delegation(
            sender="orch",
            receiver="coder",
            description="test",
        )
        found = protocol.find_task(task.task_id)
        assert found is not None
        assert found.task_id == task.task_id
        assert protocol.find_task("nonexistent") is None

    def test_get_pending_tasks(self, protocol):
        """获取等待中/工作中的任务。"""
        protocol.task_delegation(
            sender="orch", receiver="coder", description="t1",
        )
        protocol.task_delegation(
            sender="orch", receiver="coder", description="t2",
        )
        pending = protocol.get_pending_tasks("coder")
        assert len(pending) == 2
        all_pending = protocol.get_pending_tasks()
        assert len(all_pending) == 2

    def test_get_pending_tasks_excludes_completed(self, protocol):
        """已完成任务不应出现在 pending 中。"""
        task = protocol.task_delegation(
            sender="orch", receiver="coder", description="t1",
        )
        task.mark_completed(result={"ok": True})
        pending = protocol.get_pending_tasks()
        assert len(pending) == 0

    def test_get_messages_by_sender(self, protocol):
        """按发送方过滤消息。"""
        protocol.direct_message("a", "b", "m1")
        protocol.direct_message("b", "a", "m2")
        a_msgs = protocol.get_messages(sender="a")
        assert len(a_msgs) == 1
        assert a_msgs[0].content == "m1"

    def test_get_messages_by_type(self, protocol):
        """按消息类型过滤。"""
        protocol.direct_message("a", "b", "m1")
        protocol.broadcast("a", "m2")
        directs = protocol.get_messages(msg_type=MessageType.DIRECT)
        assert len(directs) == 1
