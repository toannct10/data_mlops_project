"""Canonical structured Sales + Review/PhoBERT aggregates and scoped evidence."""
import math,json
from datetime import date,datetime,time,timedelta
from collections import defaultdict
from src.sales.cross_plan import CrossAnalysisEvidencePacket,Fact,RetrievedReview
from src.sales.semantic_plan import month_range,plain

def rows(cursor):return [dict(zip([d.name for d in cursor.description],r,strict=True)) for r in cursor.fetchall()]
def catalog(conn):return rows(conn.execute("SELECT restaurant_id,COALESCE(restaurant_name,'Unnamed restaurant') AS restaurant_name,is_live_demo FROM dim_sales_restaurant ORDER BY restaurant_id"))
def association(values):
 pairs=[(float(a),float(b)) for a,b in values if a is not None and b is not None];n=len(pairs)
 if n<3:return {'n':n,'pearson_r':None,'limitation':'Cần ít nhất ba kỳ có đủ cả hai chỉ số.'}
 x=sum(a for a,_ in pairs)/n;y=sum(b for _,b in pairs)/n
 xx=sum((a-x)**2 for a,_ in pairs);yy=sum((b-y)**2 for _,b in pairs)
 if not xx or not yy:return {'n':n,'pearson_r':None,'limitation':'Một chỉ số không biến thiên nên không tính tương quan.'}
 return {'n':n,'pearson_r':sum((a-x)*(b-y) for a,b in pairs)/math.sqrt(xx*yy),'limitation':'Tương quan mô tả, không chứng minh quan hệ nhân quả.'}

