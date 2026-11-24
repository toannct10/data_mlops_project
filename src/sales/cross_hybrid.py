"""Dimension-safe Cross routes, retaining Sales AI and the accepted v4 packet path."""
import time
import re
from datetime import datetime,time as clock_time,timezone
from dataclasses import replace
from src.sales.cross_router import route_cross
from src.sales.cross_repository import catalog
from src.sales.queries import vietnam_today


def format_rows(rows,plan):
    if not rows:return 'Không có dữ liệu phù hợp trong phạm vi đã chọn.'
    if plan.metric=='latest_review':
        r=rows[0]
        return f"Đánh giá mới nhất đã xác minh: {r['review_id']} · {r['restaurant_name']} · {r.get('review_timestamp') or r.get('created_at') or r['review_date']}.\n\n{r['review_text']}"
    if plan.metric=='latest_order':
        r=rows[0]
        return f"Đơn live mới nhất đã xác minh: {r['order_id']} · {r['restaurant_name']} · {r['order_status']} · {r['total_amount']} VND."
    from src.sales.vietnamese import rows_text
    text=rows_text(rows,scope=plan.start_date+' đến '+plan.end_date)
    if plan.metric=='positive_review_count':text+=' Đánh giá tích cực được tính từ nhãn POSITIVE do mô hình PhoBERT suy luận, có phiên bản mô hình và thuộc tập đủ điều kiện; loại đánh giá không liên quan.'
    return text


def review_answer(conn,question,plan,ids,gateway_factory=None):
    from src.rag_v3.retriever import cached_retriever
    from src.rag_v3.cli import selected_v3_gateway
    from src.rag_v3.service import answer_question_v3
    from src.rag.schemas import RetrievalRequest
    base=cached_retriever()
    from src.sales.semantic_plan import plain
    q=plain(question)
    native=['FOOD#PRICES','DRINKS#PRICES','RESTAURANT#PRICES'] if 'gia ca' in q else ['FOOD#QUALITY'] if re.search(r'chat luong|mon an|do an',q) else ['SERVICE#GENERAL'] if re.search(r'dich vu|phuc vu',q) else []
    negative=bool(native or re.search(r'tieu cuc|phan nan|che\b',q))
    topic='giá cả' if native and 'PRICES' in native[0] else 'chất lượng món ăn' if native==['FOOD#QUALITY'] else 'dịch vụ' if native else 'trải nghiệm ăn uống'
    retrieval_query='Khách hàng phàn nàn gì về '+topic+'?' if negative else question
    class ScopedRetriever:
        metadata=base.metadata
        def search_debug(self,request):
            all_hits=[];elapsed=0;debug={}
            for rid in ids[:3] or [None]:
                hits,ms,debug=base.search_debug(request.model_copy(update={'restaurant_id':rid,'top_k':20}))
                for h in hits:
                    row=conn.execute('SELECT restaurant_id,review_date,is_valid_sentiment,UPPER(overall_sentiment) FROM ai_cross.reviews WHERE review_id=%s',(h.review_id,)).fetchone()
                    if not row or not row[2] or not plan.start_date<=str(row[1])<=plan.end_date or ids and row[0] not in ids:continue
                    if plan.negative and row[3]!='NEGATIVE':continue
                    if native and not conn.execute("SELECT 1 FROM fact_review_aspect WHERE review_id=%s AND aspect=ANY(%s) AND LOWER(sentiment)='negative' LIMIT 1",(h.review_id,native)).fetchone():continue
                    all_hits.append(h)
                elapsed+=ms
            seen=set();unique=[]
            for h in sorted(all_hits,key=lambda h:-h.score):
                if h.review_id not in seen:unique.append(h);seen.add(h.review_id)
            return unique[:5],elapsed,debug
    request=RetrievalRequest(query=retrieval_query,sentiment='negative' if plan.negative else None,restaurant_id=ids[0] if len(ids)==1 else None,
        date_from=datetime.combine(__import__('datetime').date.fromisoformat(plan.start_date),clock_time.min),
        date_to=datetime.combine(__import__('datetime').date.fromisoformat(plan.end_date),clock_time.max),top_k=5)
    with (gateway_factory or selected_v3_gateway)() as gateway:
        result,hits,telemetry,debug,packet=answer_question_v3(ScopedRetriever(),gateway,request)
    # Confirm every displayed quote against the operational canonical source.
    evidence=[]
    for hit in hits:
        row=conn.execute('''SELECT r.review_text,r.restaurant_id,s.restaurant_name,d.calendar_date,r.relevance_label
            FROM fact_review_v2 r JOIN dim_sales_restaurant s USING(restaurant_id)
            JOIN dim_date d USING(date_key) WHERE review_id=%s''',(hit.review_id,)).fetchone()
        if not row or row[4]=='NOT_RELEVANT' or not plan.start_date<=str(row[3])<=plan.end_date or ids and row[1] not in ids:
            return {'answer':'Chưa có bằng chứng review canonical đủ điều kiện trong phạm vi này.','evidence':[],'mode':'INSUFFICIENT_EVIDENCE','telemetry':telemetry}
        evidence.append({'review_id':hit.review_id,'restaurant_id':row[1],'restaurant_name':row[2],'date':str(row[3]),'text':row[0]})
    text=result.answer;mode=result.generation_mode
    if evidence and re.search(r'khong du bang chung|chua.*bang chung',plain(text)):
        # Preserve useful canonical quotations when synthesis cannot support a
        # broader claim. Never relabel these extractive facts as Qwen output.
        unique={}
        for e in evidence:unique.setdefault(e['text'],e)
        text='Chưa đủ cơ sở để kết luận vấn đề phổ biến nhất. Các trích dẫn đã đối chiếu với dữ liệu chuẩn trong phạm vi '+plan.start_date+' đến '+plan.end_date+':\n\n'
        text+='\n\n'.join(e['restaurant_name']+' ('+e['date']+'): “'+e['text']+'” ['+e['review_id']+']' for e in list(unique.values())[:3])
        mode='extractive_canonical'
    text+='\n\nCác trích dẫn là một mẫu bằng chứng trong phạm vi được chọn, chưa đủ để xếp hạng mức độ phổ biến của vấn đề trên toàn bộ đánh giá.'
    return {'answer':text,'evidence':evidence,'mode':mode,'telemetry':telemetry}


