"""Train-only TF-IDF and twelve independent four-state heads, dev-only selection."""
import json
from pathlib import Path
from time import perf_counter
import joblib
import numpy as np
from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.svm import LinearSVC
from src.experiments.absa.metrics import score,overall,sentiment_score
from src.experiments.absa.data import save
from src.experiments.absa.evaluate import evaluate

ROOT=Path('experiments/vlsp2018_absa')


def classifier(kind,c):
    return LogisticRegression(C=c,class_weight='balanced',max_iter=1500,random_state=42,solver='lbfgs') if kind=='lr' else LinearSVC(C=c,class_weight='balanced',random_state=42,max_iter=10000,dual='auto')


def run(kind,rows,texts,protocol):
    root=ROOT/('baseline_'+kind);artifact=root/'model/model.joblib';meta=root/'model/config.json'
    if artifact.exists():
        model=joblib.load(artifact);metadata=json.loads(meta.read_text(encoding='utf-8'))
        if metadata['protocol']!=protocol:raise ValueError('Saved baseline protocol differs')
    else:
        started=perf_counter();vectorizer=TfidfVectorizer(ngram_range=(1,2),min_df=2,max_features=30000,sublinear_tf=True,lowercase=True)
        x=vectorizer.fit_transform(texts['train']);dev=vectorizer.transform(texts['dev'])
        y=np.asarray([r['labels'] for r in rows['train']]);truth=np.asarray([r['labels'] for r in rows['dev']])
        trials=[];best=None
        for c in [.5,1.,2.]:
            heads=[]
            for a in range(12):
                head=DummyClassifier(strategy='constant',constant=int(y[0,a])) if len(np.unique(y[:,a]))==1 else classifier(kind,c)
                head.fit(x,y[:,a]);heads.append(head)
            predictions=np.stack([h.predict(dev) for h in heads],axis=1);metric=score(truth,predictions)['mentioned_micro_f1']
            trials.append({'C':c,'dev_mentioned_micro_f1':metric})
            if best is None or metric>best[0]:best=(metric,c,heads)
        # A separate overall classifier is trained on aspect-derived train targets.
        train_gold=np.asarray([overall(r['labels']) for r in rows['train']]);dev_gold=np.asarray([overall(r['labels']) for r in rows['dev']])
        train_mask=train_gold!='NO_ASPECT';dev_mask=dev_gold!='NO_ASPECT';overall_best=None
        for c in [.5,1.,2.]:
            head=classifier(kind,c);head.fit(x[train_mask],train_gold[train_mask])
            metric=sentiment_score(dev_gold[dev_mask],head.predict(dev[dev_mask]))['macro_f1']
            if overall_best is None or metric>overall_best[0]:overall_best=(metric,c,head)
        model={'tfidf':vectorizer,'heads':best[2],'overall':overall_best[2]}
        metadata={'protocol':protocol,'kind':kind,'C':best[1],'overall_C':overall_best[1],'class_weight':'balanced','random_seed':42,
            'tfidf':{'ngram_range':[1,2],'min_df':2,'max_features':30000,'sublinear_tf':True,'lowercase':True,'vocabulary_size':len(vectorizer.vocabulary_)},
            'trials':trials,'selection':'DEV mentioned aspect+polarity micro-F1; independent overall DEV macro-F1',
            'seconds':perf_counter()-started}
        artifact.parent.mkdir(parents=True,exist_ok=True);joblib.dump(model,artifact);save(meta,metadata)
    def predict(values):return np.stack([h.predict(model['tfidf'].transform(values)) for h in model['heads']],axis=1)
    return evaluate(root,'TF-IDF + '+('Logistic Regression' if kind=='lr' else 'Linear SVM'),rows,texts,predict,protocol,'CPU',artifact,
      overall_predict=lambda values:model['overall'].predict(model['tfidf'].transform(values)))
