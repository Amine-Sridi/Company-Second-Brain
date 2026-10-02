-- scripts/sql/clean_mock_graph_data.sql
-- =====================================================================================
-- Removes the smoke-test / verification jobs that clutter the candidate<->job graph.
--
-- WHY THE GRAPH SPECIFICALLY
-- GET /v1/graph/candidates-jobs is built entirely from match_results: one job node per
-- job that has at least one match, one edge per MatchResult, and the candidate nodes are
-- derived by the frontend from edges[].candidate_id. So the graph is polluted by *jobs
-- with matches*, and deleting a job's matches removes both its node and any candidate
-- node that only appeared through it. No candidate row has to be touched to clean it.
--
-- WHAT GOES (measured before running: 9 jobs, 64 match_results, 29 match_explanations,
-- 9 job_descriptions)
--   Backend Engineer (Demo)                     Smoketest Full Workflow Audit
--   Batch Fetch Verification - Backend Engineer  Smoketest Interview Rounds - Schedulable
--   Embedding Space Gate Verification            Smoketest JD Repair Pass
--   Smoketest Atomic Submission                  Smoketest JD Repair Pass 3
--                                                Smoketest JD Validation Badge
--
-- THE SELECTION RULE, AND WHY IT IS NOT A TITLE LIST
-- A job is removed only if it matches a mock-data title pattern AND has **no
-- applications at all**. The second half is load-bearing: two of these titles exist
-- twice, once with an application and once without --
--   "Smoketest Full Workflow Audit"            (one has app b2f38054, accepted)
--   "Smoketest Interview Rounds - Schedulable" (one has app ca5067c1, selected)
-- so deleting by title would take out live application state. Selecting on "has no
-- applications" keeps the right one of each pair without hard-coding ids.
--
-- WHAT IS DELIBERATELY KEPT
--   * Every job that has an application, including the two the demo sequence names:
--     'Email Delivery Smoke Test Engineer' (holds a2769e7b, the interviewed application
--     the Report Detail leg uses) and 'Smoketest Live Invite Demo Engineer' (holds
--     095731d3, the live-invite leg). Note neither matches the patterns below anyway.
--   * The seven seeded demo jobs (Consultant BI Senior, Data Engineer, Consultant Big
--     Data, and the four from job_dataset.csv). They have no applications yet, which is
--     why the pattern list matters as well as the application check.
--   * **All candidate rows.** The four 'Smoketest ...' candidates each still have 7-9
--     matches against jobs being kept, so deleting them would leave dangling
--     candidate_ids on demo-relevant jobs. They stay, and they appear in the graph only
--     through those kept jobs, which is legitimate.
--
-- HOW TO RUN (PowerShell: -f reads inside the container, so copy it in first)
--   docker cp scripts\sql\clean_mock_graph_data.sql csb-postgres:/tmp/graph.sql
--   docker exec csb-postgres psql -U csb_admin -d company_second_brain -v ON_ERROR_STOP=1 -f /tmp/graph.sql
--
-- One transaction. Change the final COMMIT to ROLLBACK to see the row counts first.
-- =====================================================================================

BEGIN;

CREATE TEMP TABLE mock_jobs AS
SELECT j.id, j.title
FROM recruitment.jobs j
WHERE NOT EXISTS (SELECT 1 FROM recruitment.applications a WHERE a.job_id = j.id)
  AND (
        j.title LIKE 'Smoketest%'
     OR j.title LIKE 'Batch Fetch Verification%'
     OR j.title LIKE 'Section E Verification%'
     OR j.title LIKE 'Embedding Space Gate%'
     OR j.title = 'Backend Engineer (Demo)'
  );

-- Shown so the transaction is auditable before it commits.
SELECT title AS removing_job FROM mock_jobs ORDER BY title;

CREATE TEMP TABLE mock_matches AS
SELECT id FROM recruitment.match_results WHERE job_id IN (SELECT id FROM mock_jobs);

-- FK order: jobs <- job_descriptions <- match_results <- match_explanations.
DELETE FROM recruitment.match_explanations
 WHERE match_result_id IN (SELECT id FROM mock_matches);

DELETE FROM recruitment.match_results WHERE id IN (SELECT id FROM mock_matches);

DELETE FROM recruitment.job_descriptions WHERE job_id IN (SELECT id FROM mock_jobs);

DELETE FROM recruitment.jobs WHERE id IN (SELECT id FROM mock_jobs);

-- One dead Second Brain row from a smoke test on 2026-08-28: its candidate no longer
-- exists in Resume Intelligence. It does not affect the graph (which reads match_results,
-- not knowledge rows) and the batch fetch omits unknown ids by design, so this is tidiness
-- rather than a fix.
DELETE FROM second_brain.candidate_knowledge k
 WHERE k.source_version = 'smoketest-explain-1'
   AND NOT EXISTS (SELECT 1 FROM resume_intel.candidates c WHERE c.id = k.candidate_id);

COMMIT;

-- =====================================================================================
-- Afterwards, the graph should contain only the seeded demo jobs plus the ones holding
-- real application state:
--
--   SELECT j.title, count(m.id) AS edges, count(*) FILTER (WHERE m.qualified) AS qualified
--     FROM recruitment.jobs j JOIN recruitment.match_results m ON m.job_id = j.id
--    GROUP BY j.title ORDER BY edges DESC;
--
-- Expect 15 job nodes and 203 edges (down from 24 and 267), with the two demo-sequence
-- jobs and all 10 applications still present:
--
--   SELECT count(*) FROM recruitment.applications;                      -- expect 10
--   SELECT count(*) FROM recruitment.applications
--    WHERE id IN ('a2769e7b-f63b-4d73-8ac3-a2e443b3d46b',
--                 '095731d3-a1ba-4738-82b4-3c2a9182602f');              -- expect 2
-- =====================================================================================
