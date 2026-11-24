"""Content-free portable health, private cloud preflight and one-time data seeding."""
import argparse,hashlib,json,os,re,shutil,sqlite3,sys,time
from pathlib import Path
from urllib.request import urlopen,Request

class PortableConfigurationError(ValueError):
    """Only static, non-secret configuration diagnostics may reach the console."""

def http(url):
    with urlopen(url,timeout=10) as r:
        body=r.read();assert r.status==200
        try:return json.loads(body)
        except ValueError:return body.decode()

def service_health(name):
    endpoints={'backend':'http://127.0.0.1:8000/health','bi':'http://127.0.0.1:8501/_stcore/health','ordering':'http://127.0.0.1:8502/_stcore/health','airflow':'http://127.0.0.1:8080/health'}
    result=http(endpoints[name])
    if name=='airflow':assert all(result[k]['status']=='healthy' for k in ('metadatabase','scheduler','triggerer'))
    if name=='backend':
        import psycopg
        with psycopg.connect(os.environ['FOODY_DATABASE_URL'],connect_timeout=5) as c:
            assert c.execute('SELECT COUNT(*) FROM portable_bootstrap_state').fetchone()[0]==1
        assert result['vector_index_available']
    return {'status':'PASS','component':name}

def init_data():
    source=Path('/seed-data');target=Path('/app/data');marker=target/'.portable-seed.json'
    release=json.loads(Path('/app/config/portable/data_manifest.json').read_text())
    identity=release['index_identity']
    if marker.exists():
        if json.loads(marker.read_text())['index_identity']!=identity:raise ValueError('Different data seed already installed; refusing overwrite')
        return {'status':'REUSED','index_identity':identity}
    if any(target.iterdir()):raise ValueError('Data volume is not empty; refusing partial bootstrap overwrite')
    for p in source.iterdir():
        if p.is_dir():shutil.copytree(p,target/p.name)
        else:shutil.copy2(p,target/p.name)
    marker.write_text(json.dumps({'index_identity':identity})+'\n')
    return {'status':'INITIALIZED','index_identity':identity}


def bootstrap_ready():
    """Wait for the committed seed and restricted login, without loading models."""
    import psycopg
    expected=json.loads(Path('/app/bootstrap/database_manifest.json').read_text())['seed_version']
    deadline=time.monotonic()+300
    while True:
        try:
            with psycopg.connect(os.environ['FOODY_DATABASE_URL'],connect_timeout=3) as c:
                c.execute('SET TRANSACTION READ ONLY')
                rows=c.execute('SELECT seed_version FROM portable_bootstrap_state').fetchall()
                if rows!=[(expected,)]:raise ValueError('Seed identity mismatch')
            with psycopg.connect(os.environ['FOODY_AI_DATABASE_URL'],connect_timeout=3) as c:
                c.execute('SET TRANSACTION READ ONLY')
                c.execute('SELECT COUNT(*) FROM ai_cross.orders').fetchone()
                privileges=c.execute("SELECT has_table_privilege(current_user,'public.fact_order','UPDATE'),has_table_privilege(current_user,'public.fact_review_v2','DELETE')").fetchone()
                if privileges!=(False,False):raise ValueError('Restricted role has write privileges')
            return {'status':'PASS','seed_version':expected,'restricted_login':'SELECT verified; UPDATE/DELETE denied'}
        except (psycopg.OperationalError,psycopg.ProgrammingError):
            if time.monotonic()>=deadline:raise PortableConfigurationError('Timed out waiting for committed database seed and restricted login')
            time.sleep(2)

def models():
    manifest=json.loads(Path('config/portable/model_manifest.json').read_text());missing=[];wrong=[]
    for row in manifest['files']:
        p=Path(row['path'])
        if not p.is_file():missing.append(row['path']);continue
        h=hashlib.sha256()
        with p.open('rb') as f:
            for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
        if p.stat().st_size!=row['bytes'] or h.hexdigest()!=row['sha256']:wrong.append(row['path'])
    if missing or wrong:return {'status':'FAIL','missing':missing,'wrong_version':wrong,'instruction':'All required AI models are bundled. Re-extract the checksum-verified internal ZIP and run 06_INSTALL_MODELS.ps1. No separate model pack or automatic download.'}
    return {'status':'PASS','files_verified':len(manifest['files'])}

