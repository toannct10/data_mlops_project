"""Shared 12-head metric definitions for every primary model."""
import numpy as np
from sklearn.metrics import accuracy_score,precision_recall_fscore_support,confusion_matrix
from src.experiments.absa.data import ASPECTS,LABELS


def overall(row):
    values=[int(v) for v in row if int(v)!=0]
    if not values:return 'NO_ASPECT'
    if all(v==1 for v in values):return 'POSITIVE'
    if all(v==2 for v in values):return 'NEGATIVE'
    return 'NEUTRAL'


def score(gold,pred):
    gold=np.asarray(gold,dtype=int);pred=np.asarray(pred,dtype=int)
    if gold.shape!=pred.shape or gold.ndim!=2 or gold.shape[1]!=12:raise ValueError('ABSA arrays must be Nx12')
    if not np.isin(gold,range(4)).all() or not np.isin(pred,range(4)).all():raise ValueError('Invalid state')
    result={'exact_match':float(np.all(gold==pred,axis=1).mean()),'n_test':len(gold),'per_aspect':[]}
    for average in ['micro','macro']:
        p,r,f,_=precision_recall_fscore_support(gold.ravel(),pred.ravel(),labels=range(4),average=average,zero_division=0)
        result.update({average+'_precision':float(p),average+'_recall':float(r),average+'_f1':float(f)})
    # Aspect+polarity pairs: false positive mentions and missed mentions both count.
    gt=gold[:,:,None]==np.arange(1,4);pr=pred[:,:,None]==np.arange(1,4)
    for average in ['micro','macro']:
        p,r,f,_=precision_recall_fscore_support(gt.reshape(len(gold),36),pr.reshape(len(pred),36),average=average,zero_division=0)
        result['mentioned_'+average+'_f1']=float(f)
    mask=gold!=0
    result['polarity_on_gold_mentions']={'condition':'gold aspect is mentioned; predicted not_mentioned counts as a miss',
      'per_class':{}}
    p,r,f,support=precision_recall_fscore_support(gold[mask],pred[mask],labels=[1,2,3],zero_division=0)
    for i,k in enumerate([1,2,3]):result['polarity_on_gold_mentions']['per_class'][LABELS[k]]={'precision':float(p[i]),'recall':float(r[i]),'f1':float(f[i]),'support':int(support[i])}
    result['confusion_matrix']=confusion_matrix(gold.ravel(),pred.ravel(),labels=range(4)).tolist()
    result['polarity_confusion']=confusion_matrix(gold[mask],pred[mask],labels=range(4)).tolist()
    for i,name in enumerate(ASPECTS):
        p,r,f,_=precision_recall_fscore_support(gt[:,i,:],pr[:,i,:],average='micro',zero_division=0)
        result['per_aspect'].append({'aspect':name,'precision':float(p),'recall':float(r),'f1':float(f),'support':int((gold[:,i]!=0).sum()),
          'confusion':confusion_matrix(gold[:,i],pred[:,i],labels=range(4)).tolist()})
    return result


def sentiment_score(gold,pred):
    labels=['NEGATIVE','NEUTRAL','POSITIVE']
    p,r,f,_=precision_recall_fscore_support(gold,pred,labels=labels,average='macro',zero_division=0)
    weighted=precision_recall_fscore_support(gold,pred,labels=labels,average='weighted',zero_division=0)[2]
    return {'accuracy':float(accuracy_score(gold,pred)),'macro_precision':float(p),'macro_recall':float(r),'macro_f1':float(f),'weighted_f1':float(weighted),'n_test':len(gold)}
