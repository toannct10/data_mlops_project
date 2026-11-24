"""Recovered notebook architecture: shared PhoBERT CLS/dropout + twelve heads."""
from pathlib import Path
import torch
from torch import nn
from transformers import RobertaModel,RobertaConfig,PhobertTokenizer


class PhoBERTABSA(nn.Module):
    def __init__(self,pretrained=True):
        super().__init__()
        if pretrained:
            self.encoder=RobertaModel.from_pretrained('vinai/phobert-base-v2',revision='86cd7fd4c148980922ac11a2cf5e257f2ba639e1',local_files_only=True)
        else:
            self.encoder=RobertaModel(RobertaConfig.from_pretrained('models/phobert_absa/backbone_config',local_files_only=True),add_pooling_layer=True)
        self.dropout=nn.Dropout(.1)
        self.heads=nn.ModuleList([nn.Linear(self.encoder.config.hidden_size,4) for _ in range(12)])
    def forward(self,**tokens):
        cls=self.dropout(self.encoder(**tokens).last_hidden_state[:,0,:])
        return torch.stack([head(cls) for head in self.heads],dim=1)


def tokenizer():return PhobertTokenizer.from_pretrained('models/phobert_absa/tokenizer',local_files_only=True)


def predictor(model,tokenizer,device,max_length=256):
    def predict(values):
        model.eval()
        enc=tokenizer(list(values),padding=True,truncation=True,max_length=max_length,return_tensors='pt')
        with torch.inference_mode():result=model(**{k:v.to(device) for k,v in enc.items()}).argmax(-1).cpu().numpy()
        if str(device).startswith('cuda'):torch.cuda.synchronize()
        return result
    return predict
