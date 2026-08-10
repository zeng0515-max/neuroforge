# NeuroForge V2 调研报告

> 调研时间：2025-2026
> 调研范围：AI Agent 架构、工具系统、评估体系、安全治理、前端可视化
> 调研方法：GitHub Trending、Arxiv、LangGraph 官方文档、技术社区深度分析、项目代码通读

---

## 一、行业最新趋势（2025-2026）

### 趋势 1：Supervisor 模式成为多 Agent 编排主流

2025 年以来，**Supervisor（主管）模式** 已成为多智能体编排的事实标准。LangGraph 官方在 0.2.x 版本中强化了 Supervisor + Handoffs 模式，相比早期的 Swarm 和线性流水线：

- **Supervisor 模式**：集中式控制，由一个主管 Agent 决定何时调用哪个子 Agent，适合有明确阶段性依赖的任务（如代码生成流水线）
- **Swarm 模式**：去中心化，Agent 自主决定通信对象，适合探索性任务
- **Handoffs 模式**：Agent 间直接交接对话上下文，适合客服/多角色协作场景

**NeuroForge 当前状态**：已实现 Supervisor 节点（`supervisor_node`），具备动态路由能力，架构方向正确。但 Supervisor 决策逻辑仍为硬编码的 `if-elif` 链，缺乏基于 LLM 的智能路由。

### 趋势 2：Plan-Execute-Reflect 循环成为 Agent Loop 标配

OpenHands、SWE-agent 等标杆项目均采用 **Plan → Execute → Reflect** 三段式 Agent Loop：

- **Plan**：LLM 生成执行计划和子任务列表
- **Execute**：逐步执行计划中的每个步骤
- **Reflect**：执行完成后反思总结，决定是否需要重新规划

相比传统的 ReAct（Reason + Act）模式，Plan-Execute 更适合复杂任务的分解和长程规划。

**NeuroForge 当前状态**：仅有 ReAct 模式，缺乏 Plan-Execute 循环。`code_gen_agent.py` 实现了 ReAct 推理-行动模式，但没有独立的 Planner Agent。

### 趋势 3：MCP（Model Context Protocol）协议快速普及

MCP 协议自 2025 年初由 Anthropic 发布以来，已成为 Agent 与外部工具/数据源集成的标准协议。核心更新：

- **2025-03-26 重大更新**：规范了工具注册、资源访问、Prompt 模板三大原语
- **生态系统**：已有 100+ 官方 MCP Server（文件系统、Git、搜索、数据库等）
- **双向通信**：支持 Agent 主动发现和调用外部服务

**NeuroForge 当前状态**：已实现 MCP 双向打通（`mcp_client.py` + `mcp_server.py`），但仅支持基础工具调用，未集成官方 MCP Server 生态。

### 趋势 4：SWE-Bench 成为代码 Agent 评估基准

SWE-Bench 已成为代码生成 Agent 的事实标准评估基准：

- **SWE-Bench Verified**：真实 GitHub Issue 修复，最接近实际编程场景
- **2025 年开源榜单**：CGM 以 50.40% 分数位居开源第一
- **关键洞察**：Agent 能力由模型和 harness 共同决定，同一模型换一套框架成绩差 27%

**NeuroForge 当前状态**：缺乏标准化 Benchmark，仅有功能验证测试，无 SWE-Bench 对齐。

### 趋势 5：可观测性三件套（Tracing + Metrics + Logs）成标配

2025 年 AI Agent 可观测性已形成标准三件套：

- **Tracing**：LangFuse / LangSmith 做链路追踪，每次模型调用、工具调用、状态转移都可回放
- **Metrics**：Token 消耗、延迟、成功率等量化指标
- **Logs**：结构化日志记录 Agent 行为

**NeuroForge 当前状态**：自研 MetricsCollector + AlertManager，功能可用但缺乏专业级 Tracing 和可视化看板。未集成 LangFuse/LangSmith。

### 趋势 6：Docker/MicroVM 沙箱成代码执行标准

主流代码 Agent 均使用容器化沙箱执行代码：

- **E2B**：SaaS 级沙箱，支持代码执行、浏览器自动化
- **Daytona**：OCI 原生沙箱，已被 OpenAI Agents SDK 选为官方提供商
- **Kubernetes Agent Sandbox**：K8s 官方项目，支持私有化部署
- **OpenHands**：自带 Workspace 沙箱，支持文件系统持久化

