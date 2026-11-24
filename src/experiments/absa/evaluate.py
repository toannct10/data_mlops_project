"""Single final evaluator and immutable completed-evaluation ledger."""
import hashlib
import json
from pathlib import Path
from time import perf_counter
import numpy as np
import pandas as pd
from src.experiments.absa.data import ASPECTS,LABELS,save
from src.experiments.absa.metrics import score,overall,sentiment_score


def file_hash(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()


def plot_matrix(matrix,path,title,labels=LABELS):
    import matplotlib;matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(6,5),layout='constrained')
    ax.imshow(matrix,cmap='Blues');ax.set_xticks(range(len(labels)),labels,rotation=30,ha='right');ax.set_yticks(range(len(labels)),labels)
    ax.set(xlabel='Predicted',ylabel='Gold',title=title)
    for y,row in enumerate(matrix):
        for x,value in enumerate(row):ax.text(x,y,str(value),ha='center',va='center',fontsize=8)
    fig.savefig(path,dpi=160);plt.close(fig)


def evaluate(root,model_name,rows,texts,predict,protocol,device,artifact,overall_predict=None):
    root=Path(root);done=root/'metrics/test_metrics.json'
    identity={**protocol,'model_sha256':file_hash(artifact),'evaluator_sha256':file_hash(__file__),
              'metric_code_sha256':file_hash('src/experiments/absa/metrics.py')}
    if done.exists():
        result=json.loads(done.read_text(encoding='utf-8'))
        if result['identity']!=identity:raise ValueError('Completed evaluation identity changed; preserve it before a separately named experiment')
        return result
    for folder in ['predictions','metrics','figures','logs']:(root/folder).mkdir(parents=True,exist_ok=True)
    # Warmup uses train text only; final test is evaluated once, batch size one.
    predict([texts['train'][0]])
    predictions=[];latencies=[]
    for text in texts['test']:
        start=perf_counter();out=predict([text]);latencies.append((perf_counter()-start)*1000);predictions.append(np.asarray(out)[0].tolist())
    gold=np.asarray([r['labels'] for r in rows['test']]);pred=np.asarray(predictions)
    result=score(gold,pred);result.update({'model':model_name,'identity':identity,
       'latency':{'mean_ms':float(np.mean(latencies)),'p50_ms':float(np.percentile(latencies,50)),
                  'p95_ms':float(np.percentile(latencies,95)),'device':device,'batch_size':1,
                  'scope':'presegmented text -> classifier; segmentation cost measured separately'},
       'artifact_size_mb':Path(artifact).stat().st_size/1e6})
    gt=[overall(x) for x in gold];mask=[i for i,v in enumerate(gt) if v!='NO_ASPECT']
    if overall_predict is None:
        predicted=[overall(x) for x in pred]
        # No detected aspect predicts NEUTRAL under the product rule, not sample removal.
        predicted=['NEUTRAL' if p=='NO_ASPECT' else p for p in predicted]
    else:predicted=list(overall_predict([texts['test'][i] for i in range(len(gt))]))
    result['derived_overall']=sentiment_score([gt[i] for i in mask],[predicted[i] for i in mask])
    result['derived_overall']['excluded_no_gold_aspect']=len(gt)-len(mask)
    records=[]
    for i,(g,p) in enumerate(zip(gold,pred)):
        record={'row_id':f'test:{i}','gold_overall':gt[i],'predicted_overall':predicted[i],'latency_ms':latencies[i]}
        record.update({f'gold_{a}':int(g[j]) for j,a in enumerate(ASPECTS)})
        record.update({f'pred_{a}':int(p[j]) for j,a in enumerate(ASPECTS)});records.append(record)
    pd.DataFrame(records).to_csv(root/'predictions/test_predictions.csv',index=False)
    pd.DataFrame(result['per_aspect']).drop(columns='confusion').to_csv(root/'metrics/per_aspect.csv',index=False)
    plot_matrix(result['confusion_matrix'],root/'figures/confusion_4state.png',model_name)
    plot_matrix(result['polarity_confusion'],root/'figures/confusion_gold_mentioned.png','Gold mentioned aspects (misses retained)')
    for a in result['per_aspect']:plot_matrix(a['confusion'],root/'figures'/('confusion_'+a['aspect'].replace('#','_').replace('&','_')+'.png'),a['aspect'])
    save(done,result);return result
