"""Resume production inference for imported, not-yet-valid canonical reviews only."""
import argparse,json,time
from collections import Counter
from pathlib import Path
from src.warehouse.loader import connection
from src.canonical.paired_import import DATASET

def enrich(*,device='cpu',batch_size=16,limit=0):
 import torch
 from psycopg.types.json import Jsonb
 from src.foody.review_models.phobert_absa_adapter import PhoBERTABSAAdapter
 torch.set_num_threads(4)
 adapter=PhoBERTABSAAdapter();started=time.monotonic();counts=Counter()
 with connection() as conn:
  counts['already_valid']=conn.execute("SELECT count(*) FROM fact_review_v2 WHERE source_dataset=%s AND (absa_model_version IS NOT NULL OR relevance_label='NOT_RELEVANT')",(DATASET,)).fetchone()[0]
  pending=conn.execute("SELECT review_id,review_text FROM fact_review_v2 WHERE source_dataset=%s AND absa_model_version IS NULL AND relevance_label IS DISTINCT FROM 'NOT_RELEVANT' ORDER BY review_id LIMIT %s",(DATASET,limit or 1000000)).fetchall()
  conn.commit()
  for pos in range(0,len(pending),batch_size):
   batch=pending[pos:pos+batch_size];predictions=adapter.predict_batch([text for _,text in batch],device=device)
   for (rid,_),result in zip(batch,predictions,strict=True):
    relevant=result['relevance_label']=='RELEVANT';version=result['metadata']['version'] if relevant else None
    native=result.get('raw_output',{}).get('native_aspect_predictions',[])
    aspects=result['aspects'];labels=set(a['label'] for a in aspects)
    for label in list(labels):
     if label.startswith('SERVICE#'):labels.add('service')
     if label.startswith('FOOD#'):labels.add('food')
     if label.endswith('#PRICES'):labels.add('price')
     if label.startswith('AMBIENCE#'):labels.add('ambience')
    conn.execute("UPDATE fact_review_v2 SET relevance_label=%s,relevance_score=%s,relevance_model=%s,relevance_model_version=%s,relevance_reason=%s,aspect_status=%s,overall_sentiment=%s,overall_sentiment_reason=%s,absa_model_version=%s,absa_predictions=%s,predicted_sentiment=%s,predicted_aspects=%s WHERE review_id=%s AND absa_model_version IS NULL AND relevance_label IS DISTINCT FROM 'NOT_RELEVANT'",(result['relevance_label'],result.get('relevance_score'),result.get('relevance_model'),result.get('relevance_model_version'),result.get('relevance_reason'),result['aspect_status'],result['overall_sentiment'],result.get('reason'),version,Jsonb(native),result['overall_sentiment'].lower() if relevant else None,Jsonb(sorted(labels)),rid))
    for a in aspects:
     conn.execute("INSERT INTO fact_review_aspect(review_id,aspect,sentiment,score,model_name,model_version) VALUES (%s,%s,%s,%s,'PhoBERT ABSA',%s) ON CONFLICT(review_id,aspect,model_version) DO NOTHING",(rid,a['label'],a['sentiment'],a['score'],version))
    counts['phobert_executed' if relevant else 'not_relevant_skipped']+=1
   conn.commit()
   if pos%(batch_size*10)==0:print(json.dumps({'processed':pos+len(batch),'pending_initial':len(pending),'seconds':round(time.monotonic()-started,1)}),flush=True)
 counts['pending_initial']=len(pending);counts['seconds']=round(time.monotonic()-started,3)
 result=dict(counts);Path('reports/paired_import/enrichment.json').write_text(json.dumps(result,indent=2)+'\n');return result

def main():
 p=argparse.ArgumentParser();p.add_argument('--device',default='cpu');p.add_argument('--batch-size',type=int,default=16);p.add_argument('--limit',type=int,default=0);a=p.parse_args();print(json.dumps(enrich(device=a.device,batch_size=a.batch_size,limit=a.limit)))
if __name__=='__main__':main()
