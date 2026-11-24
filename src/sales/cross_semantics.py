"""Semantic normalizers grounded in the packet's numerical claim contracts."""
import re
from src.sales.semantic_plan import plain
from src.sales.cross_evidence import near_zero,comparison_contract,policy

NEAR_ZERO=re.compile(r'gan nhu khong co (?:moi )?(?:tuong quan|lien (?:he|quan) tuyen tinh)|gan(?: nhu)?(?: bang)?\s*(?:0|zero|khong(?!\s+co))\b|xap xi(?: bang)?\s*(?:0|zero|khong(?!\s+co))\b|rat thap')
COMPARISON=re.compile(r'\b(it tieu cuc hon|tieu cuc hon|tich cuc hon|chiem ty le lon hon|cao hon|nhinh hon|lon hon|vuot troi(?: hon)?|thap hon|kem hon|nho hon|it hon|nhieu hon|tot hon|tuong duong|xap xi|gan bang)\b')
HIGHER={'cao hon','nhinh hon','lon hon','vuot troi','vuot troi hon','chiem ty le lon hon','nhieu hon','tot hon','tich cuc hon','tieu cuc hon'}
APPROX={'tuong duong','xap xi','gan bang'}

def aspect_hint(text):
 for aspect,words in [('DRINKS#QUALITY',['chat luong do uong','drinks#quality']),('FOOD#QUALITY',['chat luong mon an','chat luong do an','food quality','food#quality']),('SERVICE#GENERAL',['dich vu','phuc vu','service#general']),('RESTAURANT#GENERAL',['nha hang noi chung','restaurant#general']),('FOOD#PRICES',['gia mon an','food#prices']),('DRINKS#PRICES',['gia do uong','drinks#prices'])]:
  if any(w in text for w in words):return aspect
 return None

def normalize_near_zero(text,packet):
 """Mask qualitative zero, never literal r=0; require actual |r| <= epsilon."""
 supported=False;pieces=[];position=0
 for m in NEAR_ZERO.finditer(text):
  clause=re.split(r'[.!?;](?=\s|$)|\n',text[:m.start()])[-1]
  correlation=bool(re.search(r'pearson|tuong quan|he so|\br\b',clause) or 'tuong quan' in m.group())
  if not correlation:continue
  candidates=[f for f in packet.facts if f.unit=='r']
  if not candidates or not all(near_zero(f.value) for f in candidates):raise ValueError('unsupported_near_zero_interpretation')
  pieces.extend([text[position:m.start()],'NEAR_ZERO_VERIFIED']);position=m.end();supported=True
 pieces.append(text[position:]);return ''.join(pieces),supported

def comparison_supported(packet,a,b,direction):
 if a.ref==b.ref:return direction=='APPROX_EQUAL'
 contracts=packet.comparison_facts or comparison_contract(packet.facts)
 for c in contracts:
  if (c['left_fact_ref'],c['right_fact_ref'])==(a.ref,b.ref):actual=c['relation']
  elif (c['right_fact_ref'],c['left_fact_ref'])==(a.ref,b.ref):actual={'HIGHER':'LOWER','LOWER':'HIGHER','EQUAL':'EQUAL'}[c['relation']]
  else:continue
  return c['approx_equal'] if direction=='APPROX_EQUAL' else actual==direction
 return False

def temporal_guard(text,packet):
 """A prior-period assertion needs explicit values and a resolvable period."""
 used=set()
 for sentence in re.split(r'[.!?;](?=\s|$)|\n',text):
  if not re.search(r'(?:thang|ky|quy|nam) truoc|truoc do|truoc day',sentence):continue
  if not re.search(r'tang|giam|cao hon|thap hon|nhieu hon|it hon|on dinh',sentence):continue
  contracts=[c for c in packet.temporal_comparisons if c.get('previous_period_value') is not None or c.get('verified_change_pct') is not None]
  if not contracts:raise ValueError('unsupported_prior_period_comparison')
  month=re.search(r'thang\s+(\d{1,2})(?:\s*(?:nam|/)\s*(20\d{2}))?',sentence)
  if not month:raise ValueError('prior_period_comparison_needs_period')
  metrics={'delivered_revenue'} if 'doanh thu' in sentence else {'average_rating_10'} if re.search(r'rating|diem|danh gia',sentence) else set()
  candidates=[c for c in contracts if int(c['period'][5:7])==int(month.group(1)) and (not month.group(2) or c['period'][:4]==month.group(2)) and c['metric'] in metrics]
  direction='LOWER' if re.search(r'giam|thap hon|it hon',sentence) else 'HIGHER' if re.search(r'tang|cao hon|nhieu hon',sentence) else 'EQUAL'
  if not candidates or not all(c['direction']==direction for c in candidates):raise ValueError('reversed_temporal_comparison')
  used.update(c['metric'] for c in candidates)
 return used

