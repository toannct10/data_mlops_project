# AI stress test summary

Executed against the existing running Foody services and frozen current canonical data. No HTTP-200-only passes. Fifty primary IDs include 52 conversational turns; targeted attempts are separate.

## Functional acceptance

| Module | Initial, rescored | Final |
|---|---:|---:|
| Sales AI | 2/25 (8.0%) | 25/25 (100.0%) |
| Cross Analysis | 4/25 (16.0%) | 24/25 (96.0%) |

Overall final: **49/50 (98.0%)**; FAIL **1**; SKIPPED **0**.
Original initial grader: 9/50; corrected initial grader: 6/50. Original results and raw responses remain preserved.

## Execution ledger

- initial: 50 primary-case attempts; raw responses retained.
- targeted: 31 primary-case attempts; raw responses retained.
- final: 50 primary-case attempts; raw responses retained.
- presentation_targeted: 6 primary-case attempts; raw responses retained.
- final_verified: 50 primary-case attempts; raw responses retained.
- The earlier post-fix full run exposed visible-scope and citation-only false positives. The final_verified full run uses the final deployment and stronger checks; earlier attempts do not inflate its 50-case rate.

## Model SQL and fallback

- Sales AI: model SQL success among attempted primary final turns 5/6 (83.3%); coverage 6/25 (24.0%); verified fallback 16/25.
- Cross Analysis: model SQL success among attempted primary final turns 2/7 (28.6%); coverage 7/25 (28.0%); verified fallback 19/25.
- Overall model SQL success: 7/13 (53.8%). This is separate from functional acceptance; fallback does not count as model SQL success.
- All conversational turns, including both first questions: model SQL success 7/15 (46.7%). A turn counts once after its bounded generation/repair attempts; rejected individual drafts remain in raw JSON.
- Conversation SQL status in the CSV describes the final turn; raw JSON preserves both turns and their individual attempts. RAG-only synthesis is not a SQL-generation attempt.
- Fixed compound calculations and all-record aggregates are explicitly labeled verified analytics fallback. They are reusable analytical implementations, not model-generated SQL.
- Initial Sales facade inherited disabled Text-to-SQL, unlike the existing BI UI. Initial Sales failures must not be read as Qwen SQL failures or a clean model-to-model benchmark.

## Latency

- Sales AI, primary case including both turns where applicable: average 8.39s; median 0.48s; nearest-rank p95 48.12s.
- Cross Analysis, primary case including both turns where applicable: average 17.75s; median 3.65s; nearest-rank p95 54.31s.
- All 50: average 13.07s; median 2.31s; nearest-rank p95 52.22s.
- SQL generation gateway time, 14 separable turns: average 28.89s; median 29.46s; nearest-rank p95 46.91s.
- Measured qwen_inference, 17 turns: average 30.94s; median 30.14s; nearest-rank p95 50.03s.
- Measured sql_validation, 16 turns: average 0.01s; median 0.01s; nearest-rank p95 0.02s.
- Measured sql_execution, 40 turns: average 0.84s; median 0.68s; nearest-rank p95 2.19s.
- Measured retrieval, 7 turns: average 3.26s; median 2.60s; nearest-rank p95 8.84s.
- Qwen timing is gateway elapsed time, including transport and output parsing; it is not isolated GPU compute time. The final deployment times generate only, avoiding nested generate_text double counting. Earlier exploratory raw timings may overlap. Missing stages are not invented as zero.
- No concurrency or throughput claims: the Qwen idle-slot guard serialized the suite.

## Failure categories

- REVIEW_EVIDENCE_MISMATCH: 1.
- Failure rate: 1/50 (2.0%). Timeout rate: 0/50 (0.0%).

## Changes

- scope: Explicit restaurant IDs/names resolve before the UI selection; unknown entities fail closed. Follow-up anaphora retain entity and date scope.
- time: Roman-numeral quarters and month-to-month transitions parse explicitly. All-record Sales questions use the stored canonical rows, with future-dated records disclosed as stored data rather than forecasts.
- compound: Reusable parameterized read-only calculations implement contribution share, absolute growth, cancellation thresholds, median cohorts, monthly extrema, joint sentiment/revenue cohorts and consecutive-month comparisons.
- reviews: Positive counts and negative rates are distinct. Rates include their numerator/denominator. Aspect citations are checked against canonical negative PhoBERT facets before synthesis; sample limitations are explicit.
- wording: Verified facts are rendered with Vietnamese labels, currency, percentage and rating units. Comparisons explain direction and zero-denominator limits. Causal certainty is rejected.
- integration: The thin Sales HTTP facade calls the same service as BI. Backend Text-to-SQL now has the same enabled flag, row cap and timeout as BI; initial facade configuration had disabled model SQL.
- safety: API transactions are read-only with a five-second SQL statement timeout. Existing AST validation, restricted model SQL credentials and result verification remain in force.

