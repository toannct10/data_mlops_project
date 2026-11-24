import json
import os
from collections import defaultdict

import re


BUCKET = os.getenv(
    "FOODY_S3_BUCKET",
    "foody-intelligence-aip491-cloud-2026",
)

S3_PREFIX = os.getenv(
    "FOODY_LIVE_EVENT_PREFIX",
    "foody/events/v1/",
)

DATABASE = os.getenv("SNOWFLAKE_DATABASE", "FOODY")
RAW_SCHEMA = os.getenv("SNOWFLAKE_RAW_SCHEMA", "RAW")
if not all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', v) for v in (DATABASE, RAW_SCHEMA)):
    raise ValueError('Invalid Snowflake identifier')


ORDER_EVENT_TYPES = {
    "ORDER_CREATED",
    "STATUS_CHANGED",
    "ORDER_CANCELLED",
    "ORDER_DELIVERED",
}


def snowflake_connection():
    import snowflake.connector
    conn = snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        role=os.getenv("SNOWFLAKE_PIPELINE_ROLE", "FOODY_PIPELINE"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "FOODY_WH"),
        database=DATABASE,
        schema=RAW_SCHEMA,
        autocommit=False,
        login_timeout=15,
        network_timeout=15,
        session_parameters={'TIMEZONE': 'UTC', 'LOCK_TIMEOUT': 30,
                            'STATEMENT_TIMEOUT_IN_SECONDS': 120},
    )
    conn.cursor().execute('USE SECONDARY ROLES NONE')
    return conn


def _s3_client():
    import boto3
    return boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION", "ap-southeast-2"),
    )


def read_events(processed=None):
    processed = processed or {}
    s3 = _s3_client()

    events = []

    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(
        Bucket=BUCKET,
        Prefix=S3_PREFIX,
    ):
        for obj in page.get("Contents", []):
            key = obj["Key"]

            if not key.endswith(".json"):
                continue

            etag = obj.get('ETag', '').strip('"')
            if not etag:raise ValueError('Missing immutable object version')
            if key in processed:
                if processed[key] != etag:
                    raise ValueError('Previously ingested immutable S3 object changed')
                continue

            body = s3.get_object(
                Bucket=BUCKET,
                Key=key,
            )["Body"].read()

            event = json.loads(body)

            validate_event(event)
            if key != S3_PREFIX.rstrip('/')+'/'+str(event['event_id'])+'.json':
                raise ValueError('Event identity must match its canonical immutable object key')

            event["_s3_key"] = key
            event['_s3_etag'] = etag
            events.append(event)

    events.sort(
        key=lambda e: int(e.get("event_sequence", 0))
    )

    return events


def validate_event(event):
    if int(event.get('schema_version', 0)) != 1:
        raise ValueError('Unsupported event schema')
    if not event.get('event_id') or int(event.get('event_sequence', 0)) <= 0:
        raise ValueError('Missing event identity or sequence')
    kind = event.get('event_type')
    entity = 'order' if kind in ORDER_EVENT_TYPES else 'review' if kind == 'REVIEW_SUBMITTED' else None
    if entity is None or not event.get('payload', {}).get(entity, {}).get(entity + '_id'):
        raise ValueError('Invalid canonical event payload')


def collapse_events(events):
    """
    Với mỗi order chỉ giữ event có event_sequence mới nhất.

    REVIEW_SUBMITTED giữ theo review_id.
    """

    latest_orders = {}
    reviews = {}

    for event in events:
        event_type = event.get("event_type")
        sequence = int(event.get("event_sequence", 0))

        if event_type in ORDER_EVENT_TYPES:
            order = event.get("payload", {}).get("order")

            if not order:
                continue

            order_id = order.get("order_id")

            if not order_id:
                continue

            previous = latest_orders.get(order_id)

            if (
                previous is None
                or sequence > previous["event_sequence"]
            ):
                latest_orders[order_id] = {
                    "event_sequence": sequence,
                    "event": event,
                    "order": order,
                }

        elif event_type == "REVIEW_SUBMITTED":
            review = event.get("payload", {}).get("review")

            if not review:
                continue

            review_id = review.get("review_id")

            if review_id:
                previous = reviews.get(review_id)

                if (
                    previous is None
                    or sequence > previous["event_sequence"]
                ):
                    reviews[review_id] = {
                        "event_sequence": sequence,
                        "event": event,
                        "review": review,
                    }

    return latest_orders, reviews


