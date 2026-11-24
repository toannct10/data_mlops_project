"""Structured canonical planning followed by grounded, plain-text Qwen synthesis."""
import re,time
from collections import defaultdict
from pathlib import Path
from src.sales.cross_plan import ConversationState,plan_question
from src.sales.cross_repository import catalog,build_packet
from src.sales.cross_validation import validate_text,causal_assertion,number
from src.sales.queries import vietnam_today
from src.sales.semantic_plan import plain
from src.sales.cross_evidence import enrich_packet,near_zero

PROMPT_VERSION='cross_chat_v4_semantic_contract'
NATIVE_NAMES={'SERVICE#GENERAL':'dịch vụ','FOOD#QUALITY':'chất lượng món ăn','DRINKS#QUALITY':'chất lượng đồ uống','RESTAURANT#GENERAL':'nhà hàng nói chung','FOOD#PRICES':'giá món ăn','DRINKS#PRICES':'giá đồ uống','RESTAURANT#PRICES':'giá cả','AMBIENCE#GENERAL':'không gian','LOCATION#GENERAL':'vị trí','FOOD#STYLE_OPTIONS':'lựa chọn món ăn','DRINKS#STYLE_OPTIONS':'lựa chọn đồ uống','RESTAURANT#MISCELLANEOUS':'vấn đề khác'}

def label(fact):
 labels={'delivered_revenue':'doanh thu từ đơn giao thành công','negative_rate':'tỷ lệ review tiêu cực','average_rating_10':'điểm đánh giá trung bình /10','service_negative_rate':'tỷ lệ review tiêu cực về dịch vụ','price_negative_rate':'tỷ lệ review tiêu cực về giá','food_quality_negative_rate':'tỷ lệ đánh giá tiêu cực về chất lượng món ăn','food_quality_positive_rate':'tỷ lệ đánh giá tích cực về chất lượng món ăn','price_negative_count':'số lượt chê về giá','review_count':'số review','same_direction_months':'số khoảng chuyển tháng cùng hướng','opposite_direction_months':'số khoảng chuyển tháng ngược hướng','movement_intervals':'số khoảng chuyển tháng','delivered_orders':'số đơn giao thành công'}
 if fact.metric in labels:return labels[fact.metric]
 if fact.metric=='pearson_average_rating_10':return 'tương quan Pearson doanh thu với điểm đánh giá trung bình'
 if fact.metric=='pearson_negative_rate':return 'tương quan Pearson doanh thu với tỷ lệ review tiêu cực'
 if fact.metric=='negative_aspect_mentions':return 'lượt phản hồi tiêu cực của khách hàng về '+NATIVE_NAMES.get(fact.label.split()[0],fact.label.split()[0])
 return fact.label.replace('Delivered','đơn giao thành công').replace('Service','dịch vụ').replace('Food Quality','chất lượng món ăn').replace('Price','giá cả').replace('ABSA','PhoBERT')

def select_facts(packet):
 facts=list(packet.facts)
 if packet.plan.intent=='aspect_ranking':return [f for f in facts if f.metric=='negative_aspect_mentions']
 if packet.plan.negative_focus and not packet.plan.aspects and packet.plan.intent!='complaint_periods':return [f for f in facts if f.metric=='negative_rate']
 if 'FOOD#QUALITY' in packet.plan.aspects and not packet.plan.negative_focus:return [f for f in facts if f.metric in {'delivered_revenue','food_quality_positive_rate'}]
 if packet.plan.intent=='association':return [f for f in facts if f.unit=='r' or '→' in f.period]
 if packet.plan.asks_why:return [f for f in facts if f.metric in {'delivered_revenue','delivered_orders','average_rating_10','negative_rate'}]
 return facts

