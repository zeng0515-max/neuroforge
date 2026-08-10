# Benchmark Evidence

This directory keeps benchmark outputs with their provenance so claims stay verifiable.

- `golden-report.md` / `golden-report.json`: live golden evaluation with DeepSeek `deepseek-chat`, 2026-08-09, 8/8 PASS.
- `BENCHMARK_V2.md` / `benchmark_results.json`: dry-run benchmark from 2026-08-06. It made no LLM calls and only measures local pipeline mechanics.

Reproduce with:

```bash
python scripts/benchmark.py --dry-run --output-dir benchmark-results
python scripts/golden_eval.py --output-dir benchmark-results
python scripts/golden_eval.py --live --output-dir benchmark-results
```