def build_packet(conn,plan,question):
 names={r['restaurant_id']:r['restaurant_name'].split(' - ')[0] for r in catalog(conn) if r['restaurant_id'] in plan.restaurant_ids}
 args=(plan.restaurant_ids,plan.start_date,plan.end_date)
 trunc='day' if plan.grouping=='daily' else 'month'
 sales=rows(conn.execute(f"""SELECT o.restaurant_id,date_trunc('{trunc}',d.calendar_date)::date AS period,
 count(*)::int AS orders,count(*) FILTER(WHERE upper(o.order_status)='DELIVERED')::int AS delivered_orders,
 coalesce(sum(o.total_amount) FILTER(WHERE upper(o.order_status)='DELIVERED'),0)::float AS delivered_revenue,
 count(*) FILTER(WHERE upper(o.order_status)='CANCELLED')::int AS cancelled_orders,
 count(*) FILTER(WHERE upper(o.order_status)='REFUNDED')::int AS refunded_orders
 FROM fact_order o JOIN dim_date d USING(date_key) WHERE o.restaurant_id=ANY(%s) AND d.calendar_date BETWEEN %s AND %s
 GROUP BY o.restaurant_id,period ORDER BY o.restaurant_id,period""",args))
 reviews=rows(conn.execute(f"""SELECT restaurant_id,date_trunc('{trunc}',review_date)::date AS period,count(*)::int AS review_count,
 avg(rating_normalized_10)::float AS average_rating_10,
 count(*) FILTER(WHERE relevance_label='NOT_RELEVANT')::int AS not_relevant_count,
 count(*) FILTER(WHERE absa_model_version IS NOT NULL AND overall_sentiment IN ('POSITIVE','NEGATIVE','NEUTRAL'))::int AS valid_absa_count,
 count(*) FILTER(WHERE absa_model_version IS NOT NULL AND overall_sentiment='NEGATIVE')::int AS negative_count,
 count(*) FILTER(WHERE absa_model_version IS NOT NULL AND overall_sentiment='POSITIVE')::int AS positive_count,
 count(order_id)::int AS linked_review_count
 FROM canonical_review_enrichment WHERE restaurant_id=ANY(%s) AND review_date BETWEEN %s AND %s
 GROUP BY restaurant_id,period ORDER BY restaurant_id,period""",args))
 aspects=rows(conn.execute(f"""SELECT r.restaurant_id,date_trunc('{trunc}',d.calendar_date)::date AS period,a.aspect,
 count(*)::int AS mentions,count(*) FILTER(WHERE a.sentiment='negative')::int AS negative_count,
 count(*) FILTER(WHERE a.sentiment='positive')::int AS positive_count
 FROM fact_review_aspect a JOIN fact_review_v2 r ON r.review_id=a.review_id AND r.absa_model_version=a.model_version
 JOIN dim_date d ON d.date_key=r.date_key WHERE r.restaurant_id=ANY(%s) AND d.calendar_date BETWEEN %s AND %s
 AND r.relevance_label IS DISTINCT FROM 'NOT_RELEVANT'
 GROUP BY r.restaurant_id,period,a.aspect ORDER BY r.restaurant_id,period,a.aspect""",args))
 linked=rows(conn.execute("""SELECT restaurant_id,coalesce(order_status,'UNLINKED') AS order_status,count(*)::int AS review_count,
 count(*) FILTER(WHERE overall_sentiment='NEGATIVE')::int AS negative_count
 FROM canonical_review_enrichment WHERE restaurant_id=ANY(%s) AND review_date BETWEEN %s AND %s
 GROUP BY restaurant_id,order_status ORDER BY restaurant_id,order_status""",args))
 allrows={}
 for item in sales:allrows[(item['restaurant_id'],item['period'])]={**item}
 for item in reviews:allrows.setdefault((item['restaurant_id'],item['period']),{'restaurant_id':item['restaurant_id'],'period':item['period'],'orders':0,'delivered_revenue':0,'delivered_orders':0,'cancelled_orders':0,'refunded_orders':0}).update(item)
 for row in allrows.values():
  for key in ['review_count','valid_absa_count','negative_count','positive_count','not_relevant_count','linked_review_count']:row.setdefault(key,0)
  row.setdefault('average_rating_10',None);row['negative_rate']=row['negative_count']/row['valid_absa_count'] if row['valid_absa_count'] else None
  native=[a for a in aspects if a['restaurant_id']==row['restaurant_id'] and a['period']==row['period']]
  for group,selected in [('service',['SERVICE#GENERAL']),('price',['FOOD#PRICES','DRINKS#PRICES','RESTAURANT#PRICES']),('food_quality',['FOOD#QUALITY'])]:
   values=[a for a in native if a['aspect'] in selected];mentions=sum(a['mentions'] for a in values);row[group+'_mentions']=mentions;row[group+'_negative_count']=sum(a['negative_count'] for a in values);row[group+'_negative_rate']=row[group+'_negative_count']/mentions if mentions else None;row[group+'_positive_count']=sum(a['positive_count'] for a in values)
 series=list(allrows.values());summaries=[];correlations=[]
 for rid in plan.restaurant_ids:
  subset=[r for r in series if r['restaurant_id']==rid];summary={'restaurant_id':rid,'period':str(plan.start_date)+' → '+str(plan.end_date)}
  for key in ['orders','delivered_orders','delivered_revenue','cancelled_orders','refunded_orders','review_count','valid_absa_count','negative_count','positive_count','not_relevant_count','linked_review_count']+[g+k for g in ['service','price','food_quality'] for k in ['_mentions','_negative_count','_positive_count']]:summary[key]=sum(r.get(key,0) for r in subset)
  summary['average_rating_10']=sum(r['average_rating_10']*r['review_count'] for r in subset if r['average_rating_10'] is not None)/summary['review_count'] if summary['review_count'] else None
  summary['negative_rate']=summary['negative_count']/summary['valid_absa_count'] if summary['valid_absa_count'] else None
  for group in ['service','price','food_quality']:summary[group+'_negative_rate']=summary[group+'_negative_count']/summary[group+'_mentions'] if summary[group+'_mentions'] else None;summary[group+'_positive_rate']=summary[group+'_positive_count']/summary[group+'_mentions'] if summary[group+'_mentions'] else None
  summaries.append(summary)
  for x,y in [('delivered_revenue','negative_rate'),('delivered_revenue','average_rating_10'),('delivered_orders','service_negative_rate'),('delivered_revenue','price_negative_rate')]:
   correlations.append({'restaurant_id':rid,'x':x,'y':y,**association([(r.get(x) if r['orders'] else None,r.get(y)) for r in subset])})
 selected=[]
 for rid in plan.restaurant_ids:
  subset=[r for r in series if r['restaurant_id']==rid and r['orders']]
  if plan.intent in {'lowest_period','highest_period'}:selected.extend(sorted(subset,key=lambda r:(r['delivered_revenue']*(1 if plan.intent=='lowest_period' else -1),r['period']))[:1])
  elif plan.intent=='complaint_periods':
   field='price_negative_count' if any(a.endswith('#PRICES') for a in plan.aspects) else 'service_negative_count' if 'SERVICE#GENERAL' in plan.aspects else 'negative_count'
   selected.extend(sorted([r for r in subset if r.get(field,0)>0],key=lambda r:(-r.get(field,0),r['period']))[:3])
  else:selected.extend(subset)
 facts=[]
 labels={'delivered_revenue':('Doanh thu Delivered','VND'),'delivered_orders':('Đơn Delivered','orders'),'average_rating_10':('Rating trung bình /10','rating/10'),'negative_rate':('Tỷ lệ review tiêu cực trong review có ABSA','percent'),'review_count':('Số review','reviews'),'cancelled_orders':('Đơn Cancelled','orders'),'refunded_orders':('Đơn Refunded','orders'),'service_negative_rate':('Tỷ lệ Service tiêu cực trong lượt nhắc Service','percent'),'price_negative_rate':('Tỷ lệ Price tiêu cực trong lượt nhắc Price','percent'),'food_quality_negative_rate':('Tỷ lệ Food Quality tiêu cực trong lượt nhắc Food Quality','percent'),'food_quality_positive_rate':('Tỷ lệ Food Quality tích cực trong lượt nhắc Food Quality','percent')}
 for group in ['service','price','food_quality']:
  labels[group+'_negative_count']=('Số lượt '+group+' tiêu cực','mentions')
  labels[group+'_mentions']=('Số lượt nhắc '+group,'mentions')
 def add(row,metric):
  value=row.get(metric)
  if value is None:return
  label,unit=labels[metric];value=float(value);display=(f'{value*100:.2f}%' if unit=='percent' else f'{value:.2f}/10' if unit=='rating/10' else f'{value:,.0f}'.replace(',','.'))
  support=row.get('valid_absa_count') if metric=='negative_rate' else row.get(metric.replace('_negative_rate','_mentions').replace('_positive_rate','_mentions')) if metric.endswith(('_negative_rate','_positive_rate')) else None
  facts.append(Fact(ref='F'+str(len(facts)+1),restaurant_id=row['restaurant_id'],period=str(row['period']),metric=metric,label=label,value=value,unit=unit,display=display,support=support))
 metrics=['review_count','negative_rate'] if plan.intent=='aspect_ranking' else list(plan.metrics)
 if not plan.aspects and not any(m in metrics for m in ['negative_rate','average_rating_10']):metrics+=['negative_rate']
 if plan.aspects:
  groups=['price' if a.endswith('#PRICES') else 'food_quality' if a=='FOOD#QUALITY' else 'service' for a in plan.aspects]
  metrics+=list(dict.fromkeys(g+'_negative_rate' for g in groups))
  if 'food_quality' in groups:metrics+=['food_quality_positive_rate']
 no_complaint_periods=plan.intent=='complaint_periods' and not selected
 if plan.intent=='complaint_periods':
  metrics+=list(dict.fromkeys(g+'_negative_count' for g in groups))
 fact_rows=selected if plan.intent in {'lowest_period','highest_period','complaint_periods'} and not no_complaint_periods else summaries
 for row in fact_rows:
  for metric in list(dict.fromkeys(metrics)):add(row,metric)
 if plan.intent=='association':
  for row in selected[:6]:
   for metric in ['delivered_revenue','average_rating_10']:add(row,metric)
  for corr in correlations:
   if corr['pearson_r'] is not None and corr['y'] in plan.metrics:
    facts.append(Fact(ref='F'+str(len(facts)+1),restaurant_id=corr['restaurant_id'],period=str(plan.start_date)+' → '+str(plan.end_date),metric='pearson_'+corr['y'],label='Tương quan Pearson doanh thu với '+corr['y'],value=corr['pearson_r'],unit='r',display=f"{corr['pearson_r']:.3f}",support=corr['n']))
 if plan.intent=='aspect_ranking':
  grouped={}
  for a in aspects:
   key=(a['restaurant_id'],a['aspect']);entry=grouped.setdefault(key,{'restaurant_id':a['restaurant_id'],'aspect':a['aspect'],'period':str(plan.start_date)+' → '+str(plan.end_date),'negative_count':0,'mentions':0});entry['negative_count']+=a['negative_count'];entry['mentions']+=a['mentions']
  top=sorted(grouped.values(),key=lambda a:-a['negative_count'])[:6]
  for a in top:facts.append(Fact(ref='F'+str(len(facts)+1),restaurant_id=a['restaurant_id'],period=str(a['period']),metric='negative_aspect_mentions',label=a['aspect']+' số lượt tiêu cực',value=a['negative_count'],unit='mentions',display=str(a['negative_count']),support=a['mentions']))
 limitations=['Rating được chuẩn hóa về thang 10 để so sánh với review live thang 5; raw rating và raw text được giữ nguyên.','Sentiment/aspect là dự đoán của checkpoint production, không phải nhãn con người.','Dữ liệu quan sát chỉ cho phép mô tả liên hệ, không xác định nguyên nhân.']
 missing=sum(1 for r in series if r['orders'] and not r['review_count'])
 if missing:limitations.append(f'{missing} nhóm thời gian có Sales nhưng chưa có Review.')
 if any(r['not_relevant_count'] for r in summaries):limitations.append('Review NOT_RELEVANT không tham gia tỷ lệ sentiment hay thống kê aspect; rating thô vẫn là quan sát riêng.')
 if plan.intent=='complaint_periods':limitations.append('Các kỳ hiển thị được chọn theo ba số lượt phàn nàn cao nhất, không có ngưỡng suy diễn nhân quả.')
 if no_complaint_periods:limitations.append('Không có lượt aspect tiêu cực được quan sát trong phạm vi này; không có tháng phàn nàn để xếp hạng. Số liệu hiển thị là tổng toàn kỳ, không phải tháng có phàn nàn.')
 if plan.intent=='association':
  facts.sort(key=lambda f:0 if f.metric.startswith('pearson_') else 1 if '→' in f.period else 2)
  for i,fact in enumerate(facts,1):fact.ref='F'+str(i)
 evidence=retrieve_evidence(conn,plan,question,series if no_complaint_periods else selected)
 if not evidence:limitations.append('Không tìm được trích dẫn review đủ điều kiện đúng nhà hàng và kỳ đã chọn.')
 return CrossAnalysisEvidencePacket(question=question,plan=plan,restaurant_names=names,sales_metrics=sales,review_metrics=reviews,aspect_metrics=aspects,trend_metrics=correlations,comparison_metrics=summaries+[{'linked_order_review_status':linked}],facts=facts,retrieved_reviews=evidence,data_limitations=limitations,source_ids=['PostgreSQL.fact_order','PostgreSQL.fact_review_v2','PostgreSQL.fact_review_aspect','PostgreSQL.canonical_review_enrichment'])

