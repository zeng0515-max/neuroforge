# NeuroForge 项目记忆

## 项目概要
多智能体协同代码生成系统，LangGraph Supervisor 星型编排 4 Agent（需求分析→代码生成→双层评审→测试生成），评审不通过可带反馈回退重做。

## 关键架构决策
- LangGraph StateGraph 编排，Supervisor 模式路由
- 双层评审：语义评审（senior_review）+ 工具评审（tool_review）
- MCP 双向打通 + RAG 语义检索 + ChromaDB
- 事件驱动架构（EventBus）+ WebSocket 实时推送
- Docker Sandbox 安全代码执行（Local/Docker 双模式）
- 输出护栏：API Key 泄露/路径泄露/代码执行指令检测 + 自动脱敏
- 语义缓存惰性初始化（避免 chromadb ONNX 模型下载阻塞启动）

## 技术栈
- Python 3.11 + LangGraph + ChromaDB + FastAPI + WebSocket
- React Dashboard（web/dashboard.html）
- pytest 232 tests（单独运行全绿，混合跑有 async 事件循环作用域问题）

## 已知问题
- pytest-asyncio fixture scope 导致 api_smoke 与其他 async 测试混合时挂起
- ChromaDB ONNX embedding model 需网络下载（79MB），已改为惰性初始化
- LLM_API_KEY 未配置时自动进入 DryRun 模式

## 用户上下文
- 用户是大四软件工程专业学生，秋招冲刺
- 目标：靠这个项目拿大厂 offer
- 需要：面试演示用的 Dashboard、量化 Benchmark 数据、专业 README
