"""Post-generation grounding for prose, independently of any output JSON schema."""
import re
from datetime import date
from decimal import Decimal
from src.sales.semantic_plan import plain
from src.sales.cross_evidence import near_zero
from src.sales.cross_semantics import normalize_near_zero,temporal_guard,temporal_comparison_text,trend_guard,coverage_guard,COMPARISON,HIGHER,APPROX,comparison_supported,aspect_hint

NUMBER=re.compile(r'(?<![\w#])([-+]?\d+(?:[.,]\d+)*)(?:\s*(nghin|ngan|trieu|ty|%|/\s*(?:10|100)|vnd|dong|₫)\b|\s*(%|₫))?',re.I)
SCALE={'nghin':1000,'ngan':1000,'trieu':1000000,'ty':1000000000}

def numbers(value):
 """Resolve separator ambiguity only by matching a verified value and unit."""
 value=value.replace(' ','');result=set()
 if '.' in value and ',' in value:
  sep='.' if value.rfind('.')>value.rfind(',') else ','
  result.add(Decimal(value.replace(',' if sep=='.' else '.','').replace(sep,'.')))
 elif '.' in value or ',' in value:
  parts=re.split('[.,]',value)
  if len(parts)==2:result.add(Decimal(value.replace(',','.')))
  if parts[0].lstrip('+-')!='0' and all(len(p)==3 for p in parts[1:]):result.add(Decimal(''.join(parts)))
 else:result.add(Decimal(value))
 return result

def number(value):
 values=numbers(value)
 return max(values,key=abs) if not value.lstrip('+-').startswith('0') else min(values,key=abs)

def sentences(text):return [s.strip() for s in re.split(r'[.!?;](?=\s|$)|\n',text) if s.strip()]

def cause_prefix(sentence,position,size):
 return re.split(r',|\bnhung\b|\btuy nhien\b',sentence[:position])[-1][-size:]

def causal_assertion(text):
 text=re.sub(r'“[^”]*”|"[^\"]*"','',text)
 # Preserve Vietnamese distinctions before accent folding: "sau đó" is not
 # causal "do", and "đồ ăn" is not a cause connective either.
 text=re.sub(r'\bđó\b','DEM_PRONOUN',text,flags=re.I)
 text=re.sub(r'\bđồ\b','FOOD_NOUN',text,flags=re.I)
 text=re.sub(r'\b\w+\b',lambda m:'NON_CAUSAL_DO' if plain(m.group())=='do' and m.group().casefold()!='do' else m.group(),text)
 for sentence in sentences(plain(text)):
  if re.search(r'co the (?:do |boi vi|bat nguon tu|xuat phat tu)',sentence):return True
  for match in re.finditer(r'anh huong|tac dong',sentence):
   before=cause_prefix(sentence,match.start(),120)
   if not re.search(r'chua du|chua the|khong du|khong the|khong chung minh|khong co bang chung|khong ro',before):return True
  for match in re.finditer(r'khong (?:bi |chiu )?anh huong|khong co tac dong',sentence):
   if not re.search(r'chua du|khong du|khong the|chua the',cause_prefix(sentence,match.start(),90)):return True
  for match in re.finditer(r'\b(?:nguyen nhan|dan den|khien|gay ra|boi vi|because|caused|tac dong|anh huong|quan he nhan qua)\b|\bdo\s+.{0,45}\s+nen\b|\b(?:giam|thap|tang|cao)\b.{0,25}\bdo\s',sentence):
   before=cause_prefix(sentence,match.start(),85)
   after=re.split(r',|\bnhung\b|\btuy nhien\b',sentence[match.end():])[0][:85]
   if match.group() in {'nguyen nhan','quan he nhan qua'} and re.search(r'chua (?:duoc )?(?:xac dinh|chung minh|lam ro)|khong (?:the |duoc )?(?:xac dinh|chung minh)|van chua ro',after):continue
   if not re.search(r'khong|chua|khong du|khong the',before):return True
 return False

