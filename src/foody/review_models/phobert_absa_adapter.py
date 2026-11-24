"""CPU adapter reconstructed from the supplied PhoBERT state_dict and configs.

The executed source notebook was recovered during provenance audit. CLS and
12 four-class heads match it. Training used VnCoreNLP segmentation, which this
local runtime does not yet reproduce; see the experiment audit before comparison.
"""

import json
from functools import lru_cache
from pathlib import Path
from time import perf_counter

from src.foody.schema import normalize

PHOBERT_ROOT = Path("models/phobert_absa")


@lru_cache(maxsize=2)
def load_phobert_resources(root: Path):
    """Load each local checkpoint/tokenizer/proxy once per process, on CPU."""
    import torch
    from transformers import PhobertTokenizer, RobertaConfig, RobertaModel

    root = Path(root)
    spec = json.loads((root / "absa_config.json").read_text(encoding="utf-8"))
    labels = spec["labels"]
    aspects = spec["aspects"]
    if labels != ["not_mentioned", "positive", "negative", "neutral"] or len(aspects) != 12:
        raise ValueError("Unexpected PhoBERT ABSA label layout")
    backbone = RobertaConfig.from_pretrained(root / "backbone_config",
                                              local_files_only=True)
    tokenizer = PhobertTokenizer.from_pretrained(root / "tokenizer",
                                                  local_files_only=True)

    class Network(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.encoder = RobertaModel(backbone, add_pooling_layer=True)
            self.heads = torch.nn.ModuleList([
                torch.nn.Linear(backbone.hidden_size, len(labels)) for _ in aspects
            ])

        def forward(self, **tokens):
            cls = self.encoder(**tokens).last_hidden_state[:, 0]
            return torch.stack([head(cls) for head in self.heads], dim=1)

    model = Network()
    state = torch.load(root / "absa.pt", map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)
    del state
    model.eval()
    relevance = None  # Weak source proxy: disabled pending independent relevance acceptance.
    return model, tokenizer, relevance, spec


class PhoBERTABSAAdapter:
    model_id = "phobert_absa"

    def __init__(self, root: Path = PHOBERT_ROOT):
        self.root = Path(root)

    def predict(self, text: str, *, use_relevance_gate=True) -> dict:

        clean = normalize(text)
        if not clean:
            raise ValueError("Enter review text before analysis.")
        started = perf_counter()
        from src.foody.review_models.relevance import relevance_gate
        gate = relevance_gate(clean) if use_relevance_gate else {
            'relevance_label':'RELEVANT', 'relevance_model':'bypassed_for_v1_benchmark',
            'relevance_score':None,'relevance_model_version':'not_applied', 'relevance_reason':'benchmark_branch'}
        if gate['relevance_label'] == 'NOT_RELEVANT':
            spec = json.loads((self.root / 'absa_config.json').read_text(encoding='utf-8'))
            return {**gate, 'model_id':self.model_id, 'model_name':'PhoBERT ABSA',
                'sentiment':None,'sentiment_score':None,'aspects':[],
                'overall_sentiment':'NOT_RELEVANT','aspect_status':'SKIPPED_NOT_RELEVANT',
                'reason':'This review is not related to restaurant or dining experience.',
                'supported_tasks':['aspect_sentiment'], 'latency_ms':(perf_counter()-started)*1000,
                'raw_output':{'absa_execution':'SKIPPED','proxy_mapping_status':'VERIFIED_SOURCE_PROXY_LABELS'},
                'metadata':{'version':spec.get('source_commit','unknown'),'relevance_gate':gate}}
        import torch
        model, tokenizer, relevance, spec = load_phobert_resources(self.root)
        tokens = tokenizer(clean, return_tensors="pt", truncation=True,
                           max_length=int(spec["max_length"]))
        tokens = tokens.to(next(model.parameters()).device)
        with torch.inference_mode():
            probabilities = model(**tokens).softmax(dim=-1)[0].tolist()
        native = []
        detected = []
        for aspect, scores in zip(spec["aspects"], probabilities, strict=True):
            index = max(range(len(scores)), key=scores.__getitem__)
            entry = {"label": aspect, "sentiment": spec["labels"][index],
                     "score": float(scores[index])}
            native.append({**entry, "label_scores": dict(zip(spec["labels"], scores,
                                                            strict=True))})
            if index != 0:
                detected.append(entry)
        proxy_scores = None  # Retained diagnostic key; proxy is disabled.
        from src.ordering.sentiment import overall_sentiment
        overall, reason = overall_sentiment(detected)
        return {**gate, "overall_sentiment":overall, "reason":reason,
            "aspect_status":"DETECTED" if detected else "NO_ASPECT_DETECTED",
            "model_id": self.model_id,
            "model_name": "PhoBERT ABSA",
            "sentiment": None,
            "sentiment_score": None,
            "aspects": detected,
            "supported_tasks": ["aspect_sentiment"],
            "latency_ms": (perf_counter() - started) * 1000,
            "raw_output": {"native_aspect_predictions": native,
                           "unmapped_relevance_proxy_scores": proxy_scores},
            "metadata": {"framework": "PyTorch / Transformers",
                         "backbone": "PhoBERT / RoBERTa, 12 layers, hidden size 768",
                         "training_dataset": "VLSP 2018 Restaurant; source notebook recovered",
                         "version": spec.get("source_commit", "Not specified"),
                         "tokenizer": "bundled PhobertTokenizer",
                         "label_space": {"aspects": spec["aspects"],
                                         "sentiments": spec["labels"]},
                         "preprocessing": "Unicode NFC, whitespace collapse, bundled tokenizer; "
                         "training word segmentation unverified",
                         "relevance_model_usage": "0=news_proxy, 1=restaurant_proxy; weak source labels. "
                         "Weak proxy thresholds 0.2/0.8; documented uncertainty routing before ABSA",
                         "pooling_assumption": "CLS token; selected from artifact head "
                         "shape and live output checks; training forward pass unavailable",
                         "artifact_paths": [str(self.root / name) for name in
                                            ("absa.pt", "absa_config.json", "tokenizer",
                                             "backbone_config", "relevance.joblib")]},
        }

    def predict_batch(self, texts, *, device='cpu'):
        """Same production gate/tokenizer/heads, batched inference only; never train."""
        from src.foody.review_models.relevance import relevance_gate
        clean=[normalize(text) for text in texts]
        if any(not text for text in clean):raise ValueError('Empty review in inference batch')
        gates=[relevance_gate(text) for text in clean]
        active=[i for i,g in enumerate(gates) if g['relevance_label']=='RELEVANT']
        results=[None]*len(clean)
        for i,g in enumerate(gates):
            if g['relevance_label']=='NOT_RELEVANT':results[i]=self.predict(clean[i])
        if not active:return results
        import torch
        model,tokenizer,_,spec=load_phobert_resources(self.root)
        model.to(device);model.eval()
        tokens=tokenizer([clean[i] for i in active],return_tensors='pt',padding=True,truncation=True,max_length=int(spec['max_length'])).to(device)
        with torch.inference_mode():probabilities=model(**tokens).softmax(dim=-1).cpu().tolist()
        from src.ordering.sentiment import overall_sentiment
        for i,values in zip(active,probabilities,strict=True):
            native=[];aspects=[]
            for aspect,scores in zip(spec['aspects'],values,strict=True):
                index=max(range(len(scores)),key=scores.__getitem__)
                entry={'label':aspect,'sentiment':spec['labels'][index],'score':float(scores[index])}
                native.append({**entry,'label_scores':dict(zip(spec['labels'],scores,strict=True))})
                if index:aspects.append(entry)
            overall,reason=overall_sentiment(aspects)
            results[i]={**gates[i],'aspects':aspects,'overall_sentiment':overall,'reason':reason,'aspect_status':'DETECTED' if aspects else 'NO_ASPECT_DETECTED','raw_output':{'native_aspect_predictions':native},'metadata':{'version':spec['source_commit'],'preprocessing':'production Unicode normalization + bundled tokenizer','inference_device':str(device)}}
        return results
