# TESTING.md

Every test suite in this repository, the exact command to run it, and what passing looks
like. All counts below were produced by actually running each command on 2026-10-01 — if a
number here disagrees with what you see, trust what you see and update this file.

Two things this file deliberately records, because both have cost real time:

- **One suite is dangerous to run carelessly.** `tests/integration` defaults
  `DATABASE_URL` to a *live* database name and has destroyed this project's demo data
  once. See [§7](#7-cross-service-integration--read-the-warning-first).
- **The Python suites cannot catch container-only breakage.** They run on the host against
  the repo, so an import that resolves from the repo root but not inside a service image
  passes everything here and fails in production. That has happened three times. See
  [§8](#8-container-checks--what-the-suites-structurally-cannot-see).

---

## TL;DR — just run this

```bash
python scripts/run_tests.py              # every suite, live progress, green/red
python scripts/run_tests.py --containers # also check the running stack's LLM fallback
python scripts/run_tests.py --python-only -v   # skip the frontend, stream full output
```

**Expected: `836 passed, 7 skipped across 8 suites`, exit 0.** It runs every suite in this
file except `tests/integration` (§7 — deliberately excluded, it can destroy demo data),
reports each one as it finishes, and exits non-zero if any fail. It also warns when a
suite's count drifts from the number documented below, so this file and the runner cannot
quietly disagree.

`NO_COLOR` is honoured, colour turns itself off when piped, and `--color` forces it back on
for a terminal it cannot detect.

## The same thing by hand

From the repository root. Stops at the first failing suite.

```bash
python -m pytest tests/unit -q \
  ; cd services/recruitment-workflow ; python -m pytest tests -q \
  ; cd services/resume-intelligence   ; python -m pytest tests -q \
  ; cd services/second-brain          ; python -m pytest tests -q \
  ; cd services/voicehr               ; python -m pytest tests -q \
  ; cd services/recruitment-workflow/frontend ; npm test
```

Expected totals: **783 Python tests passed, 7 skipped** + **15 frontend component tests**.
This does *not* include the two `node --test` files ([§6](#6-frontend)), the cross-service
integration suite ([§7](#7-cross-service-integration--read-the-warning-first)), or the
container checks ([§8](#8-container-checks--what-the-suites-structurally-cannot-see)).

| # | Suite | Command (from repo root unless noted) | Expected |
|---|---|---|---|
| 1 | Shared unit | `python -m pytest tests/unit -q` | 174 passed, 15 subtests |
| 2 | Recruitment Workflow | `cd services/recruitment-workflow && python -m pytest tests -q` | 224 passed |
| 3 | Resume Intelligence | `cd services/resume-intelligence && python -m pytest tests -q` | 89 passed, 6 skipped |
| 4 | Second Brain | `cd services/second-brain && python -m pytest tests -q` | 8 passed |
| 5 | VoiceHR | `cd services/voicehr && python -m pytest tests -q` | 288 passed, 1 skipped |
| 6a | Frontend pure logic | see [§6](#6-frontend) — per file, not per directory | 34 passed (13 + 21) |
| 6b | Frontend components | `cd services/recruitment-workflow/frontend && npm test` | 15 passed, 2 files |
| 7 | Cross-service integration | **read [§7](#7-cross-service-integration--read-the-warning-first) first** | never been green |
| 8 | Container checks | `python scripts/check_llm_fallback.py --generate` | exit 0 |

---

## Prerequisites

```bash
pip install -r requirements-dev.txt          # pytest + pytest-asyncio, pinned
pip install -r services/recruitment-workflow/requirements.txt   # the superset most suites need
```

`requirements-dev.txt` pins `pytest` and `pytest-asyncio` deliberately — a missing
`pytest-asyncio` is what the first real CI run caught. `pytest-subtests` is *not* needed
(the shared unit suite's 15 subtests use stdlib `unittest`, which pytest reports natively).

Nothing in §1–§6 needs Docker, Postgres, Redis, Ollama, or a Gemini key. Suites that would
otherwise need them skip explicitly rather than failing — see §3.

---

## 1. Shared unit tests

```bash
python -m pytest tests/unit -q
```

**Expected: `174 passed, 15 subtests passed`.**

Exercises `/shared` directly plus the pure logic it underpins (scoring, the language gate,
schema validation). Run from the repo root — these import `shared.*`, so the root must be
the working directory.

> `tests/e2e/` contains only a `README.md`. `pytest tests/e2e` collects **0 tests** and
> that is expected, not a broken suite.

## 2. Recruitment Workflow (RWS)

```bash
cd services/recruitment-workflow && python -m pytest tests -q
```

**Expected: `224 passed`.**

The largest behavioural surface: matching, the immutable `MatchResult`, recruiter auth,
the webhook receiver and its delivery-id dedup, and `language_gate.py`. The
`InsecureKeyLengthWarning` noise is a test-only short HMAC key, not a finding.

## 3. Resume Intelligence

```bash
cd services/resume-intelligence && python -m pytest tests -q
```

**Expected: `89 passed, 6 skipped`.**

The 6 skips are environmental and correct — they are not failures being hidden:

| Skipped | Needs |
|---|---|
| `test_parsing_integration.py` live Gemini smoke test (1) | `GEMINI_API_KEY` |
| `test_db.py` (5) | `DATABASE_URL` + a reachable Postgres |

One further skip appears only when the sample resume is absent: that file is gitignored on
purpose (it is a real CV), so the test skips rather than failing in a fresh clone or in CI.

## 4. Second Brain

```bash
cd services/second-brain && python -m pytest tests -q
```

**Expected: `8 passed`.**

Small by design — it covers the one invariant that matters most here: embedding-space
versioning. `nomic-embed-text` emits exactly 768 dimensions, the same as the pgvector
column, so querying Gemini-written vectors with local ones would insert cleanly and
silently corrupt retrieval instead of failing. These tests exist to make that loud.

## 5. VoiceHR

```bash
cd services/voicehr && python -m pytest tests -q
```

**Expected: `288 passed, 1 skipped`.**

The skip is **intentional and must stay**:

```
tests/test_sufficiency.py: relevance case: covered by test_off_topic_passes_the_counter
```

`off_topic_severely_thin` carries more words and more distinct words than a case that must
score, so no quantity threshold can separate them. It is excluded from the parameterised
sufficiency check on purpose and asserted separately. If that skip disappears, something
has changed in `sufficiency.py`'s contract — don't "fix" it by deleting the skip.

## 6. Frontend

Two runners, both required, and the split is not arbitrary.

```bash
cd services/recruitment-workflow/frontend
npm ci        # first time only

# 6a. Pure logic — PER FILE, never per directory
for f in src/constants/*.test.js; do echo "--- $f"; node --test "$f"; done

# 6b. Components (vitest + jsdom)
npm test
```

**Expected: 6a → `13 passed` + `21 passed` = 34. 6b → `2 passed (2 files)`, `15 passed`.**

Why 6a runs per file: `node --test` on a directory does not reliably pick these up here.
The CI job uses the same loop with `set -e` so any failing file fails the job. These need
no DOM and no dependencies, which is why they live in Node's own runner rather than vitest.
Note `node --test` prints `ℹ pass 13`, not `# pass` — don't grep for `#`.

## 7. Cross-service integration — read the warning first

> [!CAUTION]
> **`tests/integration/conftest.py` defaults `DATABASE_URL` to a live database name.**
> Running this suite without setting it explicitly has already destroyed this project's
> demo data once. **Always set both `TEST_DATABASE_URL` and `DATABASE_URL` to a
> throwaway test database.** `TEST_DATABASE_URL` alone is not enough — it only satisfies
> `db_safety.py`'s guard; the conftest default still applies to `DATABASE_URL`.

It also **cannot be collected from the repo root** — it needs its own directory on
`sys.path` for its conftest:

```
ModuleNotFoundError: No module named 'conftest_recruitment_workflow'
```

That error means you ran it from the wrong place, not that it is broken. The correct
invocation needs a real Postgres with the three schemas and the `vector` extension:

```bash
cd tests/integration
TEST_DATABASE_URL='postgresql+psycopg://...@localhost:5432/company_second_brain_test?options=-csearch_path%3Drecruitment' \
DATABASE_URL='postgresql+psycopg://...@localhost:5432/company_second_brain_test?options=-csearch_path%3Drecruitment' \
SERVICE_API_KEY=test-service-key \
python -m pytest . -q
```

**Status: 92 tests collected; this suite has never been green anywhere.** Its CI job is
`continue-on-error: true`, so it cannot fail the build today. That is honest but temporary
— either get it green and flip the flag to `false`, or delete it. A permanently amber job
is the worst of the three options.

## 8. Container checks — what the suites structurally cannot see

**§1–§6 all run on the host, against the repo.** An import that resolves from the repo root
but not inside a service image passes every test above and fails in production. That is not
hypothetical: `evaluator.py` imported `shared.llm`, which cannot exist in the VoiceHR image
(it builds from `context: ../services/voicehr`, which contains no `/shared`) — 23 tests
passed against code that could not run. Twice more, config was read from a settings object
whose fields were absent in the deployed image, or absent from `.env` entirely.

With the stack up (`docker compose -f infra/docker-compose.yml up -d`):

```bash
python scripts/check_llm_fallback.py --generate
```

**Expected: `[ OK ]` for all four services, exit 0.** It reads each service's own settings
object *from inside its container*, probes the configured Ollama URL, confirms that
service's specific models are present, and with `--generate` sends a real completion.
`--generate` is what matters: a reachable Ollama serving a model it cannot load passes every
other check there is, which is exactly how a bad image pin got through.

Re-run it after **any** of: bumping the Ollama image, changing a `LOCAL_LLM_*` value,
rebuilding a service image, or changing a service's imports.

```bash
python scripts/reconcile_orphans.py --older-than-days 90    # dry run, exit 0, deletes nothing
```

Not a test, but safe to run and a useful sanity check that the volume and DB agree.
**Never add `--apply` without reading `handoff.md` first** — the imported recording archive
is indistinguishable from abandoned files.

## 9. What CI runs

`.github/workflows/ci.yml`, 8 jobs: `unit`, `recruitment-workflow`, `resume-intelligence`,
`second-brain`, `voicehr`, `frontend`, `integration` (non-blocking), plus the push/PR
triggers. Runners are **Ubuntu**; local validation here is **Windows**. Three things in that
workflow are OS-sensitive, so a green local run is not proof of a green CI run:

- the `node --test` per-file loop with `set -e`,
- the `psql` heredoc that bootstraps schemas in `integration`,
- VoiceHR's `working-directory` constraint.

**CI does not build the service images**, so it shares the blind spot described in §8. The
`integration` job starts a Postgres *service container* — that is not the same thing.

---

## Appendix: what is NOT a test

These are measurement and verification scripts. They call live providers, cost quota, write
rows, or depend on the machine's GPU, so they are never part of a test run. They are
documented here only so nobody mistakes a failing one for a broken build.

| Script | Purpose | Notes |
|---|---|---|
| `benchmarks/load/ingest_load_test.py` | ingestion throughput | writes real candidates; reports the Gemini/local provider split |
| `benchmarks/load/matching_latency_test.py` | `submit_job` latency | part B creates job + `match_result` rows |
| `benchmarks/local_llm/evaluation_deep_dive.py` | local-model CEFR accuracy | GPU-bound, minutes per model |
| `benchmarks/local_llm/evaluation_stability.py` | same answer on re-run? | 25 generations per model |
| `benchmarks/rubric/evaluate_real_transcripts.py` | real evaluator on real transcripts | `--survey` is free; `--run` spends Gemini quota |
