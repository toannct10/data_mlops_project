"""Resumable train/dev-only reproduction; test access is delegated to final evaluator."""
import csv
import json
import math
import os
import random
from pathlib import Path
from time import perf_counter
import numpy as np
import torch
from transformers import get_linear_schedule_with_warmup
from src.experiments.absa.data import save
from src.experiments.absa.metrics import score
from src.experiments.phobert.model import PhoBERTABSA,tokenizer

ROOT=Path('experiments/vlsp2018_absa/phobert')
MODEL=Path('models/phobert_absa_retrained')
DEFAULT={'model_name':'vinai/phobert-base-v2','model_revision':'86cd7fd4c148980922ac11a2cf5e257f2ba639e1',
    'tokenizer':'existing bundled PhobertTokenizer','max_length':256,'batch_size':8,'epochs':8,
    'learning_rate':2e-5,'weight_decay':.01,'warmup':.1,'dropout':.1,'gradient_clipping':1.,
    'patience':2,'seed':42,'preprocessing':'vncorenlp','class_weights':'train-only inverse sqrt frequency, mean normalized',
    'selection':'DEV mentioned aspect+polarity micro-F1','AMP':False,'source':'recovered executed Restaurant_ABSA_AutoOnlineData.ipynb cells 2,8,10'}


def seed(value):
    random.seed(value);np.random.seed(value);torch.manual_seed(value)
    if torch.cuda.is_available():torch.cuda.manual_seed_all(value)
    torch.backends.cudnn.benchmark=False;torch.backends.cudnn.deterministic=True


