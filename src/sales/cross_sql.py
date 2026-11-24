"""Controlled Cross SQL using the shared parser, restricted credentials and verification."""
from src.monitoring.ai_timing import stage
import json,math,os,time
from contextlib import contextmanager, nullcontext
from decimal import Decimal
from src.security.analytical_sql import validate_analytical_sql,parse_select
from src.sales.text_to_sql import observe_text_to_sql
from pydantic import BaseModel, ConfigDict, Field

class CrossSQLDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sql: str = Field(min_length=8, max_length=6000)
from src.sales.queries import jsonable

ORDER_COLUMNS={'order_id','restaurant_id','restaurant_name','order_date','order_status',
               'sales_amount','total_amount','source','created_at','updated_at'}
REVIEW_COLUMNS={'review_id','order_id','restaurant_id','restaurant_name','review_date',
                'review_timestamp','created_at','rating','review_text','overall_sentiment',
                'predicted_sentiment','predicted_aspects','absa_predictions','relevance_label',
                'relevance_score','absa_model_version','source','is_valid_sentiment'}


@contextmanager
def analytical_connection(backend):
    if backend=='snowflake':
        import snowflake.connector
        role=os.getenv('SNOWFLAKE_AI_ROLE','FOODY_AI_READER')
        conn=snowflake.connector.connect(account=os.environ['SNOWFLAKE_ACCOUNT'],
            user=os.environ['SNOWFLAKE_USER'],password=os.environ['SNOWFLAKE_PASSWORD'],
            role=role,warehouse=os.getenv('SNOWFLAKE_WAREHOUSE','FOODY_WH'),
            database=os.getenv('SNOWFLAKE_DATABASE','FOODY'),schema='STAGING',login_timeout=10,network_timeout=10)
        try:
            conn.cursor().execute('USE SECONDARY ROLES NONE')
            conn.cursor().execute('ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS=5')
            actual=conn.cursor().execute('SELECT CURRENT_ROLE()').fetchone()[0]
            if actual.upper()!=role.upper():raise ValueError('restricted_role_required')
            yield conn,'snowflake',{'orders':'FOODY.STAGING.STG_ORDERS','reviews':'FOODY.STAGING.STG_REVIEWS'}
        finally:conn.close()
    else:
        import psycopg
        dsn=os.getenv('FOODY_AI_DATABASE_URL')
        if not dsn:raise ValueError('read_only_database_credentials_not_configured')
        with psycopg.connect(dsn) as conn:
            with conn.transaction(force_rollback=True):
                conn.execute('SET TRANSACTION READ ONLY')
                conn.execute("SET LOCAL statement_timeout='5s'")
                if conn.execute('SELECT current_user').fetchone()[0]!='foody_ai_readonly':
                    raise ValueError('restricted_role_required')
                yield conn,'postgres',{'orders':'ai_cross.orders','reviews':'ai_cross.reviews'}


def quoted(value):return "'"+str(value).replace("'","''")+"'"


