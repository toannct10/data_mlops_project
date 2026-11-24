-- Additive and repeatable; retain all raw text and existing aspect rows.
ALTER TABLE fact_review_v2 ADD COLUMN IF NOT EXISTS relevance_label TEXT;
ALTER TABLE fact_review_v2 ADD COLUMN IF NOT EXISTS relevance_score DOUBLE PRECISION;
ALTER TABLE fact_review_v2 ADD COLUMN IF NOT EXISTS relevance_model TEXT;
ALTER TABLE fact_review_v2 ADD COLUMN IF NOT EXISTS relevance_model_version TEXT;
ALTER TABLE fact_review_v2 ADD COLUMN IF NOT EXISTS relevance_reason TEXT;
ALTER TABLE fact_review_v2 ADD COLUMN IF NOT EXISTS aspect_status TEXT;
ALTER TABLE fact_review_v2 DROP CONSTRAINT IF EXISTS fact_review_v2_overall_check;
ALTER TABLE fact_review_v2 ADD CONSTRAINT fact_review_v2_overall_check CHECK
  (overall_sentiment IS NULL OR overall_sentiment IN ('POSITIVE','NEUTRAL','NEGATIVE','NOT_RELEVANT'));
ALTER TABLE fact_review_v2 DROP CONSTRAINT IF EXISTS review_relevance_label_check;
ALTER TABLE fact_review_v2 ADD CONSTRAINT review_relevance_label_check CHECK
  (relevance_label IS NULL OR relevance_label IN ('RELEVANT','NOT_RELEVANT'));
