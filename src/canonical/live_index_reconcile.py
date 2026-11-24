"""Audit legacy live eligibility and resume only missing shared-index documents.

Never updates canonical rows, relevance labels, IDs or PhoBERT predictions.
Use vector phase in the existing Linux backend; lexical phase in native Windows.
"""
import argparse,hashlib,json,os,sqlite3
from pathlib import Path

def id_hash(ids):return hashlib.sha256('\n'.join(sorted(ids)).encode()).hexdigest()

def vectors(output):
 from src.warehouse.loader import connection
 from src.foody.review_runtime import VECTOR
 from src.foody.review_models.relevance import relevance_gate
 from src.rag.vector_store import client,chroma_metadata
 from src.foody.schema import save_json
 from src.ordering.index import _document,_embedding
 manifest_path=VECTOR/'index_manifest.json';manifest=json.loads(manifest_path.read_text());collection=client(VECTOR).get_collection(manifest['collection']);existing=set(collection.get(include=[])['ids']);eligible=set();excluded=[];live=[]
 with connection() as conn:
  rows=conn.execute('SELECT review_id,review_text,source,relevance_label,absa_model_version FROM fact_review_v2 ORDER BY review_id').fetchall()
  for rid,text,source,relevance,model in rows:
   reason=None;derived=relevance
   if not text or not text.strip() or '\ufffd' in text:reason='EMPTY_OR_REPLACEMENT_CHARACTER'
   elif relevance=='NOT_RELEVANT':reason='CANONICAL_NOT_RELEVANT'
   elif not model:reason='NO_ABSA_ENRICHMENT'
   elif source=='live_web' and relevance is None:
    derived=relevance_gate(text)['relevance_label']
    if derived=='NOT_RELEVANT':reason='LEGACY_LIVE_GATE_NOT_RELEVANT'
   if reason:excluded.append({'review_id':rid,'reason':reason})
   else:eligible.add(rid)
   if source=='live_web':live.append({'review_id':rid,'canonical_relevance_status':relevance,'evaluated_relevance_status':derived,'indexable':not bool(reason),'before_in_chroma':rid in existing,'exclusion_reason':reason,'missing_reason':None if rid in existing else 'Legacy live row absent from shared indexes; paired-only sync excluded live_web. Original event failure not durably recorded.'})
  missing=eligible-existing
  assert not existing-eligible,('Existing index has unexpected ineligible IDs; no deletion performed',sorted(existing-eligible))
  assert missing<={r['review_id'] for r in live},'Unexpected historical gap; refusing a broad rebuild'
  docs=[_document(conn,rid) for rid in sorted(missing)]
 if docs:
  embedder=_embedding();embeddings,_=embedder.documents([d['text'] for d in docs])
  collection.upsert(ids=[d['id'] for d in docs],documents=[d['text'] for d in docs],embeddings=embeddings.tolist(),metadatas=[chroma_metadata(d) for d in docs])
 actual=set(collection.get(include=[])['ids']);assert actual==eligible
 manifest['document_count']=collection.count();save_json(manifest_path,manifest)
 report={'status':'VECTORS_VERIFIED','canonical_count':len(rows),'eligible_canonical_count':len(eligible),'eligible_ids_sha256':id_hash(eligible),'before_chroma':len(existing),'after_chroma':len(actual),'vector_upserts':len(docs),'live_reviews':live,'intentional_exclusions':excluded,'pending_lexical_documents':docs,'canonical_rows_modified':0,'full_rebuild':False}
 Path(output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in report.items() if k not in {'live_reviews','intentional_exclusions','pending_lexical_documents'}}))

def lexical(audit_path):
 from src.foody.review_runtime import VECTOR,LEXICAL
 from src.foody.review_paths import DOCUMENTS
 from src.foody.schema import digest,save_json
 # Pure metadata conversion has no need for the Chroma client on the host.
 from src.rag.vector_store import chroma_metadata
 report=json.loads(Path(audit_path).read_text(encoding='utf-8'));docs=report['pending_lexical_documents'];source={d['id']:d for d in map(json.loads,DOCUMENTS.read_text(encoding='utf-8').splitlines())}
 with sqlite3.connect(LEXICAL/'reviews.sqlite',timeout=30) as db:
  assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
  before={r[0] for r in db.execute('SELECT review_id FROM reviews')};inserted=0
  for doc in docs:
   if doc['id'] in before:continue
   metadata=chroma_metadata(doc)
   db.execute('INSERT INTO reviews_fts(review_id,text) VALUES (?,?)',(doc['id'],doc['text']))
   db.execute('INSERT INTO reviews(review_id,restaurant_key,restaurant_id,sentiment,timestamp_epoch,metadata_json) VALUES (?,?,?,?,?,?)',(doc['id'],None,metadata['restaurant_id'],metadata['predicted_sentiment'],metadata['timestamp_epoch'],json.dumps(metadata,ensure_ascii=False)))
   db.executemany('INSERT INTO review_aspects(review_id,aspect) VALUES (?,?)',[(doc['id'],a) for a in doc['metadata']['predicted_aspects']]);inserted+=1
  after={r[0] for r in db.execute('SELECT review_id FROM reviews')};assert id_hash(after)==report['eligible_ids_sha256']
  for doc in docs:assert db.execute('SELECT text FROM reviews_fts WHERE review_id=?',(doc['id'],)).fetchone()[0]==doc['text']
  db.commit()
 changed=False
 for doc in docs:
  if doc['id'] not in source:source[doc['id']]=doc;changed=True
 assert id_hash(source)==report['eligible_ids_sha256']
 if changed:
  temp=DOCUMENTS.with_suffix('.live-sync-tmp')
  with temp.open('w',encoding='utf-8',newline='\n') as f:
   for doc in source.values():f.write(json.dumps(doc,ensure_ascii=False)+'\n')
  os.replace(temp,DOCUMENTS)
 vm=VECTOR/'index_manifest.json';lm=LEXICAL/'index_manifest.json';v=json.loads(vm.read_text());l=json.loads(lm.read_text());assert v['document_count']==len(after)
 v['documents_sha256']=digest(DOCUMENTS);l.update(document_count=len(after),source_sha256=digest(DOCUMENTS));save_json(vm,v);save_json(lm,l)
 report.update(status='PASS',before_bm25=len(before),after_bm25=len(after),lexical_upserts=inserted,registry_changed=changed,registry_count=len(source))
 Path(audit_path).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in report.items() if k not in {'live_reviews','intentional_exclusions','pending_lexical_documents'}}))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--phase',choices=['vectors','lexical'],required=True);p.add_argument('--report',required=True);a=p.parse_args()
 if a.phase=='vectors':vectors(a.report)
 else:lexical(a.report)