def generation_context(packet,facts,*,feedback=None):
 """Verified relation cards let Qwen explain results without doing arithmetic."""
 lines=['Question: '+packet.question,'Scope: '+', '.join(packet.restaurant_names.values())+'; '+str(packet.plan.start_date)+' to '+str(packet.plan.end_date)]
 if packet.required_domains:lines.append('Required answer coverage: '+', '.join(packet.required_domains)+'. Include at least one verified fact from EACH required domain, not just review commentary.')
 if packet.resolved_context.get('comparison_scope'):lines.append('Comparison-scope metadata: '+', '.join(packet.resolved_context['comparison_scope']))
 if packet.plan.asks_why:
  for why in packet.why_evidence:lines.append('Verified lowest revenue month: '+why['lowest_revenue_month']+' for '+packet.restaurant_names[why['restaurant_id']]+'. cause_available=false. Describe simultaneous observations, then acknowledge that no direct cause can be established. No previous-period comparison values are supplied.')
 if packet.plan.intent=='association':
  for f in facts:
   if f.unit=='r':lines.append('Verified interpretation: Pearson r '+f.display+(' is near zero: no clear linear association. Cannot conclude similar trends.' if near_zero(f.value) else ' describes association, not causation.'))
  for t in packet.trend_facts:lines.append('Verified movements: '+str(t['same_direction_months'])+' same-direction intervals, '+str(t['opposite_direction_months'])+' opposite-direction intervals out of '+str(t['movement_intervals'])+'. First-to-last revenue direction='+t['revenue_trend']+', rating direction='+t['rating_trend']+'. Endpoint directions compare ONLY the first and last month, not each interval. Intermediate months fluctuate: do not claim continuous increases/declines. Same direction means BOTH up or BOTH down; opposite means one up, one down.')
 counts=[f for f in facts if f.metric.endswith('_negative_count')]
 if packet.plan.intent=='complaint_periods' and counts and not any(f.value for f in counts):lines.append('No negative aspect mentions were observed by PhoBERT, so there are NO complaint months to compare. Revenue below is the FULL-PERIOD total. Do not infer an effect OR absence of an effect.')
 if len({f.period for f in facts if '→' not in f.period})<2:lines.append('No earlier-period data: do not invent increases, declines or stability.')
 if packet.plan.intent=='aspect_ranking':lines.append('These are leading negative aspect counts, not trends or an exhaustive list. Do not assume zero for omitted aspects/restaurants.')
 if any(f.metric.endswith('negative_rate') for f in facts):lines.append('HIGHER negative rate = WORSE review feedback.')
 if packet.plan.negative_focus and packet.plan.aspects and len(packet.plan.restaurant_ids)>1:
  rates=[f for f in facts if f.metric.endswith('negative_rate')]
  if rates:
   worst=max(rates,key=lambda f:f.value);name=packet.restaurant_names[worst.restaurant_id]
   lines.append('Verified requested ranking: '+name+' has the HIGHEST '+label(worst)+' ('+worst.display+'). This is NOT a revenue ranking.')
   facts=[f for f in facts if f.restaurant_id==worst.restaurant_id]
   lines.append('Focus the answer on this worst-feedback restaurant and its own revenue. Do not introduce other restaurants or a revenue ranking: their revenue figures are not in this compact context.')
 lines.append('Verified facts:')
 for f in facts:
  period=f.period[:7] if packet.plan.grouping=='monthly' and '→' not in f.period else f.period
  lines.append(packet.restaurant_names[f.restaurant_id]+' | '+period+' | '+label(f)+' = '+f.display+(' VND' if f.unit=='VND' else ''))
 grouped=defaultdict(list)
 for f in facts:grouped[(f.metric,f.period)].append(f)
 for (metric,period),group in grouped.items():
  if len({f.restaurant_id for f in group})>1 and metric not in {'review_count','negative_aspect_mentions'}:
   ordered=sorted(group,key=lambda f:-f.value)
   for high,low in zip(ordered,ordered[1:]):
    relation='HIGHER than' if high.value>low.value else 'EQUAL to'
    lines.append('Verified comparison: '+packet.restaurant_names[high.restaurant_id]+' has '+relation+' '+label(high)+' '+packet.restaurant_names[low.restaurant_id]+' ('+high.display+' vs '+low.display+').')
 if packet.food_quality_facts:
  for f in packet.food_quality_facts:lines.append('FOOD#QUALITY '+packet.restaurant_names[f['restaurant_id']]+': positive '+f"{100*f['positive_rate']:.2f}%"+', neutral '+f"{100*f['neutral_rate']:.2f}%"+', negative '+f"{100*f['negative_rate']:.2f}%"+', mentioned '+str(f['mentioned_count'])+'.')
  leaders=[]
  for metric in ['delivered_revenue','food_quality_positive_rate']:
   values=[f for f in packet.facts if f.metric==metric]
   if values:leaders.append(max(values,key=lambda f:f.value).restaurant_id)
  if len(leaders)==2 and len(set(leaders))==1:lines.append('BOTH higher revenue AND stronger Food Quality positive rate belong to '+packet.restaurant_names[leaders[0]]+'. There is no opposite-direction contrast between these two rankings.')
 quote_requested=bool(re.search(r'trich dan|vi du|khach noi|nhan xet cu the',plain(packet.question)))
 packet.resolved_context['synthesis_review_refs']=[r.ref for r in packet.retrieved_reviews[:2]] if quote_requested else []
 if quote_requested:
  for r in packet.retrieved_reviews[:2]:lines.append('['+r.ref+'] '+packet.restaurant_names[r.restaurant_id]+': '+r.text[:130])
 lines.append('Aspect rates use aspect mentions; overall negative rates use valid PhoBERT reviews. These predictions do not prove actual product quality or causality.')
 if packet.plan.intent=='complaint_periods' and counts and not any(f.value for f in counts):lines.append('Kết quả kiểm tra phạm vi: KHÔNG có tháng phàn nàn về giá để đối chiếu. Doanh thu được cung cấp là TỔNG TOÀN KỲ. Mở đầu bằng việc làm rõ giới hạn này; không khẳng định doanh thu trong một tháng phàn nàn giả định.')
 if packet.plan.negative_focus and packet.plan.aspects and len(packet.plan.restaurant_ids)>1:
  rates=[f for f in facts if f.metric.endswith('negative_rate')]
  if rates:
   worst=max(rates,key=lambda f:f.value);revenue=next((f for f in facts if f.restaurant_id==worst.restaurant_id and f.metric=='delivered_revenue'),None)
   lines.append('Xếp hạng đã kiểm chứng: '+packet.restaurant_names[worst.restaurant_id]+' có phản hồi dịch vụ kém nhất, tỷ lệ tiêu cực '+worst.display+('. Doanh thu của chính quán này: '+revenue.display+' VND.' if revenue else '.')+' Không nhầm thứ hạng dịch vụ với thứ hạng doanh thu.')
 if packet.plan.asks_why:lines.append('Tháng thấp nhất chỉ là xếp hạng DOANH THU, không phải xếp hạng review tiêu cực. Các chỉ số review là quan sát cùng tháng, không cho biết tháng đó nhiều hay ít tiêu cực hơn tháng khác. Nguyên nhân chưa xác định được từ các quan sát này.')
 if feedback:lines.append('Correct the rejected draft: '+feedback)
 return '\n'.join(lines)