## Validation and preservation

- Model-free regression: 158 passed, 2 deliberately deselected database checks. Log: logs/ai_stress_regression.log. The excluded write-denial test was not executed because this task permits read-only queries only.
- Combined final model-free regression: 257 passed, 2 deselected; logs/ai_stress_final_regression.log.
- Existing UI, SQL and response regressions: 99 passed. Log: logs/ai_stress_ui_regression_retry.log. An earlier run had 10 repository-injection failures plus a fixture absent from the image; the injection contract was restored and the existing host fixture mounted read-only for the passing rerun.
- Initial and final full-row fingerprints cover fact_order, fact_review_v2 and fact_review_aspect. See each round fingerprints_before.json / fingerprints_after.json and reference_snapshot.json.
- Oracle reads base facts independently of production planners. Frozen questions, reference values and source SQL remain in frozen_50_suite.json and reference_snapshot.json.
- The deployed backend and BI source hashes match the edited host files; see deployed_source_fingerprints.json. All eight service checks are recorded in service_health.json.
- All 22 Docker volumes match the prior accepted inventory; see volume_preservation.json. The supplementary independent raw-fact audit checked 384 numeric fields with zero conflicts; see supplementary_numeric_audit.json.
- Evaluator representation repairs flatten date-keyed dictionaries, normalize metric aliases and map explicit first/second periods to the frozen reference fields. Derived-quantity and visible month/restaurant-label checks were strengthened. References were not changed.
- No retraining, canonical imports, model/index rebuilds, cloud replay, database writes, ZIP or project copy was performed by this stress task.

## Limits and safe demonstration

- Single run per full-suite round; targeted successes do not inflate the 50-case result. The model can vary on reruns.
- All-record Sales and date-bounded Cross scopes differ intentionally. Some stored canonical orders have future business dates; all-record Sales discloses them and never presents them as forecasts.
- Qualitative citations are samples, not a statistically representative theme ranking. Aspect filtering uses existing negative PhoBERT inferences; it does not change model outputs.
- Existing FoodQuality predictions can label cancellation-only text as a culinary complaint. A stored facet and a real citation are not sufficient semantic support; the evaluator flags this conflict. Canonical predictions were preserved. Aspect aggregates describe saved model inferences, not independently human-validated labels.
- Rate-only Qwen Sales SQL may be accompanied by explicitly recorded trusted SELECTs for its numerator/denominator. These supporting queries are not represented as model-generated SQL.
- No new cloud acceptance claim is made: this suite reads the currently configured sources and does not rerun Airflow/dbt acceptance.
- See AI_SAFE_DEMO_QUESTIONS.md for tested passing examples, with observed latency and mode.

## Modified source and test files

- `app/api.py`
- `docker-compose.devops.yml`
- `src/sales/service.py`
- `src/sales/semantic_plan.py`
- `src/sales/question_scope.py`
- `src/sales/vietnamese.py`
- `src/sales/verified_simple.py`
- `src/sales/compound.py`
- `src/sales/cross_router.py`
- `src/sales/cross_hybrid.py`
- `src/sales/cross_sql.py`
- `src/monitoring/ai_timing.py`
- `src/llm/gateway.py`
- `src/rag_v3/retriever.py`
- `src/security/analytical_sql.py`
- `src/sales/text_to_sql.py`
- `tests/test_ai_stress_repairs.py`
- `scripts/ai_stress_reference.py`
- `scripts/ai_stress_runner.py`
- `scripts/ai_stress_reports.py`
- `logs/ai_stress_health.py`
- `logs/ai_stress_source_check.py`
- `logs/ai_stress_regrade.py`
- `logs/ai_stress_volumes.py`
- `logs/ai_stress_numeric_audit.py`

## Questions to avoid

- C19: Chất lượng đồ ăn có phải là nguyên nhân khiến doanh thu Chicken Plus giảm trong tháng 6/2026 không? — REVIEW_EVIDENCE_MISMATCH