**NeuroForge 当前状态**：`SandboxConfig` 支持 local/docker 两种模式，但 Docker 模式实现较浅，缺乏 MicroVM 隔离和文件系统持久化。

### 趋势 7：Prompt Injection 防护进入 Agent 专项

OWASP LLM Top 10 2025 新增了 Agent 时代的安全威胁：

- **Agentic Skills Top 10**：Agent 技能专项风险
- **MCP Top 10**：MCP 协议专项风险
- **核心威胁**：提示注入、间接注入（通过工具返回结果注入）、权限绕过

**NeuroForge 当前状态**：`guardrails.py` 实现了基础正则检测，但缺乏 LLM 辅助的语义级注入检测和 Agent 专项防护。

### 趋势 8：多模型混合路由成为成本优化标配

2025 年起，主流 Agent 系统普遍采用按任务类型选择不同模型的策略：

- 便宜模型（如 GPT-4o-mini）做审查、测试生成
- 强模型（如 Claude 3.5 Sonnet）做代码生成
- 可节省 40-60% Token 成本

**NeuroForge 当前状态**：已配置 `ModelRoutingConfig`，路由规则为 `code_gen:code, review:main, test_gen:code`，但实际路由逻辑较为简单。

### 趋势 9：RAG 进入 Modular RAG 阶段

RAG 技术已从基础 RAG（2023）→ Advanced RAG（2024）→ Modular RAG（2025）演进：

- **Parent-Child Chunking**：小块检索 + 大块投喂，兼顾检索精度和上下文完整性
- **HyDE**：假设性文档嵌入，提升语义匹配
- **GraphRAG**：知识图谱增强，支持多跳推理
- **Self-RAG**：让 LLM 自主决定是否需要检索

**NeuroForge 当前状态**：基础 RAG 实现（ChromaDB + embedding），未实现高级 RAG 技术。

### 趋势 10：3D/WebGL 可视化在 Dashboard 中的应用

Agent 执行过程的实时可视化成为差异化亮点：

- 行星/节点动画表示 Agent 状态
- 粒子流表示 Token 流
- 引力线表示 Agent 间通信

**NeuroForge 当前状态**：已有 Three.js 3D 着陆页（行星+粒子），视觉表现出色。Dashboard 为 vanilla JS 基础版本，缺乏 3D 交互。

---

## 二、标杆项目深度对比

| 维度 | OpenHands | MetaGPT | LangGraph | CrewAI | Dify | NeuroForge(当前) |
|------|-----------|---------|-----------|--------|------|-----------------|
| **编排模式** | 单 Agent CodeAct | SOP 流水线 | StateGraph 任意拓扑 | 角色+任务+流程 | Workflow DSL | Supervisor + 线性 fallback |
| **Agent Loop** | CodeAct (行动编码) | 固定 SOP 步骤 | 自定义 | ReAct + Plan | 自定义 | ReAct |
| **沙箱方案** | Workspace 自研 | 无 | 依赖外部 | 无 | 内置代码执行 | Local/Docker 基础版 |
| **工具系统** | Browser/Sandbox/File | 有限工具 | Tool Node | Tool 装饰器 | Tool 工作流 | ToolRegistry + MCP |
| **记忆系统** | Session 级 | 有限 | Checkpointer | 有限 | 对话记忆 | 三层记忆 + 语义缓存 |
| **评估体系** | SWE-Bench | 无 | 无 | 无 | 内置评测 | 基础测试 + CI 门禁 |
| **可观测性** | 自研 Trace | 无 | LangSmith 集成 | 无 | 可视化日志 | MetricsCollector |
| **安全防护** | 基础 | 无 | 基础 | 无 | 基础 | Guardrails + AST 校验 |
| **前端** | Web IDE | 无 | 无 | 无 | 可视化编辑器 | 3D 着陆页 + Dashboard |
| **多模型** | 单模型 | 单模型 | 多模型路由 | 有限 | 多模型路由 | 配置层路由 |
| **MCP 集成** | 无 | 无 | 社区方案 | 无 | 内置插件 | 双向打通 |

---

## 三、差距分析（按优先级）

### P0 — 必须改造（面试必问）

