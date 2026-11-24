-- Add provenance to the current canonical facts, never a parallel raw-data silo.
ALTER TABLE fact_order ADD COLUMN IF NOT EXISTS source_dataset TEXT;
ALTER TABLE fact_order ADD COLUMN IF NOT EXISTS source_order_id TEXT;
ALTER TABLE fact_order ADD COLUMN IF NOT EXISTS source_row_sha256 TEXT;
ALTER TABLE fact_review_v2 ADD COLUMN IF NOT EXISTS source_dataset TEXT;
ALTER TABLE fact_review_v2 ADD COLUMN IF NOT EXISTS source_order_id TEXT;
ALTER TABLE fact_review_v2 ADD COLUMN IF NOT EXISTS source_row_sha256 TEXT;
ALTER TABLE fact_review_v2 ADD COLUMN IF NOT EXISTS review_address TEXT;
ALTER TABLE fact_review_v2 ADD COLUMN IF NOT EXISTS source_review_time TEXT;
ALTER TABLE fact_review_v2 ADD COLUMN IF NOT EXISTS review_timestamp TIMESTAMPTZ;
ALTER TABLE fact_review_v2 ADD COLUMN IF NOT EXISTS absa_predictions JSONB;
CREATE UNIQUE INDEX IF NOT EXISTS ux_fact_order_source_identity ON fact_order(source_dataset,source_order_id) WHERE source_order_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_fact_review_paired_provenance ON fact_review_v2(source_dataset);
CREATE OR REPLACE VIEW canonical_review_enrichment AS
SELECT r.*, r.rating * CASE WHEN r.source='live_web' THEN 2 ELSE 1 END AS rating_normalized_10, d.calendar_date AS review_date,s.restaurant_name,
 o.order_status,o.sales_amount,o.date_key AS order_date_key,
 COALESCE(a.aspects,'[]'::jsonb) AS native_aspects
FROM fact_review_v2 r JOIN dim_date d USING(date_key)
JOIN dim_sales_restaurant s ON s.restaurant_id=r.restaurant_id
LEFT JOIN fact_order o ON o.order_id=r.order_id AND o.restaurant_id=r.restaurant_id
LEFT JOIN LATERAL (
 SELECT jsonb_agg(jsonb_build_object('aspect',f.aspect,'sentiment',f.sentiment,'score',f.score) ORDER BY f.aspect) AS aspects
 FROM fact_review_aspect f WHERE f.review_id=r.review_id AND f.model_version=r.absa_model_version
) a ON TRUE;
CREATE OR REPLACE VIEW canonical_order_review AS
SELECT o.order_id,o.restaurant_id,s.restaurant_name,d.calendar_date AS order_date,
 o.sales_amount,o.total_amount,o.order_status,o.source,o.source_dataset,
 r.review_id,r.rating,r.review_text,r.relevance_label,r.overall_sentiment,
 r.native_aspects,r.absa_predictions,r.review_date
FROM fact_order o JOIN dim_date d USING(date_key)
JOIN dim_sales_restaurant s USING(restaurant_id)
LEFT JOIN canonical_review_enrichment r ON r.order_id=o.order_id AND r.restaurant_id=o.restaurant_id;
