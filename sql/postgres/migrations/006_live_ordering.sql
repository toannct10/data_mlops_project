-- Add the three-restaurant ordering demo without replacing historical facts.
ALTER TABLE dim_sales_restaurant
  ADD COLUMN IF NOT EXISTS is_live_demo BOOLEAN NOT NULL DEFAULT FALSE;
UPDATE dim_sales_restaurant SET is_live_demo = restaurant_id IN ('2642','1803','145');

CREATE TABLE IF NOT EXISTS dim_menu_item (
  menu_item_id TEXT PRIMARY KEY,
  restaurant_id TEXT NOT NULL REFERENCES dim_sales_restaurant(restaurant_id),
  item_name TEXT NOT NULL,
  category TEXT NOT NULL,
  description TEXT,
  image_url TEXT,
  source_price_text TEXT,
  is_available BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (restaurant_id, item_name)
);
CREATE TABLE IF NOT EXISTS menu_item_variant (
  variant_id TEXT PRIMARY KEY,
  menu_item_id TEXT NOT NULL REFERENCES dim_menu_item(menu_item_id),
  variant_name TEXT NOT NULL,
  unit_price NUMERIC(14,2) CHECK (unit_price >= 0),
  is_available BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE (menu_item_id, variant_name),
  CHECK (NOT is_available OR unit_price IS NOT NULL)
);

ALTER TABLE fact_order
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS total_amount NUMERIC(14,2),
  ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'historical';
UPDATE fact_order SET total_amount=sales_amount WHERE total_amount IS NULL;
ALTER TABLE fact_order
  ALTER COLUMN total_amount SET NOT NULL,
  DROP CONSTRAINT IF EXISTS fact_order_order_status_check;
ALTER TABLE fact_order ADD CONSTRAINT fact_order_order_status_check CHECK (
  order_status IN ('Delivered','Cancelled','Refunded','PENDING','PREPARING',
                   'DELIVERING','DELIVERED','CANCELLED'));
ALTER TABLE fact_order ADD CONSTRAINT fact_order_source_check CHECK (
  source IN ('historical','live_web'));
ALTER TABLE fact_order ADD CONSTRAINT fact_order_live_check CHECK (
  source <> 'live_web' OR (created_at IS NOT NULL AND updated_at IS NOT NULL
    AND order_status IN ('PENDING','PREPARING','DELIVERING','DELIVERED','CANCELLED')));

CREATE TABLE IF NOT EXISTS fact_order_item (
  order_item_id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES fact_order(order_id),
  menu_item_id TEXT NOT NULL REFERENCES dim_menu_item(menu_item_id),
  variant_id TEXT NOT NULL REFERENCES menu_item_variant(variant_id),
  item_name_snapshot TEXT NOT NULL,
  variant_name_snapshot TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price NUMERIC(14,2) NOT NULL CHECK (unit_price >= 0),
  subtotal NUMERIC(14,2) NOT NULL CHECK (subtotal = quantity * unit_price)
);
CREATE INDEX IF NOT EXISTS ix_fact_order_item_order ON fact_order_item(order_id);

CREATE TABLE IF NOT EXISTS order_status_history (
  status_event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES fact_order(order_id),
  previous_status TEXT,
  new_status TEXT NOT NULL CHECK (new_status IN
    ('PENDING','PREPARING','DELIVERING','DELIVERED','CANCELLED')),
  changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  source TEXT NOT NULL DEFAULT 'live_web' CHECK (source='live_web')
);
CREATE INDEX IF NOT EXISTS ix_order_status_history_order ON order_status_history(order_id);

INSERT INTO dim_review_restaurant_v2 (restaurant_id,restaurant_name,source_version)
SELECT restaurant_id,restaurant_name,'live_web' FROM dim_sales_restaurant
WHERE restaurant_id IN ('2642','1803','145') ON CONFLICT (restaurant_id) DO NOTHING;
ALTER TABLE fact_review_v2
  ADD COLUMN IF NOT EXISTS order_id TEXT REFERENCES fact_order(order_id),
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'historical',
  ADD COLUMN IF NOT EXISTS overall_sentiment TEXT,
  ADD COLUMN IF NOT EXISTS overall_sentiment_reason TEXT,
  ADD COLUMN IF NOT EXISTS absa_model_version TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS ux_fact_review_v2_live_order
  ON fact_review_v2(order_id) WHERE order_id IS NOT NULL;
ALTER TABLE fact_review_v2 ADD CONSTRAINT fact_review_v2_source_check CHECK (
  source IN ('historical','live_web'));
ALTER TABLE fact_review_v2 ADD CONSTRAINT fact_review_v2_overall_check CHECK (
  overall_sentiment IS NULL OR overall_sentiment IN ('POSITIVE','NEUTRAL','NEGATIVE'));

