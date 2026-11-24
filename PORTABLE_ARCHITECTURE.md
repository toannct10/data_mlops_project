# Portable architecture

Ordering Web → portable PostgreSQL → transactional outbox → one Airflow standalone service → authorized external S3 → external Snowflake RAW → restricted dbt STAGING/MART → BI / Cross SQL.

One dedicated Compose file, one default stable project `foody-portable-demo`, service-DNS traffic, relative bind mounts and project-scoped named volumes. No container uses localhost to reach another container. Qwen concurrency=1; CPU layers=0 by default. GPU overlay is explicit, requires a compatible CUDA image and runtime probe; full GPU inference is unverified.

Database bootstrap: fresh versioned custom-format pg_dump of all canonical tables, no owners/ACL/password hashes; operational outbox/sync/source-state history excluded. Initial restore occurs only when its NEW PostgreSQL volume is empty. Later setup does not restore or reseed. A bootstrap state row records the seed version. Menu, canonical IDs, provenance, 213 collision mappings and order/review linkage remain in the relational data. Existing accepted volumes are never referenced.

RAG: coherent snapshots of source JSONL, SQLite FTS5 BM25, Chroma SQL/HNSW files and existing model identity. No index rebuild at ordinary startup. Data-init copies the immutable seed into its own named data volume once; an identity marker prevents replacing a different existing seed. Live reviews update only the portable data volume. Models mount read-only. The exact accepted PhoBERT/relevance artifacts are bundled under project-team internal distribution authorization. No separate model pack is required.

Local: cloud flags false, cloud credentials erased from the child Compose environment, metadata credential fallback disabled, cloud status DISABLED. Cloud: explicit mode and permission preflight before scheduler/export flags become true. Accepted bucket/database are denied by portable preflight. Sales keeps the existing local canonical analytics guard; Cross identifies Snowflake versus its explicitly labeled verified PostgreSQL fallback. Cloud pipeline progress is not claimed from container state alone.

The portable dbt source copy uses SNOWFLAKE_DATABASE instead of hardcoded FOODY. Only the two incremental DAGs are installed in the portable DAG discovery directory; legacy historical/batch DAGs are not installed and cannot be auto-unpaused. No historical CSV reload or manual DAG trigger is run by setup.

Images are built from public Python/Airflow bases on a teammate machine. Validation may reuse existing local base layers to conserve disk; that does not prove a cold-cache Internet build. All such differences are recorded in acceptance.


## Privacy-derived portable data

Accepted original: 20,530 RAG documents, untouched. Portable release: 20,490 source/Chroma/BM25 documents after withholding exactly 40 contact-bearing review IDs. Retained vectors and metadata are unchanged; no new embeddings or model versions were generated. The copied Chroma store was reconstructed only from retained existing vectors as an explicit packaging maintenance step, not on ordinary startup. Relational rows remain (405,412 orders / 20,865 reviews / 60,267 aspect rows), with contact spans redacted in the private-safe seed and a portable_rag_exclusions policy table. Prior accepted AI results are historical; full AI parity of this privacy-derived release is SKIPPED under the current resource/model-pack constraints.
