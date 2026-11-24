# Cloud setup — explicit external opt-in

Local needs no AWS/Snowflake account. Do not place real credentials in this guide or ZIP. Configure the extracted machine's ignored `.env.portable` only. No AWS or Snowflake server is bundled in Docker.

Use your own authorized account/resources, AWS region ap-southeast-2, a separate bucket or approved isolated namespace `foody-portable...`, and Snowflake database `FOODY_PORTABLE` (or another isolated database), warehouse FOODY_WH. Automated tests must not use the accepted bucket/database or accepted credentials. The portable preflight refuses the known accepted bucket and database FOODY.

AWS owner must inspect STS caller identity and effective bucket ownership/policies. The runtime needs s3:ListBucket with its prefix condition, s3:GetObject for duplicate-content verification, and s3:PutObject only under the immutable events prefix. No DeleteObject is needed. For SSE-KMS add only the approved key permissions; the current exporter requests AES256. Verify effective IAM plus bucket policy; an identity policy alone cannot override cross-account or explicit denial. Use an owner-authorized IAM policy simulation or approved prefix-scoped test. Set PORTABLE_PUT_PERMISSION_VERIFIED=true only after that check; this flag is an attestation, not proof from ListBucket. No permission-probe object is uploaded automatically.

Snowflake account must be the account identifier, e.g. organization-account, without https:// or .snowflakecomputing.com. Do not substitute a guessed region into an already working identifier. Check HTTP 404 separately from authentication/role failures. The runtime uses FOODY_PIPELINE, FOODY_DBT and FOODY_AI_READER; USE SECONDARY ROLES NONE is enforced for dbt/AI connections. Never use ACCOUNTADMIN as a runtime default.

## Owner preparation — never automatic

An authorized owner must create the isolated RAW.ORDERS and RAW.REVIEWS tables with the current canonical columns (including provenance, model/aspect/relevance fields and live timestamps), STAGING/MART schemas, apply the additive `sql/snowflake/incremental_event_safety.sql` and restricted grants in `incremental_runtime_roles.sql` after replacing qualified FOODY database and FOODY_WH warehouse identifiers with the reviewed isolated values. Inspect each rendered statement before execution; the original templates target the accepted architecture and must NOT be run verbatim against another environment. No setup script executes these migrations or copies canonical CSVs to Snowflake.

Grant the pipeline SELECT/INSERT/UPDATE only on its RAW relations plus audit/lock permissions; grant dbt SELECT on RAW and creation/ownership only on its own STAGING/MART objects; grant AI SELECT only on approved STAGING/MART relations. Preserve owner boundaries. Assign the three roles to authorized runtime users explicitly. Build the initial empty staging views with dbt before the role-connectivity preflight if needed; never reload accepted historical facts to make tests pass.

Fill AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, optional AWS_SESSION_TOKEN, FOODY_S3_BUCKET, FOODY_S3_PREFIX, SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD, SNOWFLAKE_DATABASE and SNOWFLAKE_WAREHOUSE privately. Then:

```powershell
.\01_SETUP.ps1 -Mode Cloud -EnableCloudWrites
.\02_START.ps1
.\03_HEALTH_CHECK.ps1
```

The preflight performs read-only STS/ListBucket and restricted Snowflake role checks before writes are enabled. Fresh export/ingest schedules are automatic (1 minute / 2 minutes), catchup=False, max_active_runs=1. Existing sequence-aware MERGE, immutable event IDs, per-event audit and quality gate remain unchanged. A cloud error leaves the committed portable outbox pending; it is not a reason to reset tables or trigger historical replay.

Full cloud acceptance remains SKIPPED until separately authorized test resources and credentials exist. Historical dbt 19/19 is a prior accepted result, not a new portable cloud test.

Sources: https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-with-s3-policy-actions.html ; https://docs.snowflake.com/en/user-guide/admin-security-access-control-overview ; https://docs.snowflake.com/en/user-guide/admin-account-identifier .


Render the reviewed isolated schema/grant script with `./07_CLOUD_PREPARE.ps1 -DatabaseName FOODY_PORTABLE -WarehouseName FOODY_WH`. It only writes diagnostics/cloud_owner_review.sql and never executes SQL. The table definitions are derived from the current incremental MERGE payload types. Run the reviewed script in a separately authorized owner session, grant the three roles to the runtime user, then use FOODY_DBT for an initial dbt build to create staging views before enabling the automatic schedules. Empty new RAW tables are not a historical-data reload. External execution of this preparation remains SKIPPED in release acceptance.


## Portability follow-up

Read CLEAN_MACHINE_ACCEPTANCE_CHECKLIST.md and MODEL_CAPABILITIES.md. The latter
separates stored analytics and SQL-only evidence from unexecuted full AI tests.
Local Compose calls use an explicit secret-free env-file override so saved Cloud
credentials cannot reappear when Windows removes empty process variables.
Cloud requires a separately authorized non-production Snowflake account because
the runtime role names are account-wide. The known accepted account is blocked;
07_CLOUD_PREPARE only renders SQL and cannot verify which account an owner later
selects. Never apply its role grants in the accepted account.