def reference_query(plan,tables):
    """Semantic oracle, never passed to Qwen as the generated answer."""
    review=plan.metric in {'negative_review_count','positive_review_count','negative_review_rate','review_count','average_rating_10','latest_review'}
    table=tables['reviews' if review else 'orders'];field='review_date' if review else 'order_date'
    where=f"{field} >= DATE {quoted(plan.start_date)} AND {field} <= DATE {quoted(plan.end_date)}"
    if plan.restaurant_ids:where+=' AND restaurant_id IN ('+','.join(map(quoted,plan.restaurant_ids))+')'
    negative="CASE WHEN is_valid_sentiment AND UPPER(overall_sentiment)='NEGATIVE' THEN 1 ELSE 0 END"
    delivered="CASE WHEN UPPER(order_status)='DELIVERED' THEN COALESCE(total_amount,sales_amount) ELSE 0 END"
    delivered_count="CASE WHEN UPPER(order_status)='DELIVERED' THEN 1 ELSE 0 END"
    expressions={'negative_review_count':f'SUM({negative})','review_count':'COUNT(*)',
        'positive_review_count':"SUM(CASE WHEN is_valid_sentiment AND UPPER(overall_sentiment)='POSITIVE' THEN 1 ELSE 0 END)",
        'negative_review_rate':f'SUM({negative})*1.0/NULLIF(SUM(CASE WHEN is_valid_sentiment THEN 1 ELSE 0 END),0)',
        'average_rating_10':"AVG(CASE WHEN source='live_web' THEN rating*2 ELSE rating END)",
        'delivered_revenue':f'SUM({delivered})','delivered_orders':f'SUM({delivered_count})',
        'average_order_value':f'SUM({delivered})/NULLIF(SUM({delivered_count}),0)',
        'cancellation_rate':"SUM(CASE WHEN UPPER(order_status)='CANCELLED' THEN 1 ELSE 0 END)*1.0/NULLIF(COUNT(*),0)"}
    if plan.metric=='latest_order':
        return f"SELECT order_id,restaurant_id,restaurant_name,order_status,total_amount,created_at,order_date FROM {table} WHERE {where} AND source='live_web' ORDER BY created_at DESC NULLS LAST,order_id DESC LIMIT 1"
    if plan.metric=='latest_review':
        return f"SELECT review_id,order_id,restaurant_id,restaurant_name,review_text,rating,overall_sentiment,relevance_label,review_timestamp,created_at,review_date FROM {table} WHERE {where} ORDER BY COALESCE(review_timestamp,CASE WHEN source='live_web' THEN created_at ELSE CAST(review_date AS TIMESTAMP) END) DESC,review_id DESC LIMIT 1"
    if plan.metric=='sales_negative_reviews':
        ow=where;rw=where.replace('order_date','review_date')
        return f"""WITH sales AS (SELECT restaurant_id,MAX(restaurant_name) AS restaurant_name,SUM({delivered}) AS delivered_revenue FROM {tables['orders']} WHERE {ow} GROUP BY restaurant_id),
        feedback AS (SELECT restaurant_id,SUM({negative}) AS negative_review_count FROM {tables['reviews']} WHERE {rw} GROUP BY restaurant_id)
        SELECT sales.restaurant_id,sales.restaurant_name,sales.delivered_revenue,feedback.negative_review_count FROM sales JOIN feedback ON sales.restaurant_id=feedback.restaurant_id
        WHERE feedback.negative_review_count>0 ORDER BY delivered_revenue DESC,sales.restaurant_id LIMIT {plan.limit}"""
    expr=expressions[plan.metric];group='';prefix='';tail=''
    if plan.dimension in {'month','day'}:
        dimension=f"DATE_TRUNC('{plan.dimension}',{field})";prefix=dimension+' AS period,';group=' GROUP BY '+dimension
        tail=' ORDER BY '+(plan.metric+' DESC NULLS LAST,period' if plan.ranking else 'period')
    elif plan.dimension=='restaurant':
        prefix='restaurant_id,MAX(restaurant_name) AS restaurant_name,';group=' GROUP BY restaurant_id';tail=f' ORDER BY {plan.metric} DESC NULLS LAST,restaurant_id'
    supporting=f",SUM({negative}) AS negative_review_count,SUM(CASE WHEN is_valid_sentiment THEN 1 ELSE 0 END) AS eligible_review_count" if plan.metric=='negative_review_rate' else ''
    return f'SELECT {prefix}{expr} AS {plan.metric}{supporting} FROM {table} WHERE {where}{group}{tail} LIMIT {plan.limit}'


@stage('sql_execution')
def execute(conn,statement,dialect):
    # A rejected model query must not leave PostgreSQL's outer read-only transaction aborted.
    with conn.transaction(force_rollback=True) if dialect=='postgres' else nullcontext():
        cursor=conn.cursor()
        try:
            cursor.execute(statement)
            keys=[c[0].lower() for c in cursor.description];rows=cursor.fetchmany(101)
            if len(rows)>100:raise ValueError('result_limit')
            return jsonable([dict(zip(keys,r,strict=True)) for r in rows])
        finally:cursor.close()


def same_results(actual,expected):
    if len(actual)!=len(expected):return False
    for a,b in zip(actual,expected):
        if set(a)!=set(b):return False
        for key,value in a.items():
            other=b[key]
            if isinstance(value,(int,float,Decimal)) and isinstance(other,(int,float,Decimal)):
                if not math.isclose(float(value),float(other),rel_tol=1e-11,abs_tol=.00001):return False
            elif str(value)!=str(other):return False
    return True