def retry_feedback(reason,packet=None):
 if reason.startswith('missing_sales_detail') and packet:
  values=[packet.restaurant_names[f.restaurant_id]+' '+f.display+' VND' for f in packet.facts if f.metric=='delivered_revenue' and ('→' in f.period or packet.plan.asks_why)]
  return 'Your answer omitted Sales. Include one verified revenue amount or Sales ranking: '+'; '.join(values[:3])+'. Keep a verified Review/Aspect observation too.'
 if reason.startswith('missing_review_detail') and packet:return 'Your answer omitted Review/PhoBERT evidence. Include a verified rating, negative rate, aspect rate/count or aspect ranking from the facts above.'
 if reason.startswith('unsupported_prior_period'):return 'You claimed a change against a previous period, but no previous-period value or verified change is supplied. Remove that contrast. State only the selected period observations and the causal limitation.'
 if reason=='unsupported_zero_claim':return 'You claimed an unsupported absence or extreme of negative feedback. The supplied negative rate is not zero and is not a ranking across months. Remove the absence/ranking claim and describe only the supplied rate and revenue, with the causal limitation.'
 if reason=='comparison_ambiguous' and packet and packet.plan.asks_why:return 'No comparison-period values are supplied. Do not claim higher/lower than previous months. The cause is unknown: acknowledge that limitation, then report the selected period and observed values only.'
 if reason.startswith('reversed_ranking:'):return 'Your ranking was false. Verified extreme: '+reason.split(':',1)[1]+'. Do not conflate revenue and review rankings.'
 if reason=='missing_causal_limitation':return 'Explicitly acknowledge that the cause cannot be identified from these data, then give observations only. No hypotheses or unsupported prior-period comparisons.'
 if reason.startswith('unsupported_number'):return 'Unsupported number or wrong metric/entity. Use only the exact verified values and units, with no rounding or additional calculations.'
 if reason=='contradictory_direction_description':return 'You called one metric decreasing and the other increasing the same direction. That is opposite direction. Use only the verified interval counts and distinguish them from first-to-last endpoint trends.'
 if reason=='unsupported_monotonic_trend':return 'You claimed a continuous increase/decrease. Intermediate months fluctuate. The supplied UP/DOWN endpoint directions compare only the first and last month. Describe the verified movement counts or weak correlation instead of a steady trend.'
 if reason.startswith('reversed_numeric_comparison') and packet:
  metric=reason.split(':',1)[-1];values=[f for f in packet.facts if f.metric==metric and '→' in f.period]
  return 'You reversed the '+metric+' comparison. Verified descending values: '+'; '.join(packet.restaurant_names[f.restaurant_id]+' '+f.display for f in sorted(values,key=lambda f:-f.value))+'. State the corrected conclusion in Vietnamese prose. Retain a verified revenue fact as well as a Review/Aspect observation whenever both domains are required.'
 if reason in {'reversed_ranking','unsupported_superlative','comparison_ambiguous'}:return 'The comparison was reversed or ambiguous. State the backend-verified direction and values with explicit restaurant names.'
 if 'correlation' in reason:return 'The verified coefficient is near zero: no clear linear association is established. Answer cautiously with the statistic; do not assert similar trends or echo these instructions.'
 if 'complaint' in reason:
  revenue='; '.join(packet.restaurant_names[f.restaurant_id]+' '+f.display+' VND' for f in packet.facts if f.metric=='delivered_revenue' and '→' in f.period) if packet else ''
  return 'Your opening falsely claimed complaint months existed. There are ZERO complaint months, so the requested monthly comparison is unavailable. Include this verified Sales amount explicitly: FULL-PERIOD revenue = '+revenue+'. Do not call it complaint-month revenue or claim stability/effects.'
 if 'causal' in reason:return 'Causality cannot be determined. Give verified observations only, without reasons or claims of no effect.'
 if 'citation' in reason or 'quote' in reason:return 'Individual reviews need a supplied [E] ID and exact quotations. Aggregate-only statements need no [E] citation.'
 return 'Use natural Vietnamese prose, not headings or validator instructions. Correct this error: '+reason

