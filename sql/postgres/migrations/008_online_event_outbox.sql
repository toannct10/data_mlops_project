-- Additive transactional event archive. No historical facts are changed.
CREATE TABLE IF NOT EXISTS online_event_outbox (
  event_id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  event_sequence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
  event_type TEXT NOT NULL CHECK(event_type IN
    ('ORDER_CREATED','STATUS_CHANGED','ORDER_CANCELLED','ORDER_DELIVERED','REVIEW_SUBMITTED')),
  entity_id TEXT NOT NULL,
  restaurant_id TEXT NOT NULL,
  event_timestamp TIMESTAMPTZ NOT NULL,
  payload JSONB NOT NULL,
  published_at TIMESTAMPTZ,
  s3_object_key TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error_category TEXT
);
CREATE INDEX IF NOT EXISTS online_event_pending ON online_event_outbox(event_sequence)
  WHERE published_at IS NULL;

CREATE OR REPLACE FUNCTION foody_capture_order_event() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE order_row JSONB; items JSONB; kind TEXT;
BEGIN
  SELECT to_jsonb(o) INTO order_row FROM fact_order o WHERE order_id=NEW.order_id;
  SELECT COALESCE(jsonb_agg(to_jsonb(i) ORDER BY order_item_id),'[]'::jsonb)
    INTO items FROM fact_order_item i WHERE order_id=NEW.order_id;
  kind := CASE WHEN NEW.previous_status IS NULL THEN 'ORDER_CREATED'
    WHEN NEW.new_status='CANCELLED' THEN 'ORDER_CANCELLED'
    WHEN NEW.new_status='DELIVERED' THEN 'ORDER_DELIVERED' ELSE 'STATUS_CHANGED' END;
  INSERT INTO online_event_outbox(event_type,entity_id,restaurant_id,event_timestamp,payload)
  VALUES(kind,NEW.order_id,order_row->>'restaurant_id',NEW.changed_at,
    jsonb_build_object('order',order_row,'items',items,'history',to_jsonb(NEW)));
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS foody_order_outbox ON order_status_history;
CREATE TRIGGER foody_order_outbox AFTER INSERT ON order_status_history
  FOR EACH ROW EXECUTE FUNCTION foody_capture_order_event();

CREATE OR REPLACE FUNCTION foody_capture_review_event() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE aspects JSONB;
BEGIN
  IF NEW.source <> 'live_web' THEN RETURN NEW; END IF;
  SELECT COALESCE(jsonb_agg(to_jsonb(a) ORDER BY aspect,model_version),'[]'::jsonb)
    INTO aspects FROM fact_review_aspect a WHERE review_id=NEW.review_id;
  INSERT INTO online_event_outbox(event_type,entity_id,restaurant_id,event_timestamp,payload)
  VALUES('REVIEW_SUBMITTED',NEW.review_id,NEW.restaurant_id,NEW.created_at,
    jsonb_build_object('review',to_jsonb(NEW),'aspects',aspects));
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS foody_review_outbox ON fact_review_v2;
-- Capture aspects only after the review and all its aspect rows have been written.
CREATE CONSTRAINT TRIGGER foody_review_outbox AFTER INSERT ON fact_review_v2
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION foody_capture_review_event();