def verify_scope(statement,plan,dialect,tables):
    from sqlglot import exp
    tree=parse_select(statement,dialect)
    columns={c.name.lower() for c in tree.find_all(exp.Column)}
    literals={str(n.this).upper() for n in tree.find_all(exp.Literal)}
    if plan.metric in {'negative_review_count','sales_negative_reviews'} and not ({'is_valid_sentiment','overall_sentiment'}<=columns and 'NEGATIVE' in literals):
        raise ValueError('sentiment_definition')
    if plan.metric in {'delivered_revenue','delivered_orders','average_order_value','sales_negative_reviews'} and not ('order_status' in columns and 'DELIVERED' in literals):
        raise ValueError('revenue_definition')
    if any(isinstance(n,(exp.Or,exp.Not)) for n in tree.walk()):raise ValueError('unsafe_scope_expression')
    for select in tree.find_all(exp.Select):
        base=any('.'.join(p.name.lower() for p in t.parts) in {v.lower() for v in tables.values()}
                 and t.find_ancestor(exp.Select) is select for t in select.find_all(exp.Table))
        if not base:continue
        where=select.args.get('where')
        if where is None:raise ValueError('scope_filter_required')
        literals={str(n.this) for n in where.find_all(exp.Literal)}
        if not {plan.start_date,plan.end_date}<=literals:raise ValueError('period_scope')
        id_filters=[]
        for n in where.find_all(exp.EQ,exp.In):
            if isinstance(n.this,exp.Column) and n.this.name.lower()=='restaurant_id':
                id_filters.extend(str(x.this) for x in n.find_all(exp.Literal))
        if set(id_filters)!=set(plan.restaurant_ids):raise ValueError('restaurant_scope')


def answer_sql(question,plan,gateway_factory=None):
    backend=os.getenv('FOODY_CROSS_ANALYTICS_BACKEND','postgres');fallback_notice=None
    try:return _answer_sql(question,plan,backend,gateway_factory)
    except Exception as error:
        if backend!='snowflake':raise
        # Explicitly label a verified local fallback; never present it as Snowflake.
        fallback_notice='Snowflake hiện không sẵn sàng; kết quả dưới đây dùng PostgreSQL canonical đã kiểm chứng.'
        result=_answer_sql(question,plan,'postgres',gateway_factory)
        result['source_notice']=fallback_notice;result['warehouse_status']='UNAVAILABLE';return result