ORDER_MERGE_SQL = f"""
MERGE INTO {DATABASE}.{RAW_SCHEMA}.ORDERS AS t

USING (
    SELECT
        %s::TEXT AS ORDER_ID,
        %s::TEXT AS RESTAURANT_ID,
        %s::TEXT AS DATE_KEY,
        %s::NUMBER(18,2) AS SALES_AMOUNT,
        %s::TEXT AS ORDER_STATUS,

        CAST(%s AS TIMESTAMP_TZ)::TIMESTAMP_NTZ AS CREATED_AT,
        CAST(%s AS TIMESTAMP_TZ)::TIMESTAMP_NTZ AS UPDATED_AT,
        CAST(%s AS TIMESTAMP_TZ)::TIMESTAMP_NTZ AS DELIVERED_AT,
        CAST(%s AS TIMESTAMP_TZ)::TIMESTAMP_NTZ AS CANCELLED_AT,

        %s::NUMBER(18,2) AS TOTAL_AMOUNT,

        %s::TEXT AS SOURCE,
        %s::TEXT AS SOURCE_DATASET,
        %s::TEXT AS SOURCE_ORDER_ID,
        %s::TEXT AS SOURCE_ROW_SHA256,

        %s::TEXT AS RESTAURANT_NAME,
        %s::TEXT AS CANONICAL_ORDER_ID,

        CAST(%s AS TIMESTAMP_TZ)::TIMESTAMP_NTZ AS ORDER_DATE,

        %s::TEXT AS SOURCE_FILE
) AS s

ON t.ORDER_ID = s.ORDER_ID

WHEN MATCHED THEN UPDATE SET
    RESTAURANT_ID       = s.RESTAURANT_ID,
    DATE_KEY            = s.DATE_KEY,
    SALES_AMOUNT        = s.SALES_AMOUNT,
    ORDER_STATUS        = s.ORDER_STATUS,
    CREATED_AT          = s.CREATED_AT,
    UPDATED_AT          = s.UPDATED_AT,
    DELIVERED_AT        = s.DELIVERED_AT,
    CANCELLED_AT        = s.CANCELLED_AT,
    TOTAL_AMOUNT        = s.TOTAL_AMOUNT,
    SOURCE              = s.SOURCE,
    SOURCE_DATASET      = s.SOURCE_DATASET,
    SOURCE_ORDER_ID     = s.SOURCE_ORDER_ID,
    SOURCE_ROW_SHA256   = s.SOURCE_ROW_SHA256,
    RESTAURANT_NAME     = COALESCE(
                            s.RESTAURANT_NAME,
                            t.RESTAURANT_NAME
                          ),
    CANONICAL_ORDER_ID  = s.CANONICAL_ORDER_ID,
    ORDER_DATE          = s.ORDER_DATE,
    SOURCE_FILE         = s.SOURCE_FILE,
    LOADED_AT           = CURRENT_TIMESTAMP()

WHEN NOT MATCHED THEN INSERT (
    ORDER_ID,
    RESTAURANT_ID,
    DATE_KEY,
    SALES_AMOUNT,
    ORDER_STATUS,
    CREATED_AT,
    UPDATED_AT,
    DELIVERED_AT,
    CANCELLED_AT,
    TOTAL_AMOUNT,
    SOURCE,
    SOURCE_DATASET,
    SOURCE_ORDER_ID,
    SOURCE_ROW_SHA256,
    RESTAURANT_NAME,
    CANONICAL_ORDER_ID,
    ORDER_DATE,
    SOURCE_FILE,
    LOADED_AT
)
VALUES (
    s.ORDER_ID,
    s.RESTAURANT_ID,
    s.DATE_KEY,
    s.SALES_AMOUNT,
    s.ORDER_STATUS,
    s.CREATED_AT,
    s.UPDATED_AT,
    s.DELIVERED_AT,
    s.CANCELLED_AT,
    s.TOTAL_AMOUNT,
    s.SOURCE,
    s.SOURCE_DATASET,
    s.SOURCE_ORDER_ID,
    s.SOURCE_ROW_SHA256,
    s.RESTAURANT_NAME,
    s.CANONICAL_ORDER_ID,
    s.ORDER_DATE,
    s.SOURCE_FILE,
    CURRENT_TIMESTAMP()
)
"""


