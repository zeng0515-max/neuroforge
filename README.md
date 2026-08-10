# NeuroForge V3

> Production-oriented Agent Engineering Platform for AI-assisted software delivery.

NeuroForge turns a single natural-language requirement into a structured, reviewed, and testable software artifact. The project is not a "multi-agent demo": it is built around four engineering pillars that interviewers and production teams actually care about.

**Current evidence:** 467 tests passing, 58.6% branch coverage, Golden Live eval 8/8 PASS (DeepSeek `deepseek-chat`), Ruff clean on `app/` and `tests/`.

1. **Deterministic governance** - a runtime policy layer, kill switch, permission rings, and audit trail control what an agent is allowed to do.
2. **Durable execution** - a SQLite-backed job queue with retries, worker leases, crash recovery, and an explicit worker process.
3. **Evaluability** - a golden dataset, deterministic evaluation gates, SLO checks, and honest benchmark provenance.
4. **Observability** - structured metrics, alerts, trace ids, stage-level token tracking, health checks, and a real-time dashboard.

## What changed from V2

| Dimension | V2 status | V3 status |
| --- | --- | --- |
| Project narrative | "Multi-Agent collaborative code generation" | "Agent engineering platform with measurable production readiness" |
| Runtime control | Prompt-level guardrails | Deterministic policy layer + kill switch + tool permission audit |
| Job lifecycle | In-memory background tasks only | SQLite durable queue, retries, leases, worker process |
| Evaluation | Static metrics, unverifiable README claims | Golden suite + SLO checks + provenance-aware benchmark |
| Deployment | CI deploy steps were TODO placeholders | Image is built, pushed to GHCR, scanned, and health-smoked on release |

## Repository map

```text
app/
  agents/             Specialized agents (requirement, codegen, review, test)
  api/v1/             FastAPI routers: tasks, agents, jobs, pipeline, eval, slo
  core/               Config, LLM adapter, runtime policy, task queue, observability
  eval/               Metrics engine, golden suite, CI gate
  graph/              Unified engine, pipeline, orchestrator, state
  tools/              AST validator, tool registry, RAG, MCP
  workspace/          Local / Docker sandbox
docs/                 Architecture, ADRs, interview guide
benchmark-results/    Golden reports and provenance-aware benchmark output
scripts/              Setup, worker, benchmark, golden eval, deploy helpers
src/                  Golden coding task fixtures
tests/                pytest suite (unit + API + evaluation)
web/                  Dashboard and landing page
```

## Quick start

```powershell
# Windows
.\scripts\setup.ps1
copy .env.example .env
# edit .env with your LLM key
.\scripts\run.ps1
```

```bash
# macOS / Linux
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python scripts/init_vector_db.py
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Verify:

```bash
pytest tests/ -q
python scripts/golden_eval.py --output-dir benchmark-results
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/slo
```

Run the durable worker separately when you want job execution decoupled from the API process:

```bash
python scripts/worker.py --worker-id worker-1
```

## Core APIs

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/tasks` | Submit a pipeline task (async) |
| `POST /api/v1/jobs` | Enqueue a durable job |
| `GET /api/v1/jobs/{task_id}` | Inspect durable job state and retries |
| `GET /api/v1/pipeline/{task_id}/stream` | SSE pipeline progress |
| `GET /api/v1/slo` | Current SLO status against targets |
| `GET /api/v1/metrics` | Metrics snapshot |
| `GET /api/v1/eval/report` | Eval report from completed tasks |

All routes require the `API_KEY` header when configured.

## Runtime policy

`app/core/runtime_policy.py` is the deterministic gate in front of sandbox execution and tool calls:

- `ActionKind`: read, write, execute, network, llm_call, tool_call.
- Kill switch: blocks mutating and execution actions until an operator disables it.
- Custom rules: predicates fail closed, so an exception in policy code denies the action.
- Violations: kept in a bounded audit trail and exposed through the same policy API.

Example:

```python
from app.core.runtime_policy import ActionKind, runtime_policy

runtime_policy.set_kill_switch(True, reason="release incident")
decision = runtime_policy.allow(ActionKind.EXECUTE, actor="sandbox")
assert decision.allowed is False
```

## Durable task queue

`app/core/task_queue.py` stores jobs in SQLite and supports:

- Atomic claim with worker id and lock token.
- Retry with `max_attempts` and automatic requeue after a failed attempt.
- Crash recovery by requeueing stale leases.
- Cancel, stats, and paginated inspection.

The API layer is intentionally decoupled from the worker. A process crash does not lose a queued job, and stale running jobs are reclaimed after the lease expires.

## Golden evaluation

`app/eval/golden_suite.py` defines a deterministic dataset of 8 coding tasks. Each case checks:

- Minimum file count and line count.
- Python syntax via `ast.parse`.
- Required symbols and keywords.

The suite is used by CI and can be run against the live pipeline:

```bash
python scripts/golden_eval.py --output-dir benchmark-results           # offline, deterministic
python scripts/golden_eval.py --live --output-dir benchmark-results    # needs a configured LLM
```

The gate uses the same thresholds as the production eval framework: `PASS >= 80%`, `WARN >= 50%`, otherwise `BLOCK`.

## Benchmark honesty

The repository does not claim that dry-run numbers are model quality numbers.

- `scripts/benchmark.py --dry-run` measures local pipeline mechanics only and records `mode: dry-run` in both JSON and Markdown outputs.
- `scripts/benchmark.py --live` records the actual model name and mode.
- `benchmark-results/BENCHMARK_V2.md` and `benchmark-results/benchmark_results.json` now include a provenance section.

Before claiming "review pass rate increased by X%" or "token cost reduced by Y%", run a live benchmark with a named model and save the output. Interviewers will ask for exactly that evidence.

## Production readiness

The checklist below reflects what a "local demo" usually lacks:

- [x] Structured API and schemas with Pydantic validation.
- [x] Guardrails against prompt injection and unsafe output.
- [x] Runtime policy and tool permission rings.
- [x] Sandboxed execution (local subprocess or Docker).
- [x] Durable job queue with retries and leases.
- [x] Golden evaluation and CI quality gate.
- [x] SLO endpoint and metrics snapshot.
- [x] Container build, vulnerability scan, health smoke in release workflow.
- [ ] Real multi-instance deployment (Kubernetes / managed queue) remains future work.

## Documentation

- [Architecture V3](docs/ARCHITECTURE_V3.md)
- [ADR index](docs/ADR_V3.md)
- [Interview guide](docs/INTERVIEW_GUIDE.md)
- [Benchmark policy](docs/BENCHMARK_POLICY.md)
- [Benchmark report](benchmark-results/BENCHMARK_V2.md)
- [Resume project card](docs/RESUME_PROJECT_CARD.md)

## External references used for the V3 direction

- ByteDance 27th campus Agent R&D JD: task planning, context construction, multi-tool orchestration, result verification, reusable agent framework, evaluation.
- Nowcoder interviewer review: project credibility requires deployment, quantified output, personal ownership, and answers that survive follow-up questions.
- MLflow "Building Production-Ready AI Agents in 2026": runtime governance, evaluation inside workflows, observability, and skill-boundary security.
- Anthropic "Building Effective Agents": workflows over free-form agents when the outcome space is structured, plus explicit failure recovery and memory design.
- OWASP LLM / Agentic Skills Top 10: security at the skill and plugin boundary.
