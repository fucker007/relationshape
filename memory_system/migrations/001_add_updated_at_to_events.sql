-- Migration: Add updated_at column to events table
-- This fixes the UndefinedColumnError when updating events

ALTER TABLE events ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

-- Update existing rows to have updated_at = created_at
UPDATE events SET updated_at = created_at WHERE updated_at IS NULL;

-- Make the column NOT NULL
ALTER TABLE events ALTER COLUMN updated_at SET NOT NULL;