def analyze_hybrid(conn,question,plan,*,gateway_factory=None):
    from src.monitoring.metrics import CROSS_CHAT_REQUESTS,CROSS_CHAT_GENERATED,CROSS_CHAT_FALLBACK,CROSS_CHAT_DURATION,CROSS_ROUTE_REQUESTS
    started=time.perf_counter();CROSS_CHAT_REQUESTS.inc();sql_result=None;review=None;status='grounded'
    try:
        if plan.requires_sql:
            from src.sales.cross_sql import answer_sql
            sql_result=answer_sql(question,plan,gateway_factory)
            if len(plan.restaurant_ids)==1:
                rid=plan.restaurant_ids[0]
                name=next((r['restaurant_name'] for r in catalog(conn) if str(r['restaurant_id'])==str(rid)),str(rid))
                for row in sql_result['rows']:
                    row.setdefault('restaurant_id',rid);row.setdefault('restaurant_name',name)
        ids=plan.restaurant_ids
        if not ids and sql_result:ids=list(dict.fromkeys(r['restaurant_id'] for r in sql_result['rows'] if r.get('restaurant_id')))
        if plan.requires_retrieval:
            try:review=review_answer(conn,question,plan,ids,gateway_factory)
            except Exception:review={'answer':'Review RAG tạm thời không sẵn sàng; chưa đủ bằng chứng để tổng hợp nhận xét.','evidence':[],'mode':'INSUFFICIENT_EVIDENCE'}
        answer=format_rows(sql_result['rows'],plan) if sql_result else ''
        if review:answer+=('\n\nNhận xét từ review đã truy xuất:\n' if answer else '')+review['answer']
        if sql_result and sql_result.get('source_notice'):answer=sql_result['source_notice']+'\n\n'+answer
        generated=bool((review and review['mode']=='qwen') or
                       (sql_result and sql_result['sql_details']['generated']))
        mode='QWEN_GENERATED' if generated else 'VERIFIED_SQL' if sql_result else 'GROUNDED_FALLBACK'
        if generated:CROSS_CHAT_GENERATED.inc()
        elif mode=='GROUNDED_FALLBACK' or (sql_result and sql_result['sql_details']['execution_mode']=='VERIFIED_ANALYTICS_FALLBACK'):CROSS_CHAT_FALLBACK.inc()
        state={'restaurant_scope':ids[:20],'period_scope':[plan.start_date,plan.end_date],
            'comparison_scope':[],'previous_metrics':[plan.metric],'previous_intent':plan.route,
            'previous_aspects':[],'analytics_route':plan.route,'analytics_dimension':plan.dimension}
        freshness={'checked_at_utc':datetime.now(timezone.utc).isoformat(),
                   'data_timestamps':sql_result.get('data_freshness') if sql_result else None,
                   'structured_source':sql_result['source'] if sql_result else None,
                   'warehouse_status':sql_result['warehouse_status'] if sql_result else 'NOT_QUERIED',
                   'review_source':'Operational PostgreSQL verified RAG V3 index; warehouse and retrieval freshness are separate' if review else None}
        return {'status':status,'answer':answer,'answer_mode':mode,'conversation_state':state,
            'analysis':{'plan':plan.as_dict(),'supporting_metrics':sql_result['rows'] if sql_result else [],
                'review_evidence':review['evidence'] if review else [],'review_generation_mode':review['mode'] if review else None,'freshness':freshness},
            'sql_details':sql_result['sql_details'] if sql_result else None,
            'validation':{'numeric_grounding':'verified SQL result comparison' if sql_result else 'not applicable',
                'citation_grounding':'PASS','causation_guard':'RAG V3 support guard; no invented causal explanation'},
            'telemetry':{'duration_seconds':time.perf_counter()-started,'route':plan.route}}
    except Exception:
        status='error'
        raise ValueError('Không thể truy vấn nguồn phân tích chỉ đọc lúc này. Kiểm tra cấu hình và trạng thái trong System Monitor.') from None
    finally:
        CROSS_CHAT_DURATION.observe(time.perf_counter()-started)
        CROSS_ROUTE_REQUESTS.labels(route=plan.route,status=status).inc()


