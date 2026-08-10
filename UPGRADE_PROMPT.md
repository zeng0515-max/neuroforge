# NeuroForge 全方位改造升级 Prompt

> **使用方式**：将本文件全部内容复制粘贴给 AI Agent（ChatGPT / Claude / DeepSeek / WorkBuddy 等），即可启动全方位改造。
> AI 会先联网调研、再分析项目、最后执行改造。建议配合 Claude Code / Cursor / WorkBuddy 等具备文件读写能力的工具使用。

---

## 一、角色与使命

你是一位顶级的 AI Agent 架构师兼全栈工程师，曾主导过 OpenHands、MetaGPT、Dify 等明星开源项目的设计。现在你需要对一个名为 **NeuroForge** 的多智能体协同代码生成系统进行全方位的改造升级。

你的工作分三个阶段：
1. **调研学习**：深入各大技术平台和开源社区，系统学习 2025-2026 年 AI Agent 领域的最新技术、最佳实践和架构模式
2. **差距分析**：将学到的知识与 NeuroForge 当前代码逐项对比，输出结构化的差距报告
3. **执行改造**：按照优先级对项目进行代码级改造，每一处改动都要达到生产级质量

---

## 二、项目背景

### 项目信息
- **项目名称**：NeuroForge — 多智能体协同代码生成与审查系统
- **GitHub**：https://github.com/zeng0515-max/neuroforge
- **本地路径**：`D:\multi-agent-code-system`
- **技术栈**：Python 3.11 + LangGraph 0.2.62 + ChromaDB + FastAPI + WebSocket + Three.js
- **测试**：438 tests passing，覆盖率 57%

### 当前架构概要
- LangGraph StateGraph 编排，Supervisor 星型模式路由 4 个 Agent
- 四个智能体：需求分析(RequirementAgent) → 代码生成(CodeGenAgent, ReAct) → 双层审查(ReviewAgent, AST+LLM) → 测试生成(TestGenAgent)
- 审查不通过可带反馈回退重做，形成闭环
- 双架构并存：旧版 `app/graph/pipeline.py`（Pipeline 状态机）+ 新版 `app/unified_orchestrator.py`（统一编排门面）
- 已有能力：MCP 双向打通、RAG 语义检索(ChromaDB)、EventBus 事件总线、WebSocket 实时推送、Docker Sandbox 安全执行、输出护栏(API Key 泄露检测+脱敏)、语义缓存(惰性初始化)、OpenTelemetry 追踪、HITL 人工审批节点、Checkpoint 断点续跑
- 前端：Three.js 3D 深空着陆页 + 实时管道仪表板（vanilla JS）

### 核心代码规模
- `app/agents/` — 5 个 Agent 文件，~5300 行（review_agent 1518 行，test_gen_agent 1654 行，code_gen_agent 1180 行）
- `app/core/` — 12 个核心模块，~4000 行（llm_adapter 996 行，memory_system 971 行，memory 526 行）
- `app/graph/` — 3 个编排文件，~3800 行（task_decomposer 1884 行，orchestrator 1095 行，pipeline 815 行）
- `app/tools/` — 8 个工具模块（AST 校验、代码执行、RAG、MCP、技能注册、浏览器工具等）
- `app/api/v1/` — 6 个 API 路由模块

### 我的个人情况（改造目标导向）
- **身份**：大四软件工程专业学生
- **状态**：秋招冲刺期，无实习经历
- **目标**：靠这个项目在秋招中拿到大厂（字节/腾讯/阿里/美团/百度等）的 offer
- **需求**：
  - 项目质量和深度达到大厂面试标准
  - 有可现场演示的 Dashboard
  - 有量化的 Benchmark 数据
  - README 专业度足以让面试官 3 分钟内理解项目价值
  - 架构设计经得起追问（"为什么选 LangGraph 不选 AutoGen？" "Supervisor 模式比线性流水线好在哪里？"）

---

## 三、Phase 1 — 调研学习（必须先做）

### 3.1 调研平台清单

请依次访问以下平台，搜索 AI Agent / Multi-Agent / LLM Application 相关的最新内容：