def save_resume(model,opt,scheduler,state,config,protocol):
    folder=ROOT/'checkpoints';folder.mkdir(parents=True,exist_ok=True)
    target=folder/'resume.pt';tmp=folder/'resume.pt.tmp'
    payload={'model':model.state_dict(),'optimizer':opt.state_dict(),'scheduler':scheduler.state_dict(),
       'state':state,'config':config,'protocol':protocol,'python_rng':random.getstate(),
       'numpy_rng':np.random.get_state(),'torch_rng':torch.get_rng_state(),
       'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else []}
    torch.save(payload,tmp);os.replace(tmp,target)
    # Required resume filenames share one bundle inode, not three GB-scale copies.
    for name in ['last_model.pt','optimizer_state.pt','scheduler_state.pt']:
        alias=folder/name
        if alias.exists():alias.unlink()
        os.link(target,alias)
    save(folder/'training_state.json',dict(state,checkpoint_format='shared bundle with model/optimizer/scheduler keys; aliases are hardlinks'))


def train(rows,texts,protocol,config=None,device='auto',resume=True):
    config={**DEFAULT,**(config or {})};seed(config['seed']);torch.set_num_threads(4)
    device=torch.device('cuda' if device=='auto' and torch.cuda.is_available() else 'cpu' if device=='auto' else device)
    env={'device':str(device),'torch':torch.__version__,'cuda':torch.version.cuda,
        'gpu':torch.cuda.get_device_name(0) if device.type=='cuda' else None,
        'vram_bytes':torch.cuda.get_device_properties(0).total_memory if device.type=='cuda' else None}
    for folder in ['checkpoints','logs','tensorboard','predictions','metrics','figures']:(ROOT/folder).mkdir(parents=True,exist_ok=True)
    MODEL.mkdir(parents=True,exist_ok=True);save(ROOT/'logs/device.json',env);print(json.dumps(env),flush=True)
    completed=MODEL/'training_config.json'
    if completed.exists():
        saved=json.loads(completed.read_text(encoding='utf-8'))
        if saved['config']!=config or saved['protocol']!=protocol:raise ValueError('Completed PhoBERT protocol/config differs')
        return MODEL/'absa.pt',saved
    tok=tokenizer();model=PhoBERTABSA(pretrained=True).to(device)
    y=np.asarray([r['labels'] for r in rows['train']]);dev_y=np.asarray([r['labels'] for r in rows['dev']])
    criteria=[]
    for a in range(12):
        count=np.bincount(y[:,a],minlength=4);weights=np.zeros(4,dtype=np.float32);mask=count>0
        weights[mask]=1/np.sqrt(count[mask]);weights[mask]/=weights[mask].mean()
        criteria.append(torch.nn.CrossEntropyLoss(weight=torch.tensor(weights,device=device)))
    opt=torch.optim.AdamW(model.parameters(),lr=config['learning_rate'],weight_decay=config['weight_decay'])
    batches=math.ceil(len(y)/config['batch_size']);steps=batches*config['epochs']
    scheduler=get_linear_schedule_with_warmup(opt,int(config['warmup']*steps),steps)
    state={'epoch':0,'next_batch':0,'best_dev':-1.,'best_epoch':None,'bad_epochs':0,'history':[],
           'elapsed_seconds':0.,'partial_loss_sum':0.,'partial_samples':0}
    checkpoint=ROOT/'checkpoints/resume.pt'
    if checkpoint.exists():
        if not resume:raise ValueError('Resume checkpoint exists; use --resume rather than epoch zero')
        saved=torch.load(checkpoint,map_location=device,weights_only=False) # trusted locally generated bundle only
        if saved['config']!=config or saved['protocol']!=protocol:raise ValueError('Resume protocol/config differs')
        model.load_state_dict(saved['model']);opt.load_state_dict(saved['optimizer']);scheduler.load_state_dict(saved['scheduler']);state=saved['state']
        random.setstate(saved['python_rng']);np.random.set_state(saved['numpy_rng']);torch.set_rng_state(saved['torch_rng'].cpu())
        if device.type=='cuda' and saved['cuda_rng']:torch.cuda.set_rng_state_all([v.cpu() for v in saved['cuda_rng']])
        del saved
        print(f"Resuming epoch {state['epoch']+1}, next batch {state['next_batch']}",flush=True)
    from torch.utils.tensorboard import SummaryWriter
    writer=SummaryWriter(str(ROOT/'tensorboard'));started=perf_counter();prior_elapsed=state['elapsed_seconds']
    def encode(values):return {k:v.to(device) for k,v in tok(values,padding=True,truncation=True,max_length=config['max_length'],return_tensors='pt').items()}
    try:
        for epoch in range(state['epoch'],config['epochs']):
            epoch_start=perf_counter();order=np.random.default_rng(config['seed']+epoch).permutation(len(y));model.train()
            first=state['next_batch'] if epoch==state['epoch'] else 0
            loss_sum=state['partial_loss_sum'];n=state['partial_samples']
            for batch in range(first,batches):
                idx=order[batch*config['batch_size']:(batch+1)*config['batch_size']]
                targets=torch.tensor(y[idx],device=device);opt.zero_grad(set_to_none=True)
                logits=model(**encode([texts['train'][i] for i in idx]))
                loss=torch.stack([criterion(logits[:,a,:],targets[:,a]) for a,criterion in enumerate(criteria)]).mean()
                if not torch.isfinite(loss):raise RuntimeError('Non-finite loss; no invalid checkpoint published')
                loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),config['gradient_clipping']);opt.step();scheduler.step()
                loss_sum+=float(loss.item())*len(idx);n+=len(idx)
                state.update(epoch=epoch,next_batch=batch+1,partial_loss_sum=loss_sum,partial_samples=n,elapsed_seconds=prior_elapsed+perf_counter()-started)
                if (batch+1)%50==0:
                    print(f'epoch={epoch+1} batch={batch+1}/{batches} loss={loss_sum/n:.6f}',flush=True)
                if (batch+1)%100==0:save_resume(model,opt,scheduler,state,config,protocol)
            model.eval();predictions=[];dev_loss=0
            with torch.inference_mode():
                for offset in range(0,len(dev_y),config['batch_size']):
                    target=torch.tensor(dev_y[offset:offset+config['batch_size']],device=device)
                    logits=model(**encode(texts['dev'][offset:offset+config['batch_size']]))
                    dev_loss+=float(torch.nn.functional.cross_entropy(logits.reshape(-1,4),target.reshape(-1)).item())*len(target)
                    predictions.extend(logits.argmax(-1).cpu().tolist())
            metrics=score(dev_y,predictions)
            row={'epoch':epoch+1,'train_loss':loss_sum/n,'dev_loss':dev_loss/len(dev_y),
                 'dev_micro_f1':metrics['micro_f1'],'dev_macro_f1':metrics['macro_f1'],
                 'dev_exact_match':metrics['exact_match'],'dev_pair_micro_f1':metrics['mentioned_micro_f1'],
                 'mentioned_micro_f1':metrics['mentioned_micro_f1'],'mentioned_macro_f1':metrics['mentioned_macro_f1'],
                 'learning_rate':scheduler.get_last_lr()[0],'epoch_seconds':perf_counter()-epoch_start}
            state['history'].append(row);print(json.dumps(row),flush=True)
            for key,value in row.items():writer.add_scalar(key,value,epoch+1)
            with (ROOT/'logs/history.csv').open('w',newline='',encoding='utf-8') as f:
                out=csv.DictWriter(f,fieldnames=list(row));out.writeheader();out.writerows(state['history'])
            with (ROOT/'logs/training.log').open('a',encoding='utf-8') as f:f.write(json.dumps(row)+'\n')
            if metrics['mentioned_micro_f1']>state['best_dev']:
                state.update(best_dev=metrics['mentioned_micro_f1'],best_epoch=epoch+1,bad_epochs=0)
                tmp=MODEL/'absa.pt.tmp';torch.save(model.state_dict(),tmp);os.replace(tmp,MODEL/'absa.pt')
            else:state['bad_epochs']+=1
            state.update(epoch=epoch+1,next_batch=0,partial_loss_sum=0.,partial_samples=0,elapsed_seconds=prior_elapsed+perf_counter()-started)
            save_resume(model,opt,scheduler,state,config,protocol)
            if state['bad_epochs']>=config['patience']:break
        result={'config':config,'protocol':protocol,'best_epoch':state['best_epoch'],'best_dev_pair_micro_f1':state['best_dev'],
                'training_seconds':state['elapsed_seconds'],'device':env,'completed_epochs':state['epoch']}
        save(completed,result);return MODEL/'absa.pt',result
    finally:writer.close()
