-- scripts/sql/clean_benchmark_rows.sql
-- =====================================================================================
-- Removes the rows this project's own benchmarks wrote into the demo database.
--
-- WHY THIS EXISTS
-- benchmarks/load/ingest_load_test.py and benchmarks/load/matching_latency_test.py both
-- exercise the REAL endpoints, so they create real candidates, jobs and match_results.
-- Run enough times, they come to dominate the demo UI: the Candidates and Jobs lists are
-- ordered newest-first, so "Load Test Candidate 12" and "Backend Engineer k40 r2" sit at
-- the top of the first two screens of the demo.
--
-- WHAT IT TOUCHES (measured 2026-10-01, before running)
--   recruitment.match_results      225 (benchmark jobs) + 13 (load-test candidates)
--   recruitment.job_descriptions    12
--   recruitment.jobs                12
--   resume_intel.ingest_jobs        68
--   second_brain.candidate_knowledge 68
--   resume_intel.candidates         68
--   applications / interview_rounds / recruiter_decisions / match_explanations: 0
--
-- WHAT IT DELIBERATELY DOES NOT TOUCH
--   * Any job not named 'Backend Engineer k%'. In particular the demo's own fixtures --
--     'Email Delivery Smoke Test Engineer', 'Smoketest Live Invite Demo Engineer' --
--     and the application the demo sequence uses (a2769e7b-f63b-4d73-8ac3-a2e443b3d46b).
--   * 'Embedding Space Gate Verification' and its 7 matches against real candidates.
--     Only its 13 matches against load-test candidates go, because those would otherwise
--     point at candidate rows that no longer exist. Leaving them would not crash anything
--     -- the batch fetch omits unknown ids by design (milestone 53) -- but a dangling
--     reference in a demo database is not worth keeping.
--
-- IDENTIFICATION
-- Candidates are matched on the generated email (load.test.<n>.<hex>@example.com), not on
-- name: extraction truncated 30 of the 68 names to just "Load Test", so a name-based
-- filter silently misses them. Both filters were cross-checked and agree on exactly 68
-- rows with no asymmetry in either direction.
--
-- HOW TO RUN
--   docker exec -i csb-postgres psql -U csb_admin -d company_second_brain \
--     -v ON_ERROR_STOP=1 -f - < scripts/sql/clean_benchmark_rows.sql
--
-- It is one transaction: it either all applies or none of it does. To see the row counts
-- WITHOUT committing, change the final COMMIT to ROLLBACK and run it again.
-- =====================================================================================

BEGIN;

-- The two source sets, resolved once so every DELETE below agrees on what it is removing.
CREATE TEMP TABLE bj AS
    SELECT id FROM recruitment.jobs WHERE title LIKE 'Backend Engineer k%';

CREATE TEMP TABLE lc AS
    SELECT id FROM resume_intel.candidates WHERE email LIKE 'load.test.%@example.com';

-- Every match_result to remove: all of a benchmark job's, plus any match against a
-- load-test candidate on a job that is being kept.
CREATE TEMP TABLE mr AS
    SELECT id FROM recruitment.match_results WHERE job_id IN (SELECT id FROM bj)
    UNION
    SELECT id FROM recruitment.match_results WHERE candidate_id IN (SELECT id FROM lc);

-- Deletion order follows the foreign keys, deepest dependant first:
--   jobs <- job_descriptions <- match_results <- {applications, match_explanations}
--   applications <- {interview_rounds, recruiter_decisions}
-- The first four are expected to remove 0 rows; they are here so the script stays correct
-- if a future benchmark run does create an application.
DELETE FROM recruitment.recruiter_decisions
 WHERE application_id IN (SELECT id FROM recruitment.applications
                           WHERE job_id IN (SELECT id FROM bj));

DELETE FROM recruitment.interview_rounds
 WHERE application_id IN (SELECT id FROM recruitment.applications
                           WHERE job_id IN (SELECT id FROM bj));

DELETE FROM recruitment.applications
 WHERE job_id IN (SELECT id FROM bj)
    OR match_result_id IN (SELECT id FROM mr);

DELETE FROM recruitment.match_explanations
 WHERE match_result_id IN (SELECT id FROM mr);

DELETE FROM recruitment.match_results WHERE id IN (SELECT id FROM mr);

DELETE FROM recruitment.job_descriptions WHERE job_id IN (SELECT id FROM bj);

DELETE FROM recruitment.jobs WHERE id IN (SELECT id FROM bj);

-- Candidate side. second_brain has no foreign key to resume_intel by design (no
-- cross-schema grants -- see CLAUDE.md), so its row must be removed explicitly rather
-- than cascading.
DELETE FROM resume_intel.ingest_jobs WHERE candidate_id IN (SELECT id FROM lc);

DELETE FROM second_brain.candidate_knowledge WHERE candidate_id IN (SELECT id FROM lc);

DELETE FROM resume_intel.candidates WHERE id IN (SELECT id FROM lc);

COMMIT;

-- =====================================================================================
-- Afterwards, to confirm (expect 0, 0, and the demo application still present):
--
--   SELECT count(*) FROM recruitment.jobs WHERE title LIKE 'Backend Engineer k%';
--   SELECT count(*) FROM resume_intel.candidates WHERE email LIKE 'load.test.%@example.com';
--   SELECT count(*) FROM recruitment.applications
--    WHERE id = 'a2769e7b-f63b-4d73-8ac3-a2e443b3d46b';
-- =====================================================================================