def entity_mentions(text,packet):
 found=[]
 for rid,name in packet.restaurant_names.items():
  brand=plain(name).replace('ga ran ','');aliases={brand}
  if brand.startswith('starbucks'):aliases.add('starbucks')
  matches=[m for alias in aliases for m in re.finditer(r'\b'+re.escape(alias)+r'\b',text)]
  found.extend((m.start(),m.end(),rid) for m in matches)
 return sorted(set(found))

def metric_hint(text):
 if any(w in text for w in ['dich vu','phuc vu','service']):return 'service_negative_rate'
 if any(w in text for w in ['food quality','chat luong']):
  polarity=max([('negative',text.rfind('tieu cuc')),('positive',text.rfind('tich cuc')),('neutral',text.rfind('trung tinh'))],key=lambda x:x[1])[0] if any(w in text for w in ['tieu cuc','tich cuc','trung tinh']) else 'positive'
  return 'food_quality_'+polarity+'_rate'
 if re.search(r'\b(?:ve gia|gia ca|prices?)\b',text):return 'price_negative_rate'
 if 'ty le' in text and 'tieu cuc' in text:return 'negative_rate'
 if 'rating' in text or 'diem trung binh' in text or 'danh gia trung binh' in text:return 'average_rating_10'
 if 'doanh thu' in text:return 'delivered_revenue'
 return None

def matched_facts(match,text,facts,packet):
 unit=(match.group(2) or match.group(3) or '').replace(' ','').lower()
 values={v*SCALE.get(unit,1) for v in numbers(match.group(1))}
 prefix=text[max(0,match.start()-100):match.start()]
 mentions=entity_mentions(prefix,packet);rid=mentions[-1][2] if mentions else None
 suffix=text[match.end():match.end()+100]
 following=entity_mentions(suffix,packet)
 if following and re.match(r'\s*(?:cho|cua|tai|o)\s+',suffix):rid=following[0][2]
 context=sentences(text[:match.start()])[-1] if text[:match.start()].strip() else ''
 parallel=list(re.finditer(r'lan luot',context))
 if parallel:
  marker=parallel[-1];entities=list(dict.fromkeys(r[2] for r in entity_mentions(context[:marker.start()],packet)))
  position=len(list(NUMBER.finditer(context[marker.end():])))
  if len(entities)>1 and position<len(entities):rid=entities[position]
 # Parenthesized A-vs-B values follow the entity order, not the last object.
 opened=text.rfind('(',0,match.start());closed=text.find(')',match.end())
 if opened>=0 and closed>=0 and ')' not in text[opened:match.start()] and re.search(r'\bvs\b|so voi',text[opened:closed]):
  preceding=sentences(text[:opened])[-1] if text[:opened].strip() else ''
  entities=list(dict.fromkeys(r[2] for r in entity_mentions(preceding,packet)))
  position=len(list(NUMBER.finditer(text[opened:match.start()])))
  if len(entities)==2 and position<2:rid=entities[position]
 candidates=[f for f in facts if rid is None or f.restaurant_id==rid]
 facet=aspect_hint(context)
 if facet:candidates=[f for f in candidates if f.metric!='negative_aspect_mentions' or f.label.startswith(facet)]
 if unit in {'%','/100'}:
  candidates=[f for f in candidates if f.unit=='percent']
  local=re.split(r'\bva\b|\bcon\b|\btrong khi\b',context)[-1]
  hint=metric_hint(local) or metric_hint(context)
  if hint=='negative_rate' and packet.plan.aspects and not any(f.metric==hint for f in facts):
   aspect_metrics={f.metric for f in facts if f.metric.endswith('_negative_rate')}
   if len(aspect_metrics)==1:hint=next(iter(aspect_metrics))
  if hint in {'delivered_revenue','average_rating_10'}:hint=None
  if hint:candidates=[f for f in candidates if f.metric==hint]
 elif unit=='/10':candidates=[f for f in candidates if f.unit=='rating/10']
 elif unit in SCALE or unit in {'vnd','dong','₫'}:candidates=[f for f in candidates if f.unit=='VND']
 found=[]
 for fact in candidates:
  target=Decimal(str(fact.value))*(100 if unit in {'%','/100'} else 1)
  tolerance=Decimal('.005') if fact.unit in {'percent','rating/10'} else Decimal('.00051') if fact.unit=='r' else Decimal('.00001')
  if any(abs(v-target)<=tolerance for v in values):found.append(fact)
  elif not unit and fact.support is not None and Decimal(fact.support) in values and re.search(r'mau so|co mau|tong (?:so )?luot nhac|tren tong|trong so|tren\s*$',prefix):found.append(fact)
 return found

