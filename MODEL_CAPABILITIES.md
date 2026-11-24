# Included models and actual capability evidence

All required local AI model files are bundled. No external/private model pack is
required. 06_INSTALL_MODELS.ps1 verifies these files in place; setup repeats the
same checks. Missing/corrupt files cause a clear failure and re-extraction advice,
never an automatic substitute download.

| Model | Included | Executed validation |
|---|---|---|
| Qwen2.5-3B-Instruct Q4_K_M GGUF | Yes, unchanged | SHA256/header; earlier accepted runtime is historical; no second portable Qwen server started |
| Multilingual MiniLM-L12-v2 ONNX | Yes, unchanged | SHA256 and 384-dimension existing-vector reconciliation; no embedding model reload or historical re-embedding |
| Fine-tuned PhoBERT ABSA | Yes, exact accepted checkpoint | Actual production CPU adapter load and one synthetic forward; 12 heads x 4 labels |
| Relevance proxy | Yes, exact accepted artifact | Actual joblib deserialization, predict_proba and production relevance gate |

Stored analytics, SQL-only Sales/Cross, ordering creation/cancellation and core
monitoring were previously exercised in the isolated portable project. Full
Qwen-backed text-to-SQL, grounded RAG/50-question parity and live review -> new
embedding remain SKIPPED until the resource-appropriate full acceptance run.

Exact newly included artifacts:

- models/phobert_absa/absa.pt — 540216658 bytes — SHA256
  c4df8093f202deb83589b6884504f6cb9567ed6d6e62bd60a10845d46e2fef48
- models/phobert_absa/relevance.joblib — 6590404 bytes — SHA256
  924dff55776c160ecb6f13f155ea731cbe28069a5731aa93cfcc6648545f3a77

Authorization is for internal teammate distribution only, confirmed by the
project team and recorded in config/portable/distribution_policy.json. Applicable
upstream model/dataset licenses remain binding; public/commercial redistribution
is not granted here. No model version changed and no training was performed.

Compatibility warning: the relevance artifact records scikit-learn 1.6.1 while
the unchanged accepted runtime uses 1.7.2. Loading/prediction passed the bounded
smoke but emitted InconsistentVersionWarning. Do not infer broad accuracy/parity
from one input. This warning and the historical C19 evidence issue remain recorded.