REVIEW_MERGE_SQL = f"""
MERGE INTO {DATABASE}.{RAW_SCHEMA}.REVIEWS AS t

USING (
    SELECT
        %s::TEXT AS REVIEW_ID,
        %s::TEXT AS RESTAURANT_ID,
        %s::TEXT AS DATE_KEY,
        %s::FLOAT AS RATING,

        %s::TEXT AS PREDICTED_SENTIMENT,
        %s::FLOAT AS SENTIMENT_CONFIDENCE,
        %s::TEXT AS PREDICTED_ASPECTS,
        %s::TEXT AS REVIEW_TEXT,
        %s::TEXT AS ORDER_ID,

        CAST(%s AS TIMESTAMP_TZ)::TIMESTAMP_NTZ AS CREATED_AT,

        %s::TEXT AS SOURCE,
        %s::TEXT AS OVERALL_SENTIMENT,
        %s::TEXT AS OVERALL_SENTIMENT_REASON,
        %s::TEXT AS ABSA_MODEL_VERSION,

        %s::TEXT AS RELEVANCE_LABEL,
        %s::FLOAT AS RELEVANCE_SCORE,
        %s::TEXT AS RELEVANCE_MODEL,
        %s::TEXT AS RELEVANCE_MODEL_VERSION,
        %s::TEXT AS RELEVANCE_REASON,

        %s::TEXT AS ASPECT_STATUS,
        %s::TEXT AS SOURCE_DATASET,
        %s::TEXT AS SOURCE_ORDER_ID,
        %s::TEXT AS SOURCE_ROW_SHA256,
        %s::TEXT AS REVIEW_ADDRESS,
        %s::TEXT AS SOURCE_REVIEW_TIME,

        CAST(%s AS TIMESTAMP_TZ)::TIMESTAMP_NTZ AS REVIEW_TIMESTAMP,

        %s::TEXT AS ABSA_PREDICTIONS,
        %s::TEXT AS RESTAURANT_NAME,
        %s::TEXT AS CANONICAL_ORDER_ID,

        CAST(%s AS TIMESTAMP_TZ)::TIMESTAMP_NTZ AS REVIEW_DATE,

        %s::TEXT AS COMMENT,
        %s::TEXT AS SOURCE_FILE
) AS s

ON t.REVIEW_ID = s.REVIEW_ID

WHEN MATCHED THEN UPDATE SET
    RESTAURANT_ID             = s.RESTAURANT_ID,
    DATE_KEY                  = s.DATE_KEY,
    RATING                    = s.RATING,
    PREDICTED_SENTIMENT       = s.PREDICTED_SENTIMENT,
    SENTIMENT_CONFIDENCE      = s.SENTIMENT_CONFIDENCE,
    PREDICTED_ASPECTS         = s.PREDICTED_ASPECTS,
    REVIEW_TEXT               = s.REVIEW_TEXT,
    ORDER_ID                  = s.ORDER_ID,
    CREATED_AT                = s.CREATED_AT,
    SOURCE                    = s.SOURCE,
    OVERALL_SENTIMENT         = s.OVERALL_SENTIMENT,
    OVERALL_SENTIMENT_REASON  = s.OVERALL_SENTIMENT_REASON,
    ABSA_MODEL_VERSION        = s.ABSA_MODEL_VERSION,
    RELEVANCE_LABEL           = s.RELEVANCE_LABEL,
    RELEVANCE_SCORE           = s.RELEVANCE_SCORE,
    RELEVANCE_MODEL           = s.RELEVANCE_MODEL,
    RELEVANCE_MODEL_VERSION   = s.RELEVANCE_MODEL_VERSION,
    RELEVANCE_REASON          = s.RELEVANCE_REASON,
    ASPECT_STATUS             = s.ASPECT_STATUS,
    SOURCE_DATASET            = s.SOURCE_DATASET,
    SOURCE_ORDER_ID           = s.SOURCE_ORDER_ID,
    SOURCE_ROW_SHA256         = s.SOURCE_ROW_SHA256,
    REVIEW_ADDRESS            = s.REVIEW_ADDRESS,
    SOURCE_REVIEW_TIME        = s.SOURCE_REVIEW_TIME,
    REVIEW_TIMESTAMP          = s.REVIEW_TIMESTAMP,
    ABSA_PREDICTIONS          = s.ABSA_PREDICTIONS,
    RESTAURANT_NAME           = COALESCE(
                                  s.RESTAURANT_NAME,
                                  t.RESTAURANT_NAME
                                ),
    CANONICAL_ORDER_ID        = s.CANONICAL_ORDER_ID,
    REVIEW_DATE               = s.REVIEW_DATE,
    COMMENT                   = s.COMMENT,
    SOURCE_FILE               = s.SOURCE_FILE,
    LOADED_AT                 = CURRENT_TIMESTAMP()

WHEN NOT MATCHED THEN INSERT (
    REVIEW_ID,
    RESTAURANT_ID,
    DATE_KEY,
    RATING,
    PREDICTED_SENTIMENT,
    SENTIMENT_CONFIDENCE,
    PREDICTED_ASPECTS,
    REVIEW_TEXT,
    ORDER_ID,
    CREATED_AT,
    SOURCE,
    OVERALL_SENTIMENT,
    OVERALL_SENTIMENT_REASON,
    ABSA_MODEL_VERSION,
    RELEVANCE_LABEL,
    RELEVANCE_SCORE,
    RELEVANCE_MODEL,
    RELEVANCE_MODEL_VERSION,
    RELEVANCE_REASON,
    ASPECT_STATUS,
    SOURCE_DATASET,
    SOURCE_ORDER_ID,
    SOURCE_ROW_SHA256,
    REVIEW_ADDRESS,
    SOURCE_REVIEW_TIME,
    REVIEW_TIMESTAMP,
    ABSA_PREDICTIONS,
    RESTAURANT_NAME,
    CANONICAL_ORDER_ID,
    REVIEW_DATE,
    COMMENT,
    SOURCE_FILE,
    LOADED_AT
)
VALUES (
    s.REVIEW_ID,
    s.RESTAURANT_ID,
    s.DATE_KEY,
    s.RATING,
    s.PREDICTED_SENTIMENT,
    s.SENTIMENT_CONFIDENCE,
    s.PREDICTED_ASPECTS,
    s.REVIEW_TEXT,
    s.ORDER_ID,
    s.CREATED_AT,
    s.SOURCE,
    s.OVERALL_SENTIMENT,
    s.OVERALL_SENTIMENT_REASON,
    s.ABSA_MODEL_VERSION,
    s.RELEVANCE_LABEL,
    s.RELEVANCE_SCORE,
    s.RELEVANCE_MODEL,
    s.RELEVANCE_MODEL_VERSION,
    s.RELEVANCE_REASON,
    s.ASPECT_STATUS,
    s.SOURCE_DATASET,
    s.SOURCE_ORDER_ID,
    s.SOURCE_ROW_SHA256,
    s.REVIEW_ADDRESS,
    s.SOURCE_REVIEW_TIME,
    s.REVIEW_TIMESTAMP,
    s.ABSA_PREDICTIONS,
    s.RESTAURANT_NAME,
    s.CANONICAL_ORDER_ID,
    s.REVIEW_DATE,
    s.COMMENT,
    s.SOURCE_FILE,
    CURRENT_TIMESTAMP()
)
"""


