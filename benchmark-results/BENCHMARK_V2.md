# NeuroForge V2 Benchmark Report

**Started:** 2026-08-09T12:02:53.504965  |  **Completed:** 2026-08-09T12:10:14.955291  |  **Config:** `{"dry_run": true, "quick": true, "iterations": 3, "python_version": "3.11.1", "llm_model": "", "command": "scripts\\benchmark.py --dry-run --quick --output-dir ."}`

## Provenance
- **Mode:** dry-run
- **LLM model:** N/A
- **Command:** `scripts\benchmark.py --dry-run --quick --output-dir .`
- **Honest statement:** DRY-RUN benchmark: no LLM API was called; these numbers measure local pipeline mechanics only and must not be presented as model quality.

## Overall Score
- **Grade:** B
- **Tracked categories:** 8

### Category Scores (avg ms)

| Category | Avg Latency (ms) |
|----------|------------------|
| audit_throughput | 6.11 |
| code_generation | 4657.03 |
| memory_system | 255.93 |
| pipeline_speed | 7670.39 |
| review_quality | 1268.44 |
| security_scan | 0.06 |
| token_consumption | 1297.95 |
| tool_execution | 0.09 |

## Detailed Results

### pipeline_speed

| Benchmark | Min(ms) | Max(ms) | Avg(ms) | P50(ms) | P95(ms) | P99(ms) | N |
|-----------|---------|---------|---------|---------|---------|---------|---|
| e2e_pipeline_linear | 1152.17 | 1454.25 | 1313.66 | 1152.17 | 1334.55 | 1334.55 | 3 |
| stream_pipeline_e2e | 12849.56 | 13022.35 | 12935.47 | 12849.56 | 12934.49 | 12934.49 | 3 |
| stage_requirement | 0.88 | 1.14 | 1.01 | 0.88 | 1.02 | 1.02 | 3 |
| stage_code_gen | 26688.48 | 33553.30 | 29472.26 | 26688.48 | 28174.99 | 28174.99 | 3 |
| stage_review | 1431.25 | 1619.29 | 1548.54 | 1431.25 | 1595.09 | 1595.09 | 3 |
| stage_test_gen | 734.31 | 762.68 | 751.40 | 734.31 | 757.22 | 757.22 | 3 |

### token_consumption

| Benchmark | Min(ms) | Max(ms) | Avg(ms) | P50(ms) | P95(ms) | P99(ms) | N |
|-----------|---------|---------|---------|---------|---------|---------|---|
| token_baseline_lru | 1040.88 | 1357.34 | 1222.89 | 1040.88 | 1270.46 | 1270.46 | 3 |
| token_baseline_rate | 1238.45 | 1548.08 | 1359.89 | 1238.45 | 1293.14 | 1293.14 | 3 |
| token_baseline_csv | 1174.20 | 1412.80 | 1311.06 | 1174.20 | 1346.19 | 1346.19 | 3 |

### memory_system

| Benchmark | Min(ms) | Max(ms) | Avg(ms) | P50(ms) | P95(ms) | P99(ms) | N |
|-----------|---------|---------|---------|---------|---------|---------|---|
| search_latency | 110.09 | 117.98 | 114.85 | 110.09 | 116.48 | 116.48 | 3 |
| unified_search_latency | 109.29 | 148.90 | 124.09 | 109.29 | 114.07 | 114.07 | 3 |
| write_episodic_latency | 0.09 | 0.21 | 0.14 | 0.09 | 0.11 | 0.11 | 3 |
| consolidation_speed | 1267.64 | 1312.93 | 1285.64 | 1267.64 | 1276.36 | 1276.36 | 3 |
| hit_rate_normalized | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 3 |
| working_memory_100_rw | 7.47 | 16.85 | 10.86 | 7.47 | 8.27 | 8.27 | 3 |

### review_quality

| Benchmark | Min(ms) | Max(ms) | Avg(ms) | P50(ms) | P95(ms) | P99(ms) | N |
|-----------|---------|---------|---------|---------|---------|---------|---|
| static_analysis_speed | 0.11 | 0.12 | 0.11 | 0.11 | 0.11 | 0.11 | 3 |
| security_scan_speed | 0.07 | 0.08 | 0.08 | 0.07 | 0.08 | 0.08 | 3 |
| llm_review_speed | 3757.16 | 4339.51 | 4073.56 | 3757.16 | 4124.01 | 4124.01 | 3 |
| issue_detection_rate | 1000.00 | 1000.00 | 1000.00 | 1000.00 | 1000.00 | 1000.00 | 3 |

### code_generation

| Benchmark | Min(ms) | Max(ms) | Avg(ms) | P50(ms) | P95(ms) | P99(ms) | N |
|-----------|---------|---------|---------|---------|---------|---------|---|
| gen_speed_hello | 6298.72 | 6875.69 | 6639.93 | 6298.72 | 6745.39 | 6745.39 | 3 |
| gen_speed_fib | 9576.12 | 10497.92 | 9988.17 | 9576.12 | 9890.48 | 9890.48 | 3 |
| parse_rate_normalized | 1000.00 | 1000.00 | 1000.00 | 1000.00 | 1000.00 | 1000.00 | 3 |
| validation_pass_rate | 1000.00 | 1000.00 | 1000.00 | 1000.00 | 1000.00 | 1000.00 | 3 |

### tool_execution

| Benchmark | Min(ms) | Max(ms) | Avg(ms) | P50(ms) | P95(ms) | P99(ms) | N |
|-----------|---------|---------|---------|---------|---------|---------|---|
| list_tools_latency | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 3 |
| register_tool_latency | 0.26 | 0.28 | 0.27 | 0.26 | 0.26 | 0.26 | 3 |
| execute_tool_no_approval | 0.02 | 0.04 | 0.03 | 0.02 | 0.02 | 0.02 | 3 |
| ast_validate_tool_latency | 0.08 | 0.09 | 0.08 | 0.08 | 0.08 | 0.08 | 3 |
| code_executor_latency | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |

### audit_throughput

| Benchmark | Min(ms) | Max(ms) | Avg(ms) | P50(ms) | P95(ms) | P99(ms) | N |
|-----------|---------|---------|---------|---------|---------|---------|---|
| log_single_entry | 0.25 | 0.36 | 0.31 | 0.25 | 0.33 | 0.33 | 3 |
| log_100_entries_batch | 16.21 | 21.19 | 17.99 | 16.21 | 16.58 | 16.58 | 3 |
| query_and_count_audit | 0.01 | 0.02 | 0.01 | 0.01 | 0.01 | 0.01 | 3 |

### security_scan

| Benchmark | Min(ms) | Max(ms) | Avg(ms) | P50(ms) | P95(ms) | P99(ms) | N |
|-----------|---------|---------|---------|---------|---------|---------|---|
| injection_detection_5_payloads | 0.08 | 0.19 | 0.12 | 0.08 | 0.09 | 0.09 | 3 |
| injection_avg_per_payload | 0.04 | 0.07 | 0.06 | 0.04 | 0.06 | 0.06 | 3 |
| pii_detection_6_payloads | 0.05 | 0.05 | 0.05 | 0.05 | 0.05 | 0.05 | 3 |
| pii_avg_per_payload | 0.01 | 0.01 | 0.01 | 0.01 | 0.01 | 0.01 | 3 |
| full_security_scan_combined | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
