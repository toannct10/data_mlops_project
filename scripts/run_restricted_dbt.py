"""Run dbt with its configured primary role only, including every worker connection."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


def main():
    import snowflake.connector
    original_connect = snowflake.connector.connect
    expected = os.getenv('SNOWFLAKE_DBT_ROLE', 'FOODY_DBT').upper()
    if expected in {'ACCOUNTADMIN', 'SECURITYADMIN', 'SYSADMIN', 'USERADMIN', 'PUBLIC'}:
        raise ValueError('Restricted dbt role required')
    lock = Lock()

    def connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        try:
            with conn.cursor() as cursor:
                cursor.execute('USE SECONDARY ROLES NONE')
                role, secondary = cursor.execute('SELECT CURRENT_ROLE(),CURRENT_SECONDARY_ROLES()').fetchone()
                context = json.loads(secondary)
                if role.upper() != expected or context.get('roles') not in ('', [], None):
                    raise ValueError('dbt connection inherited unexpected roles')
            record = {'observed_at_utc':datetime.now(timezone.utc).isoformat(),
                      'role':role, 'secondary_roles':context, 'session_id':conn.session_id}
            with lock:
                path = Path('outputs/airflow/cloud_dbt_sessions.jsonl')
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('a', encoding='utf-8') as output:
                    output.write(json.dumps(record)+'\n')
            return conn
        except Exception:
            conn.close()
            raise

    snowflake.connector.connect = connect
    try:
        from dbt.cli.main import dbtRunner
        outcome = dbtRunner().invoke(sys.argv[1:])
        return 0 if outcome.success else 1
    finally:
        snowflake.connector.connect = original_connect


if __name__ == '__main__':raise SystemExit(main())