#### G1：双架构并存，缺乏统一入口

**问题**：`pipeline.py`（LangGraph StateGraph）+ `unified_orchestrator.py`（自研 DAG 引擎）+ `LinearPipeline` 三套执行路径并存，维护成本高，且容易造成混淆。

**影响**：面试官会问"为什么有两个编排引擎？"，无法给出清晰回答。

**改造方案**：统一到一套编排引擎，删除冗余代码。

#### G2：Supervisor 决策逻辑硬编码

**问题**：`supervisor_node()` 使用 `if-elif` 链做路由决策，无法根据 Review 反馈动态调整策略（如代码质量差时增加一轮生成+审查）。

**影响**：Supervisor 退化为"按阶段顺序执行"，未发挥 Supervisor 模式的核心优势。

**改造方案**：引入 LLM 驱动的 Supervisor，让模型根据当前状态智能决策下一步。

#### G3：缺乏 Plan-Execute Agent Loop

**问题**：代码生成 Agent 仅有 ReAct 模式，没有 Plan-Execute-Reflect 循环。对于复杂任务（如设计一个完整的 Web 应用），缺乏分步规划和反思能力。

**影响**：复杂任务的成功率和代码质量受限。

**改造方案**：新增 `PlannerAgent`，实现 Plan→Execute→Reflect 循环。

#### G4：缺乏标准化 Benchmark

**问题**：无 SWE-Bench 对齐的 Benchmark 脚本，无法量化系统能力。438 个测试是功能测试，不是性能 Benchmark。

**影响**：面试时无法给出"这个系统修复了 X% 的 Bug"这类量化数据。

**改造方案**：新增 `scripts/benchmark.py`，集成 SWE-Bench Lite 评估。

#### G5：缺乏专业级可观测性

**问题**：自研 MetricsCollector 功能可用但缺乏可视化看板和 Tracing 回放能力。无 LangFuse/LangSmith 集成。

**影响**：无法展示 Agent 执行过程的全貌，面试演示效果打折扣。

**改造方案**：集成 LangFuse，实现链路追踪 + Token 成本看板。

#### G6：代码文件过大，违反工程规范

**问题**：
- `review_agent.py`：1518 行
- `test_gen_agent.py`：1654 行
- `task_decomposer.py`：1884 行
- `llm_adapter.py`：996 行

均远超单个文件 600 行的标准。

**影响**：代码可维护性差，面试时展示代码结构不优雅。

**改造方案**：按职责拆分大文件。

### P1 — 重要改造（加分项）

#### G7：RAG 技术待升级

**问题**：当前仅实现基础 RAG（ChromaDB + BGE-M3 embedding），未实现 Parent-Child Chunking、HyDE、Re-ranking 等高级技术。

**改造方案**：实现 Parent-Child Chunking + 简单 Re-ranking。

#### G8：工具权限分级不完善

**问题**：`ToolRegistry` 已实现 FREE/OBSERVE/MODIFY/DANGEROUS 四级权限，但实际工具注册时未正确分配权限等级，也缺乏高风险操作的审批流程。

**改造方案**：完善工具权限配置，实现高风险工具的人工审批。

#### G9：Token 成本追踪粒度不够

**问题**：`MetricsCollector` 仅按模型和 Agent 汇总 Token，缺少按任务/阶段/工具的细粒度追踪。

**改造方案**：实现按任务→阶段→Agent→工具的四级 Token 成本追踪。

#### G10：流式输出仅为阶段级

**问题**：当前流式推送仅在阶段完成时发送事件，缺乏 Token 级流式输出（打字机效果）。

**改造方案**：利用 LLM 的 streaming 接口实现 Token 级实时推送。

#### G11：缺乏审计日志

**问题**：无全链路审计日志，无法追溯"谁在什么时候做了什么操作"。

**改造方案**：新增 `app/core/audit.py`，实现操作审计。

#### G12：前端 Dashboard 交互性不足

**问题**：Dashboard 为 vanilla JS 实现，仅展示状态，缺乏可交互的 Agent 流程图、Token 实时流等。

**改造方案**：增强 Dashboard，增加 Agent 流程可视化和 Token 成本图表。

### P2 — 锦上添花

#### G13：Docker 沙箱实现待增强

