"""Durable, content-free task observations. A query timestamp is not data freshness."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

STAGES = ('outbox_export', 'snowflake_load', 'dbt_build', 'quality_gate')
DIRECTORY = Path('outputs/airflow/cloud_status')


def record_stage(stage, status, *, run_id=None, details=None, error_category=None):
    if stage not in STAGES:
        raise ValueError('Unknown cloud stage')
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = DIRECTORY / (stage + '.json')
    prior = {}
    if path.exists():
        try: prior = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError): pass
    now = datetime.now(timezone.utc).isoformat()
    value = {'stage':stage, 'status':status, 'run_id':run_id, 'observed_at_utc':now,
             'last_success_utc':now if status == 'SUCCESS' else prior.get('last_success_utc'),
             'error_category':error_category, 'details':details or {}}
    temporary = path.with_suffix('.' + str(os.getpid()) + '.tmp')
    temporary.write_text(json.dumps(value, default=str) + '\n', encoding='utf-8')
    temporary.replace(path)
    return value


def task_success(context):
    mapping = {'publish_committed_events':'outbox_export',
               'load_snowflake_incremental':'snowflake_load',
               'dbt_build':'dbt_build', 'quality_gate':'quality_gate'}
    task = context['task_instance']
    result = task.xcom_pull(task_ids=task.task_id) or {}
    # Only explicit summaries leave the task, never event bodies or credentials.
    details = {k:v for k,v in result.items() if k in {
        'published','failed','s3_events','orders_before','orders_after',
        'reviews_before','reviews_after','raw_loaded_at','returncode','status'}}
    record_stage(mapping[task.task_id], 'SUCCESS', run_id=context['run_id'], details=details)


def task_failure(context):
    mapping = {'publish_committed_events':'outbox_export',
               'load_snowflake_incremental':'snowflake_load',
               'dbt_build':'dbt_build', 'quality_gate':'quality_gate'}
    record_stage(mapping[context['task_instance'].task_id], 'FAILED',
                 run_id=context['run_id'], error_category=type(context.get('exception')).__name__)


def cloud_snapshot(directory=DIRECTORY, now=None):
    if os.getenv('FOODY_PORTABLE')=='true' and os.getenv('FOODY_PORTABLE_MODE','local')=='local':
        return {'status':'DISABLED','configured':False,'stages':{s:{'status':'DISABLED'} for s in STAGES},'last_success_utc':None,'age_seconds':None,'raw_loaded_at':None,'notice':'LOCAL DEMO — Cloud synchronization disabled.'}
    now = now or datetime.now(timezone.utc)
    stages = {}
    for stage in STAGES:
        try: stages[stage] = json.loads((Path(directory)/(stage+'.json')).read_text(encoding='utf-8'))
        except (OSError, ValueError): stages[stage] = {'status':'NOT_OBSERVED'}
    successful = stages['quality_gate'].get('last_success_utc')
    age = None if not successful else max(0, (now-datetime.fromisoformat(successful)).total_seconds())
    failed = any(v['status']=='FAILED' for v in stages.values())
    complete = all(v['status']=='SUCCESS' for v in stages.values())
    # A quality gate from an older run must not certify an unfinished newer build.
    same_run = stages['quality_gate'].get('run_id') == stages['dbt_build'].get('run_id') == stages['snowflake_load'].get('run_id')
    healthy = complete and same_run and age is not None and age < 600
    return {'status':'HEALTHY' if healthy else 'ERROR' if failed else 'DEGRADED',
            'configured':bool(os.getenv('SNOWFLAKE_ACCOUNT')), 'stages':stages,
            'last_success_utc':successful, 'age_seconds':age,
            'raw_loaded_at':stages['snowflake_load'].get('details',{}).get('raw_loaded_at')}
