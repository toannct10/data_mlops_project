"""Parameterized read-only all-record aggregates for queries without a date restriction.

Model SQL policy is unchanged. Templates contain no answers or question identifiers.
"""
import re
from src.sales.semantic_plan import plain,parse_time_scope
from src.sales.vietnamese import rows_text

def answer(conn,restaurant_id,question,*,today):
    q=plain(question)
    if re.search(r'chiem|loai bo|chenh lech|tang manh|tu thang|sang thang|so sanh|trung binh',q):return None
    if parse_time_scope(q,today=today).periods or re.search(r'hom nay|hom qua|ngay mai|tuan nay|thang nay|thang truoc|quy',q):return None
    month=bool(re.search(r'thang nao',q));rank=bool(re.search(r'cao nhat|thap nhat|nhieu.+nhat|it.+nhat|top\s*\d+',q))
    global_scope=bool(re.search(r'toan bo|toan he thong|nha hang nao|top\s*\d+.*nha hang',q))
    count=bool(re.search(r'don hang|don giao|don da giao',q))
    if not (month or global_scope or count or re.search(r'tong.*doanh thu',q)):return None
    metric='cancellation_rate' if 'ty le' in q and 'huy' in q else 'delivered_orders' if re.search(r'giao thanh cong|da giao',q) else 'total_orders' if count and 'doanh thu' not in q else 'delivered_revenue'
    dimension='period' if month else 'restaurant' if re.search(r'nha hang nao|top\s*\d+.*nha hang',q) else 'scalar'
    top=re.search(r'top\s*(\d+)',q);limit=min(int(top[1]),20) if top else 1
    prefix="DATE_TRUNC('month',order_date)::date AS period," if dimension=='period' else 'restaurant_id,MAX(restaurant_name) AS restaurant_name,' if dimension=='restaurant' else ''
    aggregates="""COUNT(*) AS total_orders,COUNT(*) FILTER(WHERE UPPER(order_status)='DELIVERED') AS delivered_orders,
       COUNT(*) FILTER(WHERE UPPER(order_status)='CANCELLED') AS cancelled_orders,
       COALESCE(SUM(COALESCE(total_amount,sales_amount)) FILTER(WHERE UPPER(order_status)='DELIVERED'),0) AS delivered_revenue,
       COUNT(*) FILTER(WHERE UPPER(order_status)='CANCELLED')::numeric/NULLIF(COUNT(*),0) AS cancellation_rate,
       MIN(order_date) AS data_start,MAX(order_date) AS data_end"""
    where='' if global_scope else ' WHERE restaurant_id=%s';params=[] if global_scope else [str(restaurant_id)]
    group=" GROUP BY DATE_TRUNC('month',order_date)::date" if dimension=='period' else ' GROUP BY restaurant_id' if dimension=='restaurant' else ''
    minimum=re.search(r'it nhat\s+(\d+)\s+don',q)
    if minimum and not group:return None
    having=' HAVING COUNT(*) >= %s' if minimum else ''
    if minimum:params.append(int(minimum[1]))
    order=' ORDER BY '+metric+(' ASC' if re.search(r'thap nhat|it nhat',q) and not minimum else ' DESC')+(',period' if dimension=='period' else ',restaurant_id' if dimension=='restaurant' else '') if group else ''
    sql='SELECT '+prefix+aggregates+' FROM ai_cross.orders'+where+group+having+order+' LIMIT %s';params.append(limit)
    from src.sales.compound import execute
    data=execute(conn,sql,params)
    name=conn.execute('SELECT MAX(restaurant_name) FROM ai_cross.orders WHERE restaurant_id=%s',(str(restaurant_id),)).fetchone()[0] if not global_scope else None
    window_sql='SELECT MIN(order_date) AS first_date,MAX(order_date) AS last_date FROM ai_cross.orders'+where
    window_params=[] if global_scope else [str(restaurant_id)]
    window=execute(conn,window_sql,window_params)[0];start,end=window['first_date'],window['last_date']
    scope='toàn bộ hệ thống' if global_scope else 'nhà hàng đang chọn'
    scope+=f', các bản ghi đang lưu từ {start} đến {end}'
    answer=rows_text(data,restaurant_name=name,scope=scope,metrics={metric,'cancelled_orders','total_orders'} if metric=='cancellation_rate' else {metric})
    if end and end>today:answer+=' Có bản ghi mang ngày sau hôm nay trong bộ dữ liệu; tổng này mô tả dữ liệu đang lưu, không phải dự báo doanh thu tương lai.'
    return {'route':'SALES_SQL','source':'PostgreSQL canonical read-only aggregates','answer':answer,
        'restaurant_id':str(restaurant_id),'sql_facts':data,'display_rows':data,'review_evidence':[],
        'status':'ok' if data else 'no_data','execution_mode':'VERIFIED_ANALYTICS_FALLBACK',
        'query_plan':{'intent':'rank' if group else 'aggregate','metric':metric,'restaurant_ids':[] if global_scope else [str(restaurant_id)],'scope':scope},
        'sql_details':{'sql':sql,'parameters':params,'supporting_queries':[{'sql':window_sql,'parameters':window_params,'generated':False}], 'generated':False,'execution_mode':'VERIFIED_ANALYTICS_FALLBACK','validation_status':'Fixed parameterized SELECT; read-only connection','generation_attempt_count':0}}
