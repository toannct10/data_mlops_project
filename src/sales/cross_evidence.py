"""Pure, read-only analytical claim contracts derived from canonical packets.

No SQL mutations, inference, index writes or dataset-specific answer strings.
"""
import json,math,itertools
from collections import defaultdict
from pathlib import Path
from functools import lru_cache
from src.sales.cross_plan import Fact
from src.sales.semantic_plan import plain

@lru_cache(maxsize=1)
def policy():
 value=json.loads((Path(__file__).resolve().parents[2]/'config/cross_analysis_validation.json').read_text())
 assert 0<=value['near_zero_correlation_epsilon']<1
 assert 0<=value['approx_equal_relative_tolerance']<.1
 return value

def near_zero(value):return abs(float(value))<=policy()['near_zero_correlation_epsilon']

def approximate(a,b):
 p=policy();return abs(a-b)<=max(p['approx_equal_absolute_tolerance'],p['approx_equal_relative_tolerance']*max(abs(a),abs(b)))

def comparison_contract(facts):
 groups=defaultdict(list);result=[]
 for f in facts:
  facet=f.label.split()[0] if f.metric=='negative_aspect_mentions' else None
  groups[(f.metric,f.period,facet)].append(f)
 for (metric,period,facet),group in groups.items():
  for a,b in itertools.combinations(group,2):
   if a.restaurant_id==b.restaurant_id:continue
   result.append({'ref':'C'+str(len(result)+1),'metric':metric,'period':period,'aspect':facet,'left_restaurant_id':a.restaurant_id,'right_restaurant_id':b.restaurant_id,'left_value':a.value,'right_value':b.value,'left_fact_ref':a.ref,'right_fact_ref':b.ref,'relation':'HIGHER' if a.value>b.value else 'LOWER' if a.value<b.value else 'EQUAL','difference':a.value-b.value,'approx_equal':approximate(a.value,b.value)})
 return result

def add_fact(packet,rid,period,metric,value,unit,label,support=None):
 if value is None:return
 old=next((f for f in packet.facts if f.restaurant_id==rid and f.period==str(period) and f.metric==metric and (metric!='negative_aspect_mentions' or f.label==label)),None)
 if old:
  assert math.isclose(old.value,float(value),rel_tol=1e-10,abs_tol=1e-7),(metric,old.value,value)
  return
 display=f'{value*100:.2f}%' if unit=='percent' else f'{value:.2f}/10' if unit=='rating/10' else f'{value:.3f}' if unit=='r' else f'{value:,.0f}'.replace(',','.')
 packet.facts.append(Fact(ref='F'+str(len(packet.facts)+1),restaurant_id=rid,period=str(period),metric=metric,label=label,value=float(value),unit=unit,display=display,support=support))

def required_domains(packet):
 q=plain(packet.question)
 sales=any(w in q for w in ['doanh thu','sales','don hang','so don']) or packet.plan.asks_why
 review=any(w in q for w in ['review','danh gia','rating','food quality','dich vu','phan nan','che']) or packet.plan.negative_focus or packet.plan.intent=='aspect_ranking'
 if not sales and not review:sales=review=True
 if packet.plan.asks_why:review=True
 return [d for d,needed in [('sales',sales),('review',review)] if needed]