| 平台 | 搜索关键词 | 关注内容 |
|------|-----------|---------|
| **GitHub Trending** | `ai agent`, `multi-agent`, `llm framework` | 2025-2026 年高 Star 项目，看架构设计和 Release Notes |
| **GitHub 项目** | OpenHands, MetaGPT, ChatDev, LangGraph, CrewAI, AutoGen, SWE-agent, OpenManus, Dify, Coze, Agno, PydanticAI, SmolAgents | 核心架构、Agent Loop 设计、工具系统、沙箱方案 |
| **Arxiv** | `multi-agent llm`, `agent benchmark`, `agent framework` | 2025-2026 年论文，关注 SWE-Bench、AgentBench 最新数据 |
| **LangChain / LangGraph 官方文档** | Supervisor, Swarm, Handoffs, Memory, Checkpointer, Subgraphs | 生产级 Agent 编排最佳实践 |
| **掘金 / CSDN / 知乎** | `AI Agent 架构`, `多智能体`, `LangGraph 实战`, `Agent 工程化` | 中文社区的实战经验和踩坑总结 |
| **Hugging Face Blog** | `agent`, `smolagents`, `tools` | Hugging Face 团队的 Agent 设计理念 |
| **Anthropic Blog** | `Building effective agents`, `Tool use`, `Computer use` | Claude 团队的 Agent 设计哲学 |
| **OpenAI Cookbook** | `function calling`, `structured outputs`, `agents` | OpenAI 官方的 Agent 实现模式 |
| **Lil'Log (lilianweng.github.io)** | `LLM Agent`, `Agent Survey` | Lilian Weng 的 Agent 综述，业界权威 |
| **SWE-Bench Leaderboard** | 最新排名和方法 | 顶级 Agent 的评测数据 |

### 3.2 调研主题清单

对每个调研主题，输出：**行业最佳实践 → NeuroForge 当前状态 → 差距 → 改造建议**

#### A. Agent 编排架构
- [ ] Supervisor 模式 vs Swarm/Handoff 模式 vs 层级模式 — 各自适用场景和优劣
- [ ] Agent Loop 设计（OpenHands CodeAct vs ReAct vs Plan-Execute）
- [ ] 状态管理最佳实践（Checkpoint、Time Travel、State Schema 设计）
- [ ] Agent 间通信模式（消息总线 vs 直接调用 vs 共享黑板）
- [ ] 任务分解策略（MetaGPT SOP vs LangGraph Subgraph vs 递归分解）

#### B. 工具系统
- [ ] MCP (Model Context Protocol) 2026 最新进展和生态
- [ ] 工具权限分级和审批机制
- [ ] 工具发现和自动注册
- [ ] Structured Tool Output（Pydantic schema 驱动）
- [ ] 浏览器自动化工具集成（Browser Use / Playwright）

#### C. 沙箱与代码执行
- [ ] Docker 沙箱安全最佳实践（OpenHands / E2B / Daytona）
- [ ] 代码执行的资源限制和超时控制
- [ ] 沙箱环境的文件系统持久化
- [ ] 多语言代码执行支持

#### D. 评估与可观测性
- [ ] Agent 评估方法论（SWE-Bench、HumanEval、AgentBench）
- [ ] LLM 可观测性三件套：Tracing + Metrics + Logs
- [ ] LangFuse / LangSmith / Braintrust 集成方案
- [ ] Token 成本追踪和优化
- [ ] 幻觉检测和代码质量自动评估

#### E. 记忆与 RAG
- [ ] Agent 记忆系统设计（短期/长期/情景/语义）
- [ ] RAG 高级技术（HyDE、Re-ranking、Parent-Child Chunking、GraphRAG）
- [ ] 语义缓存的命中率优化
- [ ] 知识库的增量更新和版本管理

#### F. 安全与治理
- [ ] Prompt Injection 防护（OWASP LLM Top 10）
- [ ] 输出护栏（Guardrails AI / NeMo Guardrails）
- [ ] API Key 和敏感信息泄露检测
- [ ] 速率限制和配额管理
- [ ] Agent 行为审计日志

#### G. 前端与可视化
- [ ] Agent 执行过程的实时可视化方案
- [ ] 3D/WebGL 在 Dashboard 中的应用
- [ ] 流式输出（SSE / WebSocket）的前端处理最佳实践
- [ ] 可交互的 Agent 流程图（React Flow / Vue Flow）

#### H. 工程化与部署
- [ ] Agent 应用的 CI/CD 最佳实践
- [ ] Docker 容器化部署优化
- [ ] 多环境配置管理（dev/staging/prod）
- [ ] 性能基准测试和回归检测

### 3.3 调研输出要求

调研完成后，生成一份 **`RESEARCH_REPORT_V2.md`** 文件，结构如下：

