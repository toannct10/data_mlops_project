"""Audit then import paired files into existing canonical facts; raw comments stay exact."""
import argparse,csv,hashlib,json
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from src.warehouse.loader import connection

DATASET='paired_3restaurant_2025_2026'
LIVE_IDS={'145','1803','2642'}
def fingerprint(obj):return hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
def read_pair(sales_path,reviews_path):
 def read(p):
  with Path(p).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
 sales=read(sales_path);reviews=read(reviews_path)
 assert len(sales)==len(reviews)==5400,'Expected the provided 5400-row pair'
 assert len({s['order_id'] for s in sales})==len(sales),'Duplicate source order IDs'
 assert len({r['order_id'] for r in reviews})==len(reviews),'Duplicate source review order IDs'
 by_order={s['order_id']:s for s in sales}
 assert set(by_order)=={r['order_id'] for r in reviews},'Unmatched source order IDs'
 assert {s['restaurant_id'] for s in sales}==LIVE_IDS
 for s in sales:
  s['_date']=datetime.strptime(s['order_date'],'%d/%m/%Y').date();s['_date_key']=int(s['_date'].strftime('%Y%m%d'));s['_amount']=Decimal(s['sales_amount']);s['_hash']=fingerprint({k:v for k,v in s.items() if not k.startswith('_')})
  assert s['order_status'] in {'Delivered','Cancelled','Refunded'} and s['_amount']>=0
 for r in reviews:
  r['_date']=datetime.strptime(r['Review Time'],'%d/%m/%Y').date();r['_date_key']=int(r['_date'].strftime('%Y%m%d'));r['_rating']=Decimal(r['Rating']);r['_hash']=fingerprint({k:v for k,v in r.items() if not k.startswith('_')})
  assert r['RestaurantID']==by_order[r['order_id']]['restaurant_id'],'Restaurant mismatch'
  assert r['_date']==by_order[r['order_id']]['_date'],'Order/review date mismatch'
  assert 0<=r['_rating']<=10 and r['Comment'] is not None
 assert min(s['_date'].isoformat() for s in sales)=='2025-01-01' and max(s['_date'].isoformat() for s in sales)=='2026-06-30'
 return sales,reviews

def counts(conn):return dict(conn.execute("SELECT 'orders',count(*) FROM fact_order UNION ALL SELECT 'reviews',count(*) FROM fact_review_v2"))
def audit(conn,sales,reviews):
 existing={r[0]:r[1:] for r in conn.execute('SELECT order_id,restaurant_id,date_key,sales_amount,order_status FROM fact_order WHERE order_id=ANY(%s)',([s['order_id'] for s in sales],))}
 sc=Counter(new=0,existing=0,conflicting=0);rc=Counter(new=0,existing=0,conflicting=0);conflicts=[];mapping={};claimed=set();collisions=[]
 for item in sales:
  source_id=item['order_id'];old=existing.get(source_id);wanted=(item['restaurant_id'],item['_date_key'],item['_amount'],item['order_status'])
  canonical=source_id
  if old is not None and old!=wanted:
   canonical='PAIR-'+hashlib.sha256((DATASET+':'+source_id+':'+item['restaurant_id']+':'+item['_date'].isoformat()).encode()).hexdigest()[:40]
   collisions.append({'source_order_id':source_id,'canonical_order_id':canonical,'restaurant_id':item['restaurant_id'],'order_date':item['_date'].isoformat(),'existing_restaurant_id':old[0],'existing_core_sha256':fingerprint(old)})
  item['_canonical_order_id']=canonical
  current=conn.execute('SELECT restaurant_id,date_key,sales_amount,order_status FROM fact_order WHERE order_id=%s',(canonical,)).fetchone()
  kind='new' if current is None else 'existing' if current==wanted else 'conflicting';sc[kind]+=1
  if kind=='conflicting':conflicts.append({'entity':'canonical_order','id':canonical})
 by_source={item['order_id']:item['_canonical_order_id'] for item in sales}
 for item in reviews:item['_canonical_order_id']=by_source[item['order_id']]
 for r in reviews:
  row=conn.execute('SELECT review_id,restaurant_id,date_key,rating,review_text FROM fact_review_v2 WHERE order_id=%s',(r['_canonical_order_id'],)).fetchone()
  if row is None:
   matches=conn.execute('SELECT review_id,restaurant_id,date_key,rating,review_text FROM fact_review_v2 WHERE order_id IS NULL AND restaurant_id=%s AND date_key=%s AND rating=%s AND review_text=%s',(r['RestaurantID'],r['_date_key'],r['_rating'],r['Comment'])).fetchall()
   if len(matches)>1:rc['conflicting']+=1;conflicts.append({'entity':'ambiguous_review','order_id':r['order_id']});continue
   row=matches[0] if matches else None
  wanted=(r['RestaurantID'],r['_date_key'],r['_rating'],r['Comment'])
  if row is not None and (row[1:]!=wanted or row[0] in claimed):rc['conflicting']+=1;conflicts.append({'entity':'review','order_id':r['order_id']});continue
  rid=row[0] if row else 'paired_'+hashlib.sha256((DATASET+':'+r['order_id']).encode()).hexdigest()
  mapping[r['order_id']]=rid;claimed.add(rid);rc['existing' if row else 'new']+=1
 return {'source_sales':len(sales),'source_reviews':len(reviews),'matched_order_ids':len(set(s['order_id'] for s in sales)&set(r['order_id'] for r in reviews)),'restaurant_mismatches':0,'sales':dict(sc),'reviews':dict(rc),'conflicts':conflicts,'canonical_before':counts(conn),'raw_order_id_collisions':len(collisions),'resolved_collisions':collisions,'unresolved_conflicts':len(conflicts)},mapping

