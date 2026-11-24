"""Pinned VLSP data: immutable raw bytes and ordered original memberships."""
import csv
import hashlib
import io
import json
import unicodedata
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ASPECTS=json.loads(Path('models/phobert_absa/absa_config.json').read_text(encoding='utf-8'))['aspects']
LABELS=['not_mentioned','positive','negative','neutral']
ROOT=Path('data/experiments/vlsp2018_restaurant/raw')
FROZEN=Path('experiments/vlsp2018_absa/frozen')
COMMIT='4f75c638f1885ccf899b5515adef89da54a119a4'
MIRROR='63f952ad797c48ca27058ca0da009b9e453727b2'
SPLITS={'train':2961,'dev':1290,'test':500}


def sha(body):return hashlib.sha256(body).hexdigest()


def save(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def fetch(url):
    with urllib.request.urlopen(url,timeout=90) as r:return r.read()


def records(body,split=None):
    rows=list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))
    if not rows:raise ValueError('Empty source')
    if not set(ASPECTS).issubset(rows[0]):raise ValueError('Missing 12 native aspects')
    result=[]
    for row in rows:
        text=row.get('Review',row.get('text',row.get('review')))
        if not text or not text.strip():raise ValueError('Missing review text')
        part=split or {'validation':'dev','valid':'dev'}.get(row.get('type'),row.get('type'))
        if part not in SPLITS:raise ValueError('Missing/invalid original split')
        labels=[int(row[a]) for a in ASPECTS]
        if any(x not in range(4) for x in labels):raise ValueError('Invalid aspect state')
        text=' '.join(unicodedata.normalize('NFC',text).split())
        result.append({'split':part,'text':text,'labels':labels})
    return result


def signature(row):return sha(json.dumps([row['text'],row['labels']],ensure_ascii=False,separators=(',',':')).encode())


def ensure_dataset():
    manifest=ROOT/'dataset_manifest.json'
    if manifest.exists():
        m=json.loads(manifest.read_text(encoding='utf-8'))
        for f in m['files']:
            if sha((ROOT/f['file']).read_bytes())!=f['sha256']:raise ValueError('Immutable raw source changed')
        return m
    url=f'https://huggingface.co/datasets/visolex/VLSP2018-ABSA-Restaurant/resolve/{MIRROR}/VLSP2018-ABSA-Restaurant.csv'
    body=fetch(url); mirrored=records(body)
    if Counter(r['split'] for r in mirrored)!=SPLITS:raise ValueError('Mirror split counts differ')
    originals=[];verification=[]
    for i,split in enumerate(SPLITS,1):
        name=f'{i}-VLSP2018-SA-Restaurant-{split}.csv'
        original=fetch(f'https://raw.githubusercontent.com/ds4v/absa-vlsp-2018/{COMMIT}/datasets/vlsp2018_restaurant/{name}')
        rows=records(original,split)
        if len(rows)!=SPLITS[split]:raise ValueError('Original source count differs')
        expected=Counter(signature(r) for r in rows)
        actual=Counter(signature(r) for r in mirrored if r['split']==split)
        if expected!=actual:raise ValueError(f'Mirror membership/labels differ: {split}; no substitution allowed')
        verification.append({'file':name,'sha256':sha(original),'rows':len(rows)})
        originals.extend(rows)
    # Store only the verified mirror, not a second copy of the same dataset.
    ROOT.mkdir(parents=True,exist_ok=True);target=ROOT/'VLSP2018-ABSA-Restaurant.csv'
    if target.exists() and target.read_bytes()!=body:raise ValueError('Refusing raw overwrite')
    target.write_bytes(body)
    m={'source':url,'source_type':'public_mirror_not_official','mirror_revision':MIRROR,
       'original_project':'ds4v/absa-vlsp-2018','original_commit':COMMIT,
       'downloaded_at':datetime.now(timezone.utc).isoformat(),'row_count':len(mirrored),
       'columns':list(next(csv.DictReader(io.StringIO(body.decode('utf-8-sig')))).keys()),
       'split_counts':SPLITS,'label_mapping':dict(enumerate(LABELS)),
       'files':[{'file':target.name,'sha256':sha(body)}],
       'original_verification':verification,'verification':'per-split multiset of normalized text + all 12 labels equals original pinned CSVs',
       'license_note':'Public mirror; original VLSP resource terms apply. Public accessibility is not a new license grant.'}
    save(manifest,m);return m


def freeze():
    m=ensure_dataset();rows=records((ROOT/m['files'][0]['file']).read_bytes())
    # Mirror order is frozen as well as membership; no randomized resplitting.
    memberships={s:[{'row_id':f'{s}:{i}', 'content_sha256':signature(r)} for i,r in enumerate([r for r in rows if r['split']==s])] for s in SPLITS}
    definition={'dataset_sha256':m['files'][0]['sha256'],'memberships':memberships,'counts':SPLITS}
    target=FROZEN/'split_manifest.json'
    if target.exists() and json.loads(target.read_text(encoding='utf-8'))!=definition:raise ValueError('Frozen membership changed')
    save(target,definition);save(FROZEN/'dataset_manifest.json',m)
    save(FROZEN/'label_mapping.json',{'aspects':ASPECTS,'states':dict(enumerate(LABELS))})
    return rows,m


def load():
    m=ensure_dataset();target=FROZEN/'split_manifest.json'
    if not target.exists():raise ValueError('Freeze dataset first')
    rows=records((ROOT/m['files'][0]['file']).read_bytes());frozen=json.loads(target.read_text(encoding='utf-8'))
    for split in SPLITS:
        actual=[signature(r) for r in rows if r['split']==split]
        if actual!=[r['content_sha256'] for r in frozen['memberships'][split]]:raise ValueError('Frozen membership mismatch')
    return {s:[r for r in rows if r['split']==s] for s in SPLITS},m