def sequence_guarded_merge(sql, timestamp):
    """Only newer events may replace state; legacy rows also retain newer timestamps."""
    sql = sql.replace('%s::TEXT AS SOURCE_FILE',
        '%s::TEXT AS SOURCE_FILE, %s::NUMBER AS LAST_EVENT_SEQUENCE, %s::TEXT AS LAST_EVENT_ID')
    legacy_guard = "AND (UPPER(t.ORDER_STATUS) NOT IN ('DELIVERED','CANCELLED') OR UPPER(s.ORDER_STATUS)=UPPER(t.ORDER_STATUS))" if timestamp=='UPDATED_AT' else ''
    source_time = 'COALESCE(s.UPDATED_AT,s.CREATED_AT)' if timestamp=='UPDATED_AT' else 's.CREATED_AT'
    target_time = 'COALESCE(t.UPDATED_AT,t.DELIVERED_AT,t.CANCELLED_AT,t.CREATED_AT)' if timestamp=='UPDATED_AT' else 't.CREATED_AT'
    sql = sql.replace('WHEN MATCHED THEN UPDATE SET', f'''WHEN MATCHED AND (
        (t.LAST_EVENT_SEQUENCE IS NOT NULL AND s.LAST_EVENT_SEQUENCE > t.LAST_EVENT_SEQUENCE)
        OR (t.LAST_EVENT_SEQUENCE IS NULL AND {source_time} IS NOT NULL
            AND ({target_time} IS NULL OR {source_time} >= {target_time}) {legacy_guard})
        ) THEN UPDATE SET
        LAST_EVENT_SEQUENCE = s.LAST_EVENT_SEQUENCE, LAST_EVENT_ID = s.LAST_EVENT_ID,''')
    sql = sql.replace('    SOURCE_FILE,\n    LOADED_AT',
        '    SOURCE_FILE,\n    LAST_EVENT_SEQUENCE,\n    LAST_EVENT_ID,\n    LOADED_AT')
    return sql.replace('    s.SOURCE_FILE,\n    CURRENT_TIMESTAMP()',
        '    s.SOURCE_FILE,\n    s.LAST_EVENT_SEQUENCE,\n    s.LAST_EVENT_ID,\n    CURRENT_TIMESTAMP()')


