CREATE TABLE IF NOT EXISTS live_sync_state (
  entity_type TEXT NOT NULL CHECK (entity_type IN ('order','review')),
  entity_id TEXT NOT NULL,
  cloud_status TEXT NOT NULL CHECK (cloud_status IN
    ('NOT_CONFIGURED','SYNCED','FAILED')),
  checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (entity_type,entity_id)
);