CREATE TABLE IF NOT EXISTS fact_review_aspect (
  review_id TEXT NOT NULL REFERENCES fact_review_v2(review_id),
  aspect TEXT NOT NULL,
  sentiment TEXT NOT NULL CHECK (sentiment IN ('positive','negative','neutral')),
  score DOUBLE PRECISION CHECK (score BETWEEN 0 AND 1),
  model_name TEXT NOT NULL,
  model_version TEXT NOT NULL,
  inferred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (review_id,aspect,model_version)
);
CREATE INDEX IF NOT EXISTS ix_fact_review_aspect_review ON fact_review_aspect(review_id);

-- Existing daily, weekly, monthly, and summary views retain their shape.
-- Daily is the common source; case-folding preserves historical title-case statuses.
CREATE OR REPLACE VIEW restaurant_daily_sales AS
SELECT o.restaurant_id, d.calendar_date AS period_start,
       COUNT(*)::INTEGER AS order_count,
       COALESCE(SUM(o.sales_amount),0) AS gross_sales,
       COALESCE(SUM(o.sales_amount) FILTER (WHERE UPPER(o.order_status)='DELIVERED'),0)
         AS delivered_sales,
       COUNT(*) FILTER (WHERE UPPER(o.order_status)='DELIVERED')::INTEGER AS delivered_count,
       COUNT(*) FILTER (WHERE UPPER(o.order_status)='CANCELLED')::INTEGER AS cancelled_count,
       COUNT(*) FILTER (WHERE UPPER(o.order_status)='REFUNDED')::INTEGER AS refunded_count,
       ROUND(COALESCE(SUM(o.sales_amount) FILTER
         (WHERE UPPER(o.order_status)='DELIVERED'),0) /
         NULLIF(COUNT(*) FILTER (WHERE UPPER(o.order_status)='DELIVERED'),0),2) AS aov,
       ROUND(COUNT(*) FILTER (WHERE UPPER(o.order_status)='CANCELLED')::NUMERIC /
         COUNT(*),4) AS cancellation_rate,
       ROUND(COUNT(*) FILTER (WHERE UPPER(o.order_status)='REFUNDED')::NUMERIC /
         COUNT(*),4) AS refund_rate
FROM fact_order o JOIN dim_date d ON d.date_key=o.date_key
WHERE d.calendar_date <= (NOW() AT TIME ZONE 'Asia/Ho_Chi_Minh')::DATE
GROUP BY o.restaurant_id,d.calendar_date;

CREATE OR REPLACE VIEW restaurant_monthly_order_status_values AS
SELECT o.restaurant_id,
       DATE_TRUNC('month', d.calendar_date)::DATE AS period_start,
       COUNT(*)::INTEGER AS order_count,
       COUNT(*) FILTER (WHERE UPPER(o.order_status)='CANCELLED')::INTEGER
         AS cancelled_order_count,
       COALESCE(SUM(o.sales_amount) FILTER
         (WHERE UPPER(o.order_status)='CANCELLED'),0) AS cancelled_order_value,
       COUNT(*) FILTER (WHERE UPPER(o.order_status)='REFUNDED')::INTEGER
         AS refunded_order_count,
       COALESCE(SUM(o.sales_amount) FILTER
         (WHERE UPPER(o.order_status)='REFUNDED'),0) AS refunded_order_value,
       ROUND(COUNT(*) FILTER (WHERE UPPER(o.order_status)='CANCELLED')::NUMERIC /
         NULLIF(COUNT(*),0),4) AS cancellation_rate,
       ROUND(COUNT(*) FILTER (WHERE UPPER(o.order_status)='REFUNDED')::NUMERIC /
         NULLIF(COUNT(*),0),4) AS refund_rate
FROM fact_order o JOIN dim_date d ON d.date_key=o.date_key
WHERE d.calendar_date <= (NOW() AT TIME ZONE 'Asia/Ho_Chi_Minh')::DATE
GROUP BY o.restaurant_id,DATE_TRUNC('month',d.calendar_date)::DATE;

CREATE OR REPLACE VIEW restaurant_live_demo_daily_sales AS
SELECT o.restaurant_id,d.calendar_date AS period_start,
       COUNT(*)::INTEGER AS order_count,
       COUNT(*) FILTER (WHERE o.order_status='DELIVERED')::INTEGER AS delivered_count,
       COALESCE(SUM(o.total_amount) FILTER (WHERE o.order_status='DELIVERED'),0)
         AS delivered_sales,
       COUNT(*) FILTER (WHERE o.order_status='CANCELLED')::INTEGER AS cancelled_count
FROM fact_order o JOIN dim_date d ON d.date_key=o.date_key
WHERE o.source='live_web'
GROUP BY o.restaurant_id,d.calendar_date;

CREATE OR REPLACE VIEW restaurant_live_demo_daily_reviews AS
SELECT r.restaurant_id,d.calendar_date AS period_start,
       COUNT(*)::INTEGER AS review_count,
       COUNT(*) FILTER (WHERE r.overall_sentiment='NEGATIVE')::INTEGER AS negative_count
FROM fact_review_v2 r JOIN dim_date d ON d.date_key=r.date_key
WHERE r.source='live_web'
GROUP BY r.restaurant_id,d.calendar_date;