def comparison_metric(text,packet,facts,previous=None):
 available={f.metric for f in facts}
 hint=None
 for local in reversed(re.split(r',(?=\s)|\bva\b|\bnhung\b|\bma con(?: co)?\b|\btrong khi\b',text)):
  hint=metric_hint(local)
  if hint is None and 'tich cuc' in local:
   choices={m for m in available if m.endswith('_positive_rate')}
   if len(choices)==1:hint=next(iter(choices))
  if hint:break
 if hint=='negative_rate' and hint not in available:
  choices={m for m in available if m.endswith('_negative_rate')}
  if len(choices)==1:hint=next(iter(choices))
 if hint is None and 'tich cuc' in text:
  choices={m for m in available if m.endswith('_positive_rate')}
  if len(choices)==1:hint=next(iter(choices))
 if hint and hint not in available and any(f.metric=='negative_aspect_mentions' for f in facts):hint='negative_aspect_mentions'
 return hint or previous

def validate_comparisons(text,packet,facts):
 previous_entities=set();previous_metric=None;previous_subject=None;used=set()
 for sentence in sentences(text):
  mentions=entity_mentions(sentence,packet)
  for comp in COMPARISON.finditer(sentence):
   before=[r for r in mentions if r[0]<comp.start()];after=[r for r in mentions if r[0]>=comp.end()]
   metric=comparison_metric(sentence[:comp.end()],packet,facts,previous_metric)
   facet=aspect_hint(sentence[:comp.end()]);eligible=[f for f in facts if metric!='negative_aspect_mentions' or not facet or f.label.startswith(facet)]
   left=before[-1][2] if before else previous_subject if 'quan nay' in sentence else None
   coordinated=re.split(r'\bva\b|\bnhung\b|\bma con(?: co)?\b|\bdong thoi\b',sentence[:comp.start()])
   if len(coordinated)>1 and not entity_mentions(coordinated[-1],packet) and previous_subject:left=previous_subject
   right={after[0][2]} if after else set()
   if not right and left:
    earlier=[r[2] for r in before if r[2]!=left]
    if earlier:right={earlier[-1]}
   if not right and left:
    if re.search(r'ca hai|hai quan',sentence[comp.end():]) and len(previous_entities)==2:right=previous_entities-{left}
    elif not sentence[comp.end():].strip() or re.search(r'quan con lai',sentence[comp.end():]):
     choices=previous_entities-{left} if previous_entities else set(packet.plan.restaurant_ids)-{left}
     if len(choices)==1:right=choices
    elif comp.group() in {'nhieu hon','it hon'}:
     right={previous_subject} if previous_subject and previous_subject!=left else set(packet.plan.restaurant_ids)-{left}
   pairs=[(a,b) for a in eligible for b in eligible if left and a.restaurant_id==left and b.restaurant_id in right and a.metric==b.metric==metric and a.period==b.period]
   if not pairs:
    nums=list(NUMBER.finditer(sentence));ls=[n for n in nums if n.end()<=comp.start()];rs=[n for n in nums if n.start()>=comp.end()];pair=(ls[-1],rs[0]) if ls and rs else tuple(rs[:2]) if len(rs)>=2 else ()
    if len(pair)==2:
     a=matched_facts(pair[0],sentence,eligible,packet);b=matched_facts(pair[1],sentence,eligible,packet);pairs=[(x,y) for x in a for y in b if x.metric==y.metric and x.period==y.period and (not metric or x.metric==metric)]
   if not pairs:raise ValueError('comparison_ambiguous')
   higher=comp.group() in HIGHER
   if comp.group() in {'tot hon','kem hon'} and all(a.metric.endswith('negative_rate') for a,b in pairs):higher=not higher
   direction='APPROX_EQUAL' if comp.group() in APPROX else 'HIGHER' if higher else 'LOWER'
   if not all(comparison_supported(packet,a,b,direction) for a,b in pairs):raise ValueError('reversed_numeric_comparison:'+str(metric))
   used.update(a.metric for a,b in pairs)
   previous_entities={f.restaurant_id for pair in pairs for f in pair};previous_metric=pairs[0][0].metric;previous_subject=pairs[0][0].restaurant_id
  for comp in re.finditer(r'\b(cao nhat|lon nhat|thap nhat|kem nhat|tot nhat|it nhat|nhieu nhat)\b',sentence):
   if packet.plan.intent in {'lowest_period','highest_period'} and ('thang' in sentence or packet.why_evidence and len(packet.plan.restaurant_ids)==1 and 'doanh thu' in sentence):continue
   metric=comparison_metric(sentence[:comp.end()],packet,facts,previous_metric);before=[r for r in mentions if r[0]<comp.start()];after=[r for r in mentions if r[0]>=comp.end()];rid=(before[-1] if before else after[0] if after else (None,None,previous_subject))[2]
   if metric in {'food_quality_positive_rate','service_negative_rate','price_negative_rate'} and not any(f.metric==metric for f in facts) and any(f.metric=='negative_aspect_mentions' for f in facts):metric='negative_aspect_mentions'
   candidates=[f for f in facts if f.metric==metric];own=[f for f in candidates if f.restaurant_id==rid]
   if metric=='negative_aspect_mentions':
    native='FOOD#QUALITY' if 'mon an' in sentence else 'DRINKS#QUALITY' if 'do uong' in sentence else 'SERVICE#GENERAL' if 'dich vu' in sentence else None
    if native:candidates=[f for f in candidates if f.label.startswith(native)];own=[f for f in candidates if f.restaurant_id==rid]
   if not own or len({f.restaurant_id for f in candidates})<2:raise ValueError('unsupported_superlative')
   negative=metric.endswith('negative_rate');want_max=comp.group(1) in {'cao nhat','lon nhat','nhieu nhat'} or comp.group(1)=='kem nhat' and negative or comp.group(1)=='tot nhat' and not negative
   target=(max if want_max else min)(f.value for f in candidates)
   if not all(f.value==target for f in own):
    correct=[f for f in candidates if f.value==target];raise ValueError('reversed_ranking:'+metric+':'+','.join(packet.restaurant_names[f.restaurant_id]+'='+f.display for f in correct))
   for a in own:
    for b in candidates:
     if a.restaurant_id!=b.restaurant_id and a.period==b.period and not comparison_supported(packet,a,b,'EQUAL' if a.value==b.value else 'HIGHER' if want_max else 'LOWER'):raise ValueError('missing_verified_comparison:'+metric)
   previous_subject=rid
   used.add(metric)
 return used

