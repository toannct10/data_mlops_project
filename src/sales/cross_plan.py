"""Typed, bounded conversational planning over the canonical analytical layer."""
import re
from datetime import date,timedelta
from pydantic import BaseModel,ConfigDict,Field
from src.sales.semantic_plan import plain,parse_time_scope,next_month,month_range

class ConversationState(BaseModel):
 model_config=ConfigDict(extra='forbid')
 restaurant_scope:list[str]=Field(default_factory=list,max_length=20)
 period_scope:list[str]=Field(default_factory=list,max_length=2)
 comparison_scope:list[str]=Field(default_factory=list,max_length=2)
 previous_metrics:list[str]=Field(default_factory=list,max_length=12)
 previous_intent:str=''
 previous_aspects:list[str]=Field(default_factory=list,max_length=12)
 analytics_route:str=''
 analytics_dimension:str=''
class CrossAnalyticalPlan(BaseModel):
 model_config=ConfigDict(extra='forbid')
 restaurant_ids:list[str]
 start_date:date
 end_date:date
 grouping:str='monthly'
 intent:str='comparison'
 aspects:list[str]=Field(default_factory=list)
 metrics:list[str]=Field(default_factory=list)
 scope_source:dict=Field(default_factory=dict)
 asks_why:bool=False
 negative_focus:bool=False
class Fact(BaseModel):
 ref:str
 restaurant_id:str
 period:str
 metric:str
 label:str
 value:float
 unit:str
 display:str
 support:int|None=None
class RetrievedReview(BaseModel):
 ref:str
 review_id:str
 order_id:str|None=None
 restaurant_id:str
 period:str
 text:str
 overall_sentiment:str|None=None
 aspects:list[dict]=Field(default_factory=list)
class CrossAnalysisEvidencePacket(BaseModel):
 cause_available:bool=False
 semantic_policy:dict=Field(default_factory=dict)
 required_domains:list[str]=Field(default_factory=list)
 trend_facts:list[dict]=Field(default_factory=list)
 comparison_facts:list[dict]=Field(default_factory=list)
 food_quality_facts:list[dict]=Field(default_factory=list)
 why_evidence:list[dict]=Field(default_factory=list)
 temporal_comparisons:list[dict]=Field(default_factory=list)
 resolved_context:dict=Field(default_factory=dict)
 question:str
 plan:CrossAnalyticalPlan
 restaurant_names:dict[str,str]
 sales_metrics:list[dict]
 review_metrics:list[dict]
 aspect_metrics:list[dict]
 trend_metrics:list[dict]
 comparison_metrics:list[dict]
 facts:list[Fact]
 retrieved_reviews:list[RetrievedReview]
 data_limitations:list[str]
 source_ids:list[str]

