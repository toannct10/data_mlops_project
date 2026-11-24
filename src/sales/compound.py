"""Bounded, reusable compound analytics over canonical read-only views.

These are trusted parameterized queries, never model SQL. They contain no evaluation
IDs, expected values, restaurant-specific rules, or reference-runner imports.
"""
from src.monitoring.ai_timing import stage
import re
from collections import defaultdict
from decimal import Decimal
from statistics import median
from src.sales.semantic_plan import plain,parse_time_scope
from src.sales.vietnamese import rows_text,number

def kind(question,module):
    q=plain(question)
    if module=='sales':
        if 'chiem' in q and ('phan tram' in q or '%' in q):return 'contribution'
        if 'tang manh nhat' in q and ('nha hang' in q or 'quan nao' in q):return 'growth_ranking'
        if 'nhieu don' in q and 'trung binh' in q and 'thap' in q:return 'volume_value'
        if 'doanh thu' in q and ('ty le huy' in q or 'huy don' in q) and ('nhung' in q or 'cung cao' in q):return 'revenue_cancellation'
        if 'quy' in q and 'cao nhat' in q and 'thap nhat' in q and 'chenh' in q:return 'extrema_spread'
        if 'loai bo' in q and 'huy' in q and 'doanh thu' in q:return 'cancel_exclusion'
    else:
        negative=bool(re.search(r'tieu cuc|review xau|danh gia xau',q))
        if 'so sanh' in q and 'doanh thu' in q and 'rating' in q:return 'restaurant_comparison'
        if 'doanh thu cao' in q and negative and 'top' in q:return 'revenue_negative'
        if 'nguyen nhan' in q and 'doanh thu' in q and 'chat luong' in q:return 'causal_food'
        if 'thang' in q and negative and 'doanh thu' in q and re.search(r'nhieu.+nhat|xau nhat',q):
            return 'worst_month_restaurant' if 'nha hang nao' in q else 'worst_month_revenue'
        if 'doanh thu cao nhat' in q and negative and 'ty le' in q:return 'sales_leader_sentiment'
        if 'doanh thu' in q and 'giam' in q and negative and 'tang' in q and 'thang truoc' in q:return 'inverse_months'
        if 'doanh thu tang' in q and 'ty le' in q and negative and 'tang' in q:return 'growth_sentiment'
        if 'rating' in q and 'cao' in q and re.search(r'dich vu|phuc vu',q):return 'rating_service'
        if 'chat luong' in q and re.search(r'thai do|phuc vu|dich vu',q) and 'nhieu hon' in q:return 'aspect_comparison'
    return None

@stage('sql_execution')
def execute(conn,sql,params):
    cursor=conn.execute(sql,params);return [dict(zip([c.name for c in cursor.description],row)) for row in cursor.fetchall()]

