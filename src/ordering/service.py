"""Parameterized PostgreSQL writes for the three-restaurant ordering demo."""

import json
from datetime import datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.ordering.menu import LIVE_IDS
from src.ordering.sentiment import overall_sentiment

VIETNAM = ZoneInfo("Asia/Ho_Chi_Minh")
NEXT = {"PENDING": "PREPARING", "PREPARING": "DELIVERING",
        "DELIVERING": "DELIVERED"}


def _one(cursor):
    row = cursor.fetchone()
    return dict(zip((column.name for column in cursor.description), row,
                    strict=True)) if row is not None else None


def _date_key(conn, when):
    day = when.astimezone(VIETNAM).date()
    key = int(day.strftime("%Y%m%d"))
    conn.execute("""INSERT INTO dim_date(date_key,calendar_date,day,month,year)
        VALUES (%s,%s,%s,%s,%s) ON CONFLICT (date_key) DO NOTHING""",
        (key, day, day.day, day.month, day.year))
    return key


def create_order(conn, restaurant_id, lines, *, now=None):
    """Read server-side prices, reject unavailable variants, then write one atomic order."""
    restaurant_id = str(restaurant_id)
    if restaurant_id not in LIVE_IDS or not lines:
        raise ValueError("Choose a live-demo restaurant and at least one menu item")
    if any(type(line.get("quantity")) is not int or
           not 1 <= line["quantity"] <= 99 for line in lines):
        raise ValueError("Quantity must be an integer from 1 to 99")
    if _one(conn.execute("SELECT restaurant_id FROM dim_sales_restaurant WHERE "
                         "restaurant_id=%s AND is_live_demo=TRUE", (restaurant_id,))) is None:
        raise ValueError("Restaurant is not enabled for live ordering")
    details = []
    total = Decimal(0)
    for line in lines:
        quantity = line["quantity"]
        item = _one(conn.execute("""SELECT m.menu_item_id,m.item_name,
            v.variant_id,v.variant_name,v.unit_price
            FROM menu_item_variant v JOIN dim_menu_item m USING (menu_item_id)
            WHERE v.variant_id=%s AND m.restaurant_id=%s
              AND m.is_available=TRUE AND v.is_available=TRUE
              AND v.unit_price IS NOT NULL""",
            (str(line["variant_id"]), restaurant_id)))
        if item is None:
            raise ValueError("Menu variant is unavailable or has no exact price")
        subtotal = Decimal(item["unit_price"]) * quantity
        details.append((item, quantity, subtotal))
        total += subtotal
    now = now or datetime.now(VIETNAM)
    order_id = f"LIVE-{now:%Y%m%d}-{uuid4().hex[:12].upper()}"
    date_key = _date_key(conn, now)
    conn.execute("""INSERT INTO fact_order
        (order_id,restaurant_id,date_key,sales_amount,total_amount,order_status,
         source,created_at,updated_at)
        VALUES (%s,%s,%s,%s,%s,'PENDING','live_web',%s,%s)""",
        (order_id, restaurant_id, date_key, total, total, now, now))
    for number, (item, quantity, subtotal) in enumerate(details, 1):
        conn.execute("""INSERT INTO fact_order_item
            (order_item_id,order_id,menu_item_id,variant_id,item_name_snapshot,
             variant_name_snapshot,quantity,unit_price,subtotal)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (f"{order_id}-ITEM-{number}", order_id,
             item["menu_item_id"], item["variant_id"], item["item_name"],
             item["variant_name"], quantity, item["unit_price"], subtotal))
    conn.execute("""INSERT INTO order_status_history
        (order_id,previous_status,new_status,changed_at)
        VALUES (%s,NULL,'PENDING',%s)""", (order_id, now))
    return {"order_id": order_id, "restaurant_id": restaurant_id,
            "status": "PENDING", "total_amount": total, "source": "live_web"}


def get_order(conn, order_id):
    return _one(conn.execute("""SELECT order_id,restaurant_id,total_amount,
        order_status AS status,source,created_at,updated_at,delivered_at,cancelled_at
        FROM fact_order WHERE order_id=%s""", (order_id,)))


def list_orders(conn, *, restaurant_id=None, limit=50):
    if restaurant_id is not None and str(restaurant_id) not in LIVE_IDS:
        raise ValueError("Unknown live-demo restaurant")
    cursor = conn.execute("""SELECT o.order_id,o.restaurant_id,r.restaurant_name,
        o.total_amount,o.order_status AS status,o.created_at,o.delivered_at
        FROM fact_order o JOIN dim_sales_restaurant r USING (restaurant_id)
        WHERE o.source='live_web' AND (%s::text IS NULL OR o.restaurant_id=%s)
        ORDER BY o.created_at DESC LIMIT %s""",
        (restaurant_id, restaurant_id, min(max(int(limit), 1), 100)))
    columns = [column.name for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def change_status(conn, order_id, *, cancel=False, now=None):
    current = _one(conn.execute("""SELECT order_id,order_status,source
        FROM fact_order WHERE order_id=%s FOR UPDATE""", (order_id,)))
    if current is None or current["source"] != "live_web":
        raise ValueError("Unknown live order")
    before = current["order_status"]
    if cancel:
        if before not in NEXT:
            raise ValueError("Only an undelivered order can be cancelled")
        after = "CANCELLED"
    else:
        if before not in NEXT:
            raise ValueError("Order has no permitted next status")
        after = NEXT[before]
    now = now or datetime.now(VIETNAM)
    conn.execute("""UPDATE fact_order SET order_status=%s,updated_at=%s,
        delivered_at=CASE WHEN %s='DELIVERED' THEN %s ELSE delivered_at END,
        cancelled_at=CASE WHEN %s='CANCELLED' THEN %s ELSE cancelled_at END
        WHERE order_id=%s""", (after, now, after, now, after, now, order_id))
    conn.execute("""INSERT INTO order_status_history
        (order_id,previous_status,new_status,changed_at) VALUES (%s,%s,%s,%s)""",
        (order_id, before, after, now))
    return {"order_id": order_id, "previous_status": before, "status": after}


def live_daily_facts(conn, restaurant_id, *, day=None):
    """Use the uncached delivered-only SQL view for the current demo day."""
    day = day or datetime.now(VIETNAM).date()
    if str(restaurant_id) not in LIVE_IDS:
        raise ValueError("Unknown live-demo restaurant")
    row = _one(conn.execute("""SELECT delivered_count,delivered_sales,
        cancelled_count,order_count FROM restaurant_live_demo_daily_sales
        WHERE restaurant_id=%s AND period_start=%s""", (str(restaurant_id), day)))
    return {"restaurant_id": str(restaurant_id), "day": day,
            "delivered_count": int(row["delivered_count"]) if row else 0,
            "delivered_revenue": row["delivered_sales"] if row else Decimal(0),
            "cancelled_count": int(row["cancelled_count"]) if row else 0,
            "order_count": int(row["order_count"]) if row else 0}


def submit_review(conn, order_id, rating, comment, *, predict=None, now=None):
    """Keep raw text and the native PhoBERT aspects; one review per delivered order."""
    if type(rating) is not int or not 1 <= rating <= 5 or not comment.strip():
        raise ValueError("Rating must be 1–5 and review text cannot be empty")
    order = _one(conn.execute("""SELECT order_id,restaurant_id,order_status,source
        FROM fact_order WHERE order_id=%s FOR UPDATE""", (order_id,)))
    if order is None or order["source"] != "live_web" or order["order_status"] != "DELIVERED":
        raise ValueError("Only a delivered live order can be reviewed")
    if conn.execute("SELECT 1 FROM fact_review_v2 WHERE order_id=%s", (order_id,)).fetchone():
        raise ValueError("This order already has a review")
    if predict is None:
        from src.foody.review_models.phobert_absa_adapter import PhoBERTABSAAdapter

        predict = PhoBERTABSAAdapter().predict
    from src.monitoring.metrics import PHOBERT_REQUESTS

    try:
        result = predict(comment)
    except Exception:
        PHOBERT_REQUESTS.labels(status="failed").inc()
        raise
    PHOBERT_REQUESTS.labels(status="success").inc()
    aspects = result["aspects"]
    overall, reason = (('NOT_RELEVANT', result.get('reason')) if result.get('relevance_label') == 'NOT_RELEVANT'
                       else overall_sentiment(aspects))
    now = now or datetime.now(VIETNAM)
    review_id = "live_" + uuid4().hex
    date_key = _date_key(conn, now)
    version = str(result["metadata"]["version"]) if overall != "NOT_RELEVANT" else None
    conn.execute("""INSERT INTO fact_review_v2
        (review_id,order_id,restaurant_id,date_key,rating,review_text,source,
         created_at,predicted_sentiment,predicted_aspects,overall_sentiment,
         overall_sentiment_reason,absa_model_version)
        VALUES (%s,%s,%s,%s,%s,%s,'live_web',%s,%s,%s::jsonb,%s,%s,%s)""",
        (review_id, order_id, order["restaurant_id"], date_key, rating, comment,
         now, overall.lower(), json.dumps(
             [item["label"] for item in aspects]), overall, reason, version))
    conn.execute("""UPDATE fact_review_v2 SET relevance_label=%s,relevance_score=%s,
        relevance_model=%s,relevance_model_version=%s,relevance_reason=%s,aspect_status=%s
        WHERE review_id=%s""", (result.get('relevance_label','RELEVANT'),result.get('relevance_score'),
        result.get('relevance_model','injected_predictor'),result.get('relevance_model_version',version),
        result.get('relevance_reason'),result.get('aspect_status','DETECTED' if aspects else 'NO_ASPECT_DETECTED'),review_id))
    for aspect in aspects:
        conn.execute("""INSERT INTO fact_review_aspect
            (review_id,aspect,sentiment,score,model_name,model_version)
            VALUES (%s,%s,%s,%s,'PhoBERT ABSA',%s)""",
            (review_id, aspect["label"], aspect["sentiment"],
             aspect.get("score"), version))
    return {"review_id": review_id, "order_id": order_id,
            "restaurant_id": order["restaurant_id"], "overall_sentiment": overall,
            "reason": reason, "aspects": aspects, "model_version": version,
            "comment": comment, "rating": rating, "created_at": now,
            "relevance_label":result.get('relevance_label','RELEVANT'),
            "relevance_score":result.get('relevance_score'), "proxy_decision":result.get('proxy_decision'),
            "aspect_status":result.get('aspect_status','DETECTED' if aspects else 'NO_ASPECT_DETECTED')}
