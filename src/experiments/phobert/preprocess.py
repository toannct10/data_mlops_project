"""Original NFC + VnCoreNLP word segmentation. No silent raw-text fallback."""
import hashlib
import json
import os
import shutil
import subprocess
import unicodedata
import urllib.request
from functools import lru_cache
from pathlib import Path

ASSETS=Path('data/experiments/tools/vncorenlp').resolve()
FILES=['VnCoreNLP-1.2.jar','models/wordsegmenter/wordsegmenter.rdr','models/wordsegmenter/vi-vocab']


def normalize(text):return ' '.join(unicodedata.normalize('NFC',str(text)).split())


def setup():
    java=shutil.which('java')
    if not java:raise RuntimeError('Java is required for vncorenlp preprocessing')
    version=subprocess.run([java,'-version'],capture_output=True,text=True,check=True)
    if not os.getenv('JAVA_HOME'):
        real=Path(java).resolve()
        if (real.parent.parent/'bin/java.exe').exists():os.environ['JAVA_HOME']=str(real.parent.parent)
    if not os.getenv('JAVA_HOME'):
        java_root=Path(os.getenv('ProgramFiles','C:/Program Files'))/'Java'
        candidates=sorted(java_root.glob('*/bin/server/jvm.dll'))
        if candidates:os.environ['JAVA_HOME']=str(candidates[-1].parents[2])
    ASSETS.mkdir(parents=True,exist_ok=True)
    for name in FILES:
        target=ASSETS/name
        if not target.exists():
            target.parent.mkdir(parents=True,exist_ok=True)
            url='https://raw.githubusercontent.com/vncorenlp/VnCoreNLP/master/'+name
            tmp=target.with_suffix(target.suffix+'.download')
            with urllib.request.urlopen(url,timeout=120) as r:tmp.write_bytes(r.read())
            tmp.replace(target)
    processor=segmenter()
    example='Đồ ăn ngon và nhân viên nhiệt tình.'
    output=' '.join(processor.word_segment(example))
    if not output or '_' not in output:raise RuntimeError('VnCoreNLP segmentation smoke failed')
    record={'version':'VnCoreNLP-1.2 / py_vncorenlp-0.1.4','java_version':(version.stderr+version.stdout).strip(),
      'segmentation_status':'PASS','example':output,
      'files':{f:hashlib.sha256((ASSETS/f).read_bytes()).hexdigest() for f in FILES}}
    (ASSETS/'setup_manifest.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return record


@lru_cache(maxsize=1)
def segmenter():
    if any(not (ASSETS/f).is_file() for f in FILES):raise RuntimeError('Run setup_vncorenlp.py; assets missing')
    import py_vncorenlp
    cwd=Path.cwd()
    try:return py_vncorenlp.VnCoreNLP(annotators=['wseg'],save_dir=str(ASSETS),max_heap_size='-Xmx1g')
    finally:os.chdir(cwd)


def preprocess(text,mode='vncorenlp'):
    text=normalize(text)
    if mode=='raw':return text
    if mode!='vncorenlp':raise ValueError('Unknown preprocessing mode')
    return ' '.join(segmenter().word_segment(text))


def prepare(rows,dataset_hash,mode='vncorenlp'):
    setup_record=json.loads((ASSETS/'setup_manifest.json').read_text(encoding='utf-8')) if mode=='vncorenlp' else {}
    identity=hashlib.sha256(json.dumps([dataset_hash,mode,setup_record.get('files',{})],sort_keys=True).encode()).hexdigest()
    target=Path('data/experiments/vlsp2018_restaurant')/('preprocessed_'+identity[:16]+'.json')
    if target.exists():return json.loads(target.read_text(encoding='utf-8')),identity
    result={s:[preprocess(r['text'],mode) for r in values] for s,values in rows.items()}
    target.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    return result,identity
