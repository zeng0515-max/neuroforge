"""v1 版本接口包。

包含路由模块：
- tasks:    任务管理（提交/查询/SSE 流式/审批）
- agents:   多智能体编排（运行/流式/分析/拆解/记忆）
- pipeline: 新版流水线（token 级流式/审计/指标/记忆/Agent 消息）
- execute:  代码执行验证与多轮对话修改
- eval:     评估指标与 CI 门禁
- misc:     健康检查/知识库/工具/模型/可观测性
- skills:   技能库管理
- websocket: WebSocket 实时推送
"""

from app.api.v1.pipeline import router as pipeline_router

__all__ = ["pipeline_router"]
