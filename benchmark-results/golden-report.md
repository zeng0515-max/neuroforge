# Golden Evaluation Report

- Mode: live
- Model: deepseek-chat
- Command: `scripts\golden_eval.py --live --output-dir benchmark-results`
- Total: 8
- Passed: 8
- Failed: 0
- Pass rate: 100.0%
- Gate: PASS

| Case | Passed | Reason |
| --- | --- | --- |
| lru_cache | PASS | all deterministic checks passed |
| rate_limiter | PASS | all deterministic checks passed |
| csv_parser | PASS | all deterministic checks passed |
| thread_safe_counter | PASS | all deterministic checks passed |
| simple_queue | PASS | all deterministic checks passed |
| fizzbuzz | PASS | all deterministic checks passed |
| fibonacci | PASS | all deterministic checks passed |
| data_cleaner | PASS | all deterministic checks passed |