def grounded_fallback(packet):
 facts=select_facts(packet);parts=[]
 counts=[f for f in facts if f.metric.endswith('_negative_count')]
 if packet.plan.asks_why:parts.append('Chưa đủ dữ liệu để xác định nguyên nhân của mức doanh thu này; các quan sát cùng kỳ chỉ giúp mô tả tình hình.')
 if packet.plan.intent=='complaint_periods' and counts and not any(f.value for f in counts):parts.append('PhoBERT chưa ghi nhận lượt phàn nàn về khía cạnh này, nên không có tháng phù hợp để đối chiếu doanh thu.')
 for rid,name in packet.restaurant_names.items():
  selected=[f for f in facts if f.restaurant_id==rid][:3]
  if not selected:continue
  clauses=[]
  for f in selected:
   if f.metric=='delivered_revenue':
    period=''
    if '→' not in f.period and packet.plan.grouping=='monthly':period=' trong tháng '+str(int(f.period[5:7]))+' năm '+f.period[:4]
    clauses.append('doanh thu'+period+' đạt '+f.display+' đồng')
   elif f.metric=='negative_aspect_mentions':clauses.append(label(f)+' ở mức '+f.display+' lượt')
   else:clauses.append(label(f).lower()+' ở mức '+f.display)
  parts.append('Với '+name+', '+ '; '.join(clauses)+'.')
 if packet.plan.intent=='association' and any(f.unit=='r' and near_zero(f.value) for f in facts):parts.append('Hệ số gần 0, nên chưa thấy hai chỉ số có liên hệ tuyến tính rõ trong phạm vi này. Dữ liệu quan sát chưa đủ để kết luận quan hệ nhân quả.')
 return '\n\n'.join(parts) if parts else 'Chưa có đủ dữ liệu trong phạm vi này để đối chiếu Sales và Review.'