```markdown
# NeuroForge V2 调研报告

## 一、行业最新趋势（2025-2026）
（从调研中提炼出 10 条最重要的趋势，每条附来源链接）

## 二、标杆项目深度对比
| 维度 | OpenHands | MetaGPT | LangGraph | CrewAI | Dify | NeuroForge(当前) |
|------|-----------|---------|-----------|--------|------|-----------------|
| 编排模式 | ... | ... | ... | ... | ... | ... |
| 沙箱方案 | ... | ... | ... | ... | ... | ... |
| ... | ... | ... | ... | ... | ... | ... |

## 三、差距分析（按优先级）
### P0 — 必须改造（面试必问）
### P1 — 重要改造（加分项）
### P2 — 锦上添花

## 四、改造方案
### 4.1 架构升级路线
### 4.2 代码级改造清单（文件 → 改动内容 → 预期效果）
### 4.3 新增模块设计
### 4.4 测试和评估方案
```

---

## 四、Phase 2 — 差距分析与改造规划

基于调研结果，对 NeuroForge 进行逐文件、逐模块的差距分析。以下是已知的改造方向（需根据调研结果补充和调整）：

### 4.1 架构层改造

| 改造项 | 当前状态 | 目标状态 | 涉及文件 |
|--------|---------|---------|---------|
| 双架构统一 | pipeline.py + unified_orchestrator.py 并存 | 统一到一套编排引擎，删除冗余 | `app/graph/`, `app/agent_executor.py` |
| Supervisor 智能体 | 线性流水线，无主管调度 | 真正的 Supervisor 模式，动态路由和重试决策 | `app/graph/pipeline.py` |
| Agent 间通信 | 直接函数调用 | 事件驱动消息总线（参考 OpenHands EventStream） | `app/core/events.py` |
| 任务分解 | task_decomposer.py 1884 行单文件 | 模块化分解，支持递归和并行子任务 | `app/graph/task_decomposer.py` |
| Plan-Execute 模式 | 无 | 增加 Plan→Execute→Reflect 循环 | 新增 `app/agents/planner_agent.py` |

### 4.2 Agent 能力改造

| 改造项 | 当前状态 | 目标状态 | 涉及文件 |
|--------|---------|---------|---------|
| 多模型混用 | 单模型配置 | 按任务类型选模型（便宜模型做审查，强模型做生成） | `app/core/config.py`, `app/core/llm_adapter.py` |
| 流式输出 | 仅轮询/SSE | 真正的 Token 级流式输出 | `app/core/llm_adapter.py`, API 层 |
| Agent 自主工具选择 | 固定工具集 | 动态工具发现和自主选择 | `app/tools/tool_registry.py` |
| 记忆系统 | 基础记忆 | 多层记忆（短期对话 + 长期经验 + 情景记忆） | `app/core/memory.py`, `app/core/memory_system.py` |
| MicroAgent 插件 | 无插件系统 | 支持注入领域特定 prompt 和工具的插件机制 | 新增 `app/plugins/` |

### 4.3 工具系统改造

| 改造项 | 当前状态 | 目标状态 | 涉及文件 |
|--------|---------|---------|---------|
| 工具权限分级 | 无权限控制 | 低/中/高风险三级，高风险需审批 | `app/tools/tool_registry.py` |
| 浏览器工具 | browser_tools.py 基础版 | 集成 Browser Use 或 Playwright | `app/tools/browser_tools.py` |
| Structured Output | 部分 | 所有工具输出统一 Pydantic Schema | `app/tools/` |
| MCP 生态扩展 | 基础 MCP 双向 | 预置常用 MCP Server（文件系统、搜索、Git） | `app/tools/mcp_server.py` |

### 4.4 评估与可观测性改造

| 改造项 | 当前状态 | 目标状态 | 涉及文件 |
|--------|---------|---------|---------|
| Benchmark 数据 | README 有基础数据 | 可复现的自动化 Benchmark 脚本 + 量化报告 | `scripts/benchmark.py` |
| 可观测性看板 | observability.py 数据收集 | 集成 LangFuse 或自建 Grafana 看板 | `app/core/observability.py` |
| Token 成本追踪 | 基础统计 | 按任务/Agent/阶段细粒度追踪 | `app/core/llm_adapter.py` |
| CI 门禁 | 无 | PR 自动触发测试 + 评估 + 质量门禁 | 新增 `.github/workflows/` |

### 4.5 安全改造

