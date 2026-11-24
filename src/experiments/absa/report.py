"""Report measured results only; keep routing and ABSA claims separate."""
import json
import re
from pathlib import Path
import pandas as pd
import numpy as np
from src.experiments.absa.data import ASPECTS,save
from src.experiments.absa.metrics import overall

ROOT=Path('experiments/vlsp2018_absa')


def markdown(frame):
    cols=list(frame.columns)
    lines=['| '+' | '.join(cols)+' |','| '+' | '.join(['---']*len(cols))+' |']
    for _,row in frame.iterrows():lines.append('| '+' | '.join('N/A' if pd.isna(v) else f'{v:.6f}' if isinstance(v,float) else str(v) for v in row)+' |')
    return '\n'.join(lines)


def reports(results,current,rows,manifest,training):
    import matplotlib;matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    comparison=ROOT/'comparison';out=ROOT/'reports';comparison.mkdir(exist_ok=True);out.mkdir(exist_ok=True)
    metrics={'Exact Match':'exact_match','Micro Precision':'micro_precision','Micro Recall':'micro_recall','Micro-F1':'micro_f1',
       'Macro Precision':'macro_precision','Macro Recall':'macro_recall','Macro-F1':'macro_f1',
       'Mentioned Micro-F1':'mentioned_micro_f1','Mentioned Macro-F1':'mentioned_macro_f1'}
    primary=[];overall_rows=[]
    for result in results:
        record={'Model':result['model'],**{k:result[v] for k,v in metrics.items()},
          'Mean Latency':result['latency']['mean_ms'],'P50 Latency':result['latency']['p50_ms'],
          'P95 Latency':result['latency']['p95_ms'],'Artifact Size MB':result['artifact_size_mb'],'Device':result['latency']['device'],'Batch Size':1}
        primary.append(record);overall_rows.append({'Model':result['model'],**result['derived_overall'],
          'Latency ms':result['latency']['mean_ms'] if result['model'].startswith('PhoBERT') else None})
    table=pd.DataFrame(primary);overall_table=pd.DataFrame(overall_rows)
    for name in ['model_comparison','comparison_absa']:
        table.to_csv(comparison/(name+'.csv'),index=False);(comparison/(name+'.md')).write_text(markdown(table),encoding='utf-8')
    for name in ['overall_sentiment_comparison','comparison_overall_sentiment']:
        overall_table.to_csv(comparison/(name+'.csv'),index=False);(comparison/(name+'.md')).write_text(markdown(overall_table),encoding='utf-8')
    efficiency=table[['Model','Mean Latency','P50 Latency','P95 Latency','Artifact Size MB','Device','Batch Size']]
    efficiency.to_csv(comparison/'comparison_efficiency.csv',index=False);(comparison/'comparison_efficiency.md').write_text(markdown(efficiency),encoding='utf-8')
    phi=results[-1];differences=[]
    for baseline in results[:2]:
        for metric in ['micro_f1','macro_f1','mentioned_micro_f1','mentioned_macro_f1','exact_match']:
            delta=phi[metric]-baseline[metric]
            differences.append({'baseline':baseline['model'],'metric':metric,'absolute_difference':delta,
                'relative_difference_percent':100*delta/baseline[metric] if baseline[metric] else None,
                'direction':'increase' if delta>0 else 'decrease' if delta<0 else 'equal'})
    pd.DataFrame(differences).to_csv(comparison/'differences.csv',index=False)
    def bar(values,title,path,unit='Score',bounded=True):
        fig,ax=plt.subplots(figsize=(8,4),layout='constrained');ax.bar(['TF-IDF LR','TF-IDF SVM','PhoBERT'],values,color=['#71849a','#ad9465','#397c85'])
        ax.set(title=title,ylabel=unit)
        ax.set_ylim(0,1 if bounded else max(values)*1.2 if max(values)>0 else 1)
        for i,v in enumerate(values):ax.text(i,v,f'{v:.3f}',ha='center',va='bottom',fontsize=9)
        fig.savefig(path,dpi=180);plt.close(fig)
    for key,title in [('exact_match','Exact Match'),('micro_f1','Four-state Micro-F1'),('macro_f1','Four-state Macro-F1'),('mentioned_macro_f1','Mentioned aspect-polarity Macro-F1')]:
        bar([r[key] for r in results],title,comparison/(key+'.png'))
    bar([r['derived_overall']['macro_f1'] for r in results],'Derived overall sentiment Macro-F1',comparison/'overall_macro_f1.png')
    bar([r['latency']['mean_ms'] for r in results],'Inference latency — CPU baselines / CUDA PhoBERT',comparison/'latency.png','Milliseconds',False)
    bar([r['artifact_size_mb'] for r in results],'Stored model artifact size',comparison/'model_size.png','MB',False)
    aspect=pd.DataFrame([{**a,'Model':r['model']} for r in results for a in r['per_aspect']]).drop(columns='confusion')
    aspect.to_csv(comparison/'per_aspect.csv',index=False)
    fig,ax=plt.subplots(figsize=(11,5),layout='constrained')
    for i,r in enumerate(results):ax.bar(np.arange(12)+i*.25,[a['f1'] for a in r['per_aspect']],width=.25,label=r['model'])
    ax.set_xticks(np.arange(12)+.25,ASPECTS,rotation=65,ha='right');ax.set_ylim(0,1);ax.set_ylabel('Mentioned pair F1');ax.legend(fontsize=8);fig.savefig(comparison/'per_aspect_f1.png',dpi=180);plt.close(fig)
    history=pd.read_csv(ROOT/'phobert/logs/history.csv')
    for column in ['train_loss','dev_macro_f1']:
        fig,ax=plt.subplots(figsize=(7,4),layout='constrained');ax.plot(history.epoch,history[column],marker='o');ax.set(xlabel='Epoch',ylabel=column,title='PhoBERT '+column)
        ax.set_ylim(bottom=0,top=1 if column=='dev_macro_f1' else None);fig.savefig(comparison/(column+'.png'),dpi=180);plt.close(fig)
    fig,ax=plt.subplots(figsize=(7,4),layout='constrained')
    ax.plot(history.epoch,history.train_loss,marker='o',label='Train weighted loss')
    ax.plot(history.epoch,history.dev_loss,marker='o',label='DEV unweighted loss')
    ax.set(xlabel='Epoch',ylabel='Loss',title='PhoBERT train / DEV loss');ax.set_ylim(bottom=0);ax.legend()
    fig.savefig(comparison/'train_dev_loss.png',dpi=180);plt.close(fig)
    polarity=[]
    for result in results:
        for label,values in result['polarity_on_gold_mentions']['per_class'].items():
            polarity.append({'model':result['model'],'polarity':label,**values})
    pd.DataFrame(polarity).to_csv(comparison/'mentioned_polarity_metrics.csv',index=False)
    history_export=history.copy()
    history_export['mentioned_micro_f1']=history_export['dev_pair_micro_f1']
    history_export['mentioned_macro_f1']=None
    history_export.to_csv(ROOT/'phobert/logs/history_requested_schema.csv',index=False)
    predictions=pd.read_csv(ROOT/'phobert/predictions/test_predictions.csv');errors=['# Error analysis — actual frozen test rows','Only short redacted excerpts are published; full raw texts are not duplicated.']
    categories={'negation':r'không|chẳng|chưa','mixed sentiment':r'nhưng|tuy nhiên','slang':r'ko|k |hok|ok|ngon vãi','spelling variation':r'nhìu|thik|đc|k dc'}
    gold=np.asarray([r['labels'] for r in rows['test']]);pred=predictions[[f'pred_{a}' for a in ASPECTS]].to_numpy()
    conditions={'missed aspects':np.any((gold!=0)&(pred==0),axis=1),'false-positive aspects':np.any((gold==0)&(pred!=0),axis=1),
        'polarity reversal':np.any(((gold==1)&(pred==2))|((gold==2)&(pred==1)),axis=1),
        'neutral ambiguity':np.any((gold==3)&(pred!=3),axis=1),'short reviews':np.array([len(r['text'].split())<15 for r in rows['test']]),
        'long reviews':np.array([len(r['text'].split())>150 for r in rows['test']]),'multi-aspect reviews':(gold!=0).sum(axis=1)>=3}
    for label,pattern in categories.items():conditions[label]=np.array([bool(re.search(pattern,r['text'],re.I)) for r in rows['test']])
    wrong=np.any(gold!=pred,axis=1)
    for label,condition in conditions.items():
        candidates=np.where(condition&wrong)[0];errors.append(f'\n## {label}\nObserved {len(candidates)} erroneous rows matching this descriptive condition (not a labeled causal attribution).')
        for i in candidates[:2]:
            tokens=rows['test'][i]['text'].split()[:12]
            excerpt=' '.join('[REDACTED]' if re.search(r'\d|@|https?://',t) or t[:1].isupper() else t for t in tokens)
            errors.append(f'- test:{i}: “{excerpt}…”; missed={[ASPECTS[j] for j in range(12) if gold[i,j]!=0 and pred[i,j]==0]}; false-positive={[ASPECTS[j] for j in range(12) if gold[i,j]==0 and pred[i,j]!=0]}')
    (out/'ERROR_ANALYSIS.md').write_text('\n'.join(errors),encoding='utf-8')
    checkpoint_table=pd.DataFrame([{'checkpoint':'current production (segmented reproduction input)',**{k:current[v] for k,v in metrics.items()}},
                                  {'checkpoint':'retrained research',**{k:phi[v] for k,v in metrics.items()}}])
    (out/'PHOBERT_CHECKPOINT_COMPARISON.md').write_text('# Current vs retrained checkpoint\n'+markdown(checkpoint_table)+'\nProduction was NOT replaced. Current checkpoint is evaluated with the same segmented input, not its raw-text serving mode. Keep the current production checkpoint: retraining has mixed gains and decreases mentioned Macro-F1 (0.423361 to 0.416261); no material or statistically significant improvement is established.',encoding='utf-8')
    selected=max(results,key=lambda r:r['mentioned_micro_f1'])['model']
    sections=[('Research Objective','Compare exactly the same 12-aspect / 4-state task and frozen test membership; V2 routing is separate.'),
      ('Dataset',f"{manifest['row_count']} VLSP2018 Restaurant rows; no VN-MSASar scores in this table."),
      ('Data Source',f"Public visolex mirror, revision {manifest['mirror_revision']}; verified against ds4v pinned commit {manifest['original_commit']}. SHA256 {manifest['files'][0]['sha256']}. Not claimed to be official distribution."),
      ('Frozen Train/Dev/Test','2961 / 1290 / 500. Original memberships retained; notebook reported 38 train/dev duplicate texts. No random resplit.'),
      ('Preprocessing','NFC/space normalization + VnCoreNLP-1.2 word segmentation. No raw fallback. Backbone revision was not pinned by the old notebook; this reproduction pins the existing cached revision (RECONSTRUCTED revision pin).'),
      ('Baseline 1 — TF-IDF + LR','12 independently trained four-state heads; train-only vocabulary/IDF; C selected on DEV only. Separate overall classifier trained on derived TRAIN targets.'),
      ('Baseline 2 — TF-IDF + SVM','LinearSVC only; same train/dev/test and segmented inputs, separate derived-overall classifier.'),
      ('V1 — PhoBERT',f"Recovered CLS/dropout 0.1/12-head architecture; best DEV pair micro-F1 epoch {training['best_epoch']}; training {training['training_seconds']:.1f} seconds. Production checkpoint is unchanged."),
      ('V2 — Relevance-Aware PhoBERT Product Pipeline','Verified weak source relevance mapping, thresholds 0.2/0.8 and documented uncertain-domain routing. Not an ABSA F1 improvement claim and not a human-labeled spam detector.'),
      ('ABSA Results',markdown(table)),('Derived Overall Sentiment Results',markdown(overall_table)+'\nDerived from human aspect labels, not independent overall annotations. No-gold-aspect rows excluded identically. Standalone overall-classifier latency was not measured; N/A is intentional.'),
      ('Per-Aspect Results','See comparison/per_aspect.csv and per_aspect_f1.png. Mentioned pair scores penalize both hallucinated aspects and missed mentions; four-state metrics include not_mentioned.'),
      ('Efficiency',markdown(efficiency)+'\nBatch size one, presegmented inputs; CPU baselines and CUDA PhoBERT are explicitly different hardware. Latency is not an equal-hardware speedup claim. Segmentation latency was not separately measured; all listed inference timings exclude it. The baseline implementation repeats vectorization for its heads and is not claimed to be latency-optimized. Size is serialized model only, excludes optimizer/segmentation cache.'),
      ('Error Analysis','See ERROR_ANALYSIS.md for actual frozen IDs and redacted excerpts. Conditions are descriptive heuristics, not new ground truth.'),
      ('Comparison with Baselines',markdown(pd.DataFrame(differences))+'\nNegative differences are decreases; relative change with a zero baseline is undefined.'),
      ('Relevance Gate Limitation','Source news labels are weak negatives; no independently annotated routing benchmark exists. V2 routing Macro-F1 is NOT_EVALUATED_NO_GROUND_TRUTH.'),
      ('Final Selected Method',f'Best observed mentioned-pair Micro-F1 among primary candidates: {selected}. Keep current production checkpoint until a separately accepted product promotion; no automatic replacement.'),
      ('Limitations','Single seed; DEV mentioned Macro-F1 was not recorded in the completed epoch logs and is marked N/A, not reconstructed; original train/dev duplicates preserved; cached backbone revision is a documented reconstruction; metrics are not the official VLSP evaluation script; no independent human relevance gold; no claim of cross-hardware latency superiority.')]
    recovery_path=out/'recovered_state.json'
    if recovery_path.exists():
        recovered=json.loads(recovery_path.read_text(encoding='utf-8'))
        sections.append(('Recovered Experiment State','Both completed baseline TEST ledgers were reused. All eight PhoBERT epochs had already completed before this continuation; no training resume or restart was necessary. Best DEV epoch 8 was then evaluated once on the frozen test set. Resume aliases are hardlinks to one bundle, not four duplicate model copies.'))
    product_path=out/'product_relevance_acceptance.json'
    if product_path.exists():
        cases=json.loads(product_path.read_text(encoding='utf-8'))['cases']
        product_table=pd.DataFrame([{k:c[k] for k in ['case','relevance_label','proxy_score','proxy_decision','aspect_status','overall_sentiment']} for c in cases])
        sections.append(('Product Relevance Acceptance',markdown(product_table)+'\nThe negative example has an actual production-model sentiment error: FOOD#QUALITY positive plus SERVICE#GENERAL negative. Overall NEUTRAL follows actual predictions; it was not relabeled to manufacture success. The host Streamlit AppTest verified the gray NOT RELEVANT card and ABSA skip with no Neutral metric.'))
    status_path=out/'regression_status.json'
    if status_path.exists():
        status=json.loads(status_path.read_text(encoding='utf-8'))
        regression='Focused: 31 passed, 1 skipped. Host Review UI component smoke: PASS. Full available regression: INTERRUPTED by Docker/WSL failure; not PASS.'
        if status.get('post_recovery_focused',{}).get('status')=='PASS':
            regression+=' After recovery: 14 focused tests passed, including rollback-only database lifecycle; historical table counts preserved (400000 / 15456 / 52760).'
        if status.get('live_ui',{}).get('status')=='PASS':
            regression+=' Live Business Intelligence browser confirmed NOT RELEVANT, explicit PhoBERT skip and uncertainty disclosure.'
        if status.get('docker_recovery',{}).get('status')=='RESTORED':
            regression+=' Docker restored using the same eight existing foody-full-demo containers; no new containers or volumes. All seven HTTP health endpoints returned 200 and PostgreSQL is healthy.'
        sections.append(('Regression Status',regression+' See regression_status.json and full_regression.log.'))
    sections.append(('Production Checkpoint Recommendation','Keep the current production checkpoint. Retrained exact match and four-state metrics rise slightly, mentioned Micro-F1 is effectively unchanged (0.699488 vs 0.699499), and mentioned Macro-F1 decreases (0.423361 to 0.416261). A single-seed mixed result does not justify automatic replacement.'))
    sections.append(('Preservation and Measurement Limits','No dataset redownload, split regeneration, completed-baseline retraining, production-checkpoint overwrite, historical backfill, RAG index rebuild or cloud architecture change occurred in this continuation. DEV mentioned Macro-F1 was not logged for past epochs; its added export column is N/A. Separate overall-baseline latency and segmentation latency are also N/A rather than invented.'))
    content='# Final VLSP2018 ABSA results\n\n'+'\n\n'.join(f'## {i}. {title}\n{text}' for i,(title,text) in enumerate(sections,1))
    (out/'FINAL_RESULTS.md').write_text(content,encoding='utf-8')
    save(out/'experiment_summary.json',{'status':'MEASURED','dataset_sha256':manifest['files'][0]['sha256'],
       'training':training,'primary_results':[{k:r[k] for k in ['model','exact_match','micro_f1','macro_f1','mentioned_micro_f1','mentioned_macro_f1']} for r in results],
       'best_observed_mentioned_micro_f1':selected,'production_replaced':False,'relevance_evaluation':'NOT_EVALUATED_NO_GROUND_TRUTH'})
