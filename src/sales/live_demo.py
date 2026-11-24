"""Bounded live-demo Sales questions over uncached, parameterized PostgreSQL views."""

from datetime import date
from decimal import Decimal

from src.ordering.menu import LIVE_IDS
from src.sales import queries
from src.sales.semantic_plan import plain


def _result(answer, rows, *, metric, day, status="ok"):
    return {"route": "SALES_SQL", "source": "PostgreSQL Live Demo",
            "answer": answer, "sql_facts": queries.jsonable(rows),
            "display_rows": queries.jsonable(rows), "review_evidence": [],
            "status": status, "execution_mode": "DETERMINISTIC_FALLBACK",
            "query_plan": {"intent": "LIVE_DEMO", "metric": metric,
                           "data_scope": "live_web", "day": day.isoformat(),
                           "sql_mode": "predefined_parameterized_live_demo_view"},
            "sql_details": {"tables_used": ["restaurant_live_demo_daily_sales"],
                            "validation_status": "Predefined parameterized SQL",
                            "semantic_verification": "Delivered-only view; live_web scope",
                            "generation_attempt_count": 0}}


def answer_live_demo(conn, restaurant_id, question, *, today=None):
    """Return None outside explicit demo/today scope; never use a historical total."""
    value = plain(question)
    if not any(word in value for word in ("live demo", "hom nay", "gan sao", "vua dat")):
        return None
    day = today or queries.vietnam_today()
    if not isinstance(day, date):
        raise ValueError("Live demo date is invalid")
    if "review" in value or "danh gia" in value:
        rows = queries._rows(conn.execute("""SELECT d.restaurant_id,d.restaurant_name,
            COALESCE(r.negative_count,0) AS negative_count
            FROM dim_sales_restaurant d
            LEFT JOIN restaurant_live_demo_daily_reviews r
              ON r.restaurant_id=d.restaurant_id AND r.period_start=%s
            WHERE d.is_live_demo=TRUE ORDER BY negative_count DESC,d.restaurant_id""",
                                        (day,)))
        if not rows:
            return _result("Không có quán live demo.", rows,
                           metric="negative_reviews", day=day, status="no_data")
        top = rows[0]
        return _result(f"Trong live demo ngày {day}, {top['restaurant_name']} có "
                       f"{top['negative_count']} review tiêu cực (cao nhất hoặc đồng hạng).",
                       rows, metric="negative_reviews", day=day)
    rows = queries._rows(conn.execute("""SELECT d.restaurant_id,d.restaurant_name,
        COALESCE(s.order_count,0) AS order_count,
        COALESCE(s.delivered_count,0) AS delivered_count,
        COALESCE(s.delivered_sales,0) AS delivered_sales,
        COALESCE(s.cancelled_count,0) AS cancelled_count
        FROM dim_sales_restaurant d
        LEFT JOIN restaurant_live_demo_daily_sales s
          ON s.restaurant_id=d.restaurant_id AND s.period_start=%s
        WHERE d.is_live_demo=TRUE ORDER BY d.restaurant_id""", (day,)))
    if len(rows) != len(LIVE_IDS) or {row["restaurant_id"] for row in rows} != set(LIVE_IDS):
        raise ValueError("Live-demo restaurant scope is not ready")
    if "3 quan" in value or "quan demo nao" in value or "gan sao" in value:
        metric = "delivered_count" if "don" in value and "doanh thu" not in value else \
            "delivered_sales"
        ordered = sorted(rows, key=lambda row: (-Decimal(row[metric]), row["restaurant_id"]))
        if "nhieu nhat" in value or "cao nhat" in value:
            first = ordered[0]
            answer = (f"Ngày {day}, {first['restaurant_name']} dẫn đầu live demo: "
                      f"{first[metric]:,.0f} " + ("đơn Delivered." if metric ==
                                               "delivered_count" else "VND Delivered."))
        else:
            answer = "So sánh live demo ngày " + str(day) + ": " + "; ".join(
                f"{row['restaurant_name']}: {row[metric]:,.0f} " +
                ("đơn Delivered" if metric == "delivered_count" else "VND Delivered")
                for row in ordered) + "."
        return _result(answer, ordered, metric=metric, day=day)
    if str(restaurant_id) not in LIVE_IDS:
        raise ValueError("Select one of the three live-demo restaurants")
    row = next(item for item in rows if item["restaurant_id"] == str(restaurant_id))
    if "truoc va sau" in value and "vua dat" in value:
        latest = queries._row(conn.execute("""SELECT order_id,total_amount
            FROM fact_order WHERE restaurant_id=%s AND source='live_web'
              AND order_status='DELIVERED' AND delivered_at IS NOT NULL
              AND date_key=%s
            ORDER BY delivered_at DESC,order_id DESC LIMIT 1""",
                                         (str(restaurant_id), int(day.strftime("%Y%m%d")))))
        if latest is None:
            return _result("Chưa có đơn live demo Delivered để so sánh trước/sau.", [],
                           metric="delivered_sales", day=day, status="no_data")
        before = Decimal(row["delivered_sales"]) - Decimal(latest["total_amount"])
        row = {**row, "last_order_id": latest["order_id"],
               "before_revenue": before, "after_revenue": row["delivered_sales"]}
        answer = (f"Live demo {row['restaurant_name']}: trước đơn "
                  f"{latest['order_id']}: {before:,.0f} VND; sau Delivered: "
                  f"{row['delivered_sales']:,.0f} VND; tăng "
                  f"{latest['total_amount']:,.0f} VND.")
        return _result(answer, [row], metric="delivered_sales", day=day)
    metric = "delivered_count" if "don" in value and "doanh thu" not in value else \
        "delivered_sales"
    answer = (f"Live demo ngày {day}, {row['restaurant_name']}: "
              f"{row[metric]:,.0f} " + ("đơn Delivered." if metric ==
                                       "delivered_count" else "VND doanh thu Delivered."))
    return _result(answer, [row], metric=metric, day=day)
