"""Idempotent menu seed derived from the supplied Menu quán.docx."""

import json
from pathlib import Path

from src.warehouse.loader import migrate

MENU = Path("config/live_demo_menu.json")
LIVE_IDS = ("2642", "1803", "145")


def menu_source():
    payload = json.loads(MENU.read_text(encoding="utf-8"))
    if {item["restaurant_id"] for item in payload["items"]} != set(LIVE_IDS):
        raise ValueError("Menu must contain exactly the three live-demo restaurants")
    for item in payload["items"]:
        if item["is_available"] != bool(item["variants"]):
            raise ValueError("Menu availability must match deterministic variants")
        for variant in item["variants"]:
            if not isinstance(variant["unit_price"], int) or variant["unit_price"] <= 0:
                raise ValueError("Orderable menu variants require a positive numeric price")
    return payload


def seed_menu(conn):
    """Upsert stable IDs and prices; preserve source-owned restaurant names."""
    migrate(conn)
    payload = menu_source()
    existing = {row[0] for row in conn.execute(
        "SELECT restaurant_id FROM dim_sales_restaurant WHERE restaurant_id=ANY(%s)",
        (list(LIVE_IDS),)).fetchall()}
    if existing != set(LIVE_IDS):
        raise ValueError("Load accepted historical Sales restaurants before menu seed")
    conn.execute("UPDATE dim_sales_restaurant SET is_live_demo=(restaurant_id=ANY(%s))",
                 (list(LIVE_IDS),))
    conn.execute("""INSERT INTO dim_review_restaurant_v2
        (restaurant_id,restaurant_name,source_version)
        SELECT restaurant_id,restaurant_name,'live_web' FROM dim_sales_restaurant
        WHERE restaurant_id=ANY(%s)
        ON CONFLICT (restaurant_id) DO NOTHING""", (list(LIVE_IDS),))
    items = variants = unavailable = 0
    for item in payload["items"]:
        conn.execute("""INSERT INTO dim_menu_item
            (menu_item_id,restaurant_id,item_name,category,source_price_text,is_available)
            VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (menu_item_id) DO UPDATE SET
            item_name=EXCLUDED.item_name,category=EXCLUDED.category,
            source_price_text=EXCLUDED.source_price_text,
            is_available=EXCLUDED.is_available,updated_at=now()""",
            (item["item_id"], item["restaurant_id"], item["item_name"],
             item["category"], item["source_price_text"], item["is_available"]))
        items += 1
        unavailable += not item["is_available"]
        for number, variant in enumerate(item["variants"], 1):
            conn.execute("""INSERT INTO menu_item_variant
                (variant_id,menu_item_id,variant_name,unit_price,is_available)
                VALUES (%s,%s,%s,%s,TRUE) ON CONFLICT (variant_id) DO UPDATE SET
                variant_name=EXCLUDED.variant_name,unit_price=EXCLUDED.unit_price,
                is_available=TRUE""", (f"{item['item_id']}-v{number}", item["item_id"],
                                       variant["name"], variant["unit_price"]))
            variants += 1
    return {"restaurants": len(LIVE_IDS), "items": items,
            "variants": variants, "unavailable_items": unavailable}


def list_live_restaurants(conn):
    cursor = conn.execute("""SELECT restaurant_id,restaurant_name,is_live_demo
        FROM dim_sales_restaurant WHERE is_live_demo=TRUE
        ORDER BY CASE restaurant_id WHEN '2642' THEN 1 WHEN '1803' THEN 2 ELSE 3 END""")
    return [dict(zip((column.name for column in cursor.description), row,
                     strict=True)) for row in cursor.fetchall()]


def list_menu(conn, restaurant_id):
    if str(restaurant_id) not in LIVE_IDS:
        raise ValueError("Ordering is limited to the three live-demo restaurants")
    cursor = conn.execute("""SELECT m.menu_item_id,m.item_name,m.category,
        m.description,m.image_url,m.source_price_text,m.is_available,
        v.variant_id,v.variant_name,v.unit_price,v.is_available AS variant_available
        FROM dim_menu_item m LEFT JOIN menu_item_variant v USING (menu_item_id)
        WHERE m.restaurant_id=%s ORDER BY m.category,m.item_name,v.variant_name""",
        (str(restaurant_id),))
    columns = [column.name for column in cursor.description]
    grouped = {}
    for row in cursor.fetchall():
        value = dict(zip(columns, row, strict=True))
        key = value["menu_item_id"]
        if key not in grouped:
            grouped[key] = {field: value[field] for field in
                            ("menu_item_id", "item_name", "category", "description",
                             "image_url", "source_price_text", "is_available")}
            grouped[key]["variants"] = []
        if value["variant_id"] is not None:
            grouped[key]["variants"].append({field: value[field] for field in
                                             ("variant_id", "variant_name", "unit_price",
                                              "variant_available")})
    return list(grouped.values())
