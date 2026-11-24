# Clean-machine teammate acceptance checklist

NOT EXECUTED ON A SEPARATE MACHINE. Record actual PASS/FAIL/SKIPPED, tester,
date/time/timezone, OS/CPU/RAM, Docker allocation, free disk, Docker/Compose
versions, received ZIP SHA256 and tested image IDs. This checklist is not a PASS.

## Obtain and verify the one internal ZIP

- [ ] Compare Get-FileHash -Algorithm SHA256 with the adjacent current ZIP
  checksum/RELEASE_ARCHIVE_METADATA.json from the trusted teammate handoff.
- [ ] Extract once to a writable path containing spaces on a separate Windows
  machine with PowerShell 5.1/7, Docker Desktop, WSL2 and Linux/amd64 containers.
- [ ] Check SHA256SUMS.txt and all model_manifest.json hashes, including both
  fine-tuned models already inside this ZIP. Do not obtain a separate model pack.
- [ ] Respect internal-only authorization and all included upstream licenses.
  Confirm Internet for external Docker image/package builds; no image tar is
  bundled. No full-AI minimum RAM or CUDA support is certified by this checklist.

## Local installation and actual startup

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\06_INSTALL_MODELS.ps1
.\01_SETUP.ps1 -Mode Local
.\02_START.ps1
.\03_HEALTH_CHECK.ps1
```

- [ ] 06 succeeds with no pack argument; all accepted model versions are present.
- [ ] A deliberately missing/corrupt synthetic fixture causes verification/setup
  to fail clearly. Do not corrupt a real accepted model to run a negative test.
- [ ] First public image pulls and both app/worker builds really succeed on this
  machine. Record pip check and image IDs; cached-host tests are not cold-build proof.
- [ ] Local startup works with all AWS/Snowflake credentials blank. Local UI says
  Cloud disabled, writers are false, DAGs schedule=None/paused and no cloud calls
  occur. Switching from saved synthetic Cloud values to Local must blank all
  credential fields in effective Compose configuration.
- [ ] Project is foody-portable-demo, database DNS is postgres and only its own
  network/volumes are used. Resolve conflicts by changing PORTABLE_*_PORT.
- [ ] Initial seed: 405412 orders, 20865 reviews, 60267 aspects, 40 privacy
  exclusions and no inherited operational outbox rows. Read-only queries only.
- [ ] Source/BM25/Chroma each 20490 documents; retained ID SHA256
  c3623b48e00db1ac9a8bac95fb05c7344e62b497cccfbc02cc420e602c528834.
  Excluded IDs are absent from retrieval/citations. Three pre-existing unindexed
  NULL-relevance rows remain accounted for; do not manufacture extra embeddings.
- [ ] All eight services truly healthy: PostgreSQL, Qwen, backend, BI, Ordering,
  Prometheus, Grafana, Airflow. Verify Airflow scheduler/metadatabase/triggerer,
  Prometheus targets/real query and Grafana datasource; HTTP 200 alone is insufficient.

## Actual AI, persistence and privacy tests

- [ ] Execute actual PhoBERT/relevance predictions; record the known sklearn
  1.6.1 -> 1.7.2 serialization warning and evaluate broader parity explicitly.
- [ ] Verify SQL-only facts separately from Qwen generation. Test actual Qwen
  text-to-SQL, grounded RAG, restaurant/date filters, empty evidence and citations.
- [ ] Run the separately supplied frozen 50-question QA protocol and reviewed
  separate-target harness. Those QA assets are external tests, not required AI
  runtime model files. Historical Sales 25/25 / Cross 24/25 are not new PASS.
- [ ] C19 FoodQuality citation remains unresolved until corrected and retested.
- [ ] Create uniquely identifiable portable test orders; deliver and review one,
  test relevance and PhoBERT aspects, exact order/review linkage, transactional
  outbox and one incremental RAG update. Test irrelevant review exclusion. Record
  intentional count changes; do not rebuild historical indexes.
- [ ] Repeat setup: same project/volumes/passwords, no reseed or duplicate assets.
- [ ] Stop/start with 04_STOP.ps1 / 02_START.ps1: all data/index updates persist.
- [ ] 05_DIAGNOSE.ps1 outputs allowlisted statuses/error categories without secrets
  or raw review/event payloads.

## Optional Cloud and sign-off

- [ ] Use separately authorized non-production S3 resources and a separate
  Snowflake account. Known accepted account/database/bucket are blocked. Fixed
  Snowflake role names are account-wide; a new database alone does not isolate them.
- [ ] Privately configure .env.portable. Owner verifies endpoint, prefix-scoped
  List/Get/Put policy, isolated database/warehouse and restricted role grants.
- [ ] 07_CLOUD_PREPARE.ps1 renders only; owner selects/reviews the non-production
  account before applying SQL. Never use administrator roles as runtime defaults.
- [ ] Run 01_SETUP.ps1 -Mode Cloud -EnableCloudWrites after owner authorization.
  Without opt-in or permissions, writers stay disabled. No accepted credentials.
- [ ] Let scheduled DAGs process new portable IDs, verify S3 and Snowflake
  RAW/STAGING/MART, actual dbt build/tests, duplicate and older-event replay
  protection. No canonical CSV reload, manual historical trigger or data reset.
- [ ] Return to Local; ensure credentials/writers/schedules are disabled again.
- [ ] Mark actual GPU inference SKIPPED unless separately exercised. Attach
  sanitized commands, exit codes, timestamps, hashes, latency/memory and test IDs.
- [ ] Do not certify full portability until separate-machine startup and relevant
  full AI/Cloud tests have actually passed; disclose every remaining FAIL/SKIPPED.