ASPECT_WORDS={
 'SERVICE#GENERAL':('dich vu','phuc vu','service'),
 'FOOD#QUALITY':('chat luong do an','food quality','do an','mon an'),
 'FOOD#PRICES':('ve gia','gia ca','price','dat','gia'),
}
def plan_question(question,catalog,*,selected_id=None,start=None,end=None,state=None,today=None):
 today=today or date.today();state=ConversationState.model_validate(state or {});q=plain(question)
 if not q.strip() or len(q)>2000:raise ValueError('Câu hỏi cần từ 1 đến 2.000 ký tự.')
 ids=[]
 for item in catalog:
  rid=str(item['restaurant_id']);prefix=plain(item['restaurant_name'].split(' - ')[0]);aliases={plain(item['restaurant_name'])}
  if item.get('is_live_demo'):
   aliases.add(prefix)
   if len(prefix.split()[0])>=5:aliases.add(prefix.split()[0])
  if item.get('is_live_demo') and prefix.startswith('ga ran '):aliases.add(prefix[7:])
  if any(a and a in q for a in aliases) or re.search(r'\b(?:id|quan|nha hang)\s+'+re.escape(rid)+r'\b',q):ids.append(rid)
 multi=bool(re.search(r'\b3\s+(?:quan|nha hang)\b|\bba\s+quan\b|quan nao',q))
 if multi:ids=[str(r['restaurant_id']) for r in catalog if r.get('is_live_demo')]
 restaurant_source='explicit' if ids else 'conversation' if state.restaurant_scope else 'BI selector'
 ids=ids or state.restaurant_scope or ([str(selected_id)] if selected_id else [])
 if not ids or len(ids)>20:raise ValueError('Hãy chỉ rõ tối đa 20 nhà hàng để so sánh.')
 known={str(r['restaurant_id']) for r in catalog}
 if not set(ids)<=known:raise ValueError('Nhà hàng không thuộc dữ liệu canonical.')
 temporal=re.sub(r'nua (dau|cuoi) (?:nam )?(20\d{2})',r'6 thang \1 nam \2',q)
 if re.search(r'\bthang\s+\d{1,2}\b',temporal) and not re.search(r'20\d{2}',temporal):
  prior=state.period_scope or ([str(start),str(end)] if start and end else [])
  years={x[:4] for x in prior}
  if len(years)==1:temporal+=' nam '+next(iter(years))
  else:raise ValueError('Tháng này thuộc năm nào? Hãy bổ sung năm để tránh ghép sai kỳ.')
 full_dates=re.findall(r'\b(\d{1,2})/(\d{1,2})/(20\d{2})\b',temporal)
 scope=parse_time_scope(temporal,today=today)
 if full_dates:
  dates=[date(int(y),int(m),int(d)) for d,m,y in full_dates]
  first,last=min(dates),max(dates);period_source='explicit dates'
 elif scope.periods:
  first=date.fromisoformat(min(scope.periods)+'-01');last=date.fromisoformat(next_month(max(scope.periods))+'-01')-timedelta(days=1);period_source='explicit question'
 elif state.period_scope:
  first,last=map(date.fromisoformat,state.period_scope);period_source='conversation'
 elif start and end:first,last=date.fromisoformat(str(start)),date.fromisoformat(str(end));period_source='BI date selector'
 else:raise ValueError('Hãy chọn khoảng ngày hoặc nêu kỳ trong câu hỏi.')
 if last<first:raise ValueError('Khoảng ngày không hợp lệ.')
 if (last-first).days>3*366:raise ValueError('Mỗi lượt phân tích hỗ trợ tối đa ba năm; hãy thu hẹp kỳ.')
 last=min(last,today)
 if first>last:raise ValueError('Chưa có quan sát cho kỳ tương lai này.')
 aspects=[]
 aspect_question=re.sub(r'\bdanh gia\b','rating',q)
 for aspect,words in ASPECT_WORDS.items():
  if any(re.search(r'\b'+re.escape(word)+r'\b',aspect_question) for word in words):aspects.append(aspect)
 if 'FOOD#PRICES' in aspects:aspects.extend(['DRINKS#PRICES','RESTAURANT#PRICES'])
 if not aspects and any(w in q for w in ['khia canh do','van de do']):aspects=state.previous_aspects
 negative=any(w in q for w in ['tieu cuc','phan nan','che','kem','negative'])
 if 'thap nhat' in q and 'thang' in q:intent='lowest_period'
 elif 'cao nhat' in q and 'thang' in q:intent='highest_period'
 elif negative and aspects and any(w in q for w in ['nhung thang','nhieu phan nan']):intent='complaint_periods'
 elif any(w in q for w in ['xu huong','giong nhau','tuong quan','lien he','di cung','bien dong']):intent='association'
 elif any(w in q for w in ['chu yeu','van de gi','aspect nao']):intent='aspect_ranking'
 elif multi or len(ids)>1:intent='restaurant_comparison'
 else:intent='period_comparison'
 metrics=['delivered_revenue']
 if re.search(r'\bdon\b',q):metrics.append('delivered_orders')
 if any(w in q for w in ['rating','danh gia']):metrics.append('average_rating_10')
 if (negative and not aspects) or 'sentiment' in q:metrics.extend(['negative_rate','review_count'])
 elif not aspects and 'review' in q:metrics.extend(['negative_rate','review_count'])
 if any(w in q for w in ['huy','cancel']):metrics.append('cancelled_orders')
 if any(w in q for w in ['hoan','refund']):metrics.append('refunded_orders')
 if intent=='aspect_ranking':aspects=[]
 grouping='daily' if any(w in q for w in ['theo ngay','hang ngay']) else 'monthly'
 if grouping=='daily' and (last-first).days>92:raise ValueError('Phân tích theo ngày cần khoảng không quá 93 ngày.')
 return CrossAnalyticalPlan(restaurant_ids=list(dict.fromkeys(ids)),start_date=first,end_date=last,grouping=grouping,intent=intent,aspects=list(dict.fromkeys(aspects)),metrics=list(dict.fromkeys(metrics)),scope_source={'restaurants':restaurant_source,'period':period_source},asks_why=any(w in q for w in ['tai sao','vi sao','nguyen nhan']),negative_focus=negative)
