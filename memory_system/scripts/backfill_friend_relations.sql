-- Deliberate data backfill: relationships stuck at relation_type='other' whose linked
-- person appears in an event whose text mentions a friendship cue → promote to 'friend'.
-- Root cause was the old COALESCE clobber (now fixed in upsert_relationship); this repairs
-- historical rows. Review the SELECT before running the UPDATE.

-- Preview:
--   SELECT r.owner_id, p.name, r.relation_type
--   FROM relationships r JOIN person_nodes p ON p.person_id = r.to_person_id
--   WHERE r.relation_type = 'other' AND EXISTS (
--     SELECT 1 FROM events e WHERE e.owner_id = r.owner_id
--       AND r.to_person_id = ANY(e.participant_ids)
--       AND (e.summary ~ '(好朋友|朋友|死党|闺蜜|铁哥们|兄弟)'
--            OR COALESCE(e.source_message,'') ~ '(好朋友|朋友|死党|闺蜜|铁哥们|兄弟)'));

UPDATE relationships r
SET relation_type = 'friend', updated_at = NOW()
WHERE r.relation_type = 'other'
  AND EXISTS (
    SELECT 1 FROM events e
    WHERE e.owner_id = r.owner_id
      AND r.to_person_id = ANY(e.participant_ids)
      AND (e.summary ~ '(好朋友|朋友|死党|闺蜜|铁哥们|兄弟)'
           OR COALESCE(e.source_message, '') ~ '(好朋友|朋友|死党|闺蜜|铁哥们|兄弟)')
  );
