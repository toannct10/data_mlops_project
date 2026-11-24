-- Apply after migrations 001-004 using the local PostgreSQL owner/admin.
-- Set a private password out of band: \password foody_ai_readonly in psql.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'foody_ai_readonly') THEN
    CREATE ROLE foody_ai_readonly LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
  END IF;
END $$;
ALTER ROLE foody_ai_readonly NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
GRANT CONNECT ON DATABASE foody TO foody_ai_readonly;
CREATE SCHEMA IF NOT EXISTS ai_sales;
REVOKE ALL ON SCHEMA ai_sales FROM PUBLIC;
REVOKE ALL ON SCHEMA ai_sales FROM foody_ai_readonly;
GRANT USAGE ON SCHEMA ai_sales TO foody_ai_readonly;
CREATE OR REPLACE VIEW ai_sales.dim_sales_restaurant AS
  SELECT restaurant_id, restaurant_name FROM public.dim_sales_restaurant;
CREATE OR REPLACE VIEW ai_sales.restaurant_monthly_sales AS
  SELECT * FROM public.restaurant_monthly_sales;
CREATE OR REPLACE VIEW ai_sales.restaurant_weekly_sales AS
  SELECT * FROM public.restaurant_weekly_sales;
CREATE OR REPLACE VIEW ai_sales.restaurant_sales_summary AS
  SELECT * FROM public.restaurant_sales_summary;
REVOKE ALL ON ALL TABLES IN SCHEMA ai_sales FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA ai_sales FROM foody_ai_readonly;
GRANT SELECT ON ai_sales.dim_sales_restaurant,
  ai_sales.restaurant_monthly_sales,
  ai_sales.restaurant_weekly_sales,
  ai_sales.restaurant_sales_summary TO foody_ai_readonly;
ALTER ROLE foody_ai_readonly SET search_path = ai_sales;

-- Additive analytical views only. No canonical facts are replaced or reloaded.
CREATE SCHEMA IF NOT EXISTS ai_cross;
REVOKE ALL ON SCHEMA ai_cross FROM PUBLIC;
CREATE OR REPLACE VIEW ai_cross.orders AS
SELECT o.order_id,o.restaurant_id,s.restaurant_name,d.calendar_date AS order_date,
       o.order_status,o.sales_amount,o.total_amount,o.source,o.created_at,o.updated_at
FROM public.fact_order o JOIN public.dim_sales_restaurant s USING(restaurant_id)
JOIN public.dim_date d USING(date_key);
CREATE OR REPLACE VIEW ai_cross.reviews AS
SELECT r.review_id,r.order_id,r.restaurant_id,s.restaurant_name,
       d.calendar_date AS review_date,r.review_timestamp,r.created_at,r.rating,
       r.review_text,UPPER(COALESCE(r.overall_sentiment,r.predicted_sentiment)) AS overall_sentiment,
       r.predicted_sentiment,r.predicted_aspects,r.absa_predictions,r.relevance_label,
       r.relevance_score,r.absa_model_version,r.source,
       (r.absa_model_version IS NOT NULL AND r.relevance_label IS DISTINCT FROM 'NOT_RELEVANT'
        AND UPPER(COALESCE(r.overall_sentiment,r.predicted_sentiment)) IN ('POSITIVE','NEUTRAL','NEGATIVE')) AS is_valid_sentiment
FROM public.fact_review_v2 r JOIN public.dim_sales_restaurant s USING(restaurant_id)
JOIN public.dim_date d USING(date_key);
REVOKE ALL ON ALL TABLES IN SCHEMA ai_cross FROM PUBLIC;
GRANT USAGE ON SCHEMA ai_cross TO foody_ai_readonly;
GRANT SELECT ON ai_cross.orders,ai_cross.reviews TO foody_ai_readonly;
CREATE OR REPLACE VIEW ai_sales.restaurant_monthly_order_status_values AS
SELECT * FROM public.restaurant_monthly_order_status_values;
GRANT SELECT ON ai_sales.restaurant_monthly_order_status_values TO foody_ai_readonly;
ALTER ROLE foody_ai_readonly SET default_transaction_read_only = on;
ALTER ROLE foody_ai_readonly SET statement_timeout = '5s';

ALTER ROLE foody_ai_readonly PASSWORD :'ai_password';
CREATE TABLE IF NOT EXISTS public.portable_bootstrap_state(seed_version text PRIMARY KEY,loaded_at timestamptz DEFAULT now());
INSERT INTO public.portable_bootstrap_state(seed_version) VALUES ('foody-private-safe-20261123') ON CONFLICT DO NOTHING;
