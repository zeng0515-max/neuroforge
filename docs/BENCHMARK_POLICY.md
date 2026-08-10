# Benchmark Policy

This file exists so that every number in the repository can be traced to a run, a model, and a command.

## Rules

1. A dry-run benchmark may measure local pipeline mechanics only. It must never be described as model quality, review quality, or token-cost optimization against an LLM.
2. Every benchmark report must include:
   - mode (`dry-run` or `live`)
   - model name (live only)
   - command used
   - start and end time
   - iteration count
3. A live benchmark requires a configured LLM and should save both JSON and Markdown outputs.
4. Golden evaluation is the minimum regression gate. It is deterministic and does not require an LLM.
5. Any README/resume claim such as "review pass rate +X%" must reference a saved live report. Do not fabricate or infer it from dry-run timing.

## Commands

```bash
# Offline deterministic gate
python scripts/golden_eval.py --output-dir benchmark-results

# Local mechanics benchmark (no LLM)
python scripts/benchmark.py --dry-run --quick --output-dir benchmark-results

# Live model benchmark (requires valid API key)
python scripts/benchmark.py --live --output-dir benchmark-results

# Live golden evaluation (requires valid API key)
python scripts/golden_eval.py --live --output-dir benchmark-results
```

## Current repository evidence

The checked-in `benchmark-results/benchmark_results.json` is a dry-run benchmark from 2026-08-06. It measures local pipeline mechanics and does not support any claim about a specific LLM's code-generation quality. Any future live result should be committed with its provenance intact.