def _answer_sql(question,plan,backend,gateway_factory):
    started=time.perf_counter();attempts=[]
    with analytical_connection(backend) as (conn,dialect,tables):
        schema={tables['orders']:ORDER_COLUMNS,tables['reviews']:REVIEW_COLUMNS}
        if plan.metric!='sales_negative_reviews':
            review=plan.metric in {'negative_review_count','positive_review_count','negative_review_rate','review_count','average_rating_10','latest_review'}
            schema={tables['reviews' if review else 'orders']:REVIEW_COLUMNS if review else ORDER_COLUMNS}
        reference=reference_query(plan,tables)
        safe_reference,_=validate_analytical_sql(reference,schema,dialect=dialect)
        expected=execute(conn,safe_reference,dialect)
        answer_rows=expected
        details={'sql':safe_reference,'generated':False,'execution_mode':'VERIFIED_ANALYTICS_FALLBACK','validation_status':'PASS','backend':backend,'attempts':attempts,'tables_used':[]}
        if plan.metric.startswith('latest_'):details['execution_mode']='VERIFIED_SQL_LOOKUP'
        # Latest-record lookups need no model call.
        if not plan.metric.startswith('latest_'):
            if gateway_factory is None:
                from src.rag_v3.cli import selected_v3_gateway
                gateway_factory=selected_v3_gateway
            compact={k:sorted(v) for k,v in schema.items()}
            system=('Generate one read-only '+dialect+' SELECT, optional SELECT CTEs. Use only supplied tables/columns. Return JSON with exactly one key: sql. '
                'Use literal dates with >= and <= and exact restaurant IN filter only when IDs supplied. '
                'Revenue counts DELIVERED only using COALESCE(total_amount,sales_amount). Negative reviews require is_valid_sentiment AND UPPER(overall_sentiment)=\'NEGATIVE\'. '
                'Month dimension uses DATE_TRUNC(\'month\', date) AS period, never a restaurant ranking. '
                'is_valid_sentiment is BOOLEAN: use SUM(CASE WHEN is_valid_sentiment AND UPPER(overall_sentiment)=\'NEGATIVE\' THEN 1 ELSE 0 END), never SUM(boolean). '
                'Date columns are DATE. DATE_TRUNC takes a quoted text unit first, then a date. '
                'restaurant_id is TEXT: always quote IDs, for example IN (\'145\'), never IN (145). '
                'For review metrics filter and group review_date; for sales metrics use order_date. Do not substitute created_at for business dates. '
                'For a ranking ORDER BY the requested metric DESC first, then period or restaurant_id ASC as tie breaker. '
                'ORDER BY may contain ONLY returned output aliases: month ranking uses metric DESC, period ASC; never add restaurant_id to month ranking. '
                'Use the exact expected output aliases. Ranking ties sort by period or restaurant_id ascending. Ratings normalize live_web scale 5 by multiplying 2. No explanation in SQL.')
            user=json.dumps({'question':question,'schema':compact,'plan':plan.as_dict(),'expected_columns':list(expected[0]) if expected else [plan.metric]},ensure_ascii=False)
            for number in range(2):
                statement=None
                try:
                    with gateway_factory() as gateway:
                        raw,usage=gateway.generate(system=system,user=user,response_schema=CrossSQLDraft.model_json_schema(),prompt_version='cross_sql_v2',max_new_tokens=500,component='cross_analysis_text_to_sql',timeout_seconds=150)
                    draft=CrossSQLDraft.model_validate(raw)
                    statement,used=validate_analytical_sql(draft.sql,schema,dialect=dialect)
                    verify_scope(statement,plan,dialect,tables)
                    actual=execute(conn,statement,dialect)
                    if not same_results(actual,expected):raise ValueError('result_shape_or_semantic_mismatch')
                    attempts.append({'attempt':number+1,'status':'PASS'})
                    details.update(sql=statement,generated=True,execution_mode='QWEN_TEXT_TO_SQL',tables_used=used)
                    answer_rows=actual
                    observe_text_to_sql('success',usage.get('latency_ms',0),0,(time.perf_counter()-started)*1000,tables=())
                    break
                except Exception as error:
                    reason=str(error) if isinstance(error,ValueError) else type(error).__name__
                    if type(error).__name__=='UndefinedFunction':reason='function_type_mismatch'
                    if type(error).__name__=='GroupingError':reason='grouping_or_ordering'
                    reason=reason if reason in {'grouping_or_ordering','function_type_mismatch','unexpected_parameters','table_allowlist','column_allowlist','period_scope','restaurant_scope','result_shape_or_semantic_mismatch','non_select','function_allowlist','unsafe_scope_expression','scope_filter_required','row_limit','sentiment_definition','revenue_definition','wildcard_select','cast_type'} else 'query_rejected'
                    from src.monitoring.metrics import CROSS_CHAT_VALIDATION_FAILURES
                    CROSS_CHAT_VALIDATION_FAILURES.inc()
                    attempts.append({'attempt':number+1,'status':'REJECTED','reason':reason,'error_category':type(error).__name__,
                                     'validated_sql':statement})
                    user+='\nPrevious rejection: '+reason+'. Correct only this error. Preserve dimension '+plan.dimension+' and expected aliases, exact date bounds and restaurant IDs. One correction only.'
            else:observe_text_to_sql('fallback',0,0,(time.perf_counter()-started)*1000)
        stamp='loaded_at' if backend=='snowflake' else 'updated_at'
        freshness=execute(conn,f"SELECT MAX(order_date) AS latest_order_business_date,MAX({stamp}) AS latest_order_update_utc FROM {tables['orders']}",dialect)[0]
        stamp='loaded_at' if backend=='snowflake' else 'created_at'
        freshness.update(execute(conn,f"SELECT MAX(review_date) AS latest_review_business_date,MAX({stamp}) AS latest_review_update_utc FROM {tables['reviews']}",dialect)[0])
        return {'rows':answer_rows,'sql_details':details,'source':backend,'warehouse_status':'AVAILABLE' if backend=='snowflake' else 'NOT_SELECTED','data_freshness':freshness,'checked_at_utc':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}
