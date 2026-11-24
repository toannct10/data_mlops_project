"""Opt-in, bounded relevance-only backfill. No ABSA inference or index rebuild."""
import argparse
import json
from src.foody.review_models.relevance import relevance_gate
from src.warehouse.loader import connection


def run(conn, *, apply=False, limit=100):
    if not 1 <= limit <= 10000: raise ValueError('limit must be 1..10000')
    from src.foody.review_models.relevance import rule_config
    config,checksum=rule_config(); version=config['version']+':'+checksum[:12]
    counts=conn.execute("""SELECT COUNT(*), COUNT(*) FILTER(WHERE relevance_label IS NOT NULL),
        COUNT(*) FILTER(WHERE relevance_model_version IS DISTINCT FROM %s)
        FROM fact_review_v2 WHERE source='historical'""",(version,)).fetchone()
    processed=0
    if apply:
        rows=conn.execute("""SELECT review_id,review_text FROM fact_review_v2 WHERE source='historical'
            AND relevance_model_version IS DISTINCT FROM %s ORDER BY review_id LIMIT %s
            FOR UPDATE SKIP LOCKED""",(version,limit)).fetchall()
        for review_id,text in rows:
            result=relevance_gate(text)
            # Preserve existing sentiment for relevant records and ALL aspect rows/raw text.
            conn.execute("""UPDATE fact_review_v2 SET relevance_label=%s,relevance_score=NULL,
                relevance_model=%s,relevance_model_version=%s,relevance_reason=%s,
                overall_sentiment=CASE WHEN %s='NOT_RELEVANT' THEN 'NOT_RELEVANT' ELSE overall_sentiment END,
                aspect_status=CASE WHEN %s='NOT_RELEVANT' THEN 'SKIPPED_NOT_RELEVANT' ELSE aspect_status END
                WHERE review_id=%s""",(result['relevance_label'],result['relevance_model'],version,
                result['relevance_reason'],result['relevance_label'],result['relevance_label'],review_id))
            processed+=1
    return dict(zip(['historical_total','already_has_relevance','pending_for_rule_version'],counts),processed=processed,dry_run=not apply)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--apply',action='store_true');parser.add_argument('--limit',type=int,default=100)
    args=parser.parse_args()
    with connection() as conn: result=run(conn,apply=args.apply,limit=args.limit)
    print(json.dumps(result))


if __name__=='__main__':main()