def validate_generated(raw,packet,allowed_facts=None):
 """Compatibility for historical saved JSON drafts; production uses validate_text."""
 if isinstance(raw,str):return validate_text(raw,packet,allowed_facts)
 paragraphs=raw.get('paragraphs',[raw]);answers=[]
 for p in paragraphs:
  expected=set(p.get('fact_refs',[]))|set(p.get('review_refs',[]))
  if set(re.findall(r'\b[FE]\d+\b',p['text']))!=expected:raise ValueError('unknown_or_missing_citation')
  supported=[f for f in (allowed_facts or packet.facts) if f.ref in p.get('fact_refs',[])]
  answers.append(validate_text(p['text'],packet,supported))
 return '\n\n'.join(answers)

def render_declared_citations(raw):
 """Read-only compatibility for old acceptance artifacts, never synthesis."""
 result=dict(raw)
 result['text']=re.sub(r'\[((?:[FE]\d+\s*,\s*)+[FE]\d+)\]',lambda m:' '.join('['+r.strip()+']' for r in m.group(1).split(',')),result['text'])
 for prefix,key in [('F','fact_refs'),('E','review_refs')]:
  if not re.search(r'\['+prefix+r'\d+\]',result['text']):result['text']+=' '+ ' '.join('['+r+']' for r in result.get(key,[]) if re.fullmatch(prefix+r'\d+',r))
 return result

def analyze_chat(conn,question,*,selected_id=None,start=None,end=None,state=None,gateway_factory=None,force_validation_failure=False):
 from src.sales.cross_hybrid import dispatch
 return dispatch(conn,question,selected_id=selected_id,start=start,end=end,state=state,gateway_factory=gateway_factory,force_validation_failure=force_validation_failure)