| 改造项 | 当前状态 | 目标状态 | 涉及文件 |
|--------|---------|---------|---------|
| Prompt Injection 防护 | guardrails.py 基础版 | 集成 OWASP LLM Top 10 防护清单 | `app/core/guardrails.py` |
| 输出脱敏 | 基础 API Key 检测 | 正则 + 启发式 + LLM 三重检测 | `app/core/guardrails.py` |
| 审计日志 | 无 | 全链路审计日志（谁/何时/做了什么） | 新增 `app/core/audit.py` |

### 4.6 前端改造

| 改造项 | 当前状态 | 目标状态 | 涉及文件 |
|--------|---------|---------|---------|
| Dashboard 交互 | vanilla JS + 基础可视化 | 可交互的 Agent 流程图 + 实时 Token 流 | `web/dashboard.html`, `web/js/` |
| 着陆页 | Three.js 3D 深空 | 优化性能 + 增加项目展示动效 | `web/index.html` |
| 移动端适配 | 无 | 响应式设计 | `web/css/` |

### 4.7 文档与面试准备改造

| 改造项 | 当前状态 | 目标状态 | 涉及文件 |
|--------|---------|---------|---------|
| README | 已有中英双语 | 增加架构决策记录(ADR)、面试 FAQ、演示 GIF | `README.md` |
| 技术文档 | 有 doccx | 更新为 V2 架构文档 | `docs/` |
| 面试指南 | 有 INTERVIEW_GUIDE.md | 补充 V2 新特性的面试问答 | `docs/INTERVIEW_GUIDE.md` |
| 演示脚本 | 无 | 一键演示脚本 + 演示话术 | 新增 `scripts/demo.py` |

---

## 五、Phase 3 — 执行改造（代码级）

### 5.1 执行原则

1. **渐进式改造**：每次只改一个模块，改完跑测试，确保 438 tests 不回退
2. **向后兼容**：旧 API 保持可用，新功能通过新 API 或 feature flag 开放
3. **代码质量**：
   - 每个函数不超过 80 行，每个文件不超过 600 行（当前 review_agent 1518 行、test_gen_agent 1654 行需拆分）
   - 所有公开函数有 docstring + type hints
   - 复杂逻辑有注释说明"为什么这么做"
   - 遵循 SOLID 原则和设计模式
4. **测试先行**：新增功能先写测试，改造旧代码先补测试
5. **提交规范**：每个改造点一个 commit，message 格式 `feat/fix/refactor(scope): description`

### 5.2 执行顺序

```
Step 1: 调研报告（输出 RESEARCH_REPORT_V2.md）
Step 2: 差距分析（更新 RESEARCH_REPORT_V2.md 的差距分析章节）
Step 3: 架构统一（合并 pipeline.py + unified_orchestrator.py）
Step 4: Supervisor 模式实现（真正的动态路由和重试决策）
Step 5: 事件驱动架构重构（Agent 间通信改为消息总线）
Step 6: Agent 拆分和重构（大文件拆分 + Plan-Execute 模式）
Step 7: 多模型混用（配置层 + 适配器层）
Step 8: 流式输出（LLM 适配器 + API + WebSocket）
Step 9: 工具系统升级（权限分级 + Structured Output + MCP 扩展）
Step 10: 记忆系统升级（多层记忆）
Step 11: 评估系统升级（自动化 Benchmark + CI 门禁）
Step 12: 安全升级（Prompt Injection 防护 + 审计日志）
Step 13: 前端升级（可交互流程图 + Token 流）
Step 14: 文档升级（README V2 + ADR + 面试 FAQ + 演示脚本）
Step 15: 全量测试 + Benchmark 运行 + 性能优化
```

### 5.3 代码规范要求

```python
# ✅ 好的代码示例
class SupervisorAgent:
    """Supervisor 智能体：负责动态路由和重试决策。

    使用 LangGraph conditional edges 实现基于状态的动态路由。
    当审查失败时，根据失败原因选择重试代码生成或直接修改。

    Design Decision:
        选择 Supervisor 而非 Swarm 模式，因为代码生成流程
        有明确的阶段性和顺序依赖（需求→代码→审查→测试），
        Supervisor 的集中控制更适合这种场景。

    References:
        - LangGraph Supervisor: https://langchain-ai.github.io/langgraph/
        - MetaGPT SOP: https://github.com/geekan/MetaGPT
    """

    MAX_RETRIES = 3  # 最多重试 3 次，避免无限循环

    def __init__(self, config: SupervisorConfig):
        self._config = config
        self._router = self._build_router()

    def route(self, state: PipelineState) -> str:
        """根据当前状态决定下一个 Agent。"""
        ...
```

### 5.4 测试要求