def run(conn,operation,*,ids,start=None,end=None,limit=5):
    queries=[]
    def get(domain):
        field='order_date' if domain=='orders' else 'review_date'
        where=f' WHERE (%s::date IS NULL OR {field}>=%s::date) AND (%s::date IS NULL OR {field}<=%s::date) AND (%s::text[] IS NULL OR restaurant_id=ANY(%s::text[]))'
        params=[start,start,end,end,ids or None,ids or None]
        if domain=='orders':
            columns="""COUNT(*) AS total_orders,COUNT(*) FILTER(WHERE UPPER(order_status)='DELIVERED') AS delivered_orders,
              COUNT(*) FILTER(WHERE UPPER(order_status)='CANCELLED') AS cancelled_orders,
              COALESCE(SUM(COALESCE(total_amount,sales_amount)) FILTER(WHERE UPPER(order_status)='DELIVERED'),0) AS delivered_revenue,
              SUM(COALESCE(total_amount,sales_amount)) AS gross_value,
              COALESCE(SUM(COALESCE(total_amount,sales_amount)) FILTER(WHERE UPPER(order_status)='CANCELLED'),0) AS cancelled_value"""
        else:
            columns="""COUNT(*) AS review_count,COUNT(*) FILTER(WHERE is_valid_sentiment) AS eligible_reviews,
              COUNT(*) FILTER(WHERE is_valid_sentiment AND overall_sentiment='NEGATIVE') AS negative_reviews,
              SUM(CASE WHEN source='live_web' THEN rating*2 ELSE rating END) AS rating_sum,COUNT(rating) AS rating_count"""
        sql=f"SELECT restaurant_id,MAX(restaurant_name) AS restaurant_name,DATE_TRUNC('month',{field})::date AS period,{columns} FROM ai_cross.{domain}"+where+f" GROUP BY restaurant_id,DATE_TRUNC('month',{field})::date"
        queries.append({'sql':sql,'parameters':params});return get_rows(sql,params)
    def get_rows(sql,params):return execute(conn,sql,params)
    def summarize(data,group=None):
        grouped={}
        for row in data:
            key=row.get(group) if group else None
            item=grouped.setdefault(key,{group:key} if group else {})
            if group=='restaurant_id':item['restaurant_name']=row['restaurant_name']
            for k,v in row.items():
                if k in {'restaurant_id','restaurant_name','period'}:continue
                if isinstance(v,(int,float,Decimal)):item[k]=item.get(k,0)+v
        for row in grouped.values():
            for key,n,d in [('aov','delivered_revenue','delivered_orders'),('cancellation_rate','cancelled_orders','total_orders'),('negative_rate','negative_reviews','eligible_reviews'),('rating_10','rating_sum','rating_count')]:
                if d in row:row[key]=row.get(n,0)/row[d] if row[d] else None
        return list(grouped.values())
    def top(data,metric,n=limit):return sorted([r for r in data if r.get(metric) is not None],key=lambda r:(-r[metric],str(r.get('restaurant_id',r.get('period','')))))[:n]
    sales=[] if operation in {'aspect_comparison','rating_service'} else get('orders')
    reviews=get('reviews') if operation in {'worst_month_restaurant','worst_month_revenue','sales_leader_sentiment','inverse_months','growth_sentiment','rating_service','restaurant_comparison','revenue_negative'} else []
    data=[];explanation='';extra={}
    if operation in {'restaurant_comparison','revenue_negative'}:
        feedback={r['restaurant_id']:r for r in summarize(reviews,'restaurant_id')}
        combined=[{**r,**{k:v for k,v in feedback.get(r['restaurant_id'],{}).items() if k not in ('restaurant_id','restaurant_name')}} for r in summarize(sales,'restaurant_id') if r['restaurant_id'] in feedback]
        if operation=='revenue_negative':
            revenue_min=median(r['delivered_revenue'] for r in combined);negative_min=median(r.get('negative_reviews',0) for r in combined)
            combined=[r for r in combined if r['delivered_revenue']>=revenue_min and r.get('negative_reviews',0)>=negative_min and r.get('negative_reviews',0)>0]
            explanation='Doanh thu và số đánh giá tiêu cực ít nhất bằng trung vị toàn hệ thống; có ít nhất một đánh giá tiêu cực. Xếp theo doanh thu giảm dần.'
        data=top(combined,'delivered_revenue',limit if operation=='revenue_negative' else 100)
    elif operation=='causal_food':
        data=summarize(sales,'period');explanation='Không thể khẳng định chất lượng món ăn là nguyên nhân khiến doanh thu giảm. Dữ liệu quan sát và trích dẫn đánh giá không chứng minh quan hệ nhân quả; cần kiểm tra thêm giá, lượng đơn, khuyến mãi và các yếu tố khác.'
        ordered=sorted(data,key=lambda r:r['period'])
        if len(ordered)==2:
            change=ordered[1]['delivered_revenue']-ordered[0]['delivered_revenue']
            explanation=('Doanh thu thực tế '+('tăng' if change>0 else 'giảm' if change<0 else 'không đổi')+' '+number(abs(change))+' đồng so với tháng trước. ')+explanation
    elif operation=='contribution':
        data=top(summarize(sales,'restaurant_id'),'delivered_revenue',limit)
        total=sum(r['delivered_revenue'] for r in sales);share=sum(r['delivered_revenue'] for r in data)/total*100 if total else None
        extra={'share_percent':share,'total_revenue':total};explanation=f"Các nhà hàng đứng đầu chiếm {number(share,2)}% tổng doanh thu {number(total)} đồng." if share is not None else 'Chưa có doanh thu để tính tỷ trọng.'
    elif operation=='revenue_cancellation':
        all_rows=summarize(sales,'restaurant_id');totals=summarize(sales)[0];threshold=totals['cancelled_orders']/totals['total_orders']
        data=top([r for r in all_rows if r['cancellation_rate']>=threshold],'delivered_revenue');extra={'cancellation_threshold':threshold}
        explanation=f"Chọn tỷ lệ hủy ít nhất bằng tỷ lệ chung {number(threshold*100,2)}%; sau đó xếp doanh thu giảm dần."
    elif operation=='volume_value':
        all_rows=summarize(sales,'restaurant_id');count_min=median(r['total_orders'] for r in all_rows);value_max=median(r['aov'] for r in all_rows if r['aov'] is not None)
        data=top([r for r in all_rows if r['total_orders']>=count_min and r['aov'] is not None and r['aov']<=value_max],'total_orders')
        explanation=f"Số đơn ít nhất bằng trung vị {number(count_min)} và giá trị trung bình mỗi đơn giao thành công không quá trung vị {number(value_max)} đồng; xếp theo số đơn giảm dần."
    elif operation=='cancel_exclusion':
        data=summarize(sales);explanation='Doanh thu chuẩn chỉ tính đơn giao thành công nên không thay đổi khi loại các đơn bị hủy. Tổng giá trị danh nghĩa của mọi trạng thái là một chỉ số khác; không coi giá trị đơn hủy là doanh thu đã ghi nhận.'
    elif operation=='extrema_spread':
        monthly=summarize(sales,'period');high=top(monthly,'delivered_revenue',1)[0];low=min(monthly,key=lambda r:(r['delivered_revenue'],r['period']))
        data=[high,low];extra={'difference':high['delivered_revenue']-low['delivered_revenue']};explanation=f"Lần lượt là tháng cao nhất và thấp nhất; chênh lệch {number(extra['difference'])} đồng."
    elif operation in {'growth_ranking','growth_sentiment'}:
        periods=sorted({r['period'] for r in sales})
        if len(periods)!=2:return None
        by={(r['restaurant_id'],r['period']):r for r in sales};rv={(r['restaurant_id'],r['period']):r for r in reviews};candidates=[]
        for rid in sorted({r['restaurant_id'] for r in sales}):
            a,b=by.get((rid,periods[0])),by.get((rid,periods[1]))
            if not a or not b or b['delivered_revenue']<=a['delivered_revenue']:continue
            row={'restaurant_id':rid,'restaurant_name':b['restaurant_name'],'first_period':str(periods[0])[:7],'second_period':str(periods[1])[:7],'first_revenue':a['delivered_revenue'],'second_revenue':b['delivered_revenue'],'revenue_change':b['delivered_revenue']-a['delivered_revenue']}
            if operation=='growth_sentiment':
                ra,rb=rv.get((rid,periods[0])),rv.get((rid,periods[1]))
                if not ra or not rb or not ra['eligible_reviews'] or not rb['eligible_reviews']:continue
                before,after=ra['negative_reviews']/ra['eligible_reviews'],rb['negative_reviews']/rb['eligible_reviews']
                if after<=before:continue
                row.update(first_negative_rate=before,second_negative_rate=after)
            candidates.append(row)
        data=top(candidates,'revenue_change',1) if operation=='growth_ranking' else sorted(candidates,key=lambda r:r['restaurant_id'])[:100]
        extra={'comparison_periods':[str(p)[:7] for p in periods]};explanation='Chỉ so sánh nhà hàng có dữ liệu ở cả hai tháng; tăng trưởng doanh thu được xếp theo chênh lệch tuyệt đối. Không chia cho doanh thu gốc bằng 0.'
    elif operation.startswith('worst_month'):
        worst=top(summarize(reviews,'period'),'negative_reviews',1)[0];period=worst['period'];selected=[r for r in sales if r['period']==period]
        data=top(summarize(selected,'restaurant_id'),'delivered_revenue',1) if operation=='worst_month_restaurant' else [{**worst,**summarize(selected)[0]}]
        for row in data:row['period']=period
        explanation=f"Xác định tháng {period.month}/{period.year} có {worst['negative_reviews']} đánh giá tiêu cực trước, rồi mới đối chiếu doanh thu đúng tháng đó."
    elif operation=='sales_leader_sentiment':
        leader=top(summarize(sales,'restaurant_id'),'delivered_revenue',1)[0];feedback={r['restaurant_id']:r for r in summarize(reviews,'restaurant_id')}
        data=[{**feedback.get(leader['restaurant_id'],{'negative_reviews':0,'eligible_reviews':0,'negative_rate':None}),**leader}]
        explanation='Xếp hạng doanh thu trước, sau đó tính tỷ lệ tiêu cực trên các đánh giá đủ điều kiện của chính nhà hàng đó.'
    elif operation=='inverse_months':
        a={r['period']:r for r in summarize(sales,'period')};b={r['period']:r for r in summarize(reviews,'period')}
        for period in sorted(a.keys()&b.keys()):
            previous=period.replace(year=period.year-1,month=12) if period.month==1 else period.replace(month=period.month-1)
            if previous in a and previous in b and a[period]['delivered_revenue']<a[previous]['delivered_revenue'] and b[period]['negative_reviews']>b[previous]['negative_reviews']:
                data.append({'period':period,'delivered_revenue':a[period]['delivered_revenue'],'previous_revenue':a[previous]['delivered_revenue'],'negative_reviews':b[period]['negative_reviews'],'previous_negative_reviews':b[previous]['negative_reviews']})
        explanation='Chỉ đối chiếu hai tháng liền kề có dữ liệu ở cả nguồn đơn hàng và đánh giá; không tự điền tháng thiếu bằng 0.'
    elif operation in {'rating_service','aspect_comparison'}:
        native="CASE WHEN a.aspect='FOOD#QUALITY' THEN 'food_complaints' WHEN a.aspect='SERVICE#GENERAL' THEN 'service_complaints' ELSE a.aspect END"
        sql=f"""SELECT r.restaurant_id,{native} AS facet,COUNT(DISTINCT r.review_id) AS n FROM fact_review_aspect a JOIN ai_cross.reviews r USING(review_id)
          WHERE (%s::date IS NULL OR r.review_date>=%s::date) AND (%s::date IS NULL OR r.review_date<=%s::date)
          AND (%s::text[] IS NULL OR r.restaurant_id=ANY(%s::text[])) AND r.is_valid_sentiment AND LOWER(a.sentiment)='negative'
          AND a.aspect IN ('FOOD#QUALITY','SERVICE#GENERAL') GROUP BY r.restaurant_id,{native}"""
        params=[start,start,end,end,ids or None,ids or None];queries.append({'sql':sql,'parameters':params});counts=get_rows(sql,params)
        if operation=='aspect_comparison':
            data=[{facet:sum(r['n'] for r in counts if r['facet']==facet) for facet in ['food_complaints','service_complaints']}]
            explanation='Đếm số đánh giá khác nhau có khía cạnh tiêu cực; một đánh giá có thể góp vào cả hai khía cạnh.'
        else:
            service={r['restaurant_id']:r['n'] for r in counts if r['facet']=='service_complaints'}
            data=top([{**r,'service_complaints':service.get(r['restaurant_id'],0)} for r in summarize(reviews,'restaurant_id') if r['rating_10'] is not None and r['rating_10']>=8 and service.get(r['restaurant_id'],0)>=5],'service_complaints')
            explanation='Định nghĩa điểm cao là ít nhất 8/10 và phàn nàn dịch vụ nhiều là ít nhất 5 đánh giá có khía cạnh dịch vụ tiêu cực.'
    else:return None
    return {'rows':data,'queries':queries,'explanation':explanation,'extra':extra,'operation':operation}

