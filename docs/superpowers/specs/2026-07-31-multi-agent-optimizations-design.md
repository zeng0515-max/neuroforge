# 多智能体系统三项优化设计文档

> 日期：2026-07-31
> 主题：参考 GitHub 开源多 Agent 系统最佳实践，整合三项优化到当前项目
> 状态：已批准，进入实现

## 背景

当前项目（多智能体协同代码生成与审查系统）已具备完整的 4 Agent 流水线，但 Benchmark 暴露三个真实问题：

1. **评审通过率偏低（80%）**：review 失败重试时，code_gen 盲目重跑，未利用 review 指出的具体 issues
2. **LLM 输出解析不稳定**：code_gen/test_gen 用正则解析 LLM 输出，遇到非标准格式会失败
3. **无断点续跑能力**：任务失败后无法从断点恢复，必须重头跑

## 三项优化方案

### 方案 A：Retry with Feedback（评审反馈驱动重生成）

**目标**：评审失败时把具体 issues 作为反馈传给 code_gen，让重试有方向而非盲目重跑。

**设计**：

1. `PipelineState` 新增字段 `review_feedback: List[ReviewIssue]`
2. `review_node` 在 verdict=needs_changes 时，把 issues 写入 state
3. `retry_code_gen_node` 不再只 +1，还透传 feedback
4. `code_gen_node` 检测到 review_feedback 时，在 prompt 追加"上一版问题"section

**数据流**：

```
review 失败
→ issues 写入 state.review_feedback
→ retry_step 透传 feedback
→ code_gen prompt 追加 "## 上一版问题\n{issues}"
→ 针对性修复
```

**预期效果**：评审通过率 80% → 95%+，平均重试 1.4 → 1.0

**改动文件**：
- `app/graph/state.py`：加 review_feedback 字段
- `app/agents/review_agent.py`：失败时写 feedback
- `app/agents/code_gen_agent.py`：检测 feedback 并改 prompt
- `app/graph/pipeline.py`：retry_step 透传 feedback

### 方案 B：Structured Output（Pydantic 结构化 LLM 输出）

**目标**：用 Pydantic 模型校验 LLM 输出，替代正则解析，提升稳定性。

**设计**：

1. 定义 Pydantic 输出模型：`CodePackageOutput`、`ReviewReportOutput`、`TestReportOutput`
2. `LLMAdapter` 新增 `chat_structured(messages, response_model)` 方法
3. 用 OpenAI `response_format=json_schema` 或 function calling
4. 三个 Agent 优先用结构化输出，保留正则解析作 fallback（兼容不支持 structured output 的模型）

**数据流**：

```
LLM 输出
→ Pydantic 模型校验
→ 直接生成 CodePackage / ReviewReport / TestReport
→ 失败回退正则解析
```

**预期效果**：解析失败率 5% → 0%，Token 浪费减少

**改动文件**：
- `app/core/llm_adapter.py`：新增 chat_structured
- `app/agents/code_gen_agent.py`：切换结构化输出
- `app/agents/review_agent.py`：切换结构化输出
- `app/agents/test_gen_agent.py`：切换结构化输出
- `app/graph/state.py`：新增输出模型

### 方案 C：LangGraph Checkpointer（断点续跑）

**目标**：用 SqliteSaver 持久化 LangGraph 中间状态，失败任务可从断点恢复。

**设计**：

1. 引入 `langgraph.checkpoint.sqlite.SqliteSaver`
2. `build_graph()` 接受 checkpointer 参数，compile(checkpointer=saver)
3. `run_pipeline` 支持 thread_id（用 task_id），失败后可续跑
4. 新增 API：`POST /api/v1/tasks/{id}/resume`

**数据流**：

```
每个节点执行后
→ SqliteSaver 自动持久化 state
→ 失败时 state 已存
→ /resume 触发从断点恢复
```

**预期效果**：失败任务可续跑，生产级容错

**改动文件**：
- `app/graph/pipeline.py`：集成 SqliteSaver
- `app/api/v1/tasks.py`：新增 /resume 接口
- `requirements.txt`：加 langgraph-checkpoint-sqlite

## 整合架构

三个方案互不冲突：

- A 改 state 字段（加 review_feedback）
- B 改 LLM 调用（加 chat_structured）
- C 改 graph 编译（加 checkpointer）

端到端流程：

```
需求
→ code_gen（B 结构化输出）
→ review
→ 失败？→ A 反馈驱动重生成
→ 通过？→ test_gen
→ 全程 C 持久化断点
```

## 验证计划

1. 跑 `scripts/verify_completion.py` 确认无回归
2. 跑 1 个 Benchmark 任务确认端到端可用
3. 跑 5 任务 Benchmark 对比修复前后指标

## 已知风险

- **方案 B 兼容性**：部分模型（如 DeepSeek）可能不支持 `response_format=json_schema`，需保留 fallback
- **方案 C 依赖**：需新增 `langgraph-checkpoint-sqlite` 包
- **方案 A prompt 膨胀**：feedback 段会增加 prompt 长度，需控制 issues 数量上限