- 新增功能必须有单元测试，覆盖率 ≥ 80%
- 改造旧代码时，先补充测试再改造
- 关键路径（Pipeline 端到端）必须有集成测试
- Benchmark 脚本可独立运行，输出 JSON + Markdown 报告

---

## 六、面试导向的质量标准

这个项目的最终用户是面试官。改造后的项目必须经得起以下追问：

### 6.1 架构设计追问
- "为什么选 LangGraph 不选 AutoGen / CrewAI？" → 需要有对比分析和决策记录
- "Supervisor 模式比线性流水线好在哪里？具体解决了什么问题？" → 需要有 before/after 对比
- "双架构为什么合并？合并后怎么保证向后兼容？" → 需要有迁移方案
- "Agent 间通信为什么用消息总线而不是直接调用？" → 需要有性能和解耦的量化对比

### 6.2 工程能力追问
- "你的 438 个测试覆盖了哪些场景？覆盖率 57% 够吗？" → 需要有覆盖率提升方案
- "多模型混用怎么实现的？省钱效果如何？" → 需要有 Token 成本对比数据
- "沙箱执行怎么保证安全？" → 需要有安全分析报告
- "语义缓存的命中率是多少？怎么测的？" → 需要有缓存命中率 Benchmark

### 6.3 深度追问
- "LangGraph 的 Checkpointer 底层是怎么实现的？你用的时候遇到过什么坑？" → 需要有源码级理解
- "ChromaDB 的 ONNX embedding model 下载问题你是怎么解决的？" → 需要有问题排查记录
- "如果你的 Agent 产生了幻觉代码，审查层能拦住吗？怎么保证？" → 需要有幻觉检测方案

### 6.4 项目亮点追问
- "这个项目最让你自豪的技术决策是什么？" → 需要有 2-3 个亮点可讲
- "如果重新做，你会怎么改？" → 需要有反思和迭代计划
- "这个项目和你面试的岗位有什么关系？" → 需要有岗位匹配分析

---

## 七、交付物清单

改造完成后，需交付以下内容：

| 序号 | 交付物 | 路径 | 说明 |
|------|--------|------|------|
| 1 | 调研报告 V2 | `RESEARCH_REPORT_V2.md` | 含行业趋势、标杆对比、差距分析、改造方案 |
| 2 | 改造后的代码 | `app/` 全目录 | 所有改造点的代码实现 |
| 3 | 测试报告 | `tests/` + `TEST_REPORT_V2.md` | 新增测试 + 覆盖率报告 |
| 4 | Benchmark 报告 | `scripts/benchmark.py` + `BENCHMARK_V2.md` | 可复现的量化数据 |
| 5 | README V2 | `README.md` | 含 ADR、面试 FAQ、演示 GIF |
| 6 | 架构文档 V2 | `docs/ARCHITECTURE_V2.md` | V2 架构设计文档 |
| 7 | 面试指南 V2 | `docs/INTERVIEW_GUIDE.md` | 补充 V2 新特性的面试问答 |
| 8 | 演示脚本 | `scripts/demo.py` | 一键演示 + 演示话术 |
| 9 | Docker 部署优化 | `Dockerfile` + `docker-compose.yml` | 多阶段构建 + 体积优化 |
| 10 | CI/CD 配置 | `.github/workflows/ci.yml` | 自动测试 + 评估 + 门禁 |

---

## 八、约束条件

1. **不破坏现有功能**：438 tests 必须持续通过
2. **不引入不必要的依赖**：新依赖需在调研报告中说明理由
3. **不虚构数据**：Benchmark 数据必须真实可复现
4. **不过度设计**：每处改造都要能回答"这解决了什么问题"
5. **保持 Python 3.11 + LangGraph 技术栈**：不切换框架
6. **环境约束**：Windows 开发环境，bash shell，注意路径格式
7. **LLM API**：使用 OpenAI 兼容 API，未配置 Key 时进入 DryRun 模式

---

## 九、启动指令

请按以下步骤开始：

1. **先读项目代码**：通读 `app/` 目录下的所有 Python 文件，理解当前架构
2. **再联网调研**：按 §3.1 的平台清单和 §3.2 的主题清单进行调研
3. **输出调研报告**：生成 `RESEARCH_REPORT_V2.md`
4. **制定改造计划**：基于差距分析，列出具体的改造清单（文件 → 改动 → 优先级）
5. **逐项执行改造**：按 §5.2 的顺序执行，每步完成后跑测试
6. **输出交付物**：按 §7 的清单交付所有文件

**现在开始执行。先从通读项目代码和联网调研开始。**