**问题**：Docker 模式仅支持基础执行，缺乏文件系统持久化、资源限制等 OpenHands 级别的能力。

**改造方案**：增强 Sandbox，挂载工作目录，增加 CPU/内存限制。

#### G14：缺乏 CI/CD 配置

**问题**：无 `.github/workflows/`，PR 不触发自动测试和评估。

**改造方案**：新增 GitHub Actions CI 配置。

#### G15：文档体系不完整

**问题**：README 已有中英双语，但缺乏架构决策记录（ADR）、面试 FAQ、演示脚本。

**改造方案**：完善文档体系。

---

## 四、改造方案

### 4.1 架构升级路线

```
当前架构                              目标架构
──────────                            ──────────
pipeline.py (LangGraph)        ──┐
                                  ├──→  unified_engine.py (统一编排引擎)
unified_orchestrator.py (DAG)  ──┘
         │
         ├──→ Supervisor V2 (LLM 驱动智能路由)
         ├──→ Agent Loop V2 (Plan-Execute-Reflect)
         ├──→ EventBus V2 (完整事件生命周期)
         └──→ State Schema V2 (更清晰的状态管理)
```

### 4.2 代码级改造清单

#### 第一阶段：架构统一 + Supervisor 升级

| 文件 | 改动内容 | 优先级 |
|------|---------|--------|
| `app/graph/pipeline.py` | 重构为 `unified_engine.py`，删除与 `unified_orchestrator.py` 重复的逻辑 | P0 |
| `app/unified_orchestrator.py` | 迁移有效逻辑到 `unified_engine.py`，保留兼容层 | P0 |
| `app/agents/review_agent.py` | 拆分为 `review_agent.py`(核心) + `review_rules.py`(规则) + `review_llm.py`(LLM 审查) | P0 |
| `app/agents/test_gen_agent.py` | 拆分为 `test_gen_agent.py`(核心) + `test_strategies.py`(策略) + `test_executor.py`(执行) | P0 |
| `app/graph/task_decomposer.py` | 拆分为 `task_analyzer.py` + `task_tree.py` + `decision_engine.py` | P0 |
| `app/agents/supervisor_agent.py` | 新增：LLM 驱动的 Supervisor 智能路由 | P0 |

#### 第二阶段：Agent 能力升级

| 文件 | 改动内容 | 优先级 |
|------|---------|--------|
| `app/agents/planner_agent.py` | 新增：Plan-Execute 模式规划 Agent | P0 |
| `app/core/llm_adapter.py` | 重构：按任务类型路由模型 + Token 细粒度追踪 + Token 级流式输出 | P0 |
| `app/tools/tool_registry.py` | 完善权限分级 + 高风险审批 + 动态工具发现 | P1 |
| `app/core/events.py` | 扩展事件类型 + 完整生命周期事件 | P1 |

#### 第三阶段：评估与可观测性

| 文件 | 改动内容 | 优先级 |
|------|---------|--------|
| `app/core/observability.py` | 增强：Token 四级追踪 + LangFuse 集成接口 | P0 |
| `app/core/audit.py` | 新增：全链路操作审计日志 | P1 |
| `scripts/benchmark.py` | 新增：SWE-Bench Lite 对齐的自动化 Benchmark | P0 |
| `.github/workflows/ci.yml` | 新增：CI 自动测试 + 质量门禁 | P1 |

#### 第四阶段：安全升级

| 文件 | 改动内容 | 优先级 |
|------|---------|--------|
| `app/core/guardrails.py` | 增强：LLM 辅助语义注入检测 + Agent 专项防护 + 三重脱敏（正则+启发式+LLM） | P1 |
| `app/core/rate_limiter.py` | 增强：分级速率限制（API/LLM/工具分开） | P2 |
| `app/tools/code_executor.py` | 增强：Docker 沙箱文件系统持久化 + 资源限制 | P2 |

#### 第五阶段：前端 + 文档

| 文件 | 改动内容 | 优先级 |
|------|---------|--------|
| `web/dashboard.html` | 增强：Agent 流程图 + Token 实时流 + 3D Dashboard | P1 |
| `web/js/dashboard.js` | 新增：Dashboard 交互逻辑 | P1 |
| `README.md` | 更新：架构决策记录 + 面试 FAQ + Demo GIF | P0 |
| `docs/ARCHITECTURE_V2.md` | 新增：V2 架构设计文档 | P0 |
| `docs/INTERVIEW_GUIDE.md` | 更新：补充 V2 新特性面试问答 | P0 |
| `scripts/demo.py` | 新增：一键演示脚本 | P1 |

