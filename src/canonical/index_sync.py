"""Resumable canonical index upserts with native Windows BM25 support."""
import argparse,json,sqlite3,time,os
from pathlib import Path
from zoneinfo import ZoneInfo
from src.canonical.paired_import import DATASET
from src.warehouse.loader import connection
from src.foody.review_runtime import VECTOR,LEXICAL
from src.foody.review_paths import DOCUMENTS
from src.foody.schema import digest,save_json
from src.rag.vector_store import client,chroma_metadata

def report(name,value):
 root=Path(os.getenv('FOODY_IMPORT_REPORT_DIR','reports/paired_import'));root.mkdir(parents=True,exist_ok=True);(root/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n');return value

def import_documents():
 with connection() as conn:
  source=conn.execute("SELECT r.review_id,r.restaurant_id,s.restaurant_name,r.review_text,r.rating,r.overall_sentiment,r.predicted_aspects,r.review_timestamp,d.calendar_date FROM fact_review_v2 r JOIN dim_sales_restaurant s USING(restaurant_id) JOIN dim_date d USING(date_key) WHERE r.source_dataset=%s AND r.relevance_label='RELEVANT' AND r.absa_model_version IS NOT NULL ORDER BY r.review_id",(DATASET,)).fetchall()
 docs=[]
 for rid,rest,name,text,rating,sentiment,aspects,stamp,day in source:
  assert text.strip() and '\ufffd' not in text,'Corrupt text is not indexable'
  when=stamp.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).replace(tzinfo=None).isoformat() if stamp else day.isoformat()
  docs.append({'id':rid,'text':text,'metadata':{'review_id':rid,'restaurant_id':rest,'restaurant_name':name,'review_time':when,'rating':float(rating),'predicted_sentiment':sentiment.lower(),'predicted_aspects':aspects,'detected_issues':[],'source':'foody_review_2025_2026','source_dataset':DATASET}})
 return docs

def sync_vectors(batch_size=128):
 from src.ordering.index import _embedding
 vm=VECTOR/'index_manifest.json';v=json.loads(vm.read_text());c=client(VECTOR).get_collection(v['collection']);before=c.count();existing=set(c.get(include=[])['ids']);docs=import_documents();pending=[d for d in docs if d['id'] not in existing];embedder=_embedding() if pending else None
 for pos in range(0,len(pending),batch_size):
  chunk=pending[pos:pos+batch_size];vectors,_=embedder.documents([d['text'] for d in chunk]);c.upsert(ids=[d['id'] for d in chunk],documents=[d['text'] for d in chunk],embeddings=vectors.tolist(),metadatas=[chroma_metadata(d) for d in chunk]);print(json.dumps({'vectors_resumed':pos+len(chunk),'remaining_initial':len(pending)}),flush=True)
 found=c.get(ids=[d['id'] for d in docs],include=['documents']);assert dict(zip(found['ids'],found['documents']))=={d['id']:d['text'] for d in docs}
 initial=v['document_count'];v['document_count']=c.count();save_json(vm,v)
 return report('vector_sync',{'status':'PASS','initial_manifest_count':initial,'actual_before_resume':before,'after':c.count(),'eligible_imported_reviews':len(docs),'upserted_this_run':len(pending),'all_imported_ids_and_text_match':True,'full_rebuild':False})

def sync_lexical():
 vm=VECTOR/'index_manifest.json';lm=LEXICAL/'index_manifest.json';v=json.loads(vm.read_text());l=json.loads(lm.read_text());docs=import_documents();old={d['id']:d for d in map(json.loads,DOCUMENTS.read_text(encoding='utf-8').splitlines())};before_docs=len(old);before_hash=digest(DOCUMENTS)
 with sqlite3.connect(LEXICAL/'reviews.sqlite',timeout=30) as db:
  assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
  existing={r[0] for r in db.execute('SELECT review_id FROM reviews')};pending=[d for d in docs if d['id'] not in existing]
  for doc in pending:
   rid=doc['id'];m=chroma_metadata(doc)
   for table in ['reviews_fts','review_aspects','reviews']:db.execute('DELETE FROM '+table+' WHERE review_id=?',(rid,))
   db.execute('INSERT INTO reviews_fts(review_id,text) VALUES (?,?)',(rid,doc['text']))
   db.execute('INSERT INTO reviews(review_id,restaurant_key,restaurant_id,sentiment,timestamp_epoch,metadata_json) VALUES (?,?,?,?,?,?)',(rid,None,m['restaurant_id'],m['predicted_sentiment'],m.get('timestamp_epoch'),json.dumps(m,ensure_ascii=False)))
   db.executemany('INSERT INTO review_aspects(review_id,aspect) VALUES (?,?)',[(rid,a) for a in doc['metadata']['predicted_aspects']])
  db.commit();after=db.execute('SELECT count(*) FROM reviews').fetchone()[0]
  found={}
  for pos in range(0,len(docs),500):
   chunk=docs[pos:pos+500];found.update(db.execute('SELECT review_id,text FROM reviews_fts WHERE review_id IN ('+','.join('?' for _ in chunk)+')',[d['id'] for d in chunk]))
  assert found=={d['id']:d['text'] for d in docs}
 for doc in docs:old[doc['id']]=doc
 assert after==v['document_count']==len(old)
 temp=DOCUMENTS.with_suffix('.import-tmp')
 with temp.open('w',encoding='utf-8',newline='\n') as f:
  for doc in old.values():f.write(json.dumps(doc,ensure_ascii=False)+'\n')
 os.replace(temp,DOCUMENTS)
 v.update(documents_sha256=digest(DOCUMENTS));l.update(document_count=after,source_sha256=digest(DOCUMENTS),path=str(LEXICAL/'reviews.sqlite'));save_json(vm,v);save_json(lm,l)
 result={'status':'PASS','before_chroma_original':before_docs,'before_bm25_original':before_docs,'before_bm25_resume':len(existing),'after_chroma':v['document_count'],'after_bm25':after,'eligible_imported_reviews':len(docs),'new_eligible_documents_total':len(old)-before_docs,'lexical_upserted_this_run':len(pending),'source_registry_unchanged':before_hash==digest(DOCUMENTS),'all_imported_ids_and_raw_text_match':True,'full_index_rebuild':False}
 return report('index_sync',result)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--phase',choices=['vectors','lexical','all'],default='all');a=p.parse_args()
 if a.phase in ['all','vectors']:print(json.dumps(sync_vectors()))
 if a.phase in ['all','lexical']:print(json.dumps(sync_lexical()))