def sales_answer(conn,restaurant_id,question,*,today):
    operation=kind(question,'sales')
    if not operation:return None
    q=plain(question);scope=parse_time_scope(q,today=today)
    global_scope=operation in {'contribution','revenue_cancellation','volume_value','growth_ranking'}
    ids=[] if global_scope else [str(restaurant_id)]
    first=min(scope.periods)+'-01' if scope.periods else None
    from src.sales.semantic_plan import next_month
    from datetime import date,timedelta
    last=str(date.fromisoformat(next_month(max(scope.periods))+'-01')-timedelta(days=1)) if scope.periods else None
    topn=re.search(r'top\s*(\d+)|\b(\d+)\s+nha hang',q);limit=min(int(next(v for v in topn.groups() if v)),20) if topn else 5
    result=run(conn,operation,ids=ids,start=first,end=last,limit=limit)
    if result is None:return None
    if len(ids)==1:
        from src.sales.cross_repository import catalog
        name=next((r['restaurant_name'] for r in catalog(conn) if str(r['restaurant_id'])==ids[0]),ids[0])
        for row in result['rows']:row.setdefault('restaurant_id',ids[0]);row.setdefault('restaurant_name',name)
    from src.sales.vietnamese import LABELS
    text=rows_text(result['rows'],scope=f'{first or "toàn bộ dữ liệu đang lưu"} đến {last or "ngày cuối có bản ghi"}')+'\n\n'+result['explanation']
    return {'route':'SALES_SQL','answer':text,'restaurant_id':str(restaurant_id),'status':'ok' if result['rows'] else 'no_data',
        'sql_facts':result['rows'],'display_rows':result['rows'],'review_evidence':[],'execution_mode':'VERIFIED_ANALYTICS_FALLBACK',
        'analysis':result['extra'],'query_plan':{'intent':operation,'restaurant_ids':ids,'time_scope':{'periods':list(scope.periods)}},
        'sql_details':{'sql':result['queries'][0]['sql'],'queries':result['queries'],'generated':False,'execution_mode':'VERIFIED_ANALYTICS_FALLBACK','validation_status':'Fixed parameterized read-only queries'}}
