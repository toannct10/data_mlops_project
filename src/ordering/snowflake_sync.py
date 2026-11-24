"""Optional RAW-layer Snowflake mirror; local writes never depend on cloud auth."""

import os


def _config():
    required = ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_WAREHOUSE",
                "SNOWFLAKE_DATABASE")
    if not all(os.getenv(name) for name in required) or not (
        os.getenv("SNOWFLAKE_PASSWORD") or os.getenv("SNOWFLAKE_PRIVATE_KEY_FILE")
    ):
        return None
    values = {name.removeprefix("SNOWFLAKE_").lower(): os.environ[name]
              for name in required}
    values["role"] = os.getenv("SNOWFLAKE_ROLE", "FOODY_INGEST_ROLE")
    if password := os.getenv("SNOWFLAKE_PASSWORD"):
        values["password"] = password
    else:
        values["private_key_file"] = os.environ["SNOWFLAKE_PRIVATE_KEY_FILE"]
    return values


def _record(conn, kind, entity_id, status):
    from src.monitoring.metrics import SNOWFLAKE_SYNC

    conn.execute("""INSERT INTO live_sync_state(entity_type,entity_id,cloud_status)
        VALUES (%s,%s,%s) ON CONFLICT (entity_type,entity_id) DO UPDATE SET
        cloud_status=EXCLUDED.cloud_status,checked_at=now()""",
        (kind, entity_id, status))
    SNOWFLAKE_SYNC.labels(status=status).inc()
    return {"local_db": "SAVED", "snowflake": status}


def sync_entity(conn, kind, entity_id, *, connect=None):
    """Merge committed local rows into RAW, then record only the observed outcome."""
    if kind not in {"order", "review"}:
        raise ValueError("Unknown live sync entity")
    if os.getenv('FOODY_PORTABLE')=='true' and os.getenv('FOODY_LIVE_EVENT_SYNC_ENABLED','false').lower()!='true':
        return _record(conn, kind, entity_id, 'NOT_CONFIGURED')
    if os.getenv('FOODY_LIVE_EVENT_SYNC_ENABLED', 'false').lower() == 'true':
        # The committed transactional outbox is the sole cloud writer in scheduled mode.
        # Saving locally or publishing S3 never implies RAW/dbt acceptance.
        return _record(conn, kind, entity_id, 'OUTBOX_PENDING')
    settings = _config()
    if settings is None:
        return _record(conn, kind, entity_id, "NOT_CONFIGURED")
    try:
        if connect is None:
            from snowflake import connector

            connect = connector.connect
        with connect(**settings) as cloud:
            with cloud.cursor() as cursor:
                if kind == "order":
                    row = conn.execute("""SELECT order_id,restaurant_id,created_at,
                        updated_at,delivered_at,cancelled_at,total_amount,order_status
                        FROM fact_order WHERE order_id=%s AND source='live_web'""",
                        (entity_id,)).fetchone()
                    if row is None:
                        raise ValueError("Live order does not exist")
                    cursor.execute("""MERGE INTO RAW.LIVE_ORDER t USING
                        (SELECT %s order_id,%s restaurant_id,%s created_at,%s updated_at,
                         %s delivered_at,%s cancelled_at,%s total_amount,%s order_status) s
                        ON t.order_id=s.order_id
                        WHEN MATCHED THEN UPDATE SET restaurant_id=s.restaurant_id,
                          created_at=s.created_at,updated_at=s.updated_at,
                          delivered_at=s.delivered_at,cancelled_at=s.cancelled_at,
                          total_amount=s.total_amount,order_status=s.order_status,
                          loaded_at=CURRENT_TIMESTAMP()
                        WHEN NOT MATCHED THEN INSERT
                          (order_id,restaurant_id,created_at,updated_at,delivered_at,
                           cancelled_at,total_amount,order_status,source)
                        VALUES (s.order_id,s.restaurant_id,s.created_at,s.updated_at,
                                s.delivered_at,s.cancelled_at,s.total_amount,
                                s.order_status,'live_web')""", row)
                    for item in conn.execute("""SELECT order_item_id,order_id,menu_item_id,
                        variant_id,quantity,unit_price,subtotal FROM fact_order_item
                        WHERE order_id=%s""", (entity_id,)).fetchall():
                        cursor.execute("""MERGE INTO RAW.LIVE_ORDER_ITEM t USING
                            (SELECT %s order_item_id,%s order_id,%s menu_item_id,
                             %s variant_id,%s quantity,%s unit_price,%s subtotal) s
                            ON t.order_item_id=s.order_item_id
                            WHEN NOT MATCHED THEN INSERT
                            (order_item_id,order_id,menu_item_id,variant_id,quantity,
                             unit_price,subtotal) VALUES (s.order_item_id,s.order_id,
                             s.menu_item_id,s.variant_id,s.quantity,s.unit_price,
                             s.subtotal)""", item)
                else:
                    row = conn.execute("""SELECT review_id,order_id,restaurant_id,
                        rating,review_text,created_at,overall_sentiment
                        FROM fact_review_v2 WHERE review_id=%s AND source='live_web'""",
                        (entity_id,)).fetchone()
                    if row is None:
                        raise ValueError("Live review does not exist")
                    cursor.execute("""MERGE INTO RAW.LIVE_REVIEW t USING
                        (SELECT %s review_id,%s order_id,%s restaurant_id,%s rating,
                         %s review_text,%s created_at,%s overall_sentiment) s
                        ON t.review_id=s.review_id
                        WHEN NOT MATCHED THEN INSERT
                        (review_id,order_id,restaurant_id,rating,review_text,created_at,
                         overall_sentiment,source) VALUES (s.review_id,s.order_id,
                         s.restaurant_id,s.rating,s.review_text,s.created_at,
                         s.overall_sentiment,'live_web')""", row)
                    for aspect in conn.execute("""SELECT review_id,aspect,sentiment,
                        score,model_version FROM fact_review_aspect
                        WHERE review_id=%s""", (entity_id,)).fetchall():
                        cursor.execute("""MERGE INTO RAW.LIVE_REVIEW_ASPECT t USING
                            (SELECT %s review_id,%s aspect,%s sentiment,%s score,
                             %s model_version) s ON t.review_id=s.review_id
                            AND t.aspect=s.aspect AND t.model_version=s.model_version
                            WHEN MATCHED THEN UPDATE SET sentiment=s.sentiment,score=s.score
                            WHEN NOT MATCHED THEN INSERT
                            (review_id,aspect,sentiment,score,model_version)
                            VALUES (s.review_id,s.aspect,s.sentiment,s.score,
                                    s.model_version)""", aspect)
            cloud.commit()
        status = "SYNCED"
    except Exception:
        # No credential details leave this boundary; local data remains authoritative.
        status = "FAILED"
    return _record(conn, kind, entity_id, status)
