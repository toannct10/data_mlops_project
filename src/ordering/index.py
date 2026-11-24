"""Idempotent single-review upsert into the accepted Chroma and BM25 stores."""

import json
import sqlite3
from datetime import datetime

from src.foody.review_paths import DOCUMENTS
from src.foody.review_runtime import LEXICAL, VECTOR
from src.foody.schema import digest, save_json
from src.rag.vector_store import chroma_metadata, client
from src.sales.queries import _row


def _document(conn, review_id):
    row = _row(conn.execute("""SELECT r.review_id,r.restaurant_id,
        d.restaurant_name,r.review_text,r.rating,r.created_at,
        r.predicted_sentiment,r.predicted_aspects
        FROM fact_review_v2 r JOIN dim_sales_restaurant d USING (restaurant_id)
        WHERE r.review_id=%s AND r.source='live_web'""", (review_id,)))
    if row is None:
        raise ValueError("Only a committed live review can be indexed")
    when = row["created_at"].isoformat() if isinstance(row["created_at"], datetime) else \
        str(row["created_at"])
    return {"id": review_id, "text": row["review_text"], "metadata": {
        "review_id": review_id, "restaurant_id": row["restaurant_id"],
        "restaurant_name": row["restaurant_name"], "review_time": when,
        "rating": float(row["rating"]),
        "predicted_sentiment": row["predicted_sentiment"],
        "predicted_aspects": row["predicted_aspects"], "detected_issues": [],
        "source": "live_web"}}


def _embedding():
    from src.rag.cli import portable_artifact_path
    from src.rag.embeddings import CANDIDATES, LocalEmbeddings
    from src.rag.evaluation import SELECTION

    selected = json.loads(SELECTION.read_text(encoding="utf-8"))
    if digest(portable_artifact_path(selected["model_file"])) != selected["model_hash"]:
        raise ValueError("Selected embedding artifact checksum differs")
    spec = next(item for item in CANDIDATES if item.name == selected["model"])
    return LocalEmbeddings(spec, local_files_only=True)


def upsert_live_review(conn, review_id, *, embedder=None):
    """Upsert by stable review ID; keep the historical index and source rows intact."""
    from src.rag.monitoring import record

    document = _document(conn, review_id)
    vector_manifest = VECTOR / "index_manifest.json"
    lexical_manifest = LEXICAL / "index_manifest.json"
    vector_info = json.loads(vector_manifest.read_text(encoding="utf-8"))
    lexical_info = json.loads(lexical_manifest.read_text(encoding="utf-8"))
    collection = client(VECTOR).get_collection(vector_info["collection"])
    embedder = embedder or _embedding()
    vectors, elapsed = embedder.documents([document["text"]])
    metadata = chroma_metadata(document)
    collection.upsert(ids=[review_id], embeddings=vectors.tolist(),
                      documents=[document["text"]], metadatas=[metadata])
    with sqlite3.connect(LEXICAL / "reviews.sqlite") as database:
        database.execute("DELETE FROM reviews_fts WHERE review_id=?", (review_id,))
        database.execute("DELETE FROM review_aspects WHERE review_id=?", (review_id,))
        database.execute("DELETE FROM reviews WHERE review_id=?", (review_id,))
        database.execute("INSERT INTO reviews_fts(review_id,text) VALUES (?,?)",
                         (review_id, document["text"]))
        database.execute("""INSERT INTO reviews
            (review_id,restaurant_key,restaurant_id,sentiment,timestamp_epoch,
             metadata_json) VALUES (?,?,?,?,?,?)""",
            (review_id, None, metadata["restaurant_id"], metadata["predicted_sentiment"],
             metadata.get("timestamp_epoch"), json.dumps(metadata, ensure_ascii=False)))
        database.executemany("INSERT INTO review_aspects(review_id,aspect) VALUES (?,?)",
                             [(review_id, aspect) for aspect in
                              document["metadata"]["predicted_aspects"]])
    found = False
    if DOCUMENTS.exists():
        with DOCUMENTS.open(encoding="utf-8") as source:
            found = any(json.loads(line)["id"] == review_id for line in source)
    if not found:
        with DOCUMENTS.open("a", encoding="utf-8") as source:
            source.write(json.dumps(document, ensure_ascii=False) + "\n")
    source_hash = digest(DOCUMENTS)
    vector_info.update(document_count=collection.count(), documents_sha256=source_hash)
    lexical_info.update(document_count=collection.count(), source_sha256=source_hash,
                        path=str(LEXICAL / "reviews.sqlite"))
    save_json(vector_manifest, vector_info)
    save_json(lexical_manifest, lexical_info)
    record("rag_incremental_upsert", "MiniLM + Chroma + BM25", "1", None,
           elapsed * 1000, "ok", evidence_count=1)
    return {"status": "INDEXED", "review_id": review_id,
            "chroma_count": collection.count(), "bm25_count": lexical_info["document_count"]}