def validate_text(answer,packet,allowed_facts=None):
 if not isinstance(answer,str) or not answer.strip():raise ValueError('empty_answer')
 answer=answer.strip()
 if answer.startswith(('{','```')):raise ValueError('answer_must_be_plain_text')
 if re.search(r'^(?:the |based on )',answer,re.I):raise ValueError('answer_must_be_vietnamese')
 if re.search(r'\b(?:viet lai|ban nhap|khong noi xu huong|su dung dung ma|chi dung cac gia tri)\b',plain(answer)):raise ValueError('instruction_echo')
 if len(answer)>5000:raise ValueError('answer_too_long')
 facts=list(allowed_facts if allowed_facts is not None else packet.facts);byref={f.ref:f for f in facts};reviews={r.ref:r for r in packet.retrieved_reviews}
 refs={r.upper() for r in re.findall(r'\b[FE]\d+\b',answer,re.I)}
 if any(r not in (byref if r.startswith('F') else reviews) for r in refs):raise ValueError('unknown_citation')
 es={r for r in refs if r.startswith('E')}
 if 'synthesis_review_refs' in packet.resolved_context and not es<=set(packet.resolved_context['synthesis_review_refs']):raise ValueError('review_evidence_not_provided')
 for sentence in sentences(plain(answer)):
  cited=[reviews[r.upper()] for r in re.findall(r'\bE\d+\b',sentence,re.I) if r.upper() in reviews]
  entities={r[2] for r in entity_mentions(sentence,packet)}
  if len(entities)==1 and cited and any(r.restaurant_id not in entities for r in cited):raise ValueError('review_entity_mismatch')
 text=answer
 for quote in re.findall('[“\"]([^”\"]+)[”\"]',answer):
  if not any(quote in reviews[r].text for r in es):raise ValueError('invented_review_quote')
  text=text.replace(quote,'')
 normalized=plain(text)
 normalized=re.sub(r'\bthang\s+(20\d{2})-(0[1-9]|1[0-2])\b',lambda m:'thang '+m.group(2)+' nam '+m.group(1),normalized)
 normalized,near_zero_supported=normalize_near_zero(normalized,packet)
 if packet.plan.intent=='association' and any(f.unit=='r' and near_zero(f.value) for f in facts) and not (near_zero_supported or re.search(r'chua thay|chua du|khong.{0,35}\bro\b|lien he.{0,15}yeu|khong co lien he tuyen tinh',normalized)):raise ValueError('missing_correlation_limitation')
 if re.search(r'\b(?:mot review|mot khach|khach hang cho biet|khach hang nhan xet|phan hoi ghi|khach ghi|review ghi|khach nhac)\b',normalized) and not es:raise ValueError('review_evidence_not_cited')
 temporal_metrics=temporal_guard(normalized,packet);trend_metrics=trend_guard(normalized,packet)
 if causal_assertion(text):raise ValueError('unsupported_causal_claim')
 if packet.plan.asks_why and not re.search(r'chua du|khong du|khong the|chua the|khong co bang chung|khong chung minh|chua (?:duoc )?xac dinh|khong co nguyen nhan (?:ro rang|xac dinh duoc) tu (?:cac )?(?:quan sat|du lieu)|nguyen nhan.{0,30}(?:chua|khong).{0,20}(?:xac dinh|ro|chung minh)',normalized):raise ValueError('missing_causal_limitation')
 if packet.plan.asks_why and re.search(r'co the lien quan den',normalized):raise ValueError('speculative_causal_reason')
 for fact in facts:
  if fact.unit=='r':
   if near_zero(fact.value):
    for claim in re.finditer(r'xu huong (?:tuong dong|giong nhau)|tuong quan manh|lien he chat che',normalized):
     if not re.search(r'khong|chua',normalized[max(0,claim.start()-50):claim.start()]):raise ValueError('weak_correlation_overstated')
   if fact.value<0 and re.search(r'tuong quan duong|lien he tuyen tinh duong',normalized) or fact.value>0 and re.search(r'tuong quan am|lien he tuyen tinh am',normalized):raise ValueError('correlation_sign_mismatch')
 counts=[f for f in packet.facts if f.metric.endswith('_negative_count')]
 if packet.plan.intent=='complaint_periods' and counts and not any(f.value for f in counts):
  if re.search(r'(?:trong|nhung) thang(?:(?![.!?;]|\bkhong\b|\bchua\b).){0,65}\bco (?:nhieu )?phan nan',normalized):raise ValueError('invented_complaint_months')
  if re.search(r'(?:mac du|cho du|du) co.{0,20}phan nan',normalized):raise ValueError('invented_complaints')
  if not re.search(r'khong (?:ghi nhan|co|quan sat duoc).{0,35}(?:phan nan|luot|thang)|chua.{0,30}ghi nhan|0\s*(?:luot|phan nan)',normalized):raise ValueError('missing_zero_complaint_limitation')
 if not any('→' not in f.period for f in facts) and re.search(r'(?:doanh thu|hoat dong giao).{0,65}(?:on dinh|tang deu|giam deu|khong giam)|khong co thay doi',normalized):raise ValueError('trend_requires_period_facts')
 periods={f.period for f in facts if '→' not in f.period}
 if len(periods)<2 and not temporal_metrics and re.search(r'\b(?:tang|giam)\b.{0,65}(?:so voi|truoc do|truoc day)',normalized):raise ValueError('trend_requires_period_facts')
 for why in packet.why_evidence:
  for sentence in sentences(normalized):
   low=re.search(r'thap nhat',sentence)
   if not low:continue
   months=list(re.finditer(r'thang\s+(\d{1,2})(?:\s*(?:nam|/)\s*(20\d{2}))?',sentence))
   if months:
    chosen=min(months,key=lambda m:min(abs(m.start()-low.start()),abs(m.end()-low.start())))
    if int(chosen.group(1))!=int(why['lowest_revenue_month'][5:7]) or chosen.group(2) and chosen.group(2)!=why['lowest_revenue_month'][:4]:raise ValueError('wrong_lowest_revenue_month')
 for sentence in sentences(normalized):
  if re.search(r'khong (?:co|ghi nhan).{0,30}(?:phan hoi|review|luot|phan nan).{0,30}(?:tieu cuc|phan nan)',sentence):
   entities={r[2] for r in entity_mentions(sentence,packet)}
   zero=[f for f in facts if f.value==0 and (not entities or f.restaurant_id in entities) and (f.metric.endswith(('_negative_count','negative_rate')) or f.metric=='negative_aspect_mentions')]
   if not zero:raise ValueError('unsupported_zero_claim')
 for d in [packet.plan.start_date,packet.plan.end_date]:normalized=normalized.replace(d.isoformat(),'').replace(d.strftime('%d/%m/%Y'),'')
 def period(m):
  month=int(m.group(1));year=int(m.group(2)) if m.group(2) else None
  if not 1<=month<=12:raise ValueError('unsupported_period')
  possible=[date(y,month,1) for y in range(packet.plan.start_date.year,packet.plan.end_date.year+1) if year is None or y==year]
  if not any(packet.plan.start_date.replace(day=1)<=d<=packet.plan.end_date for d in possible):raise ValueError('unsupported_period')
  return ''
 normalized=re.sub(r'\bthang\s+(\d{1,2})(?:\s*(?:/|nam)\s*(20\d{2}))?',period,normalized)
 def quarter(m):
  q=int(m.group(1));year=int(m.group(2)) if m.group(2) else None
  if q not in {1,2,3,4} or year and not packet.plan.start_date.year<=year<=packet.plan.end_date.year:raise ValueError('unsupported_period')
  return ''
 normalized=re.sub(r'\bquy\s+(\d)(?:\s+nam\s+(20\d{2}))?',quarter,normalized)
 for year in re.findall(r'\b20\d{2}\b',normalized):
  if not packet.plan.start_date.year<=int(year)<=packet.plan.end_date.year:raise ValueError('unsupported_year')
 normalized=re.sub(r'\b20\d{2}\b','',normalized);normalized=re.sub(r'\[[FE]\d+\]','',normalized)
 normalized=re.sub(r'(?<!\d)/\s*10\b','',normalized)
 matched=[]
 for match in NUMBER.finditer(normalized):
  found=matched_facts(match,normalized,facts,packet)
  if found:matched.append((match,found));continue
  if numbers(match.group(1))=={Decimal(len(packet.plan.restaurant_ids))} and re.match(r'\s*(?:quan|nha hang)',normalized[match.end():]):continue
  raise ValueError('unsupported_number:'+match.group(0))
 comparison_metrics=validate_comparisons(temporal_comparison_text(normalized),packet,packet.facts)|temporal_metrics|trend_metrics
 if near_zero_supported:comparison_metrics.update(f.metric for f in facts if f.unit=='r' and near_zero(f.value))
 coverage_guard(normalized,packet,matched,comparison_metrics)
 return answer
