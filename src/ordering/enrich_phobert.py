"""Resumable PhoBERT aspect inference over the existing historical Review rows."""

import argparse
import json
from pathlib import Path

from src.ordering.sentiment import overall_sentiment
from src.warehouse.loader import connection, migrate

CONFIG = Path("models/phobert_absa/absa_config.json")


def enrich_historical(conn, *, predict=None, limit=0, batch_size=25):
    """Advance only rows without this checkpoint version; never rewrite raw text."""
    migrate(conn)
    version = str(json.loads(CONFIG.read_text(encoding="utf-8")).get(
        "source_commit", "artifact_version_unknown"))
    if predict is None:
        from src.foody.review_models.phobert_absa_adapter import PhoBERTABSAAdapter

        predict = PhoBERTABSAAdapter().predict
    rows = conn.execute("""SELECT review_id,review_text FROM fact_review_v2
        WHERE source='historical' AND relevance_label IS DISTINCT FROM 'NOT_RELEVANT' AND absa_model_version IS DISTINCT FROM %s
        ORDER BY review_id LIMIT %s""", (version, limit if limit > 0 else 1_000_000)).fetchall()
    counts = {"processed": 0, "skipped": conn.execute("""SELECT COUNT(*) FROM fact_review_v2
        WHERE source='historical' AND absa_model_version=%s""", (version,)).fetchone()[0],
        "failed": 0, "model_version": version}
    for review_id, text in rows:
        try:
            result = predict(text)
            aspects = result["aspects"]
            overall, reason = (('NOT_RELEVANT', result.get('reason')) if result.get('relevance_label') == 'NOT_RELEVANT' else overall_sentiment(aspects))
            for aspect in aspects:
                conn.execute("""INSERT INTO fact_review_aspect
                    (review_id,aspect,sentiment,score,model_name,model_version)
                    VALUES (%s,%s,%s,%s,'PhoBERT ABSA',%s)
                    ON CONFLICT (review_id,aspect,model_version) DO UPDATE SET
                    sentiment=EXCLUDED.sentiment,score=EXCLUDED.score,
                    inferred_at=now()""", (review_id, aspect["label"],
                                            aspect["sentiment"], aspect.get("score"),
                                            version))
            conn.execute("""UPDATE fact_review_v2 SET overall_sentiment=%s,
                overall_sentiment_reason=%s,absa_model_version=%s
                WHERE review_id=%s""", (overall, reason, version if overall != 'NOT_RELEVANT' else None, review_id))
            conn.execute("""UPDATE fact_review_v2 SET relevance_label=%s,relevance_score=%s,relevance_model=%s,
                relevance_model_version=%s,relevance_reason=%s,aspect_status=%s WHERE review_id=%s""",
                (result.get('relevance_label','RELEVANT'),result.get('relevance_score'),result.get('relevance_model'),
                 result.get('relevance_model_version'),result.get('relevance_reason'),result.get('aspect_status'),review_id))
            counts["processed"] += 1
        except (ValueError, RuntimeError, OSError, KeyError, TypeError):
            counts["failed"] += 1
        if (counts["processed"] + counts["failed"]) % batch_size == 0:
            conn.commit()
    conn.commit()
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0,
                        help="Maximum pending historical reviews; 0 processes all")
    parser.add_argument("--batch-size", type=int, default=25)
    args = parser.parse_args()
    if args.limit < 0 or args.batch_size < 1:
        parser.error("limit must be nonnegative and batch-size positive")
    with connection() as conn:
        print(json.dumps(enrich_historical(conn, limit=args.limit,
                                           batch_size=args.batch_size)))


if __name__ == "__main__":
    main()