def retrieve_evidence(conn,plan,question,selected):
 from src.rag_v2.lexical import BM25Retriever
 from src.foody.review_runtime import LEXICAL
 from src.foody.review_paths import DOCUMENTS
 from src.rag.schemas import RetrievalRequest
 evidence=[];seen=set()
 try:retriever=BM25Retriever(LEXICAL/'reviews.sqlite',LEXICAL/'index_manifest.json',DOCUMENTS)
 except (ValueError,OSError):retriever=None
 for rid in plan.restaurant_ids:
  selected_periods={str(r['period'])[:7] for r in selected if r['restaurant_id']==rid}
  candidates=[]
  if retriever:
   hits,_=retriever.search(RetrievalRequest(query=question,restaurant_id=rid,date_from=datetime.combine(plan.start_date,time.min),date_to=datetime.combine(plan.end_date,time.max),top_k=10),top_n=30)
   candidates=[h.review_id for h in hits]
  # This bounded canonical SQL selection also provides evidence if lexical retrieval
  # has no aligned hit. Every final quote is fetched/verified against the DB below.
  aspect_filter='AND EXISTS(SELECT 1 FROM fact_review_aspect a WHERE a.review_id=r.review_id AND a.model_version=r.absa_model_version AND a.aspect=ANY(%s))' if plan.aspects else ''
  params=[rid,plan.start_date,plan.end_date]+([plan.aspects] if plan.aspects else [])
  fallback=conn.execute("SELECT r.review_id FROM canonical_review_enrichment r WHERE r.restaurant_id=%s AND r.review_date BETWEEN %s AND %s AND r.absa_model_version IS NOT NULL AND r.relevance_label IS DISTINCT FROM 'NOT_RELEVANT' "+aspect_filter+" ORDER BY (r.overall_sentiment='NEGATIVE') DESC,r.review_date DESC,r.review_id LIMIT 200",params).fetchall()
  candidates+= [r[0] for r in fallback]
  if not candidates:continue
  verified=rows(conn.execute('SELECT review_id,order_id,restaurant_id,review_date,review_text,overall_sentiment,native_aspects FROM canonical_review_enrichment WHERE review_id=ANY(%s) AND restaurant_id=%s AND review_date BETWEEN %s AND %s AND relevance_label IS DISTINCT FROM %s AND absa_model_version IS NOT NULL',(list(dict.fromkeys(candidates)),rid,plan.start_date,plan.end_date,'NOT_RELEVANT')))
  lookup={r['review_id']:r for r in verified}
  used=0
  for review_id in dict.fromkeys(candidates):
   row=lookup.get(review_id)
   if not row or review_id in seen or str(row['review_date'])[:7] not in selected_periods:continue
   if plan.aspects and not any(a['aspect'] in plan.aspects for a in row['native_aspects']):continue
   evidence.append(RetrievedReview(ref='E'+str(len(evidence)+1),review_id=review_id,order_id=row['order_id'],restaurant_id=rid,period=str(row['review_date'])[:7],text=row['review_text'][:220],overall_sentiment=row['overall_sentiment'],aspects=row['native_aspects']));seen.add(review_id);used+=1
   if used>=2 or len(evidence)>=6:break
 return evidence