def temporal_comparison_text(text):
 parts=re.split(r'([.!?;](?=\s|$)|\n)',text)
 for i in range(0,len(parts),2):
  if re.search(r'(?:thang|ky|quy|nam) truoc|truoc do|truoc day',parts[i]):parts[i]=COMPARISON.sub('VERIFIED_TEMPORAL_RELATION',parts[i])
 return ''.join(parts)

def trend_guard(text,packet):
 used=set()
 for trend in packet.trend_facts:
  for m in re.finditer(r'(cung|nguoc)\s*(?:huong|chieu)\s*\(([^)]+)\)',text):
   revenue=re.search(r'doanh thu\s+(tang|giam)',m.group(2));rating=re.search(r'(?:diem |danh gia |rating )+(tang|giam)',m.group(2))
   if revenue and rating and (revenue.group(1)==rating.group(1))!=(m.group(1)=='cung'):raise ValueError('contradictory_direction_description')
  # Counts refer to transitions, not six independent direction observations.
  for m in re.finditer(r'(\d+(?:[.,]\d+)?)\s*(%|(?:/|trong(?: so)?)\s*(\d+))?\s*(?:lan|thang|khoang|buoc|chuyen|dot|cac|bien dong|co|hai chi so|\s){0,45}(cung|dong|nguoc)\s*(?:huong|chieu)',text):
   field='opposite_direction_months' if m.group(4)=='nguoc' else 'same_direction_months';expected=trend[field]
   if m.group(2)=='%':expected=100*expected/trend['movement_intervals'] if trend['movement_intervals'] else None
   if expected is None or abs(float(m.group(1).replace(',','.'))-expected)>.005 or m.group(3) and int(m.group(3))!=trend['movement_intervals']:raise ValueError('reversed_direction_agreement_count')
  for clause in re.split(r'[,;]|\bnhung\b|\bcon\b|\btrong khi\b',text):
   if not re.search(r'\b(?:tang|giam)\b',clause) or re.search(r'chua du|khong the|khong du',clause):continue
   if re.search(r'thang\s+\d',clause):continue  # Explicit prior-period claims are checked above.
   measures=[]
   if 'doanh thu' in clause:measures.append(('revenue_trend','revenue_direction'))
   if re.search(r'rating|diem|danh gia trung binh',clause):measures.append(('rating_trend','rating_direction'))
   for field,movement in measures:
    if re.search(r'tang\s*/?\s*giam',clause):
     if not {-1,1}<={r[movement] for r in trend['movements']}:raise ValueError('unsupported_mixed_trend')
     continue
    direction='UP' if re.search(r'\btang\b',clause) else 'DOWN'
    if re.search(r'(?:tang|giam)\s+(?:dan|deu|lien tuc)|xuyen suot',clause):
     sign=1 if direction=='UP' else -1
     if not trend['movements'] or any(r[movement]!=sign for r in trend['movements']):raise ValueError('unsupported_monotonic_trend')
    if re.search(r'khong\s+(?:tang|giam)',clause):
     if trend[field]==direction:raise ValueError('reversed_verified_trend')
    elif trend[field]!=direction:raise ValueError('reversed_verified_trend')
    used.add('delivered_revenue' if field=='revenue_trend' else 'average_rating_10')
 return used

def coverage_guard(text,packet,matched,comparison_metrics):
 if not packet.required_domains:return
 sales=False;review=False
 for match,facts in matched:
  context=text[max(0,match.start()-70):match.end()+30];unit=(match.group(2) or match.group(3) or '').lower()
  for f in facts:
   if f.unit=='VND' and (re.search(r'doanh thu|sales',context) or unit in {'vnd','dong','₫','nghin','ngan','trieu','ty'}):sales=True
   if f.unit=='orders' and re.search(r'\bdon\b|giao thanh cong|huy|hoan tien',context):sales=True
   if f.unit in {'percent','rating/10','reviews','mentions'} and f.metric not in {'same_direction_months','opposite_direction_months'}:review=True
   if f.metric.startswith('pearson_') or f.metric in {'same_direction_months','opposite_direction_months','movement_intervals'} and packet.trend_facts:review=True
 sales=sales or bool(comparison_metrics&{'delivered_revenue','orders','delivered_orders','cancelled_orders','refunded_orders'})
 review=review or any(m not in {'delivered_revenue','orders','delivered_orders','cancelled_orders','refunded_orders'} for m in comparison_metrics)
 if re.search(r'khong (?:co|ghi nhan|quan sat duoc)|chua ghi nhan',text) and any(f.value==0 and f.metric.endswith('_negative_count') for f in packet.facts):review=True
 if re.search(r'\[E\d+\]',text,re.I):review=True
 if 'sales' in packet.required_domains and not sales:raise ValueError('missing_sales_detail')
 if 'review' in packet.required_domains and not review:raise ValueError('missing_review_detail')