ORDER_MERGE_SQL = sequence_guarded_merge(ORDER_MERGE_SQL, 'UPDATED_AT')
REVIEW_MERGE_SQL = sequence_guarded_merge(REVIEW_MERGE_SQL, 'CREATED_AT')


def business_date(record):
    from datetime import datetime
    return datetime.strptime(str(record['date_key']), '%Y%m%d').date().isoformat()


def restaurant_name(cursor, record):
    if record.get('restaurant_name'):
        return record['restaurant_name']
    cursor.execute(f'SELECT MAX(RESTAURANT_NAME) FROM {DATABASE}.{RAW_SCHEMA}.ORDERS WHERE RESTAURANT_ID=%s',
                   (record['restaurant_id'],))
    return cursor.fetchone()[0]


def merge_order(cursor, item):
    event = item["event"]
    order = item["order"]

    created_at = order.get("created_at")

    params = (
        order.get("order_id"),
        order.get("restaurant_id"),
        str(order.get("date_key"))
        if order.get("date_key") is not None
        else None,
        order.get("sales_amount"),
        order.get("order_status"),
        created_at,
        order.get("updated_at"),
        order.get("delivered_at"),
        order.get("cancelled_at"),
        order.get("total_amount"),
        order.get("source", "live_web"),
        order.get("source_dataset"),
        order.get("source_order_id"),
        order.get("source_row_sha256"),
        restaurant_name(cursor, order),
        order.get("order_id"),
        business_date(order),
        event["_s3_key"],
        event['event_sequence'],
        event['event_id'],
    )

    cursor.execute(ORDER_MERGE_SQL, params)