def apply(conn,sales,reviews,report,mapping):
 if report['conflicts']:raise ValueError('Conflicting or ambiguous input; nothing imported')
 # Only this additive migration; never re-run unrelated cloud migrations.
 version='010'
 if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=%s',(version,)).fetchone():
  path=Path('sql/postgres/migrations/010_canonical_paired_cross.sql');conn.execute(path.read_text(encoding='utf-8-sig'));conn.execute('INSERT INTO schema_migrations(version,filename) VALUES (%s,%s)',(version,path.name))
 for s in sales:
  d=s['_date'];conn.execute('INSERT INTO dim_date(date_key,calendar_date,day,month,year) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(s['_date_key'],d,d.day,d.month,d.year))
  conn.execute("INSERT INTO fact_order(order_id,restaurant_id,date_key,sales_amount,total_amount,order_status,source,source_dataset,source_order_id,source_row_sha256) VALUES (%s,%s,%s,%s,%s,%s,'historical',%s,%s,%s) ON CONFLICT(order_id) DO UPDATE SET source_dataset=COALESCE(fact_order.source_dataset,EXCLUDED.source_dataset),source_order_id=COALESCE(fact_order.source_order_id,EXCLUDED.source_order_id),source_row_sha256=COALESCE(fact_order.source_row_sha256,EXCLUDED.source_row_sha256)",(s['_canonical_order_id'],s['restaurant_id'],s['_date_key'],s['_amount'],s['_amount'],s['order_status'],DATASET,s['order_id'],s['_hash']))
 for r in reviews:
  rid=mapping[r['order_id']];stamp=datetime.combine(r['_date'],datetime.min.time(),tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
  conn.execute("INSERT INTO fact_review_v2(review_id,restaurant_id,date_key,rating,review_text,order_id,source,source_dataset,source_order_id,source_row_sha256,review_address,source_review_time,review_timestamp) VALUES (%s,%s,%s,%s,%s,%s,'historical',%s,%s,%s,%s,%s,%s) ON CONFLICT(review_id) DO UPDATE SET order_id=COALESCE(fact_review_v2.order_id,EXCLUDED.order_id),source_dataset=COALESCE(fact_review_v2.source_dataset,EXCLUDED.source_dataset),source_order_id=COALESCE(fact_review_v2.source_order_id,EXCLUDED.source_order_id),source_row_sha256=COALESCE(fact_review_v2.source_row_sha256,EXCLUDED.source_row_sha256),review_address=COALESCE(fact_review_v2.review_address,EXCLUDED.review_address),source_review_time=COALESCE(fact_review_v2.source_review_time,EXCLUDED.source_review_time),review_timestamp=COALESCE(fact_review_v2.review_timestamp,EXCLUDED.review_timestamp)",(rid,r['RestaurantID'],r['_date_key'],r['_rating'],r['Comment'],r['_canonical_order_id'],DATASET,r['order_id'],r['_hash'],r['Address'],r['Review Time'],stamp))
 conn.execute("UPDATE dim_sales_restaurant SET is_live_demo=TRUE WHERE restaurant_id=ANY(%s)",(list(LIVE_IDS),))
 for collision in report['resolved_collisions']:
  old=conn.execute('SELECT restaurant_id,date_key,sales_amount,order_status FROM fact_order WHERE order_id=%s',(collision['source_order_id'],)).fetchone()
  assert fingerprint(old)==collision['existing_core_sha256'],'Original historical order changed'
 report['historical_collision_rows_unchanged']=True
 report['canonical_after']=counts(conn)
 report['linked_pairs']=conn.execute('SELECT count(*) FROM fact_order o JOIN fact_review_v2 r ON r.order_id=o.order_id AND r.restaurant_id=o.restaurant_id WHERE o.order_id=ANY(%s)',([s['_canonical_order_id'] for s in sales],)).fetchone()[0]
 assert report['linked_pairs']==len(sales)
 report['duplicate_imported_order_ids']=conn.execute('SELECT count(*)-count(DISTINCT order_id) FROM fact_review_v2 WHERE order_id=ANY(%s)',([s['_canonical_order_id'] for s in sales],)).fetchone()[0]
 assert report['duplicate_imported_order_ids']==0
 return report

def main():
 p=argparse.ArgumentParser();p.add_argument('--sales',required=True);p.add_argument('--reviews',required=True);p.add_argument('--apply',action='store_true');p.add_argument('--report',default='reports/paired_import/audit.json');a=p.parse_args();sales,reviews=read_pair(a.sales,a.reviews)
 with connection() as conn:
  report,mapping=audit(conn,sales,reviews)
  if a.apply:apply(conn,sales,reviews,report,mapping)
 report['input_files']=[{'name':Path(f).name,'sha256':hashlib.sha256(Path(f).read_bytes()).hexdigest()} for f in [a.sales,a.reviews]]
 out=Path(a.report);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 if report['resolved_collisions']:
  with (out.parent/'collision_mapping.csv').open('w',encoding='utf-8',newline='') as f:
   writer=csv.DictWriter(f,fieldnames=list(report['resolved_collisions'][0]));writer.writeheader();writer.writerows(report['resolved_collisions'])
 print(json.dumps({k:v for k,v in report.items() if k not in {'resolved_collisions','conflicts'}},ensure_ascii=True))
if __name__=='__main__':main()
