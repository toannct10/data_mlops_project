"""Explicit review corpus switch; the legacy indexes remain available for rollback."""

import json
import os
from pathlib import Path

import yaml

from src.foody.review_paths import DATA_VERSION, ENRICHED, MANIFEST

CONFIG = Path("config/review_corpus.yaml")
VECTOR = Path("data/foody/vector_store_review_2025_2026")
LEXICAL = Path("data/foody/lexical_index_review_2025_2026")


def new_corpus_active():
    configured = yaml.safe_load(CONFIG.read_text("utf-8"))["active"] if CONFIG.exists() else "legacy"
    return os.getenv("FOODY_REVIEW_CORPUS", configured) == DATA_VERSION


def active_manifest():
    if new_corpus_active():
        if MANIFEST.exists():
            result=json.loads(MANIFEST.read_text(encoding="utf-8"))
            if os.getenv('FOODY_DATABASE_URL'):
                from src.warehouse.loader import connection
                with connection() as conn:
                    result['canonical_row_count']=conn.execute('SELECT count(*) FROM fact_review_v2').fetchone()[0]
                    result['canonical_source']='PostgreSQL.fact_review_v2'
                    result['frozen_source_data_version']=result['data_version']
                    result['data_version']='canonical_postgres_v2'
            return result
        return {"data_version": DATA_VERSION, "status": "artifact_unavailable",
                "row_count": None, "document_count": None,
                "restaurant_count": None, "date_min": None, "date_max": None}
    legacy = Path("data/foody/vector_store/index_manifest.json")
    return json.loads(legacy.read_text(encoding="utf-8"))


def review_table():
    import pandas as pd

    if new_corpus_active():
        if os.getenv('FOODY_DATABASE_URL'):
            from src.warehouse.loader import connection
            with connection() as conn:
                cursor=conn.execute("SELECT r.review_id,r.restaurant_id,s.restaurant_name,d.calendar_date::timestamp AS review_timestamp,r.rating AS raw_rating,r.review_text AS text,CASE WHEN r.overall_sentiment IS NOT NULL THEN lower(r.overall_sentiment) ELSE r.predicted_sentiment END AS predicted_sentiment,r.predicted_aspects,r.sentiment_confidence,r.absa_model_version IS NOT NULL AS valid_for_ai,r.order_id,r.relevance_label,r.source_dataset FROM fact_review_v2 r JOIN dim_sales_restaurant s USING(restaurant_id) JOIN dim_date d USING(date_key)")
                return pd.DataFrame(cursor.fetchall(),columns=[c.name for c in cursor.description])
        frame = pd.read_parquet(ENRICHED)
        return frame.rename(columns={"review_time": "review_timestamp",
                                     "rating": "raw_rating", "comment": "text"})
    return pd.read_parquet("data/foody/enriched/reviews.parquet")


def review_restaurants():
    frame = review_table()
    if new_corpus_active():
        return frame[["restaurant_id", "restaurant_name"]].drop_duplicates()
    return frame[["restaurant_key", "restaurant_name"]].drop_duplicates()


def review_identity(name_or_id):
    frame = review_restaurants()
    key = "restaurant_id" if new_corpus_active() else "restaurant_key"
    selected = frame.loc[frame[key].astype(str).eq(str(name_or_id)) |
                         frame.restaurant_name.eq(str(name_or_id))]
    if len(selected) != 1:
        raise ValueError(f"Restaurant not found or ambiguous: {name_or_id}")
    return str(selected.iloc[0][key]), selected.iloc[0]