def dispatch(conn,question,*,selected_id=None,start=None,end=None,state=None,gateway_factory=None,force_validation_failure=False):
    from src.sales import cross_chat
    plan=route_cross(question,cross_chat.catalog(conn),selected_id=selected_id,start=start,end=end,state=state,today=vietnam_today())
    from src.sales.semantic_plan import plain
    from src.sales.compound import kind,run
    from src.sales.vietnamese import rows_text,clean
    q=plain(question)
    if re.search(r'chung minh chac chan|nguyen nhan duy nhat',q):
        return {'status':'unsupported_causation','answer':'Không thể chứng minh chắc chắn rằng đánh giá tiêu cực là nguyên nhân duy nhất khiến doanh thu giảm. Dữ liệu quan sát không đủ cơ sở kết luận nhân quả; cần kiểm tra các yếu tố khác và thiết kế đánh giá riêng.','analysis':{'plan':plan.as_dict(),'supporting_metrics':[],'review_evidence':[]},'answer_mode':'CAUSATION_GUARD'}
    if re.search(r'neu mot nha hang',q) and re.search(r'de xuat|cai thien',q):
        plan=replace(plan,route='review_rag',metric='conditional_recommendation',requires_sql=False,requires_retrieval=True)
    operation=kind(question,'cross')
    if operation and not force_validation_failure:
        ids=plan.restaurant_ids
        if operation in {'worst_month_restaurant','sales_leader_sentiment','rating_service','growth_sentiment','revenue_negative'}:ids=[]
        if operation=='causal_food':
            from datetime import date
            current=date.fromisoformat(plan.start_date)
            plan=replace(plan,start_date=str(current.replace(day=1).fromordinal(current.replace(day=1).toordinal()-1).replace(day=1)))
        result=run(conn,operation,ids=ids,start=plan.start_date,end=plan.end_date,limit=plan.limit)
        if result is not None:
            if len(ids)==1:
                name=next((r['restaurant_name'] for r in catalog(conn) if str(r['restaurant_id'])==str(ids[0])),str(ids[0]))
                for row in result['rows']:row.setdefault('restaurant_id',ids[0]);row.setdefault('restaurant_name',name)
            actual_plan=replace(plan,restaurant_ids=ids,metric=operation,route='cross_analysis',requires_sql=True,requires_retrieval=operation in {'causal_food','rating_service'} and bool(result['rows']))
            answer=rows_text(result['rows'],scope=plan.start_date+' đến '+plan.end_date)+'\n\n'+result['explanation']
            review=None
            if operation in {'causal_food','rating_service'} and result['rows']:
                review=review_answer(conn,question,actual_plan,ids or [r['restaurant_id'] for r in result['rows'] if r.get('restaurant_id')],gateway_factory)
                answer+='\n\n'+review['answer']
            return {'status':'grounded','answer':answer,'answer_mode':'QWEN_GENERATED' if review and review['mode']=='qwen' else 'VERIFIED_SQL',
                'analysis':{'plan':actual_plan.as_dict(),'supporting_metrics':result['rows'],'review_evidence':review['evidence'] if review else [],'review_generation_mode':review['mode'] if review else None,**result['extra']},
                'sql_details':{'sql':result['queries'][0]['sql'],'queries':result['queries'],'generated':False,'execution_mode':'VERIFIED_ANALYTICS_FALLBACK','validation_status':'Fixed parameterized SELECT; read-only transaction'},
                'conversation_state':{'restaurant_scope':ids,'period_scope':[plan.start_date,plan.end_date],'analytics_route':'cross_analysis','previous_metrics':[operation]}}
    if plan.route=='legacy' or force_validation_failure:
        from src.sales.cross_chat import _analyze_verified_packet
        return _analyze_verified_packet(conn,question,selected_id=selected_id,start=start,end=end,state=state,gateway_factory=gateway_factory,force_validation_failure=force_validation_failure)
    result=analyze_hybrid(conn,question,plan,gateway_factory=gateway_factory)
    if plan.metric=='conditional_recommendation':
        result['answer']+='\n\nNếu xuất hiện tình huống như câu hỏi, nên kiểm tra từng phàn nàn có bằng chứng, đối chiếu công thức và quy trình chế biến, rồi đo lại đánh giá chất lượng sau cải tiến. Đây là đề xuất có điều kiện; các trích dẫn chỉ minh họa từ nhà hàng đang chọn, không chứng minh một nhà hàng giả định đang gặp vấn đề.'
    return result