def _json_text(value):
    if value is None:
        return None

    if isinstance(value, str):
        return value

    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def merge_review(cursor, item):
    event = item["event"]
    review = item["review"]

    created_at = review.get("created_at")
    review_timestamp = review.get("review_timestamp")

    review_text = review.get("review_text")

    params = (
        review.get("review_id"),
        review.get("restaurant_id"),
        str(review.get("date_key"))
        if review.get("date_key") is not None
        else None,
        review.get("rating"),
        review.get("predicted_sentiment"),
        review.get("sentiment_confidence"),
        _json_text(review.get("predicted_aspects", [])),
        review_text,
        review.get("order_id"),
        created_at,
        review.get("source", "live_web"),
        review.get("overall_sentiment"),
        review.get("overall_sentiment_reason"),
        review.get("absa_model_version"),
        review.get("relevance_label"),
        review.get("relevance_score"),
        review.get("relevance_model"),
        review.get("relevance_model_version"),
        review.get("relevance_reason"),
        review.get("aspect_status"),
        review.get("source_dataset"),
        review.get("source_order_id"),
        review.get("source_row_sha256"),
        review.get("review_address"),
        review.get("source_review_time"),
        review_timestamp,
        _json_text(review.get("absa_predictions")),
        restaurant_name(cursor, review),
        review.get("order_id"),
        business_date(review),
        review_text,
        event["_s3_key"],
        event['event_sequence'],
        event['event_id'],
    )

    cursor.execute(REVIEW_MERGE_SQL, params)