def enrich_packet(packet):
 packet.trend_facts=[];packet.food_quality_facts=[];packet.why_evidence=[];packet.temporal_comparisons=[]
 packet.semantic_policy=dict(policy());packet.required_domains=required_domains(packet)
 period=str(packet.plan.start_date)+' → '+str(packet.plan.end_date)
 if 'FOOD#QUALITY' in packet.plan.aspects:
  for rid in packet.plan.restaurant_ids:
   rows=[a for a in packet.aspect_metrics if a['restaurant_id']==rid and a['aspect']=='FOOD#QUALITY'];n=sum(a['mentions'] for a in rows);pos=sum(a['positive_count'] for a in rows);neg=sum(a['negative_count'] for a in rows);neutral=n-pos-neg
   if not n:continue
   for suffix,value,unit,label in [('positive_rate',pos/n,'percent','Tỷ lệ Food Quality tích cực'),('neutral_rate',neutral/n,'percent','Tỷ lệ Food Quality trung tính'),('negative_rate',neg/n,'percent','Tỷ lệ Food Quality tiêu cực'),('mentions',n,'mentions','Số lượt nhắc Food Quality')]:add_fact(packet,rid,period,'food_quality_'+suffix,value,unit,label,n if suffix.endswith('rate') else None)
   packet.food_quality_facts.append({'restaurant_id':rid,'period':period,'mentioned_count':n,'positive_count':pos,'neutral_count':neutral,'negative_count':neg,'positive_rate':pos/n,'neutral_rate':neutral/n,'negative_rate':neg/n})
 if packet.plan.intent=='association':
  for rid in packet.plan.restaurant_ids:
   sales={str(r['period']):r for r in packet.sales_metrics if r.get('restaurant_id')==rid and r.get('orders')};reviews={str(r['period']):r for r in packet.review_metrics if r.get('restaurant_id')==rid and r.get('average_rating_10') is not None};keys=sorted(sales.keys()&reviews.keys());movements=[]
   for prev,cur in zip(keys,keys[1:]):
    if packet.plan.grouping=='monthly' and (int(cur[:4])-int(prev[:4]))*12+int(cur[5:7])-int(prev[5:7])!=1:continue
    rv=sales[cur]['delivered_revenue']-sales[prev]['delivered_revenue'];rt=reviews[cur]['average_rating_10']-reviews[prev]['average_rating_10'];sx=(rv>0)-(rv<0);sy=(rt>0)-(rt<0)
    movements.append({'previous_period':prev,'period':cur,'revenue_direction':sx,'rating_direction':sy,'same_direction':sx==sy and sx!=0,'opposite_direction':sx*sy==-1})
    for metric,previous,current in [('delivered_revenue',sales[prev]['delivered_revenue'],sales[cur]['delivered_revenue']),('average_rating_10',reviews[prev]['average_rating_10'],reviews[cur]['average_rating_10'])]:packet.temporal_comparisons.append({'restaurant_id':rid,'metric':metric,'previous_period':prev,'period':cur,'previous_period_value':previous,'current_period_value':current,'verified_change_pct':(current-previous)/abs(previous) if previous else None,'direction':'HIGHER' if current>previous else 'LOWER' if current<previous else 'EQUAL'})
   if not keys:continue
   for key in keys:
    add_fact(packet,rid,key,'delivered_revenue',sales[key]['delivered_revenue'],'VND','Doanh thu Delivered')
    add_fact(packet,rid,key,'average_rating_10',reviews[key]['average_rating_10'],'rating/10','Rating trung bình /10')
   same=sum(m['same_direction'] for m in movements);opposite=sum(m['opposite_direction'] for m in movements);flat=len(movements)-same-opposite
   corr=next((r['pearson_r'] for r in packet.trend_metrics if r['restaurant_id']==rid and r['y']=='average_rating_10'),None)
   packet.trend_facts.append({'restaurant_id':rid,'period':period,'same_direction_months':same,'opposite_direction_months':opposite,'flat_intervals':flat,'movement_intervals':len(movements),'correlation':corr,'near_zero':near_zero(corr) if corr is not None else None,'revenue_trend':'UP' if sales[keys[-1]]['delivered_revenue']>sales[keys[0]]['delivered_revenue'] else 'DOWN' if sales[keys[-1]]['delivered_revenue']<sales[keys[0]]['delivered_revenue'] else 'FLAT','rating_trend':'UP' if reviews[keys[-1]]['average_rating_10']>reviews[keys[0]]['average_rating_10'] else 'DOWN' if reviews[keys[-1]]['average_rating_10']<reviews[keys[0]]['average_rating_10'] else 'FLAT','movements':movements})
   for metric,value in [('same_direction_months',same),('opposite_direction_months',opposite),('movement_intervals',len(movements))]:add_fact(packet,rid,period,metric,value,'intervals',metric)
 if packet.plan.asks_why:
  for rid in packet.plan.restaurant_ids:
   sales=[r for r in packet.sales_metrics if r.get('restaurant_id')==rid and r.get('orders')]
   if not sales:continue
   low=min(sales,key=lambda r:(r['delivered_revenue'],str(r['period'])));month=str(low['period']);review=next((r for r in packet.review_metrics if r['restaurant_id']==rid and str(r['period'])==month),{});valid=review.get('valid_absa_count',0);rate=review.get('negative_count',0)/valid if valid else None
   aspects=sorted([a for a in packet.aspect_metrics if a['restaurant_id']==rid and str(a['period'])==month and a['negative_count']>0],key=lambda a:(-a['negative_count'],a['aspect']))
   packet.why_evidence.append({'restaurant_id':rid,'lowest_revenue_month':month[:7],'revenue_amount':low['delivered_revenue'],'order_count':low['orders'],'delivered_order_count':low['delivered_orders'],'average_rating_10':review.get('average_rating_10'),'negative_review_rate':rate,'review_count':review.get('review_count',0),'cancelled_orders':low['cancelled_orders'],'refunded_orders':low['refunded_orders'],'dominant_negative_aspects':aspects[:3],'representative_review_ids':[r.review_id for r in packet.retrieved_reviews if r.restaurant_id==rid],'cause_available':False})
   for metric,value,unit,label in [('delivered_revenue',low['delivered_revenue'],'VND','Doanh thu Delivered'),('orders',low['orders'],'orders','Số đơn hàng'),('delivered_orders',low['delivered_orders'],'orders','Số đơn giao thành công'),('average_rating_10',review.get('average_rating_10'),'rating/10','Điểm đánh giá trung bình /10'),('negative_rate',rate,'percent','Tỷ lệ review tiêu cực'),('review_count',review.get('review_count',0),'reviews','Số review'),('cancelled_orders',low['cancelled_orders'],'orders','Số đơn hủy'),('refunded_orders',low['refunded_orders'],'orders','Số đơn hoàn tiền')]:add_fact(packet,rid,month,metric,value,unit,label,valid if metric=='negative_rate' else None)
   for aspect in aspects[:3]:add_fact(packet,rid,month,'negative_aspect_mentions',aspect['negative_count'],'mentions',aspect['aspect']+' số lượt tiêu cực',aspect['mentions'])
 packet.comparison_facts=comparison_contract(packet.facts)
 return packet