### 4.3 新增模块设计

#### `app/agents/supervisor_agent.py`

```python
class SupervisorAgent:
    """LLM 驱动的 Supervisor 智能路由。

    分析当前状态，决定：
    1. 调用哪个 Agent（code_gen / review / test_gen / planner）
    2. 是否需要重试（及重试策略）
    3. 是否需要人工介入（HITL）
    4. 是否需要终止（成功/失败）
    """

    def __init__(self, llm_adapter, state_schema):
        self._llm = llm_adapter
        self._state = state_schema

    def decide(self, state: PipelineState) -> SupervisorDecision:
        """基于当前状态，LLM 推理下一步路由决策。"""
        # ...
```

#### `app/agents/planner_agent.py`

```python
class PlannerAgent:
    """Plan-Execute-Reflect 循环中的规划 Agent。

    将复杂任务分解为可执行的子任务序列，
    并在执行过程中动态调整计划。
    """

    def create_plan(self, task: str, context: dict) -> ExecutionPlan:
        """生成执行计划。"""
        # ...

    def reflect(self, plan: ExecutionPlan, results: list) -> ExecutionPlan:
        """执行后反思，决定是否调整计划。"""
        # ...
```

#### `app/core/audit.py`

```python
class AuditLogger:
    """全链路审计日志。

    记录：谁（Agent/用户）在什么时候做了什么操作，
    输入和输出是什么，耗时多久。
    """

    def log_action(self, actor, action, input_data, output_data, duration_ms):
        """记录一次操作。"""
        # ...
```

### 4.4 测试和评估方案

#### 测试策略

1. **单元测试**：每个新增/修改的函数必须有单元测试，覆盖率 ≥ 80%
2. **集成测试**：Pipeline 端到端流程必须有集成测试
3. **回归测试**：改造完成后必须确保 438 个现有测试全部通过
4. **性能测试**：Benchmark 脚本可独立运行，输出 JSON + Markdown 报告

#### Benchmark 方案

```python
# scripts/benchmark.py 核心指标
class BenchmarkMetrics:
    # 成功率
    pipeline_success_rate: float      # 端到端成功率
    code_gen_success_rate: float     # 代码生成成功率
    review_pass_rate: float          # 审查通过率

    # 效率指标
    avg_pipeline_duration: float     # 平均流水线耗时
    avg_code_gen_duration: float     # 平均代码生成耗时
    avg_review_duration: float       # 平均审查耗时

    # Token 成本
    total_tokens_used: int           # 总 Token 消耗
    tokens_per_pipeline: float       # 单任务平均 Token
    cost_breakdown_by_stage: dict    # 各阶段成本分布

    # 质量指标
    avg_review_score: float          # 平均审查评分
    test_pass_rate: float            # 测试通过率
    retry_count_distribution: dict   # 重试次数分布
```

#### CI 门禁

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: pip install -r requirements.txt
      - run: python -m pytest tests/ --cov=app --cov-fail-under=80
      - run: python scripts/benchmark.py  # 性能回归检测
      - run: echo "All checks passed"
```

---

## 五、行业参考链接

| 平台 | 链接 | 说明 |
|------|------|------|
| LangGraph 官方文档 | https://langchain-ai.github.io/langgraph/ | Agent 编排最佳实践 |
| SWE-Bench | https://www.swebench.com/ | 代码 Agent 评估基准 |
| OpenHands | https://github.com/All-Hands-AI/OpenHands | 开源代码 Agent |
| MCP 协议规范 | https://modelcontextprotocol.io/ | 工具集成标准 |
| OWASP LLM Top 10 | https://owasp.org/www-project-llm-top-10/ | LLM 安全威胁 |
| LangFuse | https://langfuse.com/ | 开源 LLM 可观测性 |
| Daytona | https://www.daytona.io/ | OCI 原生 Agent 沙箱 |
| Lil'Log Agent Survey | https://lilianweng.github.io/ | Agent 综述 |

---

*报告完成时间：2026-08-07*