def _analyze_verified_packet(conn,question,*,selected_id=None,start=None,end=None,state=None,gateway_factory=None,force_validation_failure=False):
 from src.monitoring.metrics import CROSS_CHAT_REQUESTS,CROSS_CHAT_GENERATED,CROSS_CHAT_FALLBACK,CROSS_CHAT_VALIDATION_FAILURES,CROSS_CHAT_DURATION
 CROSS_CHAT_REQUESTS.inc();started=time.perf_counter();attempts=[];mode='GROUNDED_FALLBACK';tokens_in=tokens_out=0
 try:
  plan=plan_question(question,catalog(conn),selected_id=selected_id,start=start,end=end,state=state,today=vietnam_today());packet=enrich_packet(build_packet(conn,plan,question))
  prior=ConversationState.model_validate(state or {})
  packet.resolved_context={'restaurant_scope':plan.restaurant_ids,'period_scope':[str(plan.start_date),str(plan.end_date)],'comparison_scope':prior.comparison_scope,'previous_intent':prior.previous_intent}
  facts=select_facts(packet);feedback=None
  if facts:
   from src.rag_v3.cli import selected_v3_gateway
   prompt=Path('prompts/cross/cross_chat_v4.txt').read_text(encoding='utf-8-sig')
   for attempt in range(2):
    raw=None
    try:
     with (gateway_factory or selected_v3_gateway)() as gateway:
      context=generation_context(packet,facts,feedback=feedback)
      if hasattr(gateway,'session'):
       for _ in range(12):
        tokenized=gateway.session.post(gateway.url+'/tokenize',json={'content':prompt+'\n'+context},timeout=10)
        if not tokenized.ok or len(tokenized.json().get('tokens',[]))<=1450:break
        if len(facts)<=4:raise ValueError('evidence_context_too_large')
        facts=facts[:-1];context=generation_context(packet,facts,feedback=feedback)
      raw,usage=gateway.generate_text(system=prompt,user=context,prompt_version=PROMPT_VERSION,max_new_tokens=400,component='cross_analysis_generation',timeout_seconds=150)
     tokens_in+=usage.get('input_tokens') or 0;tokens_out+=usage.get('output_tokens') or 0
     if force_validation_failure:raise ValueError('forced_acceptance_validation_failure')
     answer=validate_text(raw,packet);mode='QWEN_GENERATED';attempts.append({'attempt':attempt+1,'status':'PASS','output_format':'plain_text'});break
    except Exception as error:
     reason=str(error)[:180] if isinstance(error,ValueError) else type(error).__name__
     attempts.append({'attempt':attempt+1,'status':'REJECTED','reason':reason,'draft':raw});CROSS_CHAT_VALIDATION_FAILURES.inc();feedback=retry_feedback(reason,packet)
  if mode=='QWEN_GENERATED':CROSS_CHAT_GENERATED.inc()
  else:answer=grounded_fallback(packet);CROSS_CHAT_FALLBACK.inc()
  prior=ConversationState.model_validate(state or {})
  context=ConversationState(restaurant_scope=plan.restaurant_ids,period_scope=[str(plan.start_date),str(plan.end_date)],comparison_scope=prior.comparison_scope,previous_metrics=plan.metrics,previous_intent=plan.intent,previous_aspects=plan.aspects)
  elapsed=time.perf_counter()-started
  from src.rag.monitoring import record
  record('cross_analysis_chat','Qwen2.5-3B','production',PROMPT_VERSION,elapsed*1000,'grounded',input_tokens=tokens_in,output_tokens=tokens_out,generation_mode=mode,evidence_count=len(packet.retrieved_reviews),generation_attempts=len(attempts),fallback_status=mode)
  return {'status':'grounded' if packet.facts else 'insufficient_data','answer':answer,'answer_mode':mode,'conversation_state':context.model_dump(mode='json'),'evidence_packet':packet.model_dump(mode='json'),'validation':{'attempts':attempts,'output_format':'plain_text','number_grounding':'PASS' if mode=='QWEN_GENERATED' else 'DETERMINISTIC_VERIFIED_FACTS','citation_grounding':'PASS','causation_guard':'PASS'},'telemetry':{'duration_seconds':elapsed,'input_tokens':tokens_in,'output_tokens':tokens_out,'qwen_generated':mode=='QWEN_GENERATED','fallback':mode=='GROUNDED_FALLBACK'}}
 finally:CROSS_CHAT_DURATION.observe(time.perf_counter()-started)
