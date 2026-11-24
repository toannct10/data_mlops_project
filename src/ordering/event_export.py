"""Durable outbox → immutable S3 objects. Publication is NOT warehouse acceptance."""

import hashlib
import json
import os
from datetime import UTC, datetime

from src.cloud_foundation import CloudConfig, s3_client


def encode_event(event):
    return json.dumps(
        event, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def publish_immutable(client, config, event):
    body = encode_event(event)
    checksum = hashlib.sha256(body).hexdigest()
    # event_id comes from the database UUID, not user input.
    from uuid import UUID

    identifier = str(UUID(event["event_id"]))
    key = f"{config.prefix}/events/v1/{identifier}.json"
    try:
        client.put_object(
            Bucket=config.bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
            Metadata={"sha256": checksum},
            IfNoneMatch="*",
        )
    except Exception as error:
        code = str(getattr(error, "response", {}).get("Error", {}).get("Code", ""))
        if code not in {"PreconditionFailed", "412"}:
            raise
        prior = client.get_object(Bucket=config.bucket, Key=key)["Body"].read()
        if hashlib.sha256(prior).hexdigest() != checksum:
            raise ValueError("Immutable event content mismatch") from None
    return key


def export_pending(conn, *, config=None, client=None, limit=100):
    if os.getenv("FOODY_LIVE_EVENT_SYNC_ENABLED", "false").lower() != "true":
        return {"status": "DISABLED", "published": 0}
    if not 1 <= limit <= 1000:
        raise ValueError("Event batch limit must be 1..1000")
    config = config or CloudConfig.from_env()
    client = client or s3_client(config)
    published = failed = 0
    from src.monitoring.metrics import ONLINE_EVENT_PENDING, ONLINE_EVENT_PUBLISH

    # Caller owns one transaction. SKIP LOCKED prevents concurrent publisher overlap;
    # a crash after PUT but before commit safely retries the same immutable body.
    rows = conn.execute(
        """SELECT event_id,event_sequence,event_type,entity_id,restaurant_id,
        event_timestamp,payload FROM online_event_outbox WHERE published_at IS NULL
        ORDER BY event_sequence FOR UPDATE SKIP LOCKED LIMIT %s""",
        (limit,),
    ).fetchall()
    for event_id, sequence, kind, entity, restaurant, timestamp, payload in rows:
        event = {
            "schema_version": 1,
            "event_id": event_id,
            "event_sequence": sequence,
            "event_type": kind,
            "entity_id": entity,
            "restaurant_id": restaurant,
            "event_timestamp": timestamp.astimezone(UTC).isoformat(),
            "source": "live_web",
            "payload": payload,
        }
        try:
            key = publish_immutable(client, config, event)
        except Exception as error:
            # Bounded category only; never persist SDK exception strings or secrets.
            conn.execute(
                """UPDATE online_event_outbox SET attempts=attempts+1,
                last_error_category=%s WHERE event_id=%s""",
                (type(error).__name__, event_id),
            )
            ONLINE_EVENT_PUBLISH.labels(status="failed").inc()
            failed += 1
        else:
            conn.execute(
                """UPDATE online_event_outbox SET published_at=%s,s3_object_key=%s,
                attempts=attempts+1,last_error_category=NULL WHERE event_id=%s""",
                (datetime.now(UTC), key, event_id),
            )
            ONLINE_EVENT_PUBLISH.labels(status="published").inc()
            published += 1
    pending = conn.execute(
        "SELECT COUNT(*) FROM online_event_outbox WHERE published_at IS NULL"
    ).fetchone()[0]
    ONLINE_EVENT_PENDING.set(pending)
    return {
        "status": "FAILED" if failed else "PUBLISHED",
        "published": published,
        "failed": failed,
        "pending": pending,
        "warehouse_status": "NOT_VERIFIED",
    }
