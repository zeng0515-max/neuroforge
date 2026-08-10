# NeuroForge V3 Architecture Decision Records

## ADR-101: Deterministic runtime policy layer

**Status:** Accepted

**Context:** Guardrails and prompts can be bypassed or degrade silently. The project needed a boundary that cannot be influenced by model output.

**Decision:** Add `app/core/runtime_policy.py` with `ActionKind`, a kill switch, extensible fail-closed rules, and a bounded violation audit. Sandbox execution and tool calls both consult the policy.

**Consequences:** Blocked actions are structurally impossible under the kill switch. Policy code must stay small and dependency-free so it cannot fail to import.

## ADR-102: SQLite durable job queue

**Status:** Accepted

**Context:** In-memory background tasks are lost on process restart and do not expose retries or worker leases.

**Decision:** Add `QueueTask` and `app/core/task_queue.py`. Jobs transition `queued -> running -> succeeded/failed/cancelled`, with attempts, priority, worker id, lock token, and stale lease recovery.

**Consequences:** Single-host durability improves, but the queue is not a distributed message broker. Replacing it with Redis/Postgres-backed queue is a future horizontal-scaling task.

## ADR-103: Golden evaluation gate

**Status:** Accepted

**Context:** README claims about model quality were not reproducible and mixed dry-run latency with live model results.

**Decision:** Add a deterministic 8-case golden suite and gate. Offline runs validate reference solutions; live runs evaluate real generated files. Benchmark output includes explicit provenance.

**Consequences:** CI can block regressions without spending LLM tokens. Live quality claims require a named model and saved report.

## ADR-104: Honest benchmark provenance

**Status:** Accepted

**Context:** `benchmark-results/benchmark_results.json` was generated in dry-run mode but README implied a GPT-4o baseline.

**Decision:** `scripts/benchmark.py` now writes `mode`, `llm_model`, `command`, and a statement in both JSON and Markdown. README no longer presents dry-run numbers as model quality.

**Consequences:** Interview answers can point to an honest evidence trail instead of a claim that collapses under follow-up questions.

## ADR-105: Mount unified pipeline router

**Status:** Accepted

**Context:** `app/api/v1/pipeline.py` implemented token-level streaming but was never included in the FastAPI app, so the feature was unreachable.

**Decision:** Mount `pipeline_router` in `app.main:app` alongside tasks, jobs, agents, eval, and SLO routes.

**Consequences:** The endpoint is now testable and documented; API tests cover the jobs and SLO routes.
