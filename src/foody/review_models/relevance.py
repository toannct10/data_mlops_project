"""Auditable domain rules; the numeric relevance proxy remains VERIFIED_SOURCE_PROXY_LABELS."""
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from src.foody.schema import normalize

RULES=Path('config/review_relevance_rules.json')


@lru_cache(maxsize=1)
def rule_config():
    body=RULES.read_bytes()
    return json.loads(body), hashlib.sha256(body).hexdigest()


def domain_relevance(text):
    clean=normalize(text).casefold()
    if not clean: raise ValueError('Enter review text before analysis.')
    config,checksum=rule_config()
    # Whole phrase boundaries prevent accidental matches inside unrelated words.
    evidence=[term for term in config['terms'] if re.search(r'(?<!\w)'+re.escape(term)+r'(?!\w)',clean)]
    off_topic=any(term in clean for term in config['off_topic_terms'])
    relevant=bool(evidence)
    return {'relevance_label':'RELEVANT' if relevant else 'NOT_RELEVANT',
        'relevance_score':None, 'relevance_model':'restaurant_domain_rules',
        'relevance_model_version':config['version']+':'+checksum[:12],
        'relevance_reason':'restaurant_context' if relevant else 'off_topic' if off_topic else 'no_restaurant_context',
        'proxy_mapping_status':'VERIFIED_SOURCE_PROXY_LABELS', 'gate_type':'heuristic', 'rules_sha256':checksum}


@lru_cache(maxsize=1)
def proxy_resources():
    import joblib
    root=Path('models/phobert_absa')
    body=(root/'relevance.joblib').read_bytes()
    model=joblib.load(root/'relevance.joblib')
    if list(model.classes_) != [0,1]:raise ValueError('Verified proxy class layout changed')
    config=json.loads((root/'relevance_config.json').read_text(encoding='utf-8'))
    if config['low']!=.2 or config['high']!=.8:raise ValueError('Original proxy thresholds changed')
    return model,config,hashlib.sha256(body).hexdigest()


def relevance_gate(text):
    clean=normalize(text)
    if not clean:raise ValueError('Enter review text before analysis.')
    domain=domain_relevance(clean)
    model,config,digest=proxy_resources()
    proxy_text=' '.join(clean.replace('_',' ').split()[:config['max_words']])
    scores=model.predict_proba([proxy_text])[0]
    score=float(scores[list(model.classes_).index(1)])
    short_domain=len(clean.split())<=3 and domain['relevance_label']=='RELEVANT'
    if score>=config['high']:
        label='RELEVANT';decision='CONFIDENT_RESTAURANT_PROXY';reason='restaurant_proxy'
    elif score<=config['low'] and not short_domain:
        label='NOT_RELEVANT';decision='CONFIDENT_NEWS_PROXY';reason='news_proxy'
    else:
        label=domain['relevance_label'];decision='SHORT_DOMAIN_GUARDRAIL' if short_domain else 'UNCERTAIN'
        reason='uncertain_domain_rule:'+domain['relevance_reason']
    return {**domain,'relevance_label':label,'relevance_score':score,
      'relevance_model':'weakly_supervised_source_proxy_with_domain_routing',
      'relevance_model_version':'proxy:'+digest[:12]+';rules:'+domain['rules_sha256'][:12],
      'relevance_reason':reason,'gate_type':'weakly_supervised','proxy_decision':decision,
      'proxy_scores':{'news_proxy':float(scores[0]),'restaurant_proxy':score},
      'thresholds':{'low':config['low'],'high':config['high']},'calibrated':False}
