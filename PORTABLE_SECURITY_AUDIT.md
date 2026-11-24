# Security audit — authorized internal bundle

The project team explicitly authorized inclusion of the two exact accepted
PhoBERT/relevance artifacts for internal teammate distribution. The signed-off
scope is recorded in config/portable/distribution_policy.json; upstream model and
dataset licenses remain binding. Public/commercial redistribution is not granted.

Both exact file hashes/sizes are mandatory. Authorization never permits a
different checkpoint, duplicate renamed model or replacement download. All 22
model/config/tokenizer files are declared in the model manifest and checked.

AWS/Snowflake credentials, .env.local, .env.demo, .env.portable, private keys,
diagnostics and raw logs remain excluded. The actual final archive must pass CRC,
all per-file checksums, known credential signatures, customer-contact scans,
decoded CSV/Parquet/JSONL/custom PostgreSQL COPY scans, and physical RAG exclusion
checks. Models are also scanned for original contact signatures and credentials.
Matched values are never printed. Consult the adjacent final hash-bound audit.

The sanitized seed/RAG snapshot is unchanged. Forty contact-bearing documents
remain excluded from every retrieval representation; their relational rows are
retained with contact redactions. No accepted database or index was modified.

Local's final secret-free env-file layer prevents saved Cloud credentials from
reappearing through Windows empty-variable fallback. Cloud is opt-in and rejects
known accepted account/database/bucket. Fixed account-wide roles require a
separately authorized non-production Snowflake account; owner SQL is render-only.

The legacy relevance sklearn serialization warning is disclosed in acceptance.
Passing a bounded load check is not proof of full prediction parity or clean-
machine security/portability. No credentials or new model versions were hardcoded.