def load_incremental_events():
    conn = snowflake_connection()

    try:
        cursor = conn.cursor()
        cursor.execute('BEGIN')
        # Provisioned singleton row. UPDATE takes an exclusive resource lock until commit.
        cursor.execute(f"UPDATE {DATABASE}.{RAW_SCHEMA}.EVENT_PIPELINE_LOCK SET LOCK_TOKEN=UUID_STRING() WHERE LOCK_ID=1")
        if cursor.rowcount != 1:
            raise RuntimeError('Cloud pipeline migration/lock row is missing')
        cursor.execute(f'SELECT S3_KEY, S3_ETAG FROM {DATABASE}.{RAW_SCHEMA}.EVENT_INGEST_AUDIT')
        processed = dict(cursor.fetchall())
        events = read_events(processed)
        latest_orders, reviews = collapse_events(events)

        cursor.execute(
            f"SELECT COUNT(*) FROM {DATABASE}.{RAW_SCHEMA}.ORDERS"
        )
        orders_before = cursor.fetchone()[0]

        cursor.execute(
            f"SELECT COUNT(*) FROM {DATABASE}.{RAW_SCHEMA}.REVIEWS"
        )
        reviews_before = cursor.fetchone()[0]

        for item in latest_orders.values():
            merge_order(cursor, item)

        for item in reviews.values():
            merge_review(cursor, item)

        for event in events:
            cursor.execute(f'''MERGE INTO {DATABASE}.{RAW_SCHEMA}.EVENT_INGEST_AUDIT t
                USING (SELECT %s::TEXT EVENT_ID, %s::TEXT S3_KEY, %s::TEXT S3_ETAG,
                %s::NUMBER EVENT_SEQUENCE, %s::TEXT EVENT_TYPE) s ON t.EVENT_ID=s.EVENT_ID
                WHEN NOT MATCHED THEN INSERT (EVENT_ID,S3_KEY,S3_ETAG,EVENT_SEQUENCE,EVENT_TYPE,APPLIED_AT)
                VALUES(s.EVENT_ID,s.S3_KEY,s.S3_ETAG,s.EVENT_SEQUENCE,s.EVENT_TYPE,CURRENT_TIMESTAMP())''',
                (event['event_id'],event['_s3_key'],event['_s3_etag'],event['event_sequence'],event['event_type']))

        conn.commit()

        cursor.execute(
            f"SELECT COUNT(*) FROM {DATABASE}.{RAW_SCHEMA}.ORDERS"
        )
        orders_after = cursor.fetchone()[0]

        cursor.execute(
            f"SELECT COUNT(*) FROM {DATABASE}.{RAW_SCHEMA}.REVIEWS"
        )
        reviews_after = cursor.fetchone()[0]

        result = {
            "status": "SUCCESS",
            "s3_events": len(events),
            "orders_collapsed": len(latest_orders),
            "reviews_collapsed": len(reviews),
            "orders_before": orders_before,
            "orders_after": orders_after,
            "reviews_before": reviews_before,
            "reviews_after": reviews_after,
        }
        cursor.execute(f'SELECT MAX(LOADED_AT) FROM {DATABASE}.{RAW_SCHEMA}.ORDERS')
        result['raw_loaded_at'] = str(cursor.fetchone()[0])

        print(json.dumps(result, indent=2))

        return result

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def verify_live_records():
    conn = snowflake_connection()

    try:
        cursor = conn.cursor()

        cursor.execute(
            f"""
            SELECT
                ORDER_ID,
                ORDER_STATUS,
                SALES_AMOUNT,
                RESTAURANT_ID
            FROM {DATABASE}.{RAW_SCHEMA}.ORDERS
            WHERE SOURCE = 'live_web'
            ORDER BY LOADED_AT DESC
            LIMIT 20
            """
        )

        orders = cursor.fetchall()

        cursor.execute(
            f"""
            SELECT
                REVIEW_ID,
                ORDER_ID,
                RATING,
                PREDICTED_SENTIMENT,
                RESTAURANT_ID
            FROM {DATABASE}.{RAW_SCHEMA}.REVIEWS
            WHERE SOURCE = 'live_web'
            ORDER BY LOADED_AT DESC
            LIMIT 20
            """
        )

        reviews = cursor.fetchall()

        return {
            "orders": orders,
            "reviews": reviews,
        }

    finally:
        conn.close()


def verify_cloud_quality():
    """Read-only current counts and keys; no fixed historical acceptance counts."""
    conn = snowflake_connection()
    try:
        c = conn.cursor()
        counts = {}
        for table, key in (('ORDERS','ORDER_ID'), ('REVIEWS','REVIEW_ID')):
            c.execute(f'SELECT COUNT(*), COUNT(DISTINCT {key}) FROM {DATABASE}.{RAW_SCHEMA}.{table}')
            total, unique = c.fetchone()
            c.execute(f'SELECT COUNT(*) FROM {DATABASE}.STAGING.STG_{table}')
            staged = c.fetchone()[0]
            if total != unique or total != staged:
                raise ValueError('Canonical uniqueness or staging parity failed')
            counts[table.lower()] = total
        c.execute(f'''SELECT COUNT(*) FROM {DATABASE}.{RAW_SCHEMA}.REVIEWS r
            LEFT JOIN {DATABASE}.{RAW_SCHEMA}.ORDERS o ON r.ORDER_ID=o.ORDER_ID
            WHERE r.SOURCE='live_web' AND r.ORDER_ID IS NOT NULL AND o.ORDER_ID IS NULL''')
        if c.fetchone()[0]: raise ValueError('Live review order linkage failed')
        c.execute(f'SELECT COUNT(*),COUNT(DISTINCT EVENT_ID) FROM {DATABASE}.{RAW_SCHEMA}.EVENT_INGEST_AUDIT')
        total, unique = c.fetchone()
        if total != unique: raise ValueError('Duplicate event audit identity')
        return {'status':'PASS', **counts, 'audited_events':total}
    finally:
        conn.close()


if __name__ == "__main__":
    load_incremental_events()
