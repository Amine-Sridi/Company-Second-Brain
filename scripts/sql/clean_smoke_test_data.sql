-- scripts/sql/clean_smoke_test_data.sql
-- =====================================================================================
-- Removes every smoke-test / verification job and test candidate, so the demo shows only
-- the seeded, realistic data (the jobs and candidates created on 2026-10-02).
--
-- WHY THIS GOES FURTHER THAN clean_mock_graph_data.sql
-- That script removed only test jobs with *no* applications, to protect the application
-- state the old demo sequence relied on (a2769e7b, 095731d3). The demo now runs on the
-- seeded jobs (Consultant BI Senior, Data Engineer, Consultant Big Data), which hold 10
-- interviewed applications of their own, so the test applications are no longer needed
-- and they read as test leftovers on the Jobs, Applications, Interviews and Reports pages.
--
-- WHAT GOES (measured 2026-10-07, before running)
--   17 jobs:  'Email Delivery Smoke Test Engineer', 'Backend Engineer (Demo)',
--             'Smoketest ...', 'Section E Verification ...', 'Batch Fetch Verification ...',
--             'Embedding Space Gate Verification'
--   8 candidates: the four 'Smoketest ...' candidates, 'Jean Dupont', 'Queue Test
--             Candidate', and the two 'Amine Sridi' rows created before 2026-10-01 (test
--             uploads; the 2026-10-02 'Amine Sridi' is a seeded demo candidate and stays)
--   and everything hanging off them: applications, interview rounds, recruiter decisions,
--   webhook events, match results, explanations, job descriptions, ingest jobs, knowledge
--   rows and knowledge feedback.
--
-- WHAT IS DELIBERATELY KEPT
--   * Every job created on 2026-10-02 and its applications.
--   * 'Amine Verified Demo Candidate': the only candidate whose email is exactly the
--     Resend account's verified address, so the only one a live invite can reach. Its
--     matches against the removed jobs go; the candidate row stays.
--
-- NOT TOUCHED: VoiceHR's own SQLite. Its interview rows for the removed applications stay,
-- visible only in VoiceHR's admin page. Nothing in RWS reads them back.
--
-- HOW TO RUN (PowerShell: -f reads inside the container, so copy it in first)
--   docker cp scripts\sql\clean_smoke_test_data.sql csb-postgres:/tmp/clean.sql
--   docker exec csb-postgres psql -U csb_admin -d company_second_brain -v ON_ERROR_STOP=1 -f /tmp/clean.sql
--
-- One transaction. Change the final COMMIT to ROLLBACK to see the row counts first.
-- Take a pg_dump first: this deletes application history, not just generated rows.
-- =====================================================================================

BEGIN;

CREATE TEMP TABLE sj AS
SELECT id, title FROM recruitment.jobs
WHERE title = 'Email Delivery Smoke Test Engineer'
   OR title = 'Backend Engineer (Demo)'
   OR title LIKE 'Smoketest%'
   OR title LIKE 'Section E Verification%'
   OR title LIKE 'Batch Fetch Verification%'
   OR title LIKE 'Embedding Space Gate%';

CREATE TEMP TABLE tc AS
SELECT id, name FROM resume_intel.candidates
WHERE name LIKE 'Smoketest%'
   OR name IN ('Jean Dupont', 'Queue Test Candidate')
   OR (name = 'Amine Sridi' AND created_at < '2026-10-01');

CREATE TEMP TABLE apps AS
SELECT id FROM recruitment.applications
WHERE job_id IN (SELECT id FROM sj) OR candidate_id IN (SELECT id FROM tc);

CREATE TEMP TABLE mr AS
SELECT id FROM recruitment.match_results
WHERE job_id IN (SELECT id FROM sj) OR candidate_id IN (SELECT id FROM tc);

-- Shown so the transaction is auditable before it commits.
SELECT title AS removing_job FROM sj ORDER BY title;
SELECT name AS removing_candidate FROM tc ORDER BY name;
SELECT (SELECT count(*) FROM apps) AS applications, (SELECT count(*) FROM mr) AS match_results;

-- Second Brain has no foreign keys into the other schemas (no cross-schema grants, see
-- CLAUDE.md), so its rows are removed explicitly rather than by cascade.
DELETE FROM second_brain.candidate_feedback
 WHERE application_id IN (SELECT id FROM apps) OR candidate_id IN (SELECT id FROM tc);

-- FK order: applications <- {interview_rounds, recruiter_decisions, webhook events};
-- match_results <- {applications, match_explanations}; jobs <- {job_descriptions, match_results}.
DELETE FROM recruitment.interview_webhook_events WHERE application_id IN (SELECT id FROM apps);
DELETE FROM recruitment.recruiter_decisions      WHERE application_id IN (SELECT id FROM apps);
DELETE FROM recruitment.interview_rounds         WHERE application_id IN (SELECT id FROM apps);
DELETE FROM recruitment.applications             WHERE id IN (SELECT id FROM apps);

DELETE FROM recruitment.match_explanations WHERE match_result_id IN (SELECT id FROM mr);
DELETE FROM recruitment.match_results      WHERE id IN (SELECT id FROM mr);

DELETE FROM recruitment.job_descriptions WHERE job_id IN (SELECT id FROM sj);
DELETE FROM recruitment.jobs             WHERE id IN (SELECT id FROM sj);

DELETE FROM resume_intel.ingest_jobs        WHERE candidate_id IN (SELECT id FROM tc);
DELETE FROM second_brain.candidate_knowledge WHERE candidate_id IN (SELECT id FROM tc);
DELETE FROM resume_intel.candidates         WHERE id IN (SELECT id FROM tc);

-- Knowledge rows whose candidate no longer exists at all (one, from a 2026-08-28 smoke
-- test). The batch fetch already omits unknown ids, so this is tidiness, not a fix.
DELETE FROM second_brain.candidate_knowledge k
 WHERE NOT EXISTS (SELECT 1 FROM resume_intel.candidates c WHERE c.id = k.candidate_id);

COMMIT;

-- =====================================================================================
-- Afterwards (measured 2026-10-07): 8 jobs, 10 applications, all on 2026-10-02 jobs.
--
--   SELECT j.title, count(m.id) AS matches, count(a.id) AS applications
--     FROM recruitment.jobs j
--     LEFT JOIN recruitment.match_results m ON m.job_id = j.id
--     LEFT JOIN recruitment.applications a ON a.match_result_id = m.id
--    GROUP BY j.title ORDER BY j.title;
-- =====================================================================================
