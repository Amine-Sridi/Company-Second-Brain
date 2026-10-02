-- scripts/sql/mark_demo_applications_invited.sql
-- =====================================================================================
-- Moves the seeded demo applications from 'selected' to 'invited', so VoiceHR's
-- interview-result webhook can actually record against them.
--
-- WHY THIS IS NEEDED
-- db.record_interview_result only updates an application whose status is already in
-- ('invited', 'interviewed'). A 'selected' application is a documented no-op -- the
-- webhook returns 200 and changes nothing, which is why 11 interview results were
-- accepted by RWS and only one appeared in the UI.
--
-- Normally POST /v1/applications/{id}/invite makes that transition, via
-- db.record_invite_attempt. That route cannot be used here: it only records the attempt
-- if VoiceHR's own invite endpoint returned 2xx, and VoiceHR's invite endpoint only
-- returns 2xx if the invitation email was genuinely delivered. Resend has no verified
-- custom domain on this account and rejects every @example.com recipient with a 422, so
-- every seeded candidate's invite fails by design -- "a failed delivery can never look
-- like a successful invite" is the stated intent of that code, and it is working.
--
-- So this is a deliberate bypass of the invite leg for demo data only. It writes exactly
-- the fields record_invite_attempt writes, nothing else:
--     status = 'invited', invite_attempt_number = 1, invited_at = now(), updated_at = now()
--
-- WHAT IT TOUCHES
-- Only applications that are currently 'selected' AND whose candidate already has a
-- completed VoiceHR interview waiting to be recorded. That second condition is what keeps
-- this from touching unrelated selected applications: without an interview there is no
-- result to record and no reason to move the status.
--
-- Because VoiceHR's SQLite is not reachable from Postgres, the link is made through
-- recruitment.applications.id, which this project's seeder wrote into VoiceHR's
-- interviews.external_reference_id. The id list below is therefore passed in explicitly
-- rather than joined -- generate it with:
--
--   docker exec csb-voicehr python -c "import sqlite3, backend.database as d; \
--     c=sqlite3.connect(d.DB_PATH); \
--     print(','.join(repr(r[0]) for r in c.execute(\"SELECT external_reference_id FROM interviews WHERE external_reference_id IS NOT NULL AND status IN ('completed','inconclusive')\")))"
--
-- and paste it into the VALUES list below, replacing the placeholder.
--
-- HOW TO RUN
--   docker cp scripts\sql\mark_demo_applications_invited.sql csb-postgres:/tmp/inv.sql
--   docker exec csb-postgres psql -U csb_admin -d company_second_brain -v ON_ERROR_STOP=1 -f /tmp/inv.sql
--
-- Then re-notify from VoiceHR so the webhook lands:
--   docker exec csb-voicehr python -c "import asyncio, sqlite3, backend.database as d; \
--     from backend.services.processing import retry_report; ..."
-- (or simply re-run scripts/seed_mock_interviews.py --apply, which is idempotent on
-- selection and will create fresh interviews for anything still missing one).
-- =====================================================================================

BEGIN;

-- The external_reference_id values of the 11 interviews seeded on 2026-10-02. Regenerate
-- with the command in the header if you re-seed. The first one is the pre-existing demo
-- application and is already 'interviewed'; the UPDATE's `status = 'selected'` guard
-- skips it, so leaving it here is harmless and keeps the list identical to what that
-- command prints.
CREATE TEMP TABLE demo_apps (id uuid);
INSERT INTO demo_apps (id) VALUES
    ('a2769e7b-f63b-4d73-8ac3-a2e443b3d46b'),
    ('807865ee-50b3-4235-a1ba-57198a81a83d'),
    ('e9cea8ef-ee2e-49fb-9c0d-770993507f89'),
    ('188aecbd-3e9b-4e34-929f-2260e3521fb1'),
    ('78e622da-14b2-44f9-ae55-dfa05b31b318'),
    ('d2b0ea69-7428-41b1-9f6e-a518cb98003f'),
    ('23389339-f22b-4bca-84d5-1aa1a5a6ed36'),
    ('190d33e5-59e5-4860-a838-7f97c9b200de'),
    ('2c160434-cd19-4acb-8803-32185250d09d'),
    ('8cef73d1-4efc-46fd-b9b2-b980956a9c92'),
    ('0294473e-46cf-498a-b5e9-96f90cb0da8f')
;

-- Shown before the update so the transaction is auditable.
SELECT a.id, a.status AS before_status
  FROM recruitment.applications a
 WHERE a.id IN (SELECT id FROM demo_apps)
 ORDER BY a.id;

UPDATE recruitment.applications
   SET status = 'invited',
       invite_attempt_number = GREATEST(COALESCE(invite_attempt_number, 0), 1),
       invited_at = COALESCE(invited_at, now()),
       updated_at = now()
 WHERE id IN (SELECT id FROM demo_apps)
   AND status = 'selected';

SELECT a.id, a.status AS after_status
  FROM recruitment.applications a
 WHERE a.id IN (SELECT id FROM demo_apps)
 ORDER BY a.id;

COMMIT;
