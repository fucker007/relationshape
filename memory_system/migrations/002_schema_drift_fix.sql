-- Migration 002: fix schema drift — columns written by code but missing from base DDL.
-- Safe & idempotent. Run against existing deployments.

-- relationships.state: written by upsert_relationship / update_relationship_field
ALTER TABLE relationships ADD COLUMN IF NOT EXISTS state JSONB DEFAULT '{}'::jsonb;

-- events.trauma / events.priority: written by GraphStore.update_event_field (trauma flagging)
ALTER TABLE events ADD COLUMN IF NOT EXISTS trauma   BOOLEAN DEFAULT FALSE;
ALTER TABLE events ADD COLUMN IF NOT EXISTS priority VARCHAR(10);
