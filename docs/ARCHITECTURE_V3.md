# NeuroForge V3 Architecture

> Version: 3.0 | Updated: 2026-08-09

## 1. Direction

NeuroForge V3 is positioned as an **agent engineering platform**, not as a code-generation demo. The platform surfaces four capabilities:

1. A deterministic policy layer that constrains what agents can do.
2. A durable job lifecycle that survives process restarts.
3. An evaluation loop that can block low-quality changes before they merge.
4. An observability contract that makes cost, latency, and failure rates explicit.

## 2. Layer map

```text
User / Dashboard
    |
    v
FastAPI layer (tasks, jobs, agents, pipeline, eval, slo)
    |
    +--> RuntimePolicy  --> kill switch, permission rules, audit
    +--> Guardrails     --> input/output safety scans
    +--> TaskQueue      --> SQLite durable jobs + worker lease
    +--> UnifiedEngine  --> LangGraph or LinearEngine
    |         |
    |         v
    |      Agents (Requirement / CodeGen / Review / TestGen)
    |         |
    |         v
    |      Tools (AST, RAG, MCP, registry)  --> policy check
    |         |
    |         v
    |      Sandbox (local / Docker)          --> policy check
    |
    +--> Metrics / SLO / Audit / EventBus
    +--> GoldenEvaluator / EvalReport
```

## 3. Runtime governance

Policy decisions are deterministic code, not prompt instructions.

- Every sandbox execution calls `runtime_policy.allow(ActionKind.EXECUTE, ...)`.
- Every tool call calls `runtime_policy.allow(ActionKind.TOOL_CALL, ...)`.
- A kill switch blocks write/execute/network/llm/tool actions until it is disabled.
- Custom rules fail closed.

This mirrors the production-agent guidance that governance should be enforced before actions reach the wire, rather than layered on top of model outputs.

## 4. Durable execution

The API enqueues a job; a separate worker claims it under a lease.

```text
POST /api/v1/jobs
    -> task_queue.enqueue(payload)
    -> scripts/worker.py claim(worker_id)
    -> stream_engine(task_id, user_request)
    -> complete() / fail() with retry
```

Crash behavior:

- A worker that dies leaves a running job with an expired `started_at`.
- `requeue_stale()` returns it to the queued state.
- A job that exceeds `max_attempts` becomes `failed` with the last error.

## 5. Evaluation

The golden suite contains 8 deterministic coding tasks. CI runs it offline with reference solutions to verify the gate itself, and a live run evaluates real model output when an LLM is configured.

The eval metrics engine computes:

- success rate, failure rate
- review score / retry count / critical issue count
- test pass rate
- golden pass rate and gate
- self-heal rate
- stage-level error rates

## 6. Observability

The metrics snapshot exposes:

- LLM calls, tokens, cost, latency by model/agent
- pipeline success/failure and p95 latency
- tool call totals and failure rate
- stage-level token/cost breakdown

`GET /api/v1/slo` evaluates the snapshot against configured targets and returns `not_measured` when no sample exists. It never invents a fake pass.

## 7. Deployment

- CI: lint, tests on Python 3.11/3.12, coverage gate, dry-run benchmark, golden evaluation, quality gate.
- Release: build multi-stage image, push to GHCR, vulnerability scan, run container health smoke.

The remaining production gap is multi-instance deployment with a managed queue and checkpoint database; the current SQLite queue is intentionally single-writer and suitable for a single-host production workload, not a horizontally scaled fleet.
