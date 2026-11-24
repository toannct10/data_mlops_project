# Troubleshooting

- Docker Desktop unavailable: start Desktop in Linux-container mode, wait for Engine, run docker info. Scripts stop before initialization if it is unavailable.
- Port in use: change PORTABLE_*_PORT in .env.portable. Do not stop unrelated processes. The accepted stack is not a portable dependency.
- Missing/wrong model: 01_SETUP reports the exact path and checksum mismatch. Use 06_INSTALL_MODELS with an authorized exact-version pack. No alternate model, training or automatic large download is attempted.
- RAM/GPU: CPU profile serializes Qwen, uses one encoder thread and CPU layers=0. Slow answers are possible. GPU requires a separate compatible CUDA runtime/image and a verified GPU probe. Full minimum RAM/GPU requirements were not measured on a clean machine.
- Airflow unreachable: check the configured host port, its actual /health scheduler/metadatabase/triggerer status, then sanitized diagnostics. The single standalone service has its own persistent metadata volume. Local cloud DAGs intentionally have no schedule and are paused.
- DAG import errors: verify the portable Airflow image includes the current code, pinned requirements and separate dbt venv. Legacy batch DAGs are intentionally absent from discovery.
- AWS AccessDenied: inspect authorized caller identity, bucket owner, explicit deny, prefix conditions, session token and SSE permissions. Never copy another person's keys from Docker containers.
- Snowflake HTTP 404: validate account identifier/endpoint. Authentication errors: validate the configured user and authorized role separately; keep secondary roles NONE. Do not print passwords.
- dbt failure: verify target database, RAW schema, the six active models, restricted role ownership and additive migration. Do not reload historical CSVs or switch to administrator credentials.
- Qwen loading failure: verify the bundled GGUF SHA256 and immutable image reference. Wait for model readiness, not just a running container. Use CPU profile unless CUDA is actually verified.
- Chroma/BM25 mismatch: stop the portable application and inspect source/model/ID manifests. Setup will not rebuild indexes or overwrite a different seed. Keep accepted indexes untouched. Maintenance requires a separately reviewed explicit action.
- Prometheus DOWN: inspect service DNS rag-backend:8000 and streamlit:8002, metrics endpoints and actual activeTargets.
- Grafana datasource unavailable: use provisioning URL http://prometheus:9090 and datasource uid foody-prometheus; check datasource health plus a real Prometheus query. A running Grafana container alone is not acceptance.
- Original demo is still running: use a separate stable project and port overrides. Never use down -v or volume prune.
- Diagnostics: 05_DIAGNOSE records status/ports and counts of allowlisted error categories. It intentionally omits raw payload logs, inspection environments and review text.

Known AI limitation: C19 food-quality evidence can be tagged by the saved model despite containing only order cancellation. Numeric comparison and causal refusal are safe; the food-quality evidence does not pass acceptance. Do not advertise this as fixed.