def cloud_preflight():
    if os.getenv('FOODY_PORTABLE_MODE')!='cloud':return {'status':'DISABLED','notice':'LOCAL DEMO — Cloud synchronization disabled.'}
    # Guard the accepted project even if credentials were inadvertently provided.
    database=os.getenv('SNOWFLAKE_DATABASE','');bucket=os.getenv('FOODY_S3_BUCKET','');prefix=os.getenv('FOODY_S3_PREFIX','')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',database):raise PortableConfigurationError('Use a simple isolated Snowflake database identifier')
    if database.upper()=='FOODY' or bucket=='foody-intelligence-aip491-cloud-2026' or not prefix.startswith('foody-portable'):
        raise PortableConfigurationError('Isolated portable cloud resources required; accepted bucket/database are blocked')
    if os.getenv('SNOWFLAKE_ACCOUNT','').strip().lower()=='mrjdrtb-foody-sydney':
        raise PortableConfigurationError('Accepted Snowflake account is blocked; use a separate non-production account because runtime role names are account-wide')
    required=['AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','FOODY_S3_BUCKET','SNOWFLAKE_ACCOUNT','SNOWFLAKE_USER','SNOWFLAKE_PASSWORD','SNOWFLAKE_DATABASE','SNOWFLAKE_WAREHOUSE']
    missing=[key for key in required if not os.getenv(key)]
    if missing:raise PortableConfigurationError('Missing private cloud settings: '+', '.join(missing))
    import boto3,snowflake.connector
    session=boto3.Session(region_name='ap-southeast-2');session.client('sts').get_caller_identity()
    session.client('s3').list_objects_v2(Bucket=bucket,Prefix=prefix+'/',MaxKeys=1)
    for role in ('FOODY_PIPELINE','FOODY_DBT','FOODY_AI_READER'):
        with snowflake.connector.connect(account=os.environ['SNOWFLAKE_ACCOUNT'],user=os.environ['SNOWFLAKE_USER'],password=os.environ['SNOWFLAKE_PASSWORD'],role=role,warehouse=os.environ['SNOWFLAKE_WAREHOUSE'],database=database,login_timeout=10,network_timeout=10) as c:
            c.cursor().execute('USE SECONDARY ROLES NONE')
            assert c.cursor().execute('SELECT CURRENT_ROLE()').fetchone()[0]==role
            cursor=c.cursor();cursor.execute('SHOW GRANTS TO ROLE '+role)
            columns=[d[0].lower() for d in cursor.description];grants=[dict(zip(columns,r)) for r in cursor.fetchall()]
            if role=='FOODY_AI_READER':
                if any(r.get('privilege') not in {'SELECT','USAGE'} for r in grants):raise PortableConfigurationError('FOODY_AI_READER has unexpected write/owner privileges; owner review required')
                cursor.execute('SELECT COUNT(*) FROM '+database+'.STAGING.STG_ORDERS').fetchone()
                cursor.execute('SELECT COUNT(*) FROM '+database+'.STAGING.STG_REVIEWS').fetchone()
    if os.getenv('PORTABLE_PUT_PERMISSION_VERIFIED')!='true':raise PortableConfigurationError('Owner must verify prefix-scoped s3:PutObject permission before opt-in; connectivity alone is insufficient')
    return {'status':'PASS','scope':'read-only STS, ListBucket, role connectivity; explicit prior PutObject permission attestation required','roles':['FOODY_PIPELINE','FOODY_DBT','FOODY_AI_READER']}

def health():
    import psycopg
    checks={}
    def check(name,fn):
        try:checks[name]={'status':'PASS','details':fn()}
        except Exception as e:checks[name]={'status':'FAIL','error_category':type(e).__name__}
    def db():
        with psycopg.connect(os.environ['FOODY_DATABASE_URL'],connect_timeout=5) as c:
            c.execute('SET TRANSACTION READ ONLY');assert c.execute('SELECT COUNT(*) FROM portable_bootstrap_state').fetchone()[0]==1
            return 'SELECT and bootstrap state verified'
    def model_check():
        r=models()
        if r['status']!='PASS':raise ValueError('Missing or incorrect model pack')
        return r
    check('postgres',db);check('models',model_check)
    for name,url in {'backend':'http://rag-backend:8000/health','bi':'http://streamlit:8501/_stcore/health','ordering':'http://ordering-web:8502/_stcore/health','qwen':'http://qwen:8193/health'}.items():check(name,lambda u=url:http(u))
    def airflow():
        h=http('http://airflow:8080/health');assert all(h[k]['status']=='healthy' for k in ('metadatabase','scheduler','triggerer'));return 'webserver/scheduler/metadatabase/triggerer healthy'
    check('airflow',airflow)
    def prometheus():
        r=http('http://prometheus:9090/api/v1/targets');targets=r['data']['activeTargets'];assert targets and all(t['health']=='up' for t in targets)
        q=http('http://prometheus:9090/api/v1/query?query=up');assert q['status']=='success' and q['data']['result'];return {'targets_up':len(targets),'real_up_query':True}
    check('prometheus',prometheus)
    def grafana():
        import base64
        auth=base64.b64encode(('admin:'+os.environ['PORTABLE_GRAFANA_PASSWORD']).encode()).decode()
        r=http(Request('http://grafana:3000/api/datasources/uid/foody-prometheus/health',headers={'Authorization':'Basic '+auth}));assert r['status']=='OK';return {'datasource_status':r['status']}
    check('grafana',grafana)
    if os.getenv('FOODY_PORTABLE_MODE')=='cloud':check('cloud',cloud_preflight)
    else:checks['cloud']={'status':'DISABLED','notice':'LOCAL DEMO — Cloud synchronization disabled.'}
    return {'status':'PASS' if all(v['status'] in ('PASS','DISABLED') for v in checks.values()) else 'FAIL','checks':checks}

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['init-data','bootstrap-ready','service-health','models','cloud-preflight','health']);p.add_argument('service',nargs='?');a=p.parse_args()
    try:result={'init-data':init_data,'bootstrap-ready':bootstrap_ready,'models':models,'cloud-preflight':cloud_preflight,'health':health,'service-health':lambda:service_health(a.service)}[a.action]()
    except PortableConfigurationError as e:result={'status':'FAIL','error_category':'configuration','notice':str(e)}
    except Exception as e:result={'status':'FAIL','error_category':type(e).__name__,'notice':'Check configuration/dependencies; no secret-bearing exception text is logged.'}
    print(json.dumps(result,ensure_ascii=True));return 1 if result.get('status')=='FAIL' else 0

if __name__=='__main__':sys.exit(main())
