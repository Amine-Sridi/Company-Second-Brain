# Recruiter/Admin Frontend — Handoff

Authoritative handoff for the React recruiter/admin frontend living at
`services/recruitment-workflow/frontend/`. Read this file in full before touching any
frontend code.

Every claim in this document was checked directly against the repository at the time of
writing (RWS's `main.py`/`schemas.py`, `shared/schemas/__init__.py`, and the frontend's
own file tree) — not assumed from prior conversation. Where something is proposed but not
yet built, it is labeled **PROPOSED**, not described as if it exists.

---

## 1. Purpose

This file covers the recruiter/admin React frontend only: its architecture, the API
contract it consumes, its current implementation state, and the next bounded task.

It does **not** cover:
- RWS, Resume Intelligence, Second Brain, or VoiceHR backend implementation details
  beyond what the frontend needs to know as a consumer of RWS's API.
- The system-wide architecture (schemas, scoring, matching, workflow state machine,
  webhook design). That remains owned by the repo-root `handoff.md` and each service's
  own `README.md` — this file must never contradict them, and never duplicates their
  content beyond the minimum needed to consume RWS correctly from the browser.
- Anything about VoiceHR's candidate-facing interview flow (out of scope for this
  frontend entirely).

Goal: a fresh Claude Code session should be able to read this file alone, look at the
one or two files it points to for the current `[NEXT]` task, and continue frontend work
without needing the conversation history that produced this document.

---

## 2. Frontend role in the system

**Responsibility**: give a recruiter a browser-based dashboard to run the hiring flow —
log in, add a candidate's resume to the pool, post a job from a raw JD, review the
system's ranked/scored candidates, select one, invite them to a VoiceHR interview, watch
its status, see the assessment once it lands, and accept or reject.

**What stays backend-owned** (the frontend must never reimplement any of this):
- JD structuring (Gemini, RWS-side).
- Deterministic + semantic scoring, the qualification hard-gate, and threshold logic
  (`services/recruitment-workflow/scoring.py`).
- `MatchResult` persistence and immutability.
- The application state machine (`selected → invited → interviewed → accepted/rejected`).
- Resume extraction, candidate persistence, and PII ownership (Resume Intelligence).
- Candidate knowledge/embeddings (Second Brain).
- Interview conduct and evaluation (VoiceHR).
- All service-to-service auth (API keys, HMAC webhook signatures).

The frontend's job is to **call RWS, render what RWS returns, and request the state
transitions RWS already exposes** — nothing more.

### Browser → RWS-only boundary

```
┌──────────────┐        JWT (Authorization: Bearer)        ┌────────────────────────┐
│   Browser    │ ─────────────────────────────────────────▶│           RWS          │
│ (this React  │◀───────────────────────────────────────── │ (recruitment-workflow) │
│    app)      │           JSON responses                  └────────────────────────┘
└──────────────┘                                                      │  │  │
                                                          service API keys  │  │
                                                          (never leave RWS) │  │
                                                                            ▼  ▼  ▼
                                                      ┌─────────────────┐ ┌──────────┐ ┌─────────┐
                                                      │ Resume          │ │ Second   │ │ VoiceHR │
                                                      │ Intelligence    │ │ Brain    │ │         │
                                                      │ (owns PII)      │ │          │ │         │
                                                      └─────────────────┘ └──────────┘ └─────────┘
```

The browser **never** calls Resume Intelligence, Second Brain, or VoiceHR directly, and
never receives a service API key in any response, header, or bundled config. Every
cross-service call in the diagram's lower half happens server-side, inside RWS.

### Authentication boundary

- Recruiter/admin auth: RWS's existing JWT (`POST /v1/auth/login`, `Authorization:
  Bearer <token>` on every subsequent call). One seeded recruiter account; no
  registration, no RBAC, no multi-user system yet.
- Service-to-service auth (RWS → Resume Intelligence / Second Brain / VoiceHR): static
  API keys. These exist only in RWS's own environment/settings and are never sent to,
  stored in, or reachable from frontend code.
- The frontend holds exactly one credential: the recruiter's JWT, in `sessionStorage`.

### PII handling rules

- Candidate PII (name, email, phone) is owned exclusively by Resume Intelligence.
- RWS's own data model (`MatchResult`, `Application`) is PII-free — every candidate
  reference is an opaque `candidate_id` (UUID). Verified directly in
  `services/recruitment-workflow/schemas.py`: `MatchResultResponse`, `JobResponse`, and
  `ApplicationResponse` contain no name/email/phone field anywhere.
- RWS does not persist candidate identity in its own database. Any identity the
  recruiter UI shows is fetched transiently, per request, and re-fetched (or cached
  client-side only) rather than stored server-side.
- The frontend must render the minimum identity needed for a recruiter to do their job
  (a display name) and must not request or render more than that by default (see §9).

---

## 3. Technology decisions

| Choice | Why |
|---|---|
| **React** | Component structure genuinely helps here — multiple stateful, interactive pages (ranking table, tracking table, forms) — more than a hand-rolled DOM-manipulation approach would stay maintainable for. |
| **Vite** | Fast dev server, zero-config React support via `@vitejs/plugin-react`, trivial static production build (`vite build` → `dist/`). No custom webpack config to maintain in a 2-month project. |
| **react-router-dom (BrowserRouter)** | Clean URLs (`/jobs/:jobId`) rather than hash fragments — the conventional choice for a real React app. Required one small, well-understood FastAPI serving change (§17, implemented in Phase 9); not a reason to prefer `HashRouter`. |
| **native `fetch`** | Every call this app makes is either a simple JSON request or one `multipart/form-data` upload — both are native `fetch`/`FormData` capabilities. No interceptor/cancellation/transform need that would justify a client library. |
| **CSS Modules + shared `variables.css`** | Component-scoped class names with zero extra dependency (built into Vite). Shared tokens (`variables.css`) keep color/spacing consistent without a theming library. |
| **React Context for auth only** | Auth (token + recruiter identity) is the one piece of state genuinely needed across unrelated parts of the tree — every API call and every route guard needs it. Nothing else in this app has that shape. |
| **Local component state everywhere else** | No two pages need to share jobs/matches/applications data simultaneously; lifting it to a global store would only add indirection. |
| **`sessionStorage` for the JWT** | Cleared on tab close — the right default for a recruiter demo session; not persistent across browser restarts the way `localStorage` would be. |

### Explicitly rejected / not currently justified

| Technology | Why not |
|---|---|
| **Redux** | No cross-cutting state beyond auth exists or is anticipated. Would add boilerplate and a dependency for a problem this app doesn't have. |
| **Zustand** | Same reasoning as Redux — not rejected forever, but there is no concrete need today. If a future page genuinely needs to share complex state across siblings that Context can't express cleanly, reconsider then, with the specific need stated, not preemptively. |
| **React Query / TanStack Query** | The app makes a handful of one-shot calls per page plus one polling need (interview status), solvable with a ~25-line custom hook (§12). React Query's cache-invalidation model and ~13kb+ would solve problems this app's scale doesn't have. |
| **Axios** | `fetch` already covers every request shape this app needs; Axios would be a same-capability substitute, not a capability gain. |
| **MUI / Chakra / any component library** | Nothing here needs a date picker, rich text editor, or complex data grid — tables, badges, forms, and buttons are all straightforward to hand-build. A library would impose real bundle weight and its own visual language for no corresponding need. |
| **WebSockets** | VoiceHR's completion signal already arrives at RWS as an async webhook, not a browser-visible push. Polling `GET /v1/jobs/{job_id}/applications` is sufficient and matches the project-wide "no infrastructure beyond what's demonstrably needed" rule. |
| **Next.js / SSR** | This is an internal, JWT-gated recruiter tool with no SEO or first-paint requirement that would justify SSR. A static SPA served by RWS's own FastAPI is simpler end to end. |
| **Micro-frontends** | Five pages and one shared layout do not need independent deployability. |

---

## 4. Target frontend structure

```
services/recruitment-workflow/frontend/
├── src/
│   ├── api/           # One module per resource; each wraps client.js for a specific
│   │                   # RWS resource -- auth.js/resumes.js/jobs.js/candidates.js
│   │                   # (getCandidateIdentity, getCandidates, getCandidateKnowledge
│   │                   # -- the last new in Phase 15)/applications.js
│   │                   # (selectMatch/getApplications/getAllApplications/invite/
│   │                   # accept/reject)/interviews.js (getInterviewReport, new in
│   │                   # Phase 14) all exist and are used
│   ├── auth/           # Plain JS (no React) JWT storage + decode -- callable from
│   │                    # anywhere, including api/client.js, without a hook
│   ├── context/          # AuthContext -- the one global store this app has
│   ├── router/            # AppRouter (route table) + ProtectedRoute (auth guard)
│   ├── hooks/               # Reusable custom hooks -- useCandidateIdentity and
│   │                          # usePolling both exist
│   ├── constants/            # Plain, framework-agnostic shared config (currently
│   │                           # just applicationStatus.js's STATUS_LABELS/STATUS_TONE/
│   │                           # STATUS_ORDER -- added in Phase 12 once ApplicationsPage
│   │                           # and DashboardPage both needed the exact same mapping)
│   ├── components/           # Reusable, presentational pieces shared across pages
│   │                           # (Sidebar, TopBar, PageHeader, StatusBadge, EmptyState,
│   │                           #  StatTile -- the last four added in the Phase 11
│   │                           #  admin-shell pass; AssessmentPanel added in Phase 13
│   │                           #  once InterviewsPage needed the same render logic
│   │                           #  ApplicationsPage already had, now also reused (via
│   │                           #  an adapter) by Phase 14's ReportDetailPage -- see
│   │                           #  §19/§20)
│   ├── pages/                  # One component per route, owns its own local state
│   ├── styles/                  # variables.css (shared tokens) + global.css (reset)
│   ├── App.jsx                   # AuthProvider + BrowserRouter + AppRouter
│   └── main.jsx                    # ReactDOM root
├── index.html
├── package.json
└── vite.config.js
```

Folder responsibilities, precisely:

- **`api/`** — the only code allowed to call `fetch`. Every function here takes plain
  arguments and returns plain data or throws `ApiError`; no component should import
  `fetch` directly.
- **`auth/`** — framework-agnostic auth utilities (`getToken`, `setToken`, `clearToken`,
  `decodeJwt`). Used by both `api/client.js` and `context/AuthContext.jsx`.
- **`context/`** — React wiring around `auth/`. This is deliberately the *only* context
  in the app; do not add a second one without updating §3's justification.
- **`router/`** — route table and the auth-guard wrapper. No page should implement its
  own auth check; `ProtectedRoute` is the single place that decision is made.
- **`hooks/`** — cross-page reusable logic that isn't simple enough to inline (async
  fetch-with-loading-state, polling, the candidate-identity cache reader). Not a place
  for one-off page logic.
- **`constants/`** — plain shared config objects (label/tone maps, ordering), promoted
  out of a page file only once a second page genuinely needs the same mapping (per
  CLAUDE.md's "promote once real duplication is observed" rule) — not a place for
  logic or fetch code.
- **`components/`** — presentational, reusable across ≥2 pages. Page-specific
  sub-components can live inside the page file itself if never reused.
- **`pages/`** — one per route. Owns its own `useState`/`useEffect` for whatever data
  that page needs; does not read or write any other page's state.
- **`styles/`** — `variables.css` (colors/spacing/font tokens as CSS custom properties)
  and `global.css` (box-sizing reset, body defaults). Per-component styling is a
  co-located `ComponentName.module.css`, not centralized here.

---

## 5. Routes

| Route | Access | Page |
|---|---|---|
| `/login` | Public | `LoginPage` |
| `/` | Protected | `DashboardPage` (Phase 12 — now real data, see §20) |
| `/candidates` | Protected | `CandidatesPage` (Phase 12 — new) |
| `/candidates/:candidateId` | Protected | `CandidateDetailPage` (Phase 15 — new) |
| `/jobs` | Protected | `JobsListPage` |
| `/upload` | Protected | `ResumeUploadPage` |
| `/jobs/:jobId` | Protected | `JobDetailPage` |
| `/jobs/:jobId/applications` | Protected | `ApplicationsPage` |
| `/interviews` | Protected | `InterviewsPage` (Phase 13 — new, global/unscoped) |
| `/reports` | Protected | `ReportsPage` (Phase 14 — new) |
| `/reports/:applicationId` | Protected | `ReportDetailPage` (Phase 14 — new) |
| `*` | Protected fallback | redirects to `/` (or `/login` if unauthenticated, since `ProtectedRoute` runs first) |

`LoginPage`'s post-login redirect (both the already-authenticated short-circuit and the
successful-submit path) now targets `/` instead of `/jobs`, matching Dashboard's new
role as the app's home screen. No route named here is broken or half-built —
`/candidates` was only added once `CandidatesPage` actually worked (Phase 12),
`/interviews` only once `InterviewsPage` actually worked (Phase 13), `/reports` and
`/reports/:applicationId` only once both Reports pages actually worked (Phase 14),
and `/candidates/:candidateId` only once `CandidateDetailPage` actually worked
(Phase 15). Every candidate name shown anywhere in the app now links to it:
`CandidatesPage`'s rows, `JobDetailPage`'s ranked-candidate names,
`ApplicationsPage`'s and `InterviewsPage`'s candidate names, `ReportsPage`'s rows,
and `ReportDetailPage`'s own header (via a best-effort `candidate_id` lookup
against `GET /v1/applications`, since VoiceHR's report has no concept of it — see
§20/§21).

All protected routes are nested under one `ProtectedRoute` → `Layout` wrapper in
`AppRouter.jsx` (implemented) — no page individually re-checks auth.

---

## 6. Authentication

- **Login endpoint**: `POST /v1/auth/login`, body `{ email, password }`, response
  `{ access_token, token_type: "bearer", expires_at }` (verified against
  `services/recruitment-workflow/schemas.py`'s `LoginRequest`/`LoginResponse`; RWS never
  accepts or checks `X-Api-Key` on this endpoint).
- **JWT storage**: `sessionStorage`, via `src/auth/token.js` (`getToken`/`setToken`/
  `clearToken`) — implemented.
- **JWT use in the fetch wrapper**: `src/api/client.js`'s `request()` reads
  `getToken()` and attaches `Authorization: Bearer <token>` to every call, unless
  `skipAuth: true` is passed (used only by the login call itself, which has no token
  yet) — implemented.
- **Logout**: `AuthContext.logout()` clears the token and resets in-memory state;
  `TopBar`'s Logout button calls it, then navigates to `/login` — implemented.
- **401 handling**: any non-`skipAuth` response with status `401` clears the token and
  hard-redirects (`window.location.assign("/login")`) — implemented in
  `api/client.js`. This is intentionally a full page reload, not a router-level
  navigation: it guarantees correct behavior even if triggered from outside the React
  tree, at the cost of losing in-memory state on session expiry (acceptable — a
  recruiter re-logs in and starts the next action fresh).
- **Protected route behavior**: `ProtectedRoute` reads `AuthContext.isAuthenticated`
  and renders `<Navigate to="/login" replace />` when false — implemented.
- **JWT decoding is display-only.** `decodeJwt()` reads the payload (`email`, `sub`)
  purely to show "logged in as …" in the top bar. The signature is never checked
  client-side, and nothing in this app uses the decoded payload to make an
  authorization decision — the backend is the only verifier. Route protection is based
  solely on "is a token present," not on decoded claims.
- **No service API key anywhere in frontend code.** Confirmed by construction: no file
  under `frontend/src/` references a service key, and `api/client.js` only ever
  attaches the recruiter's own JWT.

---

## 7. API contract map

All endpoints below are on **RWS only**. Response fields listed are copied from
`services/recruitment-workflow/schemas.py` and `main.py` directly, not inferred. Rows
marked **PROPOSED** are not yet implemented on the backend — see §20.

| Frontend action | Frontend API function | Endpoint | Key response fields | Expected errors |
|---|---|---|---|---|
| Log in | `api/auth.js: login(email, password)` *(exists)* | `POST /v1/auth/login` | `access_token`, `token_type`, `expires_at` | `401` invalid credentials |
| Upload a resume | `api/resumes.js: uploadResume(file)` *(implemented)* | `POST /v1/resumes` | `candidate_id`, `is_new` | `401` no/invalid JWT · `422` unsupported file type · `502` Resume Intelligence unreachable/failed |
| List jobs | `api/jobs.js: getJobs()` *(implemented)* | `GET /v1/jobs` **IMPLEMENTED** | `job_id`, `title`, `created_at`, `match_count` (`JobSummaryResponse`, newest-first) | `401` no/invalid JWT |
| Create a job from a JD | `api/jobs.js: createJob({ title, raw_description, threshold?, top_k? })` *(implemented)* | `POST /v1/jobs` | `job_id`, `job_description_id`, `version`, `title`, `required_skills`, `preferred_skills`, `min_years_experience`, `titles`, `threshold`, `scoring_config_version`, `matches: MatchResultResponse[]` (matches here have `explanation: null` — see next row) | `502` JD structuring / Second Brain retrieval or lookup failed · `422` unknown `scoring_config_version` · `500` no scoring config exists |
| View ranked candidates | `api/jobs.js: getJobMatches(jobId)` *(implemented)* | `GET /v1/jobs/{job_id}/matches` | `match_id`, `candidate_id`, `semantic_score`, `skill_score`, `experience_score`, `title_score`, `deterministic_score`, `final_score`, `hard_gate_passed`, `matched_requirements`, `missing_requirements`, `qualified`, `explanation` (populated lazily here, unlike the `POST /v1/jobs` response) | `404` job not found |
| Resolve a candidate's display name | `api/candidates.js: getCandidateIdentity(candidateId)` *(implemented)* | `GET /v1/candidates/{candidate_id}/identity` **IMPLEMENTED** | `candidate_id`, `name`, `email` (`CandidateIdentityResponse` — no `phone`, no other fields) | `401` no/invalid JWT · `404` unknown candidate · `502` Resume Intelligence unavailable |
| Select a qualified candidate | `api/applications.js: selectMatch(matchId)` *(implemented)* | `POST /v1/matches/{match_id}/select` | `ApplicationResponse` (see below) | `404` match not found · `422` not qualified · `409` already selected |
| Track applications for a job | `api/applications.js: getApplications(jobId)` *(implemented)* | `GET /v1/jobs/{job_id}/applications` | `ApplicationResponse[]` | `404` job not found |
| Invite to VoiceHR | `api/applications.js: invite(applicationId, language?)` *(implemented)* | `POST /v1/applications/{application_id}/invite` | `ApplicationResponse` (now `status: "invited"`) | `404` not found · `409` not invitable in current status · `422` candidate contact not found · `502` contact lookup or VoiceHR call failed |
| Accept | `api/applications.js: accept(applicationId)` *(implemented)* | `POST /v1/applications/{application_id}/accept` | `ApplicationResponse` (now `status: "accepted"`) | `404` not found · `409` not in a decidable state |
| Reject | `api/applications.js: reject(applicationId)` *(implemented)* | `POST /v1/applications/{application_id}/reject` | `ApplicationResponse` (now `status: "rejected"`) | `404` not found · `409` not in a decidable state |
| List all candidates | `api/candidates.js: getCandidates()` *(implemented)* | `GET /v1/candidates` **IMPLEMENTED** | `candidate_id`, `name`, `email` (optional), `created_at` (`CandidateSummaryResponse`, newest-first, transiently proxies Resume Intelligence — no RWS persistence, no `db` dependency on the route) | `401` no/invalid JWT · `502` Resume Intelligence unavailable |
| List all applications (unscoped) | `api/applications.js: getAllApplications()` *(implemented)* | `GET /v1/applications` **IMPLEMENTED** | `GlobalApplicationResponse[]` — every `ApplicationResponse` field (below) plus `job_title`, newest-selected-first across every job | `401` no/invalid JWT |
| View a full interview report | `api/interviews.js: getInterviewReport(applicationId)` *(implemented)* | `GET /v1/interviews/{application_id}/report` **IMPLEMENTED** | No `response_model` — VoiceHR's own report shape passed through as-is (candidate_name, candidate_email, overall_score, cefr_level, dimensions, strengths, concerns, evidence, stats, transcript, plus `recording: {available, url}` rewritten by RWS to a same-origin URL or `{available: false, url: null}`) | `401` no/invalid JWT · `404` unknown application or no report yet · `502` VoiceHR unavailable |
| Play an interview recording | not a separate frontend API function — `requestBlob(report.recording.url)` from `api/client.js` *(implemented)* | `GET /v1/interviews/{application_id}/recording` **IMPLEMENTED** | Raw audio bytes with the real `Content-Type` (e.g. `audio/webm`) — no JSON envelope | `401` no/invalid JWT · `404` no recording for this application · `502` VoiceHR unavailable |
| View a candidate's professional knowledge | `api/candidates.js: getCandidateKnowledge(candidateId)` *(implemented)* | `GET /v1/candidates/{candidate_id}/knowledge` **IMPLEMENTED** | `candidate_id`, `skills`, `job_titles`, `years_of_experience` (`CandidateKnowledgeResponse` — no `raw_text`, no `source_version`, no embedding; transiently proxies Second Brain via the already-existing `clients.get_candidate()` — no RWS persistence, no `db` dependency on the route) | `401` no/invalid JWT · `404` unknown candidate (no Second Brain knowledge row) · `502` Second Brain unavailable |

`ApplicationResponse` fields (from `schemas.py`, used by select/invite/accept/reject/list
above): `application_id`, `job_id`, `match_result_id`, `candidate_id`, `status`
(`"selected" | "invited" | "interviewed" | "accepted" | "rejected"`), `selected_at`,
`invite_attempt_number`, `invited_at`, `interview_status`
(`"completed" | "inconclusive" | "failed" | null`), `interview_assessment` (`dict | null`
— see §13), `interview_completed_at`, `decided_at`. `GlobalApplicationResponse` (used
only by `GET /v1/applications`) is `ApplicationResponse` plus one extra field,
`job_title` — the job-scoped `GET /v1/jobs/{job_id}/applications` is unchanged and
still returns plain `ApplicationResponse` (no `job_title`, since the job is already
implied by the URL).

**Not recruiter-facing, must never be called from this frontend**: `POST
/v1/scoring-configs` (service-key protected, `require_service_key` not
`require_recruiter`) and `POST /v1/webhooks/voicehr` (HMAC-signature protected,
VoiceHR-to-RWS only).

---

## 8. State ownership

**Global** (React Context — `AuthContext` only):
- JWT, decoded recruiter (`email`), `isAuthenticated`, `login()`, `logout()`.

**Page-local** (`useState` inside the owning page component):
- Jobs list, job detail/matches, applications list — fetched and held by
  `JobsListPage`/`JobDetailPage`/`ApplicationsPage` respectively.
- Form inputs (login form — implemented; future job-creation form, upload form).
- Upload result (`candidate_id`/`is_new` after a resume upload).

**Cache** (module-level singleton, outside React state — `hooks/useCandidateIdentity.js`,
**implemented**):
- Candidate identity, keyed by `candidate_id`, shared read access across every
  `CandidateRow` on `JobDetailPage` and every `ApplicationRow` on `ApplicationsPage` so
  the same candidate's name isn't re-fetched on every row re-render or page revisit
  within a session. A failed lookup is cached too (`status: "error"`), so a broken
  `candidate_id` degrades once per session rather than being hammered on every render.

**Temporary** (local to `ApplicationsPage`, via `hooks/usePolling.js` — **implemented**):
- The interview-status poll timer. Started only while at least one visible application
  has `status: "invited"` (derived from the page's own `applications` state on every
  render, not tracked separately), stopped on unmount, on tab-hidden, and automatically
  the moment nothing is left `invited` (the hook's own effect dependency array reacts
  to `enabled` flipping to `false` — no manual "stop" call needed from the page).

**Why no global application store**: no two pages ever need simultaneous access to the
same piece of non-auth data. Jobs, matches, and applications are each read by exactly
one page at a time, freshly fetched on navigation. Introducing a store to share data
that nothing actually shares would be speculative infrastructure — exactly what this
project's own conventions (see `CLAUDE.md`) warn against building ahead of a
demonstrated need.

---

## 9. Candidate identity / PII

This is the one architectural nuance in the whole frontend, so it gets its own section.

**Why `MatchResult`/`Application` only carry `candidate_id`**: RWS must not become a
second place candidate PII lives (per the platform's privacy boundary — Resume
Intelligence is the sole owner of name/email/phone). Verified directly:
`MatchResultResponse` and `ApplicationResponse` in `schemas.py` have no identity field
at all, only `candidate_id: UUID4`.

**Why the recruiter UI still needs a display name**: a bare UUID is not a usable hiring
interface — a recruiter cannot meaningfully "select candidate
`a637c23e-d302-407e-b9cf-27e327715965`."

**The RWS identity proxy** (`GET /v1/candidates/{candidate_id}/identity`) is now
**IMPLEMENTED and live-verified** (see §20). It is exactly the thin, JWT-protected
wrapper this section originally proposed, built directly on top of the existing
`resume_intelligence_client.get_contact()` call (previously internal-only, used by
`invite_application`):
- Resume Intelligence remains the PII owner; RWS never persists what it returns — the
  route (`get_candidate_identity` in `main.py`) takes no `db` parameter at all, so
  there is nothing for it to write to.
- The call is transient — made fresh per request, not cached in RWS's own database.
- The response (`CandidateIdentityResponse`, `extra="forbid"`) carries only
  `candidate_id`, `name`, and `email` (`Optional[EmailStr]`). No `phone`, no
  `candidate_json`, no other field — confirmed by test (`phone` asserted absent from
  the response body). Do not render `phone` anywhere in this frontend; nothing
  currently needs it (invitation is entirely server-side in RWS already, via the
  existing internal `get_contact()` call). `email` is present in the payload but the
  frontend should not render it by default in list views — keep the visible surface to
  `name` unless a specific screen has a stated reason to show more.
- Unknown `candidate_id` → `404`; Resume Intelligence unreachable/failed → `502` with a
  generic `detail` (no upstream exception text, no PII, in either the response or RWS's
  own logs).

**Frontend caching rule — implemented** (`src/hooks/useCandidateIdentity.js`):
caches resolved identities in an in-memory `Map`, scoped to the current browser
session/runtime only (a plain module variable, not `sessionStorage`/`localStorage`). It
is deliberately not persisted to any browser storage and not sent anywhere but the
identity endpoint itself — this keeps the frontend from becoming a third place
candidate PII is retained, even transiently across reloads. A lookup failure resolves
to `{ status: "error", name: null, email: null }` rather than throwing, so it is
cached and rendered like any other outcome — never allowed to break the row, let alone
the page (live-verified: see §20).

---

## 10. Page specifications

### LoginPage — **implemented**
- **Purpose**: recruiter authentication, the entry point to the app.
- **API calls**: `POST /v1/auth/login` via `api/auth.js: login()`.
- **Primary components**: self-contained form (email/password inputs, submit button,
  error banner) — no shared components used yet.
- **State**: `email`, `password`, `error`, `submitting` — all local `useState`.
- **Actions**: submit → calls `AuthContext.login()` → on success, `navigate("/jobs")`.
- **Loading state**: submit button shows "Logging in…" and disables while `submitting`.
- **Empty state**: n/a (a form has no empty-data state).
- **Error state**: inline banner rendering the backend's own `detail` (e.g. "Invalid
  email or password") via `ApiError.message`.
- **Navigation behavior**: if already authenticated (token present), renders
  `<Navigate to="/jobs" replace />` instead of the form.

### JobsListPage — **implemented**
- **Purpose**: landing page after login; browse existing jobs, start a new one.
- **API calls**: `GET /v1/jobs` via `api/jobs.js: getJobs()` (fetched fresh on every
  mount — a hard reload or a return visit always reflects current backend state, never
  stale local data); `POST /v1/jobs` via `api/jobs.js: createJob()`.
- **Primary components**: job list rendered as clickable cards (title + `match_count` +
  formatted `created_at`), plus an inline "New Job" form (title input + JD textarea,
  threshold/top_k tucked behind a collapsed "Advanced options" toggle, pre-filled with
  the backend's own defaults — `threshold=0.6`, `top_k=20`). No separate `JobCard`
  component file — the card markup is a `<button>` inline in `JobsListPage.jsx`, not
  reused elsewhere yet.
- **State**: `jobs`, `loading`, `error` (list); `showForm`, `title`, `rawDescription`,
  `showAdvanced`, `threshold`, `topK`, `creating`, `createError` (form) — all local
  `useState`.
- **Actions**: create job → on success, `navigate("/jobs/{job_id}")` to view its ranked
  matches immediately.
- **Loading/empty/error**: "Loading jobs…" / "No jobs yet — create one to get
  started." / error banner with the backend's own `detail`.
- **Navigation behavior**: clicking a job card → `/jobs/:jobId`.
- **Known live behavior**: `POST /v1/jobs` runs the full JD-structuring (Gemini) +
  Second Brain retrieval + scoring pipeline server-side before responding, so job
  creation can take several seconds and — same as resume upload (§20) — can surface a
  real transient `502` if Gemini's free tier is under load at that moment. The submit
  button's label reflects this ("Creating job… (this runs matching, may take a
  moment)"); the error banner shows the backend's real `detail` verbatim so a recruiter
  knows to retry, not a generic frontend failure message.

### ResumeUploadPage — **implemented**
- **Purpose**: add a candidate's resume to the pool (independent of any specific job).
- **API calls**: `POST /v1/resumes` via `api/resumes.js: uploadResume(file)` — builds a
  `FormData` with one `file` field and posts it through the shared `api/client.js`
  wrapper (no `Content-Type` set manually; the browser sets the multipart boundary).
- **Primary components**: a single click-or-drop zone (native HTML5 drag-and-drop
  events, no library), `accept=".pdf,.docx,.jpg,.jpeg,.png"` on the underlying file
  input as a UX hint only (the backend is authoritative on rejection), a selected-file
  chip with a Remove action, and a success panel.
- **State**: `file`, `status` (`"idle" | "uploading" | "success" | "error"`), `result`
  (`{candidate_id, is_new} | null`), `error`, `dragActive` — all local `useState`, no
  page-external state.
- **Actions**: choose/drop a file → Upload (disabled while no file is selected or a
  request is in flight) → on success, shows `candidate_id` and a plain-language
  new/deduplicated message derived from `is_new`; "Upload another resume" resets to a
  clean `idle` state for a new attempt.
- **Loading state**: Upload button reads "Uploading…" and is disabled; the drop zone
  and file input are also disabled during the request, preventing both a second submit
  and a mid-upload file swap.
- **Empty state**: no file selected → Upload button stays disabled; no separate
  "empty" message needed since the drop zone's own idle copy covers it.
- **Error state**: inline banner rendering the backend's own `detail` verbatim via
  `ApiError.message` (`422` unsupported type, `502` integration failure, or the
  generic network-failure message from `client.js`).
- **Navigation behavior**: none forced — a standalone action; deliberately no link to a
  candidate-detail page (none exists yet).

### JobDetailPage (`/jobs/:jobId`) — **implemented**
- **Purpose**: the ranked-candidates view — the core recruiter screen.
- **API calls**: `GET /v1/jobs/{job_id}/matches` via `api/jobs.js: getJobMatches()`
  (always re-fetched on mount, keyed off the `:jobId` route param — never trusts
  navigation state, so a hard browser refresh on this URL works correctly from backend
  state alone, per this task's explicit requirement); `GET /v1/jobs` via `getJobs()` as
  a **best-effort** source for the header's title/`created_at`/`match_count` (no
  dedicated `GET /v1/jobs/{job_id}` exists on RWS — see §20 — so the job list is
  searched client-side for the matching `job_id`; if this call fails, the page still
  renders the matches table with a generic "Job Detail" heading rather than failing the
  whole page over a decoration); `GET /v1/candidates/{id}/identity` per visible row via
  the `useCandidateIdentity` hook; `POST /v1/matches/{match_id}/select` via
  `api/applications.js: selectMatch()` on Select.
- **Primary components**: a candidate table with one `CandidateRow` per match
  (resolved/fallback name, `ScoreBar`, qualified/not-qualified badge, Select button) and
  an expandable detail row (score breakdown, `hard_gate_passed`, matched/missing
  requirement chips, AI explanation). `CandidateRow`/`ScoreBar`/`ScoreDetail`/
  `RequirementChips` are defined as page-local functions inside `JobDetailPage.jsx`
  (not `components/`) — `ApplicationsPage` turned out to need its own distinct
  row shape (status/interview-status/invite, not scores), so nothing has needed these
  yet; promote them only once a second page genuinely needs the same pieces.
- **State**: `job` (best-effort header, `null` until resolved), `matches`, `loading`,
  `error` at the page level; per-row `expanded`, `selectStatus`
  (`idle|selecting|selected|error`), `selectError` inside each `CandidateRow`.
- **Actions**: click a row to expand/collapse its score detail; Select (disabled while
  `!qualified` or once already `selected`) → on success **or** on a `409` conflict
  (already selected — treated as the same real end state, not an error), the row shows
  "Selected ✓" inline. **Does not auto-navigate to `/jobs/:jobId/applications`** on
  select — the recruiter stays on this page and sees the confirmation in place;
  `ApplicationsPage` is reachable via the header's "View Applications" link instead
  (added alongside `ApplicationsPage` itself — see below).
- **Loading/empty/error**: "Loading candidates…" / "No candidates matched this job." /
  error banner showing the backend's own `detail` (e.g. `404` job not found). A
  per-row candidate-identity failure never blocks the table — see `useCandidateIdentity`
  below; the row falls back to a short `candidate_id` prefix with an inline "(name
  unavailable)" note instead.
- **Navigation behavior**: none forced from Select (see above); a recruiter navigates
  back to `/jobs` via the sidebar, or forward to `/jobs/:jobId/applications` via the
  "View Applications" link in the header.
- **Known live behavior**: `explanation` on each match is generated lazily by RWS on
  first read of this endpoint (at most once per match, then cached server-side per
  `match_explanations` — see `main.py: get_job_matches`); on a cold, never-read job this
  can mean several sequential Gemini calls before the response returns, and — same
  external condition documented throughout this project — a Gemini free-tier
  slow/`503` period can make a first cold read of a several-match job take a long time
  or return some rows with `explanation: null` where generation failed. This is a
  pre-existing backend/Gemini characteristic, not something introduced or fixable by
  this frontend task; the frontend correctly renders whatever the backend returns,
  including a `null` explanation (the "AI explanation" block simply doesn't render for
  that row).

### ApplicationsPage (`/jobs/:jobId/applications`) — **implemented**
- **Purpose**: track selected candidates through invitation, interview completion, and
  the recruiter's final accept/reject decision — every transition in §12's state
  diagram is now request-able from this page.
- **API calls**: `GET /v1/jobs/{job_id}/applications` via
  `api/applications.js: getApplications()` — called both on mount (`loadApplications`,
  drives `loading`/`error`) and on every poll tick (`pollApplications`, silent — see
  §12); always re-fetched fresh, keyed off the `:jobId` route param, same "never trust
  navigation state" pattern as `JobDetailPage`, so a hard refresh on this URL reflects
  real backend state; `GET /v1/jobs` via `getJobs()` as a **best-effort** header source
  (same pattern as `JobDetailPage` — no dedicated `GET /v1/jobs/{id}` exists on RWS);
  `GET /v1/candidates/{id}/identity` per row via the existing `useCandidateIdentity`
  hook; `POST /v1/applications/{id}/invite` via `api/applications.js: invite()` on
  Invite/Resend Invite; `POST /v1/applications/{id}/accept` /
  `POST /v1/applications/{id}/reject` via `api/applications.js: accept()`/`reject()`
  on Accept/Reject.
- **Primary components**: a table with one `ApplicationRow` per application
  (resolved/fallback name, a status badge, an interview-status cell showing `—` when
  `interview_status` is `null`, and an actions cell holding whichever of
  Invite/Resend-Invite/Accept/Reject currently apply, or a terminal "Accepted ✓"/
  "Rejected" label once decided), plus an expandable `AssessmentPanel` (§13) for rows
  whose `interview_assessment` is non-null. `ApplicationRow` and `AssessmentPanel` are
  page-local functions inside `ApplicationsPage.jsx` (not `components/`), same
  reasoning as `JobDetailPage`'s `CandidateRow` family — promote only once genuinely
  shared.
- **State**: `job` (best-effort header), `applications`, `loading`, `error` at the page
  level; per-row `inviting`, `inviteError`, `deciding` (`null | "accept" | "reject"`),
  `decideError`, `expanded` inside each `ApplicationRow`. `hasInvitedApplication`
  (whether to poll) is not separate state — it's derived straight from `applications`
  on every render.
- **Actions**:
  - Invite/Resend Invite — shown **only** when `status` is `"selected"` or `"invited"`
    (`INVITABLE_STATUSES`, mirroring `main.py: invite_application`'s own gate; a UX
    hint, not a substitute for RWS's real `409` check — §22), labeled "Invite" for
    `selected` and "Resend Invite" for `invited`.
  - Accept/Reject — shown **only** when `status` is `"selected"`, `"invited"`, or
    `"interviewed"` (`DECIDABLE_STATUSES`, mirroring `db.py: _DECIDABLE_STATUSES`; same
    UX-hint-not-substitute relationship to RWS's real `409` — a decision can be made
    from `"selected"` directly, before any invite, matching RWS's own documented
    behavior that the interview is informative for the decision, not a mandatory
    gate). Reject prompts a native `window.confirm()` ("Reject this candidate? This
    cannot be undone.") before sending the request — the one place in this frontend a
    confirmation was judged worth the friction (§15's "no confirmation modals unless
    clearly beneficial"); Accept does not prompt. Once `status` becomes
    `"accepted"`/`"rejected"`, no button reappears for that row — these are terminal in
    the UI, backed by RWS's own terminal state (leaving `DECIDABLE_STATUSES` means a
    repeat call gets a real `409`, independently confirmed live — see §20).
  - Every action on a row (Invite, Accept, Reject) disables **all** of that row's
    buttons while any one is in flight (`rowBusy = inviting || deciding !== null`) —
    a UX safeguard against firing two conflicting mutations on the same application at
    once; RWS's own per-endpoint checks remain the real authority regardless. On
    success, the row is updated in place from the real returned `ApplicationResponse`
    (no local status guessing); on failure, an inline error banner (separate for
    invite vs. decide, so one doesn't clobber the other) shows the backend's own
    `detail` verbatim (`409`/`422`/`404`/`502` all rendered the same way, via
    `ApiError.message`, per §14 — no special-cased wording for any one status).
  - Click a row whose `interview_assessment` is non-null to expand/collapse
    `AssessmentPanel` (§13); rows without an assessment are not clickable at all (no
    `cursor: pointer`, no `role="button"`) — nothing to expand yet.
  - **Polling** (§12, `hooks/usePolling.js`): while any application is `"invited"`,
    the page silently re-fetches every ~7s so an interview's completion (webhook-driven
    `invited → interviewed` on RWS, entirely server-side) becomes visible without a
    manual reload — no accept/reject or invite action triggers this directly, it's
    purely an observer of backend state; deciding an application never itself starts
    or stops polling beyond what the resulting status naturally implies (an
    `"invited"` application decided straight to `"accepted"`/`"rejected"` simply stops
    being counted as `"invited"`, same as any other status change).
- **Loading/empty/error**: "Loading applications…" / "No applications yet — select a
  candidate from the ranking table." / error banner with the backend's own `detail` —
  all driven by the initial `loadApplications` call only; a polling failure never
  touches any of these three states, so already-rendered data stays exactly as it was
  through a transient poll error (see §12). A per-row candidate-identity failure never
  blocks the table either (same `useCandidateIdentity` fallback as `JobDetailPage`: a
  short `candidate_id` prefix with an inline "(name unavailable)" note).
- **Navigation behavior**: reachable from `JobDetailPage` via a "View Applications"
  link in its header (the only route to this page, since it's job-scoped and the
  sidebar's nav is global); no navigation forced from this page itself — a recruiter
  goes back to `/jobs/:jobId` or `/jobs` via the sidebar.

---

## 11. Job detail / candidate ranking requirements

Every field below comes directly from `MatchResultResponse` and must be **displayed,
never recalculated**:

- `final_score` — the headline number/bar for each candidate.
- `deterministic_score`, `semantic_score` — shown in an expandable breakdown, not the
  default row view.
- Component scores where useful: `skill_score`, `experience_score`, `title_score` — same
  expandable breakdown.
- `qualified` — rendered as a clear pass/fail badge (this is the recruiter's primary
  scanning signal).
- `hard_gate_passed` — shown in the expanded detail as the reason a candidate may be
  unqualified despite a reasonable `final_score`.
- `matched_requirements` / `missing_requirements` — rendered as chip lists (e.g. green
  for matched, red/muted for missing).
- `explanation` — the LLM-generated advisory text, rendered as-is in the expanded
  detail. Note it is `null` in the `POST /v1/jobs` response and only populated by `GET
  /v1/jobs/{job_id}/matches` (lazy, cached server-side) — the frontend should call the
  GET endpoint to display explanations, not rely on the POST response for them.

**Hard rule**: the frontend must never compute, adjust, re-rank by a different formula,
or second-guess `qualified`/`final_score`/any component score. `MatchResult` is
authoritative; the frontend is a renderer of it, not a second scorer.

---

## 12. Applications / VoiceHR flow

```
selected ──(Invite)──▶ invited ──(webhook: interview completes)──▶ interviewed ──(Accept/Reject)──▶ accepted/rejected
   │                                                                    │
   └────────────────────────(Accept/Reject also allowed here)──────────┘
```

The frontend only ever **requests** these transitions through RWS
(`/invite`, `/accept`, `/reject`) and **observes** the resulting `status` — it never
computes or assumes a transition itself. VoiceHR is never called directly by the
browser; the webhook that actually advances `invited → interviewed` happens entirely
server-side, on RWS's `POST /v1/webhooks/voicehr` (HMAC-authenticated, VoiceHR → RWS
only).

**Polling — implemented** (`hooks/usePolling.js`, generic; wired into `ApplicationsPage`):
- Runs only on `ApplicationsPage`, via `usePolling(pollApplications, { intervalMs:
  7000, enabled: hasInvitedApplication })`.
- `GET /v1/jobs/{job_id}/applications` every **~7 seconds** — `pollApplications()` is a
  silent background refresh, deliberately separate from the page's initial
  `loadApplications()` (which drives `loading`/`error`): a failed poll never touches
  `loading` or `error`, so already-rendered data is never blanked out or replaced with
  an error banner over a transient failure. Only a successful poll response ever calls
  `setApplications`.
- Only while **at least one visible application has `status: "invited"`** —
  `hasInvitedApplication` is computed fresh from the current `applications` state on
  every render (`applications.some(a => a.status === "invited")`), so it turns itself
  off automatically the moment nothing is left waiting (a webhook-driven transition to
  `interviewed`, or there was never anything invited to begin with).
- **Pauses when `document.hidden`**: the hook stops its `setInterval` entirely (not
  just skipping the fetch) and restarts it via the `visibilitychange` event once the
  tab becomes visible again.
- **Stops entirely** once no application is in a pending-interview state, and its
  timer/listener are cleaned up on unmount — no manual "stop polling" button needed.
- **No WebSockets** — polling a list endpoint is sufficient for this flow's timing and
  matches the project's explicit no-new-infrastructure stance.
- **No direct VoiceHR calls, ever** — the frontend only ever re-reads RWS's own
  `GET /v1/jobs/{job_id}/applications`; the `invited → interviewed` transition itself
  happens entirely server-side via RWS's `POST /v1/webhooks/voicehr`, before polling
  ever sees it.

---

## 13. VoiceHR assessment rendering

`ApplicationResponse.interview_assessment` is `Optional[dict]`, populated verbatim from
`InterviewAssessment.model_dump()` (verified in `shared/schemas/__init__.py` and
`main.py`'s webhook handler). Its exact fields — **do not render or invent anything
beyond this list**:

| Field | Type | Notes |
|---|---|---|
| `overall_score` | `int \| null`, 0–100 | Headline number. |
| `cefr_level` | `string \| null`, ≤10 chars | e.g. `"C1"`. |
| `fluency_score` | `int \| null`, 0–100 | |
| `vocabulary_score` | `int \| null`, 0–100 | |
| `grammar_score` | `int \| null`, 0–100 | |
| `pronunciation_score` | `int \| null`, 0–100 | |
| `comprehension_score` | `int \| null`, 0–100 | |
| `inconclusive` | `bool` | If `true`, the assessment should be visually flagged as low-confidence rather than presented at face value. |
| `evaluation_status` | `"completed" \| "failed" \| "inconclusive"` | Drives which of the above are meaningful to show — e.g. on `"failed"`, scores may be absent/null and the UI should say so plainly rather than render a table of blanks. |

`AssessmentPanel` — **implemented**, part of `ApplicationsPage.jsx` (page-local
function, same convention as `JobDetailPage`'s `CandidateRow` family). Renders only
while `interview_assessment` is non-null (a row with no assessment is not even
click-expandable — `hasAssessment` gates both the expand affordance and the panel
itself). Branches on `evaluation_status`: `"failed"` renders a single plain sentence
("Evaluation failed — no scores are available for this interview.") instead of a grid
of blanks; `"completed"`/`"inconclusive"` render the full score grid, with each
individual score defensively rendered as `"—"` rather than assumed present
(`value == null ? "—" : "${value}/100"` — note this correctly distinguishes a real `0`
score from a missing one, since `== null` only matches `null`/`undefined`). The
`inconclusive` boolean is rendered as its own "Low-confidence evaluation" note
independent of `evaluation_status` — it can be `true` on a `"completed"` evaluation
too, per the schema, so it's not folded into the status badge. No field beyond §13's
table is rendered or invented, live-verified against two real fixtures with different
shapes — see §20.

---

## 14. Error-handling conventions

All handled centrally in `api/client.js` (implemented for the 401 case; the general
shape below applies to every future call built on top of it):

| Status | Frontend behavior |
|---|---|
| `400` | Not currently produced by any endpoint in §7 as documented, but if encountered: show the backend `detail` in the page's error banner. |
| `401` | **Global**: clear the token, hard-redirect to `/login` (implemented). The one exception is the login call itself (`skipAuth: true`), whose `401` means "wrong credentials" and is shown inline on `LoginPage`, not treated as session expiry. |
| `404` | Show the backend `detail` (e.g. "Job not found") in the page's error banner; do not silently redirect. |
| `409` | Show the backend `detail` (e.g. "This candidate has already been selected for this job", "Application is not in a decidable state") — these are meaningful conflict messages, not generic failures. |
| `422` | Show the backend `detail` verbatim — these are the most specific, most actionable messages the backend produces (unsupported file type, not-qualified selection attempt, missing contact info). |
| `502` | Show as an upstream/integration failure (e.g. "Resume Intelligence unreachable", "VoiceHR invitation failed") — the backend `detail` already frames it this way; don't reword it as if it were the frontend's own fault. |
| `500` | Generic "Something went wrong, please try again" — RWS's own 500 details are deliberately generic server-side already (this codebase never puts internal detail into a 500 response), so there is nothing more specific to surface. |

General rules:
- Always prefer the backend's own `detail` string when present — it is already
  human-readable and PII-safe by the backend's own established discipline (verified
  across Resume Intelligence, Second Brain, and RWS's own error-handling code).
- Never render a raw stack trace, exception type, or response body dump — `ApiError`
  only ever carries `status` and a `message` string; there is nothing lower-level to
  accidentally leak.
- A network-level failure (server unreachable) is caught in `client.js` and surfaced as
  a generic "Unable to reach the server" — implemented.

---

## 15. UX / visual direction — **implemented** (Phase 8, see §20 for full results)

This should read as a real, lightweight ATS/recruiter dashboard — not an "AI product
demo." Concretely:

- Clean sidebar navigation (Jobs, Upload Resume) + a top bar showing the logged-in
  recruiter and Logout — implemented, and (Phase 8) the sidebar now becomes a compact
  horizontal top bar below the tablet breakpoint instead of a tall vertical block
  (`Sidebar.module.css`'s own `@media (max-width: 768px)`).
- Restrained color palette — one primary accent color (`--color-primary`), neutral
  surfaces/borders, and semantic colors used only for status/error, not decoration.
  (Phase 8) `variables.css` now carries the full semantic set as shared tokens —
  `--color-success`/`-bg`, `--color-warning`/`-bg`, `--color-danger`/`-bg`,
  `--color-accent`/`-bg`, plus `--color-primary-bg`/`--color-hover-bg`/
  `--color-neutral-bg` — replacing hand-picked hex values that had drifted slightly
  different (e.g. `#f2f5fd` vs `#eaf0fd` vs `#f7f9fd` for what was meant to be the
  same "light primary tint") across `JobsListPage`/`JobDetailPage`/`ApplicationsPage`.
  Every status/qualified/evaluation badge and every action button across all five
  pages now pulls from this one set, so "green" and "red" mean the same thing
  everywhere.
- Readable tables/cards over anything dense or cramped — this is presented on a laptop
  or projector to a jury, not optimized for information density. (Phase 8) tables
  (`JobDetailPage`, `ApplicationsPage`) gained a `box-shadow: var(--shadow-sm)` for a
  subtle card lift and a `min-width` on the `<table>` itself (620px/640px) so a
  narrower viewport scrolls the table horizontally via the existing `.tableWrapper`
  rather than squeezing columns illegibly.
- Status badges with clear, distinct colors per state
  (`selected/invited/interviewed/accepted/rejected`) — implemented since Phase 7;
  (Phase 8) now backed by the shared semantic tokens above rather than five
  independently-chosen hex pairs.
- Strong information hierarchy: `final_score` and `qualified` are the first things a
  recruiter should see per candidate; component scores, matched/missing requirements,
  and explanation are one interaction away (expand), not all shown at once —
  unchanged this pass (a presentation-only pass, no interaction logic touched).
- Score visualization without gimmicks — a plain horizontal bar or number is enough; no
  chart library, no radial gauges, no animated counters. Unchanged — no chart or gauge
  was added.
- Expandable match details (collapsed by default) rather than a permanently dense row —
  unchanged.
- Minimal animation — at most a subtle loading-state transition; nothing decorative.
  (Phase 8) a shared `--transition-fast: 120ms ease` token now backs every
  hover/focus color change across the app (buttons, links, cards, table rows) — no
  keyframe animation, spinner, or motion beyond that was added, deliberately, to stay
  inside this constraint.
- Laptop/projector-first layout; basic responsive behavior only. (Phase 8) two shared
  breakpoints (768px tablet, 480px phone-width) are now used consistently across every
  page's own `@media` rules — sidebar collapse (existing), plus new: page headers wrap
  and their action buttons/links go full-width, forms stack to one column, and the
  drop zone/selected-file row on `ResumeUploadPage` stacks vertically. Not a full
  mobile redesign — still laptop/projector-first, per this section's own scope.

**Where "AI" should visibly show up**: the match explanation text, the score
breakdown/insight it's attached to, and the VoiceHR assessment — i.e., in the *content*
RWS already generated, not in the frontend's own visual branding. No AI-themed iconography,
gradients, or "powered by AI" chrome — the product should look like a hiring tool that
happens to use AI internally, not an AI showcase. Unchanged this pass — no icon, badge,
or gradient referencing "AI" was added anywhere.

**A shared keyboard-focus treatment was added to `global.css`** (not previously
documented, since it didn't exist before Phase 8): every focusable element gets a
visible `:focus-visible` outline in `--color-primary`; text inputs/textareas/selects
get a softer box-shadow ring instead of the plain outline, for a more form-appropriate
look. This is additive only — it changes what a keyboard-focused element looks like,
never what any element does.

---

## 16. Development setup

**Current commands** (from `services/recruitment-workflow/frontend/`):

```bash
npm install     # installs react, react-dom, react-router-dom, vite, @vitejs/plugin-react
npm run dev     # Vite dev server, http://localhost:5173
npm run build   # production build → dist/
npm run preview # serves the dist/ build locally, for a pre-deploy sanity check
```

**Proxy**: `vite.config.js` proxies `/v1` and `/health` to RWS
(`RWS_DEV_TARGET` env var, default `http://localhost:8003` — matching
`infra/.env`'s `RECRUITMENT_WORKFLOW_PORT` mapping). This means every API call in the
frontend code is a **relative path** (`fetch("/v1/...")`) — identical in dev (proxied)
and in production same-origin serving (§17, now implemented). Never hardcode an
absolute backend URL anywhere in `src/`. This dev-proxy path is unchanged by Phase 9 —
`vite.config.js` was not touched — and remains the day-to-day frontend dev workflow;
§17's production path is a separate, additional way to run the same built app.

Verified working: `npm install` and `npm run build` both succeed cleanly (0 errors, 63
modules as of Phase 8/9, ~0.8s build). A live login smoke test through this exact proxy
path against the real running RWS container returned a real JWT on valid credentials
and the backend's own `"Invalid email or password"` on an invalid one. Separately,
§20 now also has a live-tested production path (`docker compose build` → real
container → `http://localhost:8003/`) with no Vite dev server involved at all.

---

## 17. Production serving plan — **implemented and live-verified** (Phase 9, see §20)

Shape: `npm run build` → `frontend/dist/` → served by RWS's own FastAPI app,
same-origin (no CORS configuration needed anywhere, and none was added).

**Critical ordering rule**: FastAPI/Starlette matches routes in registration order —
first match wins. `main.py` already defines every `/v1/*` route and `/health` via
decorators. The SPA-serving routes are added **last**, physically after all of those
(the final block in the file), so they only ever catch a request that didn't match an
API route first:

```python
# main.py -- the final block in the file, after every existing @app.get/post(...)
# route including the /v1/webhooks/voicehr handler

_FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"

if _FRONTEND_DIST.is_dir():
    app.mount(
        "/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="frontend-assets"
    )

    @app.get("/", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_react_app(full_path: str = "") -> FileResponse:
        return FileResponse(_FRONTEND_DIST / "index.html")
else:
    logger.info("frontend_dist_not_found", extra={"path": str(_FRONTEND_DIST)})
```

`/assets/*` (Vite's hashed JS/CSS bundle output) is served directly; every other
non-API path falls through to `index.html`, letting `BrowserRouter` take over
client-side. As long as this block stays physically after the `/v1/*` route
definitions in the file, there is no shadowing risk — live-verified, see §20.

**One deliberate addition beyond the original sketch**: the whole block is guarded by
`_FRONTEND_DIST.is_dir()`. `frontend/dist/` only exists after a real `npm run build`
(produced by the Docker image's Node build stage below, and `.gitignore`d — never
committed). Mounting `StaticFiles` against a directory that doesn't exist raises
`RuntimeError` at import time in FastAPI, which would break `import main` — and
therefore this project's own host-side test suite (`pytest tests/`, which does exactly
that) and `npm run dev`'s Vite-proxy dev workflow — on any machine that has never run
a frontend build. The guard means: dist present (the Docker image, or a host checkout
where someone ran `npm run build` locally) → SPA routes registered; dist absent (a
fresh checkout, CI, the normal dev workflow) → those three routes are simply never
registered, and every `/v1/*`/`/health` route is completely unaffected either way.
Live-verified both ways — see §20.

**Docker**: Node is needed only to *build* the frontend, never in the runtime image —
multi-stage `services/recruitment-workflow/Dockerfile`:

```dockerfile
FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY services/recruitment-workflow/frontend/package*.json ./
RUN npm ci
COPY services/recruitment-workflow/frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY services/recruitment-workflow/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ /app/shared/
COPY services/recruitment-workflow/ /app/
RUN rm -rf /app/frontend
COPY --from=frontend-build /frontend/dist /app/frontend/dist
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**One deliberate addition beyond the original sketch here too**: `COPY
services/recruitment-workflow/ /app/` copies the *entire* service directory as one
tree, which includes `services/recruitment-workflow/frontend/` — its unbuilt JS/JSX
source, since nothing excludes a subdirectory from a directory-level `COPY`. The
`RUN rm -rf /app/frontend` immediately after removes that whole tree before the built
`dist/` is copied back in from the `frontend-build` stage, so the runtime image ends
up with only `frontend/dist/` — never frontend source, `node_modules`, or Node itself.
This is what actually delivers "copy only `frontend/dist` into the runtime image," not
just "the build happens to work."

A root-level `.dockerignore` (`/`, not `services/recruitment-workflow/`) excludes
`**/frontend/node_modules/` and `**/frontend/dist/` from the Docker build *context* —
this matters because `resume-intelligence`/`second-brain`/`recruitment-workflow` all
share the repo root as their build context (`context: ..` in
`infra/docker-compose.yml`), so one root-level file benefits all three; VoiceHR builds
from its own subdirectory context and is unaffected by it. Also excludes
`__pycache__/`, `.venv/`, `.pytest_cache/`, `.git/`, and other standard hygiene, mostly
mirroring the existing root `.gitignore`.

---

## 18. Testing strategy

The repository has no existing JavaScript/frontend test framework anywhere. Per this
project's own "boring over clever, don't introduce infrastructure ahead of a
demonstrated need" convention, **do not add one just to write a handful of tests** for
this frontend's current size.

What's worth testing, proportionally, once there's a reason to (i.e., once a bug in one
of these has actually been found, or before a final pre-demo hardening pass — not
speculatively now):
- `auth/token.js` — pure functions (`decodeJwt` especially, since malformed input must
  degrade to `null`, not throw and break the top bar).
- `ProtectedRoute` behavior — authenticated vs unauthenticated render.
- Major API error handling in `api/client.js` (401 → clear+redirect; non-401 error →
  `ApiError` with the right `detail`).
- Critical UI state transitions where a real bug would be embarrassing live in front of
  a jury (e.g. the applications table correctly reflecting a status change after
  invite/accept/reject).

If/when a framework is introduced, **Vitest** (Vite-native, zero extra config, pairs
with `@testing-library/react`) is the natural fit — not Jest, which would need its own
transform config fighting Vite's. This is a recommendation for *when* testing is
justified, not an instruction to set it up now.

**Real end-to-end smoke testing remains the primary verification method** for this
frontend, the same way it has been for every backend service in this project: run the
dev server against the real RWS (and, transitively, the real Resume
Intelligence/Second Brain/VoiceHR stack), and click/curl through the actual flow. This
has already been done once for login (§20) and should be repeated at the end of each
future phase.

---

## 19. Implementation phases

- [x] **Phase 1 — React/Vite foundation + auth**
- [x] **Phase 2 — Resume upload**
- [x] **Phase 3 — Jobs list + job creation**
- [x] **Phase 4 — Candidate ranking / identity / selection**
- [x] **Phase 5 — Applications + invitation** *(list + invite/resend-invite only, by
  explicit scope — polling and assessment display are Phase 6, accept/reject is Phase
  7)*
- [x] **Phase 6 — Polling + VoiceHR assessment display**
- [x] **Phase 7 — Accept/reject**
- [x] **Phase 8 — Visual polish / responsive states**
- [x] **Phase 9 — Docker production build + FastAPI serving**
- [x] **Phase 10 — Final frontend E2E/demo audit** *(one finding, M1, fixed post-audit
  — see §20/§24)*
- [x] **Phase 11 — Admin shell redesign** *(Phase 1 of the separate admin-dashboard
  redesign proposal — modern Sidebar/TopBar/Layout, PageHeader/StatusBadge/EmptyState/
  StatTile, a visual-only Dashboard page — see §20/§24)*
- [x] **Phase 12 — Candidates page + real Dashboard data** *(consumes the `GET
  /v1/candidates`/`GET /v1/applications` backend prerequisites from the prior
  session — new `CandidatesPage`, `DashboardPage` now shows real counts instead of
  `"—"` placeholders — see §20/§24)*
- [x] **Phase 13 — Global VoiceHR interviews page** *(new `InterviewsPage` at
  `/interviews`, read-only, built on the same `GET /v1/applications` Phase 12
  unblocked; `AssessmentPanel` promoted out of `ApplicationsPage` into a shared
  component — see §20/§24)*
- [x] **Phase 14 — VoiceHR Reports backend proxy + Reports pages** *(new VoiceHR
  service-key-gated report/recording endpoints, new RWS transient-proxy endpoints,
  new `ReportsPage`/`ReportDetailPage` — the last screen from the admin-dashboard
  redesign proposal's nav — see §20/§24; tracked cross-service in `handoff.md`)*
- [x] **Phase 15 — Candidate detail + cross-linking** *(new `CandidateDetailPage` at
  `/candidates/:candidateId`; every candidate name across the app now links to it;
  new RWS `GET /v1/candidates/{id}/knowledge` proxy to Second Brain — see §20/§24;
  tracked cross-service in `handoff.md`)*

**The frontend now has every screen named in the original admin-dashboard redesign
proposal, plus the candidate-detail cross-linking hub tying them together.** All
fifteen phases done: Dashboard, Candidates (+ detail), Upload, Jobs, VoiceHR
(interviews), and Reports, with every candidate name anywhere in the app now a real
link. §24 confirms no frontend feature work is currently queued — the next bounded
task (VoiceHR SQLite persistence hardening) is backend/infra-only, tracked in
`handoff.md`.

Future sessions: update the checkboxes and the relevant page/section status above as
each phase lands. Do not duplicate this checklist elsewhere or restate history that's
already captured here — extend it.

---

## 20. Verified current state

**Implemented and verified** (confirmed against the actual repository, not assumed):
- Frontend scaffold at `services/recruitment-workflow/frontend/`: `package.json`,
  `vite.config.js`, `index.html`, `.gitignore`.
- `src/main.jsx`, `src/App.jsx`.
- `src/api/client.js` (fetch wrapper, `ApiError`, JWT header, 401 handling),
  `src/api/auth.js` (`login()`).
- `src/auth/token.js` (`getToken`/`setToken`/`clearToken`/`decodeJwt`).
- `src/context/AuthContext.jsx` (`AuthProvider`, `useAuth`).
- `src/router/AppRouter.jsx`, `src/router/ProtectedRoute.jsx` — `ProtectedRoute`
  unchanged. **Phase 11**: `AppRouter`'s `index` route now renders `DashboardPage`
  directly (was `<Navigate to="/jobs" replace />`); the `*` fallback now redirects
  to `/` instead of `/jobs` (still falls through to `/login` first when
  unauthenticated, since `ProtectedRoute` wraps the whole tree — unchanged). No
  other route, and no auth-guard logic, changed.
- `src/pages/Layout.jsx` (unchanged) + `.module.css` (now reads the new
  `--sidebar-width` token instead of a hardcoded `220px`).
- `src/components/Sidebar.jsx` + `.module.css` — **redesigned in Phase 11**, rendered
  from one `NAV_ITEMS` array with a small hand-authored inline SVG icon per item (no
  new dependency) and a softer tinted active-state instead of a solid-fill pill;
  `linkClass`'s active/inactive logic itself is unchanged. **Phase 12**: a fourth
  item (`Candidates` → `/candidates`, a new person-icon `IconCandidates`) was added
  to `NAV_ITEMS`, in between `Dashboard` and `Upload Resume` — added only once
  `CandidatesPage` itself was working, per Phase 11's own "don't link to a page that
  doesn't exist" rule. `VoiceHR`/`Reports` remain deliberately absent.
- `src/components/TopBar.jsx` + `.module.css` — **redesigned in Phase 11**:
  identical `recruiter.email` display and `handleLogout()` call, now with a small
  inline logout icon next to the label; no auth/navigation logic changed.
- `src/pages/LoginPage.jsx` + `.module.css` — fully functional, not a placeholder;
  visually untouched by Phase 11 (out of that pass's scope). One small navigation-
  destination change in Phase 11: both post-login redirects (`isAuthenticated`
  short-circuit and successful submit) now target `/` instead of `/jobs`, since
  Dashboard is the new home route (§5) — no other line changed.
- `src/pages/ResumeUploadPage.jsx` + `.module.css` — fully functional: click-or-drop
  file selection, upload via `api/resumes.js`, loading/success/error states, "Upload
  another resume" reset. **Phase 11**: both header blocks (default view and the
  success view) now render `<PageHeader title="Upload Resume" subtitle="..." />`
  instead of their own inline `<h1>`/`<p>`; every other line, including the drop
  zone's own `role="button"`/`tabIndex` (a separate, pre-existing gap this pass did
  not touch — see §21), is unchanged.
- `src/api/resumes.js` (`uploadResume(file)`).
- `src/api/client.js`'s `request()` — accepts a `FormData` body (used by
  `uploadResume`) alongside the existing JSON-body path; no `Content-Type` is set when
  the body is `FormData`, so the browser supplies the correct multipart boundary
  itself. The JSON path (used by `login()`) is unchanged.
- `src/pages/JobsListPage.jsx` + `.module.css` — fully functional: fetches and renders
  the real jobs list, and a "New Job" creation form. **Phase 11**: the header block
  now renders `<PageHeader title="Jobs" subtitle="..." actions={<button>New Job/
  Cancel</button>} />` (the button's own `onClick`/`showForm` toggle logic is
  unchanged, just relocated into `PageHeader`'s `actions` slot), and the empty-list
  message now renders `<EmptyState title="No jobs yet" description="..." />` instead
  of a bare `<p>`. The now-superseded `.header`/`.title`/`.subtitle` rules were
  removed from `JobsListPage.module.css` (moved into `PageHeader.module.css`); the
  form/list/button CSS is untouched.
- `src/pages/JobDetailPage.jsx` + `.module.css` — fully functional: fetches and renders
  real ranked matches, with per-row candidate-identity resolution, expandable
  score/requirements/explanation detail, candidate selection, and a "View
  Applications" header link to `/jobs/:jobId/applications`. Phase 10's `onKeyDown`
  fix (M1) on `CandidateRow`'s expandable row is unchanged. **Phase 11**: the header
  now renders `<PageHeader title=... subtitle=... actions={<Link>View
  Applications</Link>} />` (the `Link`'s `to`/className and the subtitle's exact
  computed text are unchanged, just re-homed); the empty-matches message now uses
  `<EmptyState>`; the qualified/not-qualified badge now renders
  `<StatusBadge tone={match.qualified ? "success" : "neutral"}>` instead of a
  `styles.badgeQualified`/`badgeNotQualified` span (identical resulting colors —
  `success`/`neutral` map to the exact same tokens those two classes used). No
  score/select/expand logic, network call, or state variable changed.
- `src/pages/ApplicationsPage.jsx` + `.module.css` — fully functional: fetches and
  renders the real applications list for a job, per-row candidate-identity resolution,
  status + interview-status display, Invite/Resend Invite, background polling while
  any application is `"invited"`, a real `AssessmentPanel` rendering
  `interview_assessment` once an interview completes, and Accept/Reject decision
  buttons, disabled while a decision is pending, terminal "Accepted ✓"/"Rejected"
  labels once decided, and a native `window.confirm()` guard on Reject only. Phase
  10's `onKeyDown` fix (M1) on `ApplicationRow`'s expandable row is unchanged.
  **Phase 11**: the header now renders `<PageHeader title=... />` (no subtitle/
  actions, matching the original markup exactly); the empty-list message now uses
  `<EmptyState>`; both badges (`application.status` and
  `interview_assessment.evaluation_status`) now render through `<StatusBadge>` —
  `STATUS_STYLES`/`EVALUATION_STATUS_STYLES` (CSS-class-name maps) were renamed to
  `STATUS_TONE`/`EVALUATION_STATUS_TONE` (tone-name maps) with the identical
  status→color pairing preserved (`selected`→neutral, `invited`→primary,
  `interviewed`→accent, `accepted`→success, `rejected`→danger;
  `completed`→success, `inconclusive`→warning, `failed`→danger). The
  `.acceptButton:hover` rule's bare `#185f36` literal was also replaced with the new
  `--color-success-hover` token (same color, now tokenized — see §21's now-resolved
  LOW finding from the Phase 10 audit). No invite/accept/reject/poll logic, network
  call, or state variable changed. **Phase 12**: `STATUS_LABELS`/`STATUS_TONE` were
  moved out to `src/constants/applicationStatus.js` once `DashboardPage`'s
  status-breakdown section needed the exact same `status` → label/tone mapping;
  `ApplicationsPage.jsx` now imports them instead of defining them, with identical
  values. **Phase 13**: `AssessmentPanel` (and its two row helpers,
  `AssessmentScoreRow`/`AssessmentTextRow`, and `EVALUATION_STATUS_LABELS`/
  `EVALUATION_STATUS_TONE`) were removed from this file entirely and now live in
  `src/components/AssessmentPanel.jsx` (see below) — `ApplicationsPage.jsx` imports
  the shared component instead; nothing about how or when it's rendered
  (`{expanded && hasAssessment && <AssessmentPanel .../>}`) changed.
- `src/api/jobs.js` (`getJobs()`, `createJob()`, `getJobMatches()`).
- `src/api/candidates.js` (`getCandidateIdentity()`). **Phase 12** adds
  `getCandidates()` (`GET /v1/candidates`, used by `CandidatesPage` and
  `DashboardPage`).
- `src/api/applications.js` — `selectMatch()`, `getApplications(jobId)`,
  `invite(applicationId, language?)`, `accept(applicationId)`, `reject(applicationId)`.
  **Phase 12** adds `getAllApplications()` (`GET /v1/applications`); **Phase 13**
  adds its second real caller, `InterviewsPage`.
- `src/hooks/useCandidateIdentity.js` — module-level in-memory cache (§8, §9) plus the
  resolving hook itself; a lookup failure resolves to a distinct `"error"` status
  rather than throwing, so it can never break the page it's used on.
- `src/hooks/usePolling.js` — generic `usePolling(callback, { intervalMs, enabled })`
  (§12). Not fired immediately on mount (the owning page's own initial fetch covers
  first load); stops its timer entirely on `document.hidden` and restarts it on
  `visibilitychange`; cleans up on unmount and whenever `enabled`/`intervalMs`
  changes; swallows any error thrown by `callback` so a transient poll failure can
  never crash the page or blank out already-rendered data.
- **New in Phase 11** — `src/components/PageHeader.jsx` + `.module.css`: shared
  title/subtitle/actions header layout, generalized from the near-identical markup
  `JobsListPage`/`JobDetailPage`/`ApplicationsPage`/`ResumeUploadPage` each used to
  hand-roll. Purely presentational — takes `title`/`subtitle`/`actions` props, owns
  no state, makes no network call.
- **New in Phase 11** — `src/components/StatusBadge.jsx` + `.module.css`: shared
  status/qualification/evaluation pill (`tone` prop: `neutral`/`primary`/`accent`/
  `success`/`warning`/`danger`), consolidating what were three separate badge class
  sets (`JobDetailPage`'s qualified badge, `ApplicationsPage`'s status badge, its
  evaluation badge) into one component with identical resulting colors for every
  existing case (verified by cross-checking each tone against the token pair the
  class it replaced used — see below).
- **New in Phase 11** — `src/components/EmptyState.jsx` + `.module.css`: shared
  "nothing here yet" card (`title`/`description` props), replacing the bare
  `<p className={styles.status}>No X yet...</p>` pattern in `JobsListPage`,
  `JobDetailPage`, and `ApplicationsPage`'s empty-list branches. Loading/error text
  in all three pages is untouched (still each page's own `.status`/`.error` line) —
  `EmptyState` is only for a genuinely empty result, not a transient state.
- **New in Phase 11, now data-backed in Phase 12** — `src/components/StatTile.jsx` +
  `.module.css`: a labeled number in a card (`label`/`value`/`hint` props),
  unchanged since Phase 11 — only its caller (`DashboardPage`) now passes real
  numbers instead of `"—"`.
- **Rewritten in Phase 12** — `src/pages/DashboardPage.jsx` + `.module.css`: the `/`
  route (§5), now genuinely data-backed. On mount, fetches `getCandidates()`,
  `getJobs()`, and `getAllApplications()` in parallel (`Promise.all`, one shared
  loading/error state, same single-try/catch pattern every other page in this app
  already uses) and computes every number in render, not via a second copy of
  backend logic: `candidates.length`, `jobs.length`, an "In Pipeline" count
  (applications whose `status` isn't `"accepted"`/`"rejected"`), and an "Interviews
  Completed" count (`interview_status === "completed"`). A "Pipeline by status"
  section renders one `StatusBadge` + count per status via the shared
  `STATUS_LABELS`/`STATUS_TONE`/`STATUS_ORDER` from `constants/applicationStatus.js`
  — the exact same mapping `ApplicationsPage` uses, not a second one. A "Recent
  activity" section merges candidates' and jobs' own `created_at` timestamps
  (already-readable events — a candidate's name, a job's title), sorted newest-
  first, capped at 5, falling back to the existing `EmptyState` only if genuinely
  empty; deliberately does **not** include application-derived events
  (select/invite/decide), since those only carry an opaque `candidate_id` and would
  need an extra per-row identity lookup to be readable — judged not "cleanly
  derived from existing timestamps" per this task's own qualifier, so left out
  rather than built with an ugly UUID or an N+1 lookup. Quick actions are now
  Upload Resume / New Job (→ `/jobs`) / View Candidates (→ `/candidates`), always
  visible regardless of loading/error state. No charts anywhere — every number is a
  plain count or a plain list row.
- **New in Phase 12** — `src/pages/CandidatesPage.jsx` + `.module.css`: the new
  `/candidates` route. Fetches `getCandidates()` fresh on every mount (same
  always-refetch rule every list page in this app follows), with the same
  loading/error/empty (`EmptyState`)/populated states as `JobsListPage`. Renders
  name/email (or an italic "No email on file" when `null`)/formatted `created_at`
  in a table styled identically to `JobDetailPage`/`ApplicationsPage`'s own
  `.tableWrapper`/`.table` (no new shared `DataTable` component — not yet justified
  by real duplication, per this project's own "don't build ahead of demonstrated
  need" rule). A `PageHeader` action links to `/upload`. Rows are **not** clickable
  this pass — `/candidates/:candidateId` doesn't exist, and this project's standing
  rule is to never link to a page that isn't built (§21).
- **New in Phase 12** — `src/constants/applicationStatus.js`: `STATUS_LABELS`,
  `STATUS_TONE`, `STATUS_ORDER` for an `Application`'s `status` field, promoted out
  of `ApplicationsPage.jsx` once `DashboardPage` needed the identical mapping — see
  the `ApplicationsPage.jsx` entry above for the before/after.
- **New in Phase 13** — `src/components/AssessmentPanel.jsx` + `.module.css`:
  `ApplicationsPage`'s former private `AssessmentPanel`/`AssessmentScoreRow`/
  `AssessmentTextRow` functions and `EVALUATION_STATUS_LABELS`/
  `EVALUATION_STATUS_TONE` maps, moved here verbatim (byte-identical render output,
  confirmed by copying the CSS rules across unchanged into the new
  `.module.css` rather than rewriting them) once `InterviewsPage` needed the exact
  same interview-assessment render logic — a real second call site, not a
  speculative extraction. Takes one prop (`assessment`), renders nothing else, makes
  no network call.
- **New in Phase 13** — `src/pages/InterviewsPage.jsx` + `.module.css`: the new
  `/interviews` route. Fetches `getAllApplications()` on mount and on each
  `usePolling` tick (same always-refetch-on-mount, silent-background-poll pattern
  `ApplicationsPage` already uses), with the same loading/error/`EmptyState`/
  populated states every list page in this app has. Table columns: Candidate (via
  `useCandidateIdentity`, with the same "Loading…"/`(name unavailable)` fallbacks
  `ApplicationsPage` uses — a lookup failure resolves to a distinct `"error"` status
  and never throws, so it can never break this page either), Job (`application.job_title`
  directly — no extra fetch needed, `GlobalApplicationResponse` already carries it),
  Status (`StatusBadge` via the shared `constants/applicationStatus.js`), Interview
  (status label + an expand hint when an assessment exists, identical to
  `ApplicationsPage`'s own Interview column), Score (a compact "CEFR · overall/100"
  summary, or "Failed"/"—"; correctly treats `overall_score: 0` as a real score, not
  as absent, via a `!= null` check — verified against a real `overall_score: 0` row
  in the live data, see below), and Updated (`interview_completed_at ??
  invited_at ?? selected_at`, formatted the same way every other date in this app
  is). Row click toggles the shared `AssessmentPanel`, same `onClick`/`onKeyDown`/
  `role`/`tabIndex` pattern as `ApplicationsPage`'s own expandable row (including
  the M1 keyboard fix). Polling (`usePolling`, same `POLL_INTERVAL_MS = 7000`) is
  enabled only while `applications.some(a => a.status === "invited")`, evaluated
  across every job, not one. **Deliberately read-only**: no invite/accept/reject
  actions on this page — `ApplicationsPage` remains the one place a recruiter acts
  on an application; duplicating that mutation surface (and its `onUpdated`
  plumbing) here was judged not "clean and genuinely useful" per this task's own
  scope, since the whole point of this page is a cross-job glance, not a second
  place to do the same actions.
- **New in Phase 14** — `src/api/interviews.js`: `getInterviewReport(applicationId)`
  (`GET /v1/interviews/{application_id}/report`). `src/api/client.js` gained a
  sibling export, `requestBlob(path)` — same auth/401-handling as `request()`, but
  returns a `Blob` instead of parsing JSON, since a plain `<audio src>` can't attach
  the `Authorization` header itself; used only by `ReportDetailPage`'s recording
  playback.
- **New in Phase 14** — `src/pages/ReportsPage.jsx` + `.module.css`: the new
  `/reports` route. Built entirely from the already-fetched `GET /v1/applications`
  (no new backend call for the list itself, per this task's own scope) — filters to
  applications with a non-null `interview_assessment`, and renders candidate name
  (`useCandidateIdentity`), job title (`application.job_title`, no extra fetch),
  CEFR level, overall score, and completion date. Rows are clickable (`role="button"`,
  `onKeyDown` Enter/Space, same M1 pattern) and navigate to `/reports/:applicationId`
  — unlike `CandidatesPage`'s rows, this destination actually exists.
- **New in Phase 14** — `src/pages/ReportDetailPage.jsx` + `.module.css`: the new
  `/reports/:applicationId` route. Self-sufficient from one fetch
  (`getInterviewReport`) — a hard refresh on this URL reflects real backend state.
  Reuses `AssessmentPanel` via a small local adapter (`toAssessmentShape`) that maps
  VoiceHR's own nested `dimensions.fluency`/`.vocabulary`/etc. into the flat
  `fluency_score`/`vocabulary_score`/etc. shape `AssessmentPanel` already expects —
  the component itself needed zero changes. Also renders VoiceHR-only fields
  `AssessmentPanel` doesn't cover: strengths/concerns (each item rendered via a
  `noteText()` helper that accepts either a plain string or VoiceHR's real
  `{text, timestamp}` object — see the live-smoke-test finding below for why this
  matters), a transcript, and — when available — a recording player. The recording,
  if `report.recording.available`, is fetched as a blob (`requestBlob`) and played
  via `URL.createObjectURL`, revoked again on cleanup/unmount so it can't leak
  memory across report views. `evidence` (a third VoiceHR field, richer
  quote-level detail already covered in spirit by strengths/concerns) is
  deliberately not rendered, to avoid a needlessly dense page — a scope decision,
  not an oversight. No job title shown on this page (VoiceHR has no concept of RWS
  jobs) — that context lives on `ReportsPage`'s list instead.
- `src/styles/variables.css` — **substantially expanded in the Phase 8 visual-polish
  pass** (§15): semantic color tokens (`--color-success`/`-bg`, `--color-warning`/
  `-bg`, `--color-accent`/`-bg`, `--color-primary-bg`, `--color-hover-bg`,
  `--color-neutral-bg`), `--radius-pill`, `--shadow-sm`/`--shadow-md`, a
  `--spacing-5` step, a `--font-size-*` type scale (`xs`/`sm`/`base`/`md`/`lg`/`xl`),
  and `--transition-fast`. CSS custom properties can't be read inside a `@media`
  condition, so the two shared breakpoints (768px, 480px) are documented as a plain
  comment convention there instead, reused literally by every page's own `@media`
  rules. **Phase 11 adds two more, extending rather than replacing this system**:
  `--sidebar-width` (`220px` — the value `Layout.module.css`'s grid and
  `Sidebar.module.css` now both read, instead of `Layout.module.css` hardcoding it)
  and `--color-success-hover` (`#185f36` — the exact value `ApplicationsPage.module.css`'s
  `.acceptButton:hover` used to hardcode as a bare literal, now tokenized).
- `src/styles/global.css` — **extended this pass**: base typography (font smoothing,
  `line-height: 1.5`, a heading/paragraph margin reset that every page's own more
  specific `.title`/`.subtitle` class still overrides via normal CSS specificity), a
  default `cursor: pointer` on buttons (`not-allowed` on `:disabled`), and the shared
  `:focus-visible` treatment described in §15.
- `services/recruitment-workflow/main.py` — **extended this pass** (Phase 9, §17): a
  new final block (`Path`/`FileResponse`/`StaticFiles` imports added at the top,
  guarded SPA-serving routes appended after every existing route, including the
  `/v1/webhooks/voicehr` handler). No existing route, dependency, or business logic
  was touched — confirmed by the diff being purely additive at the end of the file.
- `services/recruitment-workflow/Dockerfile` — **rewritten this pass** as a two-stage
  build (Node stage builds `frontend/dist/`; the runtime `python:3.12-slim` stage
  copies it in and deletes the frontend source tree that the blanket service-directory
  `COPY` would otherwise have carried along — see §17 for exactly why that extra `RUN
  rm -rf` is necessary).
- `.dockerignore` (repo root) — **new this pass**: excludes
  `**/frontend/node_modules/`/`**/frontend/dist/` plus standard Python/VCS hygiene from
  the Docker build context shared by `recruitment-workflow`/`resume-intelligence`/
  `second-brain` (§17).
- **New in Phase 15** — `src/api/candidates.js` gained `getCandidateKnowledge(candidateId)`
  (`GET /v1/candidates/{candidate_id}/knowledge`), used only by `CandidateDetailPage`.
- **New in Phase 15** — `src/pages/CandidateDetailPage.jsx` + `.module.css`: the new
  `/candidates/:candidateId` route, the cross-linking hub every other list page's
  candidate name now points to. Three independent reads: identity reuses the
  existing `useCandidateIdentity` hook as-is (its own cache + failure handling, not
  re-implemented — "avoid duplicate API logic"); professional knowledge
  (skills/job titles/years of experience, rendered as chips) is its own fetch with
  its own loading/error state; this candidate's applications are derived by
  filtering the already-fetched `GET /v1/applications` client-side by
  `candidate_id` (no new backend endpoint for this — the global list already
  carries it). Each application row links its job title to `/jobs/:jobId` and, when
  `interview_assessment != null`, a "View Report" link to `/reports/:applicationId`
  — "Candidate applications → corresponding Job/Report where applicable," per this
  task's own wording; a working global VoiceHR interview list already exists
  (`/interviews`) so a third, redundant per-row deep link there was judged
  unnecessary. A failure in any one of the three sections never blanks the other
  two (each has independent loading/error state). Reuses `PageHeader`,
  `StatusBadge`, `EmptyState`, and the shared `constants/applicationStatus.js` —
  no new shared component was needed. A "Knowledge Graph" section is reserved as a
  labeled `EmptyState` placeholder — deliberately not the graph itself, per this
  task's own explicit instruction.
- **Cross-linking wired everywhere a candidate name appears**: `CandidatesPage`'s
  rows now navigate to `/candidates/:candidateId` (same click/keyboard pattern as
  `ReportsPage`'s rows). `JobDetailPage`'s ranked-candidate name,
  `ApplicationsPage`'s and `InterviewsPage`'s candidate names, and `ReportsPage`'s
  candidate names are each now a `<Link>` with `onClick={(e) => e.stopPropagation()}`
  so clicking the name navigates to the candidate without also triggering the
  row's own click behavior (expand-toggle on the first three, navigate-to-report
  on `ReportsPage`) — the exact same "stop the row's own handler from also firing"
  pattern this app already used for the Select/Invite/Accept/Reject button cells.
  `ReportDetailPage`'s header is the one special case: VoiceHR's report has no
  concept of RWS's `candidate_id` at all, so the page does a small best-effort
  lookup against the already-fetched-elsewhere `GET /v1/applications` to resolve
  it — if that lookup fails or doesn't find a match, the candidate's name in the
  header just stays plain text instead of a link; the report itself is never
  blocked by this.
- **Not yet built**: nothing from the admin-dashboard redesign proposal's original
  nav, and no dangling candidate-name link remains anywhere in the app. The
  reserved "Knowledge Graph" section on `CandidateDetailPage` is intentionally
  just a placeholder — the graph itself is explicitly out of scope, per this
  task's own instruction.

**Build verified**: `npm install` (66 packages, 0 errors) and `npm run build` both
succeeded after the visual-polish pass — still 63 modules, JS bundle size unchanged
(193.45 kB, same as the prior pass) though its output filename hash changed (Vite
embeds a reference to the CSS asset's own hash inside the JS entry, so a CSS-only
change still produces a new JS filename — this is expected and not evidence of a
behavior change). The actual guarantee against behavior change is `git status`
confirming zero `.jsx` files were touched this pass — every edit was to a
`.module.css` file or to `variables.css`/`global.css`. Only the CSS bundle grew,
16.55 kB → 21.80 kB, from the added rules. Built cleanly in ~0.8s, `dist/` produced,
0 errors/warnings.

**Verified — Candidate detail + cross-linking (Phase 15, this pass)**, against the
real running stack (`csb-recruitment-workflow` rebuilt with both the backend
endpoint and the frontend changes together, recreated and healthy):

- **Real candidate data, network-contract level**: logged in as the real seeded
  recruiter and called `GET /v1/candidates/{id}/knowledge` for a real candidate
  (`1cca02e3-...`, "Amine Sridi") — real `200` with real data (`skills: ["Python",
  "FastAPI", "PostgreSQL", "Docker"]`, `job_titles: ["Backend Engineer"]`,
  `years_of_experience: 3.9`) — the exact same skills that drove this candidate's
  real deterministic score in an earlier session's job-matching smoke test, now
  independently reachable through this new endpoint. An unknown candidate UUID
  returned a real `404`; no auth returned a real `401`.
- **Cross-linked data reconciled by hand against the real API responses**: for
  that same candidate, `GET /v1/applications` filtered to their one real
  application (`92a11e52-...`, job `"Report Smoke Test Engineer"`, status
  `interviewed`, a real `interview_assessment`) — confirmed this matches exactly
  what `CandidateDetailPage`'s own filter/render logic would produce: one
  application row, linking to the real job (`/jobs/{job_id}`, confirmed `200` on
  hard refresh) and a real "View Report" link (`/reports/{application_id}`,
  confirmed `200` on hard refresh, and confirmed the report's own best-effort
  candidate lookup resolves back to the same `candidate_id`).
- **Hard-refresh / SPA-routing verified against the production port**:
  `GET /candidates/{candidateId}` returned a real `200` serving the rebuilt
  `index.html` with the new build's hashed asset filenames.
- **No direct calls to other services, confirmed at the shipped-artifact level**:
  grepped the built JS bundle inside the rebuilt container for Resume
  Intelligence/Second Brain/VoiceHR hostnames and every other service's mapped
  port — zero matches, same result as every prior phase's own check. Also
  confirmed no candidate skill/experience data or exception text appeared in
  `docker logs csb-recruitment-workflow` for any of these requests.
- **Zero orphaned or missing CSS classes**: every touched or new page
  (`CandidatesPage`, `JobDetailPage`, `ApplicationsPage`, `InterviewsPage`,
  `ReportsPage`, `ReportDetailPage`, `CandidateDetailPage`) cross-checked in both
  directions against its own `.module.css` — zero mismatches.
- **Tests**: 8 new tests in `tests/test_candidate_knowledge.py` (auth boundary,
  successful lookup, `raw_text`/`source_version`/embedding confirmed absent even
  when the mocked payload carries them, `years_of_experience: null` handled
  correctly, 404 for an unknown candidate, 502-without-leaking-exception-text, and
  the no-`db`-dependency structural proof). RWS 61/61 passed both on host and
  inside the rebuilt container (53 + 8 new). Full detail is in `handoff.md`'s own
  entry for this session (the backend endpoint is cross-service work, tracked
  there per §23).
- **Build**: `npm run build` — **87 modules transformed** (up from 85 —
  `CandidateDetailPage.jsx`/`.module.css`), 0 errors/warnings, JS bundle 211.78 kB
  → 216.84 kB, CSS bundle 31.29 kB → 34.17 kB. `git status` confirms the frontend
  changes are confined to `services/recruitment-workflow/frontend/src/`, alongside
  the expected backend files (`main.py`, `schemas.py`) and their tests — no DB
  migration, no new auth mechanism, no unrelated file touched.

**Verified — VoiceHR Reports backend proxy + Reports pages (Phase 14, this pass)**,
against the real running stack (both `csb-voicehr` and `csb-recruitment-workflow`
rebuilt/recreated; `recruitment-workflow` rebuilt a second time after a real bug fix
found during this pass's own live smoke test — see below):

- **A real, live-discovered frontend bug, fixed before the final rebuild, not
  glossed over**: `ReportDetailPage`'s first draft assumed `report.strengths`/
  `report.concerns` were arrays of plain strings; a real Gemini-generated report
  produced during this pass's own smoke test showed VoiceHR's actual evaluator
  output shapes each item as `{"text": ..., "timestamp": ...}` — rendering that
  object directly would have thrown React's "Objects are not valid as a React
  child" error. Fixed with a small `noteText()` helper that accepts either shape
  (a plain string or `{text}`), verified against the real response, not a
  synthetic fixture guessed at ahead of time.
- **A real, live-discovered environment characteristic, not a code defect**:
  every previously-completed interview in the live dataset returned `404` from
  the new report endpoint, because VoiceHR's SQLite database does not survive a
  container recreate (`DB_PATH` lives outside the persisted `voicehr_data`
  volume — see `handoff.md`'s Known Limitations for the full detail). Worked
  around by driving a **real, fresh** interview through the actual pipeline for
  this pass's own verification: a real job submission (`POST /v1/jobs`, real
  Gemini JD structuring), a real selection and invite of a candidate with a real
  Resume Intelligence contact record, then a real call to
  `backend.services.processing.process_interview` **directly inside the rebuilt
  `csb-voicehr` container** (the same function `session.py`'s WebSocket handler
  calls, with a hand-written transcript standing in only for real browser
  audio/ASR — this project's established smoke-test discipline throughout).
  Produced a real Gemini evaluation (`overall_score: 83, cefr_level: "C1"`) and a
  real webhook delivery to RWS (confirmed via `docker logs
  csb-recruitment-workflow`, `outcome: "processed"`).
- **Full report/recording proxy chain verified against that real data**: `GET
  /v1/interviews/{application_id}/report` returned the complete real report
  (scores, dimensions, strengths, concerns, transcript) with `recording:
  {"available": false, "url": null}` (no recording existed yet) and zero VoiceHR
  hostname/port anywhere in the response (grepped directly). A fake recording
  file was then added inside the `csb-voicehr` container and linked to the same
  interview to exercise the other branch: `recording.available` flipped to
  `true` with a real same-origin RWS URL, and fetching that URL through RWS
  returned the exact real bytes with the correct `audio/webm` content-type —
  confirming both branches of the proxy, not just the empty-case default.
- **Real dashboard-adjacent data confirmed consistent**: the same freshly-created
  application appears in `GET /v1/applications` with a non-null
  `interview_assessment`, so `ReportsPage`'s filter (`interview_assessment !=
  null`) picks it up correctly, with the exact CEFR/overall-score/completion-date
  values matching the detail page's own fetch.
- **Hard-refresh / SPA-routing verified against the production port**: `GET
  /reports` and `GET /reports/{applicationId}` both returned real `200`s serving
  the rebuilt `index.html`, both before and after adding the `Sidebar` link (two
  separate rebuild/recreate cycles, same discipline as Phase 13's own "add to
  sidebar only after the page works" verification).
- **No direct calls to other services, confirmed at the shipped-artifact level**:
  grepped the built JS bundle inside the rebuilt container for Resume
  Intelligence/Second Brain/VoiceHR hostnames and every other service's mapped
  port — zero matches, same result as every prior phase's own check.
- **Zero orphaned or missing CSS classes**: `ReportsPage`/`ReportDetailPage`
  cross-checked in both directions against their own `.module.css` files — zero
  mismatches.
- **Tests**: VoiceHR 140/140 passed on host (129 + 11 new — auth boundary for both
  new routes including the admin-Basic-auth-doesn't-substitute case, 404s for an
  unknown/reportless interview, the recording path/URL stripped from the
  RWS-facing report response, a real bytes-served recording test, and a
  path-traversal-safety case). RWS 53/53 passed both on host and inside the
  rebuilt container (41 + 12 new — the same auth/shape/error/no-persistence
  coverage on the RWS side of both new routes). Full detail and the exact test
  names are in `handoff.md`'s own entry for this session (cross-service backend
  work is tracked there per §23).
- **Build**: `npm run build` — **85 modules transformed** (up from 80 —
  `ReportsPage.jsx`/`.module.css`, `ReportDetailPage.jsx`/`.module.css`,
  `api/interviews.js`), 0 errors/warnings, JS bundle 204.47 kB → 211.78 kB, CSS
  bundle 27.90 kB → 31.29 kB. `git status` confirms the frontend changes are
  confined to `services/recruitment-workflow/frontend/src/`, alongside the
  expected backend files (`services/voicehr/backend/api/routes/report.py`,
  `services/recruitment-workflow/main.py`/`voicehr_client.py`) and their tests —
  no DB migration, no new auth mechanism, no unrelated file touched.

**Verified — Global VoiceHR interviews page (Phase 13, this pass)**, against the
real running stack (Docker rebuilt/recreated for `recruitment-workflow` twice — once
before adding the `Sidebar` link, per this task's own "add VoiceHR to sidebar only
after the page works" instruction, and once after — real Postgres, real seeded
recruiter login), no code changed during verification itself:

- **Real interview data, network-contract level**: logged in as the real seeded
  recruiter through production port `8003` and called `GET /v1/applications` — the
  same 5 real applications Phase 12 already exercised. Manually recomputed every
  derived value `InterviewsPage`/`InterviewRow` would produce from this exact
  response and confirmed by hand: `hasInvitedApplication` is `false` (no
  `"invited"` row currently exists in this dataset, so polling would correctly stay
  off), the two `interview_status: "completed"` rows produce score summaries
  `"A2 · 0/100"` and `"C1 · 93/100"`, and the one `interview_status: "failed"` row
  produces `"Failed"`. The `"A2 · 0/100"` row is the load-bearing edge case: its
  real `overall_score` is the integer `0`, independently re-confirmed via a direct
  UTF-8-safe read of the raw JSON response (`repr()` on the parsed value, not a
  terminal print, since a literal `—`/`·` character mis-renders in a Windows
  console — a verification-tooling quirk, not a data or code issue) — proving
  `formatScoreSummary`'s `!= null` check (not a truthiness check, which would have
  wrongly treated `0` as absent) is exercised by a real row, not just reasoned
  about.
- **Job titles resolved with zero extra fetches**: every row's `application.job_title`
  came directly off the already-fetched `GlobalApplicationResponse` — confirmed no
  new network call was added anywhere for this (the only fetch `InterviewsPage`
  makes is `getAllApplications()` itself).
- **Hard-refresh / SPA-routing verified against the production port, not the dev
  proxy**: `GET /interviews` returned a real `200` serving the rebuilt `index.html`
  with the new build's hashed asset filenames, both before and after the `Sidebar`
  change — same guarantee every prior phase's production-serving check has
  confirmed for its own new route.
- **No direct calls to other services, confirmed at the shipped-artifact level**:
  grepped the actual built JS bundle inside the rebuilt container for Resume
  Intelligence/Second Brain/VoiceHR hostnames and every other service's mapped port
  — zero matches, same result as Phase 10's audit and Phase 12's re-check.
- **Zero orphaned or missing CSS classes**: `InterviewsPage.jsx`/`.module.css` and
  `AssessmentPanel.jsx`/`.module.css` cross-checked in both directions — zero
  mismatches; `ApplicationsPage.jsx`/`.module.css` re-checked after the extraction
  and confirmed still zero mismatches (no orphaned assessment-related CSS left
  behind, no missing class after the `AssessmentPanel` import replaced the local
  functions).
- **Not yet tested, no browser available in this environment** (same limitation as
  every prior pass — see §18): the actual rendered table/row-expand/polling-restart
  behavior in a live tab — verified instead by the CSS/JSX cross-check above plus
  direct code review of the render paths against the real response data captured
  above, the same standard this project has applied throughout.
- **Build**: `npm run build` — **80 modules transformed** (up from 76 —
  `InterviewsPage.jsx`/`.module.css` and `AssessmentPanel.jsx`/`.module.css`), 0
  errors/warnings, JS bundle 200.62 kB → 204.47 kB (includes the `Sidebar` icon
  addition), CSS bundle 26.29 kB → 27.90 kB. `git status` confirms every changed/
  new file is under `services/recruitment-workflow/frontend/src/` — no backend
  file touched (no new endpoint was needed; `GET /v1/applications` already existed).

**Verified — Candidates page + real Dashboard data (Phase 12, this pass)**, against
the real running stack (Docker rebuilt/recreated for `recruitment-workflow`, real
Postgres, real seeded recruiter login), no code changed during verification itself:

- **Real candidate data, network-contract level**: logged in as the real seeded
  recruiter through production port `8003` and called `GET /v1/candidates` with the
  resulting JWT — real `200` with all 6 real candidates in the database, newest-
  first (matches `CandidatesPage`'s own unmodified rendering of whatever order the
  backend returns). `GET /v1/jobs` → real `200`, 2 real jobs. `GET /v1/applications`
  → real `200`, 5 real applications with a real status breakdown
  (`{accepted: 2, rejected: 2, selected: 1}`) and 2 with `interview_status:
  "completed"` — manually recomputed `DashboardPage`'s own derived numbers against
  this real response and confirmed by hand: In Pipeline `1` (only the one
  `"selected"` row is non-terminal), Interviews Completed `2`, both matching what
  the page's own filter/count expressions would produce from this exact data.
- **Hard-refresh / SPA-routing verified against the production port, not the dev
  proxy**: `GET /` and `GET /candidates` both returned real `200`s serving the
  rebuilt `index.html` (confirmed via the new build's hashed asset filenames
  actually present in the response) — the same guarantee Phase 9's production-
  serving work exists to provide, now re-confirmed for the new route.
- **No direct calls to other services, confirmed at the shipped-artifact level**:
  grepped the actual built JS bundle inside the rebuilt container for Resume
  Intelligence/Second Brain/VoiceHR hostnames and every other service's mapped port
  — zero matches, same result as Phase 10's audit. Also grepped the new source
  files themselves (`CandidatesPage.jsx`, `DashboardPage.jsx`, `api/candidates.js`,
  `api/applications.js`, `constants/applicationStatus.js`) for any absolute
  URL/hardcoded port — none found; every new fetch goes through the existing
  `api/client.js` wrapper with a relative `/v1/...` path, same as every other page.
- **Not yet tested, no browser available in this environment** (same limitation as
  every prior pass — see §18): the actual rendered table/stat-tile/status-breakdown/
  activity-list layout, the two-column-to-one-column responsive collapse at 768px,
  and hover/focus states — verified instead by the CSS/JSX class cross-check below
  plus direct code review of the render paths against the real response shapes
  captured above.
- **Zero orphaned or missing CSS classes**: every new/changed page and component's
  `.jsx` was cross-checked against its own `.module.css` in both directions (same
  method as Phase 11's verification) — zero mismatches on `CandidatesPage` or
  `DashboardPage`.
- **Build**: `npm run build` — **76 modules transformed** (up from 73 —
  `CandidatesPage.jsx`/`.module.css` and `constants/applicationStatus.js`), 0
  errors/warnings, JS bundle 196.46 kB → 200.62 kB, CSS bundle 23.24 kB → 26.29 kB
  (the new page's table/status/activity styles). `git status` confirms every
  changed/new file is under `services/recruitment-workflow/frontend/src/` — no
  backend, migration, or Docker config file touched this pass (the Docker rebuild
  above used the *existing* Dockerfile/compose config unchanged, only picking up
  the new frontend source).

**Verified — Admin shell redesign (Phase 11, this pass)**: new modern-SaaS shell
(Sidebar/TopBar/Layout redesign), four new reusable presentational components
(`PageHeader`/`StatusBadge`/`EmptyState`/`StatTile`), a visual-only `DashboardPage`,
and the existing Jobs/Upload/Job-Detail/Applications pages restyled to use the four
new components where their markup already matched. No backend, API, auth, or
workflow-state code touched — confirmed by `git status --porcelain` at the repo root
showing changes confined to `services/recruitment-workflow/frontend/src/**`, entirely
`.jsx`/`.module.css` files, nothing elsewhere:
- `npm run build`: clean both before and after every edit in this pass — final run:
  **73 modules transformed** (up from 63 — the 10 new component/page files), 0
  errors/warnings, JS bundle 193.64 kB → 196.46 kB (the new components + Dashboard +
  hand-authored inline SVG icons), CSS bundle 21.80 kB → 23.24 kB (new component
  styles). Both bundle-size increases are additive, not a sign of removed code
  regressing — confirmed by the class-usage cross-checks below.
- **No orphaned or missing CSS classes**: every page's and every new component's
  `.jsx` was cross-checked against its own `.module.css` in both directions (every
  `styles.X` reference resolves to a real class; every class defined is actually
  referenced) — zero mismatches. `StatusBadge`'s six tone classes are read via
  dynamic `styles[resolvedTone]` rather than dot-notation, so a naive grep for
  `styles.success`/etc. undercounts its real usage — checked by hand against
  `StatusBadge.jsx`'s actual `TONES` set instead of trusting the grep alone.
- **StatusBadge color-parity check**: each new `tone` was verified to resolve to the
  exact same `background`/`color` token pair the CSS class it replaced used —
  `badgeQualified`→`success`, `badgeNotQualified`→`neutral`, `statusSelected`→
  `neutral`, `statusInvited`→`primary`, `statusInterviewed`→`accent`,
  `statusAccepted`→`success`, `statusRejected`→`danger`, `evaluationCompleted`→
  `success`, `evaluationInconclusive`→`warning`, `evaluationFailed`→`danger`. Zero
  visual regression by construction, not just by eyeballing.
- **Behavior-preservation guarantee**: every `onClick`/`onKeyDown`/`useState`/
  `useEffect`/API call in `JobsListPage`/`JobDetailPage`/`ApplicationsPage`/
  `ResumeUploadPage` is byte-for-byte unchanged — the diff in each file is
  import-list additions plus markup swaps (raw `<h1>`/`<p>`/`<span>` → the new
  components), never a changed condition, handler, or state shape. The one
  genuinely new piece of interactive markup is `DashboardPage`'s two `Link`s, both
  to already-existing, already-working routes.
- **Not yet tested, no browser available in this environment** (same limitation as
  every prior pass — see §18): the actual rendered shell (sidebar active-state
  styling, icon rendering, `DashboardPage`'s layout at various widths). Verified
  instead by the class-usage cross-checks above plus direct code review of the
  final CSS against each component's DOM structure.
- **One pre-existing, unrelated accessibility gap noticed while reading
  `ResumeUploadPage.jsx` for this pass, not fixed** (out of scope — Phase 1 of the
  redesign is shell/components only): the drop-zone `<div role="button"
  tabIndex={0}>` has an `onClick` but no `onKeyDown`, so Enter/Space don't open the
  file picker via keyboard — the same category of gap Phase 10's M1 finding fixed
  in `JobDetailPage`/`ApplicationsPage`, just not in this file. Recorded in §21
  rather than fixed, since this task's scope was explicitly visual shell/components,
  not a second accessibility pass.

**Verified — visual polish + responsive states (Phase 8 pass)**, no backend involved so
verification is build- and code-level rather than a live network smoke test (no
network call was added, changed, or removed this pass):
- **Zero behavior change, structurally guaranteed**: `git status` after the pass shows
  only `.module.css`/`variables.css`/`global.css` files touched — no `.jsx` file in
  `src/` was modified. Since CSS Modules only ever change what an existing class name
  *looks like*, never what triggers a state change or a network call, this is a
  stronger guarantee than eyeballing a diff for behavioral changes.
- **No dropped or renamed classes**: for every page/component `.jsx` file, every
  `styles.xxx` (and the two dynamic `styles[STATUS_STYLES[...]]`/
  `styles[EVALUATION_STATUS_STYLES[...]]` lookups in `ApplicationsPage.jsx`) was
  cross-checked against its `.module.css`'s actual class list — confirmed present in
  every case. A rewritten CSS file that silently dropped a class still referenced by
  JSX would compile fine (Vite doesn't fail on an unresolved CSS Module property, it's
  just `undefined`) and only show up as a visual bug, so this was checked explicitly
  rather than assumed from a clean build.
- **No undefined CSS custom properties**: every `var(--...)` reference across
  `styles/`, `pages/`, and `components/` was cross-checked against the tokens actually
  defined in `variables.css` — zero mismatches. An undefined custom property silently
  falls back to nothing (no visible error), so this was checked explicitly too.
- **Status/evaluation color distinctness**: `selected` (neutral gray), `invited`
  (primary blue), `interviewed` (accent purple), `accepted` (success green), `rejected`
  (danger red) each resolve to a visually distinct hue via the new shared semantic
  tokens — confirmed by reading the final token values, not just assumed from naming.
- **No overflow at laptop widths**: every page's own `max-width` (560–960px) sits
  comfortably inside any laptop/projector viewport (1280px+); the two table
  `min-width`s added this pass (620px/640px) are both well under every page's own
  `max-width`, so they change nothing at those widths — they only start to matter
  below ~640–680px, where `.tableWrapper`'s pre-existing `overflow-x: auto` takes over
  and the table scrolls horizontally instead of squeezing columns illegibly.
- **Responsive behavior added**: sidebar collapses to a horizontal top bar below
  768px (was already collapsing the grid to one column; now the nav itself adapts
  too); page headers, action buttons/links, and form fields wrap or stack at 768px/
  480px across `JobsListPage`/`JobDetailPage`/`ApplicationsPage`/`ResumeUploadPage`;
  the login card becomes fluid-width with a safe minimum padding instead of a fixed
  320px.
- Not yet tested, no browser available in this environment (same limitation as every
  prior pass): the actual rendered result at any specific viewport width, hover/focus
  states firing correctly, and the sidebar's horizontal-scroll behavior on a genuinely
  narrow phone width — all verified by direct code review of the final CSS against the
  DOM structure each page's `.jsx` already produces (unchanged this pass), not by
  loading the app in a browser and resizing it.

**Live-tested — Production Docker build + FastAPI serving (prior pass)**, against the
real Docker Compose stack — a rebuilt image, a real container recreate, and real HTTP
requests against the production port (`8003`), not the Vite dev proxy:
- **Backend regression, both with and without `frontend/dist/` present**: host-side
  `python -c "import main"` and the full `pytest tests/` suite (27 tests) were run
  twice — once with the local `frontend/dist/` in place (21 routes registered,
  matching the SPA block's 3 extra routes over baseline) and once with it renamed away
  to simulate a fresh checkout (18 routes, SPA block absent, `frontend_dist_not_found`
  logged) — **27/27 passed both times**, confirming the guard in §17 genuinely
  protects the existing test/dev workflow rather than just being asserted to.
- **`docker compose build recruitment-workflow`**: real multi-stage build — the
  `frontend-build` stage ran `npm ci` (66 packages) and `npm run build` (63 modules,
  identical output to the host build) *inside the image build itself*, then the
  runtime stage copied only `frontend/dist/` in. Also rebuilt `resume-intelligence`
  and `second-brain` (both share the new root `.dockerignore`'s build context) to
  confirm the new root-level file doesn't break their builds — both built successfully.
- **Container recreated and healthy**: `docker compose up -d --no-deps --force-recreate
  recruitment-workflow` → healthy within 10s.
- **No Node in the runtime container**: `which node`/`which npm` inside the running
  container both exit `1` (not found) — a real check inside the actual container, not
  an assumption from the Dockerfile's shape.
- **`frontend/dist/` layout inside the container**: confirmed via `docker exec` —
  `index.html` + `assets/` containing exactly the two hashed JS/CSS files the local
  build produced, nothing else (no `src/`, no `node_modules/`, no `package.json`).
- **`GET /` serves the real built `index.html`** (`200`, contains the actual
  `<script type="module" src="/assets/index-HASH.js">`/`<link ... href="/assets/
  index-HASH.css">` tags matching the files really present in `/assets/`).
- **Client-side routes resolve on a direct/hard-refresh request, not just client-side
  navigation**: `GET /jobs`, `GET /jobs/abc-123/applications`, and `GET /upload` all
  returned real `200`s serving the same `index.html` — exactly what lets
  `BrowserRouter` take over and render the right page after a hard refresh on a nested
  URL, the specific behavior this whole plan exists to guarantee.
- **`/v1/*` and `/health` are completely unshadowed**: `GET /health` returned the real
  `{"status": "ok", "service": "recruitment-workflow"}` JSON (not HTML); `POST
  /v1/auth/login` with wrong credentials returned a real `401` with the real backend
  `detail` (not swallowed by the catch-all); `GET /v1/jobs` with no `Authorization`
  header returned a real `401` JSON body. Then, for real: logged in with the seeded
  recruiter's actual credentials through port `8003` (not the dev proxy) and called
  `GET /v1/jobs` with the resulting JWT — real `200` with the real jobs list. This is
  the same recruiter flow the frontend itself makes, now proven to work end to end
  through the exact production port/origin a demo would actually use.
- **Static assets serve correctly**: both `/assets/index-HASH.js` and
  `/assets/index-HASH.css` returned real `200`s with correct `Content-Type` headers
  (`text/javascript`, `text/css`).
- Not yet tested, no browser available in this environment (same limitation as every
  prior pass): actually loading `http://localhost:8003/` in a real browser and
  clicking through it. Every request the browser would make was issued directly via
  `curl` instead and its response verified, which covers the network-contract level
  this project has consistently used as its verification bar when no browser is
  available — see §18.

**Final frontend E2E + demo-readiness audit (Phase 10, prior pass)** — a full live
walk of the real recruiter flow against the production-served app (login → upload →
jobs → create job → ranked candidates → identity → explanation → select →
applications → invite/resend → poll → VoiceHR assessment → accept/reject → refresh at
every page), through the real production port, no dev proxy, no code changed during
the audit itself:
- **Every documented error status triggered for real this pass**: `401` (no/bad auth,
  multiple endpoints), `404` (unknown job, unknown candidate), `409` (duplicate
  select, duplicate accept, invite/accept on a terminal application), `422`
  (unsupported upload type, inviting a candidate with no Resume Intelligence contact),
  `502` (both resume upload and job creation, under a real, currently-exhausted Gemini
  free-tier **daily** quota — `generativelanguage.googleapis.com/
  generate_content_free_tier_requests`, limit 20/day, confirmed via RWS's and Resume
  Intelligence's own logs, not assumed) — every one rendered the backend's real
  `detail` verbatim, exactly as §14 documents.
- **A real cascading external failure, handled cleanly end to end**: with Gemini's
  quota exhausted, a real VoiceHR interview-completion call also failed its own Gemini
  evaluation and gracefully degraded to a real `evaluation_status: "failed"` fixture —
  `AssessmentPanel`'s dedicated `"failed"` branch is now confirmed against genuinely
  real data, not just code review. Zero unhandled exceptions or `500`s reached the
  client anywhere in the audit, despite this real failure cascade.
- **Security invariants confirmed at the shipped-artifact level, not just source**:
  grepped the actual built JS bundle inside the running container directly — zero
  references to Resume Intelligence/Second Brain/VoiceHR hostnames or ports, and the
  only "SECRET"-like string found is React's own internal
  `__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED` marker, not a real key. Grepped
  RWS's own logs across the whole audit session for candidate names/emails/phone
  numbers — zero matches.
- **Terminal-state and polling-gate logic reconfirmed against fresh, real data**:
  a brand-new application was selected, invited, resent, driven to `"interviewed"` via
  a real webhook, and accepted, live during the audit — `hasInvitedApplication`
  verified `true` while it was `"invited"` and `false` immediately after, and no
  action button reappeared once it reached `"accepted"`.
- **One real, live-discovered data-integrity edge case** (not a frontend defect): a
  candidate present in Second Brain's scoring index with no matching Resume
  Intelligence contact record — `GET /v1/candidates/{id}/identity` correctly `404`s,
  and inviting that candidate correctly `422`s ("Candidate contact information not
  found") — both already-correct, already-existing frontend behaviors, just now
  confirmed against a real instance of the condition rather than a hypothetical one.
  This is a test-data hygiene note, not something fixed in code.
- **One real code finding (M1), fixed this pass** (see immediately below):
  `CandidateRow` (`JobDetailPage.jsx`) and `ApplicationRow` (`ApplicationsPage.jsx`)
  had `role="button" tabIndex={0}` expandable rows with no `onKeyDown` handler —
  focusable via keyboard but Enter/Space did nothing, since a `<tr>` doesn't natively
  map those keys to a click the way a real `<button>` does.
- No other code finding rose above LOW severity, and every LOW item (no favicon, one
  un-tokenized hover-shade literal, no explicit text-truncation on table-cell names)
  was judged cosmetic-only and left as-is, not fixed.

**M1 fix — keyboard support for expandable rows (this pass)**: both `CandidateRow` and
`ApplicationRow` now handle `onKeyDown` alongside the existing `onClick` — Enter or
Space toggles `expanded`, with `event.preventDefault()` called only for Space (to stop
the page from scrolling on that key); Enter needs no `preventDefault` since these rows
are never inside a `<form>`. `ApplicationsPage.jsx`'s version stays conditional on
`hasAssessment`, exactly like its existing `onClick`/`role`/`tabIndex` — a row with no
assessment gets `onKeyDown={undefined}` and stays inert, unchanged from before this
fix. **Zero other lines changed** in either file — no API call, state shape, styling,
polling, auth, or workflow logic was touched; `git diff` on both files shows only the
`onKeyDown` prop addition. Build verified: `npm run build` — 63 modules, 0
errors/warnings, JS bundle 193.45 kB → 193.64 kB (the two small added handlers), CSS
bundle unchanged (0 bytes different, confirming no styling was touched). Mouse-click
behavior verified unchanged by inspection (`onClick` handlers themselves are untouched
character-for-character); Enter/Space behavior and non-expandable-row inertness
verified by direct code review of the final logic, no browser being available in this
environment — the same standard applied throughout this project when a browser isn't
available.

**Live-tested — login (prior pass)**: `POST /v1/auth/login` through the Vite dev proxy
against the real running RWS container — valid credentials returned a real JWT (`200`);
invalid password returned the real backend `401` with `{"detail": "Invalid email or
password"}`.

**Live-tested — Jobs experience (prior pass)**, through the exact dev-proxy path
(`http://localhost:5173` → real `csb-recruitment-workflow` container → real Resume
Intelligence / Second Brain / Gemini / Postgres), issuing the same requests the app's
own `api/*.js` modules issue:
- `GET /v1/jobs`: unauthenticated → real `401`; authenticated → real `200` with the
  live seeded job (`"Backend Engineer"`, `match_count: 5`); after creating a second job
  (below), the list returned both, **newest-first**, with correct `match_count`s (`7`
  then `5`) — confirms ordering and the aggregate count are both real, not assumed.
- `POST /v1/jobs`: a real JD ("Senior Backend Engineer… Required skills: Python,
  PostgreSQL, FastAPI, Docker… Preferred: Kubernetes, Redis") submitted end to end. The
  first attempt hit a real transient Gemini `503` ("high demand") and correctly
  surfaced as RWS's own `502` — the same honest, undoctored external condition
  documented for resume upload; production model config was not touched. A retry of
  the identical request succeeded: real `200` with 7 real persisted `MatchResult` rows,
  correctly-structured requirements (`required_skills`, `preferred_skills`,
  `min_years_experience: 5.0`, `titles`), scored and sorted by RWS itself.
- `GET /v1/jobs/{job_id}/matches`: real `200` with 5 real `MatchResult` rows for the
  pre-existing job, correctly sorted by `final_score` descending, `qualified: true` on
  all 5, `matched_requirements`/`missing_requirements` populated. `explanation` came
  back `null` on every row in this run — RWS's lazy per-match Gemini explanation calls
  hit the same live `503` "high demand" condition (RWS's own `match_explanation_
  generation_failed` warning logged per match, matching `main.py: get_job_matches`'s
  documented behavior of never failing the endpoint over a missing explanation). This
  confirms the frontend's "AI explanation" block correctly renders nothing rather than
  breaking when `explanation` is `null` (verified by code review of the render
  condition, since no browser is available in this environment — see the note below).
  A second attempt against the freshly-created 7-match job (a cold job, meaning more
  sequential lazy Gemini calls than the already-cached 5-match job) exceeded a 200s
  client-side wait during the same live Gemini slow period and was abandoned
  client-side; RWS's own request/health logging showed the service itself stayed
  healthy throughout and continued serving other requests normally (`GET /v1/jobs`,
  identity lookups) while that one request was still being processed server-side. This
  is the same well-documented, external, honestly-reported Gemini free-tier flakiness
  as every prior pass in this project — not a defect in the endpoint (already proven
  correct by the successful 5-match run) or in the frontend code that calls it, and no
  workaround was applied.
- `GET /v1/candidates/{candidate_id}/identity`: real `200` with a real name/email
  (`"Smoketest Candidate"` / `smoketest.rws.notify@example.com`) for a candidate_id
  taken directly from a live match row — the exact call `useCandidateIdentity` makes.
- `POST /v1/matches/{match_id}/select`: real `201` (`status: "selected"`) on first
  selection of a qualified match; the identical request repeated returned a real `409`
  ("This candidate has already been selected for this job") — confirms the frontend's
  409-as-already-selected handling in `CandidateRow` reacts to genuine backend state,
  not an invented case; selecting a real **unqualified** match returned a real `422`
  ("Cannot select a candidate that did not qualify"), confirming the Select button's
  `disabled={!match.qualified}` is backed by the real server-side rule it mirrors, not
  a substitute for it; an unauthenticated select attempt returned a real `401`.
- Not yet tested: the rendered UI in an actual browser (no browser available in this
  environment, same limitation as every prior frontend pass) — verification is at the
  network-contract level (the exact requests/response shapes the app's own code issues
  and consumes, through the exact dev-proxy path it uses) plus a clean production
  build and direct code review of the render paths (expand/collapse, score-bar width,
  chip rendering, disabled-button states, the "Selected ✓"/"(name unavailable)" inline
  states) against the real response shapes captured above.

**Live-tested — Applications + invitation workflow (prior pass)**, through the exact
dev-proxy path, issuing the same requests `ApplicationsPage`/`api/applications.js`
issue, against real applications already present from prior live-testing (one
`"selected"` application from the Jobs-experience pass, and one pre-existing
`"interviewed"` application — with a real `interview_assessment` — from an even
earlier end-to-end pass):
- `GET /v1/jobs/{job_id}/applications`: unauthenticated → real `401`; authenticated →
  real `200` with both real applications, exactly the shape `ApplicationRow` consumes
  (`status`, `interview_status`, `interview_assessment` present in the payload but —
  correctly, given that pass's own scope — not yet read by any component; that
  changed in the following pass, see below).
- `POST /v1/applications/{id}/invite` on the `"selected"` application: real `200`,
  `status` became `"invited"`, `invite_attempt_number: 1`, `invited_at` populated —
  this went all the way through RWS's real contact-lookup-then-VoiceHR-call chain, not
  a stub. Calling it again on the now-`"invited"` application (Resend Invite) returned
  another real `200` with `invite_attempt_number: 2` — confirms the frontend's
  "Resend Invite" label and continued-enabled button for `status: "invited"` matches
  real backend-allowed behavior, not an assumption.
- `POST /v1/applications/{id}/invite` on the pre-existing `"interviewed"` application
  returned a real `409` (`"Application is not invitable (current status:
  interviewed)"`) — confirms `INVITABLE_STATUSES` correctly excludes `"interviewed"`
  (the button never renders for that row) and, independently, that if it somehow were
  called anyway, the inline error banner would render the backend's real `409` detail
  verbatim, not a generic or invented message.
- An invite on an unknown `application_id` returned a real `404` (`"Application not
  found"`) — the same `ApiError`-message rendering path handles it correctly.
- `GET /v1/candidates/{candidate_id}/identity` resolved real names for both
  candidates referenced in the applications list above (`"Smoketest Candidate"` and
  `"Smoketest Convergence Candidate"`) — the exact calls each `ApplicationRow` makes
  via `useCandidateIdentity`.
- **Correct state after refresh**: re-fetching `GET /v1/jobs/{job_id}/applications`
  after both invite calls returned the updated real state (`status: "invited"`,
  `invite_attempt_number: 2`) — since `ApplicationsPage` always re-fetches fresh on
  mount (never trusts stale local state or navigation state), a hard browser refresh
  on this URL is confirmed to reflect genuine backend truth, not a stale local copy.
- Not yet tested: the rendered UI in an actual browser (same no-browser limitation as
  every prior pass) — verified at the network-contract level plus a clean production
  build and direct code review of the render paths (status-badge/interview-status
  cells, Invite-vs-Resend-Invite label switching, disabled-while-inviting state, the
  "View Applications" link added to `JobDetailPage`) against the real response shapes
  captured above.

**Live-tested — Polling + VoiceHR assessment display (prior pass)**, against the exact
real applications left by the prior pass (one `"invited"` application, one
pre-existing `"interviewed"` application with a real assessment):
- **A real webhook-driven `invited → interviewed` transition**, the exact one polling
  is meant to pick up: called VoiceHR's own `backend.services.processing.
  process_interview` directly inside the running `csb-voicehr` container (standing in
  only for real browser audio/ASR, as established in every prior end-to-end pass in
  this project) against the pending interview for the `"invited"` application, with a
  real multi-turn transcript and a 360s duration (long enough and with enough
  candidate turns to avoid the `inconclusive` fallback). This produced a real Gemini
  evaluation, and RWS's own logs confirm the full real chain: `webhook_received` →
  `outcome: "processed"` → `POST /v1/webhooks/voicehr HTTP/1.1 200 OK`, for that exact
  `application_id`. Re-fetching `GET /v1/jobs/{job_id}/applications` immediately after
  confirmed `status: "interviewed"`, `interview_status: "completed"`, and a real
  `interview_assessment` object — exactly the transition `pollApplications()` would
  have picked up on its next tick had a browser been polling live. This is the
  strongest form of verification available without an actual browser: the real
  backend-side event `usePolling` exists to observe was independently triggered and
  independently confirmed to have landed correctly.
- **Two distinct real assessment fixtures**, both re-fetched (i.e. as a hard refresh
  would show them) and reasoned through `AssessmentPanel`'s exact render logic: the
  pre-existing fixture (`evaluation_status: "completed"`, `overall_score: 93`,
  `cefr_level: "C1"`, `inconclusive: false`) and the fixture produced this pass
  (`evaluation_status: "completed"`, all scores `0`, `cefr_level: "A2"`,
  `inconclusive: false`) — the second one incidentally exercises the
  `value == null ? "—" : ...` distinction for real (a genuine `0`, correctly not
  rendered as `"—"`), not just as a hypothetical. Both are `"completed"`, so neither
  exercised the `"failed"`-branch plain-text path in this run; that branch was
  confirmed by direct code review of `AssessmentPanel`'s `evaluation_status === "failed"`
  check instead. Neither fixture had `inconclusive: true`, so the "Low-confidence
  evaluation" note was likewise confirmed by code review of that same conditional
  rather than by triggering a live inconclusive result.
- **Correct state after refresh**: both applications' final state — `"interviewed"`
  with a real `interview_assessment`, `"interviewed"` with the pre-existing one — was
  confirmed via a fresh, un-cached `GET /v1/jobs/{job_id}/applications` call, the exact
  request `loadApplications()` issues on mount; since that call is always re-issued
  fresh (never trusts stale state), this confirms a hard browser refresh on this URL
  would show the true persisted state, assessments included.
- **Polling stopping appropriately**: after the transition above, zero applications
  remained `"invited"` for that job — confirmed directly from the re-fetched list — so
  `hasInvitedApplication` would evaluate `false` and `usePolling`'s effect would tear
  its interval down on the next render, verified by code review of the hook's
  `[enabled, intervalMs]` dependency array (§12) rather than by observing a live
  browser's network panel.
- Not yet tested, no browser available in this environment (same limitation as every
  prior pass): the interval actually firing every ~7s in a live tab, the
  `visibilitychange` pause/resume behavior, and the expand/collapse click interaction
  — all three are DOM/browser-API-dependent behaviors with no curl-level equivalent;
  each was verified by direct code review against `usePolling.js`'s and
  `ApplicationsPage.jsx`'s actual implementation instead (the same standard applied to
  every prior pass's button-disabling/drag-styling/panel-rendering claims).

**Live-tested — Accept/Reject decision actions (prior pass)**, through the exact
dev-proxy path, issuing the same requests `ApplicationsPage`/`api/applications.js`
issue, against the two real `"interviewed"` applications left by the prior pass (one
with a real assessment produced that pass, one pre-existing) plus one freshly-selected
`"selected"` application created specifically to test deciding before any invite:
- `POST /v1/applications/{id}/accept` on a real `"interviewed"` application: real
  `200`, `status` became `"accepted"`, `decided_at` populated — a real, persisted
  terminal decision, not a stub.
- `POST /v1/applications/{id}/reject` on the other real `"interviewed"` application:
  real `200`, `status` became `"rejected"`, `decided_at` populated.
- **Repeat/invalid terminal transitions, both directions**: calling `accept` again on
  the now-`"accepted"` application returned a real `409` (`"Application is not in a
  decidable state (current status: accepted)"`); calling `reject` again on the
  now-`"rejected"` one returned a real `409` with the equivalent message for
  `"rejected"` — confirms `DECIDABLE_STATUSES` correctly excludes both terminal
  statuses (no button reappears for either row) and, independently, that the inline
  error banner would render the real `409` detail verbatim if the check were somehow
  bypassed. An accept on an unknown `application_id` returned a real `404`
  (`"Application not found"`); an unauthenticated accept attempt returned a real `401`.
- **Deciding directly from `"selected"`, before any invite**: selected a fresh
  qualified match (`POST /v1/matches/{match_id}/select` → real `201`, `status:
  "selected"`, `invited_at: null`), then called `reject` on it directly — real `200`,
  `status` became `"rejected"` with `invited_at` still `null`. This confirms
  `DECIDABLE_STATUSES` correctly includes `"selected"` (not just `"invited"`/
  `"interviewed"`) and that the frontend's Accept/Reject buttons correctly render
  *alongside* the Invite button for `"selected"`/`"invited"` rows (both `canInvite`
  and `canDecide` are `true` simultaneously for those two statuses) — a real,
  independently-triggered exercise of RWS's own documented rule that "the interview is
  informative for the recruiter's decision, not a mandatory gate before deciding"
  (`db.py: transition_application`'s docstring), not just a hypothetical reading of it.
- **Correct state after refresh**: re-fetching `GET /v1/jobs/{job_id}/applications`
  after all three decisions returned the updated real terminal states
  (`"accepted"`/`"rejected"`/`"rejected"`, each with `decided_at` populated) — the
  exact request `loadApplications()` issues on mount, confirming a hard browser
  refresh on this URL would show the true persisted decision, not a stale local one.
- **Polling remains stopped for terminal applications**: the same re-fetched list
  confirmed zero applications remained `"invited"` for that job after the decisions
  (`any invited? False`, checked directly against the response) — so
  `hasInvitedApplication` would evaluate `false` and `usePolling`'s effect would never
  start (or would tear down) its interval; deciding an application never touches
  polling directly in the code, only the resulting status change does, same as any
  other transition (verified by code review of `pollApplications`/`hasInvitedApplication`
  alongside the live data above, not merely asserted).
- Not yet tested, no browser available in this environment (same limitation as every
  prior pass): the actual click flow (button → `window.confirm()` → request), the
  `confirm()` dialog itself appearing and blocking on Reject specifically, and the
  buttons visually disabling/re-enabling during a request — verified by direct code
  review of `handleDecide`/`rowBusy` against the real response shapes captured above.

**Live-tested — resume upload (prior pass)**, through the exact request path
`ResumeUploadPage`/`api/resumes.js` use (Vite dev proxy → real RWS container → real
Resume Intelligence → real Gemini → real Postgres):
- Unauthenticated upload attempt → real `401` (matches `client.js`'s expected shape).
- Authenticated upload, unsupported extension (`.exe`) → real `422` with
  `{"detail": "Unsupported file type '.exe'. Allowed: [...]"}`.
- Authenticated upload, a real `.docx` resume → first attempt hit a transient Gemini
  `503` (high demand) and correctly surfaced as RWS's own `502` — a real, honest
  external-condition result, not a code defect; production model configuration was not
  changed to work around it. A retry of the same real action succeeded end to end:
  real `201` with `{"candidate_id": "1cca02e3-...", "is_new": true}`, independently
  confirmed present in `resume_intel.candidates` via `psql` against the live container.
- Not yet tested: the rendered UI in an actual browser (no browser available in the
  environment this was built in, in either this pass or the previous one) — verification
  is at the network-contract level (the exact requests the app's own code issues,
  through the exact dev proxy path it uses) plus a clean production build. Interaction
  details that only a browser can confirm (button disabling during upload, drop-zone
  drag styling, the success panel actually rendering) were verified by code review, not
  by clicking through them.

**Backend prerequisites — both implemented and live-verified on RWS** (prior,
backend-only pass; no frontend code was touched then):
- `GET /v1/jobs` — recruiter-JWT-protected, newest-first, returns
  `JobSummaryResponse[]` (`job_id`, `title`, `created_at`, `match_count`); a service API
  key does not satisfy the JWT requirement. `match_count` comes from a single grouped
  query (`db.py: list_jobs_with_match_counts`, `LEFT JOIN` + `GROUP BY Job.id`), not an
  N+1 loop. Empty database → `[]`.
- `GET /v1/candidates/{candidate_id}/identity` — recruiter-JWT-protected, transiently
  proxies `resume_intelligence_client.get_contact()`, returns
  `CandidateIdentityResponse` (`candidate_id`, `name`, `email`; no `phone`, no other
  field). The route declares no `db` dependency, so RWS persists nothing from this
  call. Unknown candidate → `404`; Resume Intelligence unavailable → `502` with a
  generic detail (no upstream exception text or PII in the response or in RWS's logs).

Both verified with real requests against the live Docker Compose stack (`csb-
recruitment-workflow` rebuilt and recreated for that pass): `GET /v1/jobs` returned the
one real seeded job (`"Backend Engineer"`, `match_count: 5`); `GET /v1/candidates/
7e664e04-4230-47d1-918d-461b97e0d11d/identity` returned Jean Dupont's real name/email
from the live Resume Intelligence container with no `phone` field; an unknown
candidate ID returned a real `404`; both endpoints returned `401` without a JWT and
`401` with a service API key instead of one. RWS's own request logs during these calls
contain only status lines and opaque UUIDs — no candidate name/email. 27/27 RWS tests
pass (`tests/test_jobs_list.py`, new — 8 tests; `tests/test_candidate_identity.py`, new
— 6 tests; plus the 13 pre-existing resume-upload tests, unaffected).

Confirmed via a direct route-decorator grep of `services/recruitment-workflow/main.py`
at the time of writing: `/health`, `/v1/auth/login`, `/v1/resumes`, `/v1/jobs` (GET +
POST), `/v1/jobs/{job_id}/matches`, `/v1/candidates/{candidate_id}/identity`, `/v1/
scoring-configs`, `/v1/matches/{match_id}/select`, `/v1/applications/{application_id}/
accept`, `/v1/applications/{application_id}/reject`, `/v1/jobs/{job_id}/applications`,
`/v1/applications/{application_id}/invite`, `/v1/webhooks/voicehr` — no others exist.

---

## 21. Known limitations / deliberately deferred

- One recruiter/admin account (the existing seeded account); no registration.
- No RBAC or multi-user system.
- No JWT refresh flow — the default 45-minute expiry comfortably covers a demo session;
  expiry mid-session means a re-login, handled by the existing 401 flow.
- No analytics dashboard.
- No bulk actions (bulk invite, bulk accept/reject).
- No job editing or deletion.
- No pagination — `top_k` (default 20) bounds the candidate list server-side; revisit
  only if a real dataset size makes this a problem.
- No WebSockets — polling only, and only where already justified (§12).
- No dark mode.
- No candidate-facing portal (VoiceHR's own candidate interview link is a separate,
  out-of-scope flow).
- No frontend scoring/business logic of any kind — every number and decision the UI
  shows comes from RWS as-is.
- **Discovered during the Phase 10 audit, deliberately left as-is**: no explicit
  text-truncation (`text-overflow: ellipsis`) on candidate-name/job-title table cells
  — the existing `.tableWrapper` horizontal scroll already prevents real layout
  breakage, and real name/title data is short in practice; no favicon set. Both
  remain cosmetic-only, zero functional impact, not worth the churn of fixing
  speculatively. (A third Phase-10 item, `ApplicationsPage.module.css`'s
  un-tokenized `.acceptButton:hover` hex literal, was resolved in Phase 11 as a
  side effect of the CSS cleanup — see §20 — not called out separately here anymore.)
- **Discovered during the Phase 11 admin-shell pass, deliberately left as-is**:
  `ResumeUploadPage.jsx`'s drop-zone `<div role="button" tabIndex={0}>` has an
  `onClick` but no `onKeyDown`, so Enter/Space don't open the file picker via
  keyboard — the same category of gap Phase 10's finding M1 fixed in
  `JobDetailPage`/`ApplicationsPage`, just not in this file. Not fixed here because
  this task's scope was explicitly the visual shell/components, not a second
  accessibility pass; worth a small follow-up fix (mirroring M1's pattern exactly)
  whenever this file is next touched for another reason.
- **Discovered during the Phase 12 pass**: `CandidatesPage`'s rows had no
  `onClick`/keyboard handling and didn't navigate anywhere — no
  `/candidates/:candidateId` detail page existed yet, and this project's standing
  rule (established in Phase 11) is to never link to a page that isn't built.
  **Resolved in Phase 15**: `CandidateDetailPage` now exists and every row links to
  it — this bullet is kept for history, not as an open item. Also: `DashboardPage`'s
  "Recent activity" section deliberately only
  surfaces candidate-upload and job-posting events (both already carry a
  human-readable name/title inline) — application-derived events (select/invite/
  decide) were left out, since `GlobalApplicationResponse` only carries an opaque
  `candidate_id`, and resolving it to a display name for an activity feed would
  need an extra per-row identity lookup; judged not "cleanly derived from existing
  timestamps" as this task's own scope asked for, so left out rather than built
  with a raw UUID or an N+1 fetch.
- **Discovered during the Phase 13 pass, deliberately left as-is**: `InterviewsPage`
  has no invite/accept/reject actions — it is read-only by design, not an
  oversight. Recruiter actions still happen exclusively on `ApplicationsPage`
  (job-scoped); duplicating those mutation handlers (and the `onUpdated`
  row-patching they need) into a second, global page was judged not "clean and
  genuinely useful" per this task's own scope — the point of this page is a
  cross-job glance at interview progress, not a second surface to act from. If a
  future session wants that, it should reuse `ApplicationsPage`'s
  `handleInvite`/`handleDecide` pattern rather than re-inventing it. Also:
  `InterviewsPage`'s candidate name had no detail-page link at the time (same
  reason as `CandidatesPage`, above) — **resolved in Phase 15**.
- **Discovered during the Phase 14 pass, deliberately left as-is**:
  `ReportDetailPage` doesn't render VoiceHR's `evidence` field (quote-level detail
  tying specific transcript moments to specific dimensions) — its content overlaps
  with strengths/concerns, and adding a third notes section was judged to make the
  page needlessly dense rather than more useful; revisit if a future session finds
  a real need for it. `ReportsPage`'s candidate name had no detail-page link at the
  time (same reason as `CandidatesPage`/`InterviewsPage`) — **resolved in Phase
  15**, including `ReportDetailPage`'s own header (via a best-effort lookup, since
  VoiceHR's report carries no `candidate_id` — see below). No job-title context on
  `ReportDetailPage` itself remains true (VoiceHR has no concept of RWS jobs; that
  context lives on `ReportsPage`'s list). **A real
  environment characteristic, not a frontend limitation, discovered live this
  pass**: VoiceHR's SQLite database does not survive a container recreate (see
  `handoff.md`'s Known Limitations for the full detail) — every previously-real
  completed interview in the demo dataset is gone as of this session, and the one
  currently viewable via `/reports` is the fresh one this session's own live
  smoke test created. Worth knowing before a live demo: confirm at least one real
  report still exists (or regenerate one, the same way this session did) before
  relying on `/reports` having data to show.
- **Discovered during the Phase 15 pass, deliberately left as-is**:
  `CandidateDetailPage`'s applications table links each row to its Job
  (`/jobs/:jobId`) and, when a report exists, to its Report
  (`/reports/:applicationId`) — but not to a third, per-application deep link into
  the global `InterviewsPage`, since that page has no per-application URL/anchor to
  target and the Report link already covers the interview outcome once one exists;
  for an application still only invited/pending, there's nothing more specific to
  show than the Job link. `ReportDetailPage`'s candidate-name link depends on a
  best-effort lookup against `GET /v1/applications` (VoiceHR's report itself
  carries no `candidate_id`) — if that lookup fails or the application isn't found
  in the list for any reason, the name silently stays plain, unlinked text rather
  than erroring; this is intentional graceful degradation, not a bug, and never
  blocks the report's own content from rendering. The "Knowledge Graph" section on
  `CandidateDetailPage` is a labeled, static `EmptyState` placeholder only — no
  graph library was added, no data for it was fetched, per this task's own explicit
  instruction not to build it yet.
- **Test-data hygiene note, not a frontend limitation**: the Phase 10 audit found a
  real candidate present in Second Brain's scoring index with no matching Resume
  Intelligence contact record (an artifact of independent test-data seeding across
  sessions, not a code defect) — `GET /v1/candidates/{id}/identity` correctly `404`s
  and inviting that candidate correctly `422`s; both are the frontend's existing,
  correct handling of that condition, not something new to build. Worth knowing
  before a live demo: prefer candidates already confirmed to have a real contact
  record when demonstrating Select/Invite live.

---

## 22. Frontend invariants (non-negotiable)

- **Browser talks only to RWS.** No frontend code may call Resume Intelligence, Second
  Brain, or VoiceHR directly.
- **Service API keys never reach the frontend** — not in a response body, not in a
  header, not in a bundled config value, ever.
- **The backend remains authoritative for scoring and workflow.** The frontend renders
  `MatchResult`/`Application` state; it never computes, adjusts, or overrides it.
- **The frontend never causes candidate PII to be persisted server-side outside Resume
  Intelligence.** Identity lookups are transient (§9); RWS must not gain a PII column
  because the frontend asked for one.
- **Do not duplicate backend business rules** (qualification thresholds, state-machine
  transition legality, retry/idempotency logic) in frontend code — surface what the
  backend returns/rejects, don't pre-validate against a re-implemented copy of its rules.
- **Keep dependencies minimal.** Every addition to `package.json` should be justified
  against a concrete need, the same bar §3 already applied.
- **Avoid speculative frontend infrastructure** — no global store, cache layer, or
  abstraction added ahead of a page that actually needs it.

---

## 23. Session workflow for future Claude Code agents

Before starting frontend work:

1. Read this file (`FRONTEND_HANDOFF.md`) in full.
2. Inspect the specific files relevant to the current `[NEXT]` task (§24) — don't
   assume this document's prose is a substitute for reading the actual current code.
3. Implement **one** bounded frontend task — the one named in `[NEXT]`, nothing beyond
   it.
4. Run appropriate verification (`npm run build` at minimum; a live smoke test against
   the real RWS API where the task involves a new network call — see §18 on what
   "appropriate" means at this project's size).
5. Update this file: move the completed phase's checkbox in §19, update §20 with what
   was actually verified (not what was attempted), add anything newly learned to §21 if
   it's a deliberate scope decision, and rewrite §24 to name the next bounded task.
6. Update the main `handoff.md` **only** if the change affects cross-service
   architecture or status (e.g., a new RWS endpoint was added, changing RWS's own API
   surface) — routine frontend-only progress belongs in this file, not the main one.
7. Stop instead of silently moving on to the next feature — one task per session pass,
   exactly as this project's other work has been scoped throughout.

---

## 24. [NEXT]

**Phases 11–14 are done** — admin shell redesign, the Candidates page + real
Dashboard data, the global VoiceHR interviews page, and the VoiceHR Reports
backend proxy + Reports pages. Full detail for each is in §19/§20, not restated
here.

**Phase 15 (Candidate detail + cross-linking) is done** — the piece every prior
phase since Phase 12 deliberately deferred:

- **Backend** (tracked in full in `handoff.md` per §23's own rule — cross-service
  work): one new RWS endpoint, `GET /v1/candidates/{candidate_id}/knowledge`
  (recruiter-JWT, transient proxy to Second Brain via the *already-existing*
  `clients.get_candidate()` — no new Second Brain client code needed). Returns
  only `candidate_id`/`skills`/`job_titles`/`years_of_experience`; no `raw_text`,
  no `source_version`, no embedding.
- **Frontend**: new `CandidateDetailPage` at `/candidates/:candidateId` — identity
  via the existing `useCandidateIdentity` hook, professional knowledge from the
  new endpoint, and this candidate's applications (filtered client-side from the
  already-fetched `GET /v1/applications`, no new endpoint needed for that part),
  each linking to its Job and, when a report exists, its Report. A reserved,
  static "Knowledge Graph" placeholder section — not the graph itself.
- **Every candidate name in the app now links to it**: `CandidatesPage`'s rows,
  `JobDetailPage`'s ranked-candidate name, `ApplicationsPage`'s and
  `InterviewsPage`'s candidate names, `ReportsPage`'s rows, and
  `ReportDetailPage`'s header (via a best-effort lookup, since VoiceHR's own
  report has no concept of RWS's `candidate_id`).
- Live-verified against the real rebuilt/recreated `csb-recruitment-workflow`
  container: real skills/job-titles/experience for a real candidate, cross-linked
  data reconciled by hand against real `GET /v1/applications` output, hard refresh
  confirmed on `/candidates/{id}` and both cross-linked destinations. `npm run
  build`: 87 modules, 0 errors. Full detail in §20.

**`[NEXT]` for this file: none — no frontend feature work is currently queued.**
Every screen from the original admin-dashboard redesign proposal exists, works,
and is fully cross-linked. The next bounded task across the whole project is
**VoiceHR SQLite persistence hardening** — backend/infrastructure only (VoiceHR's
`DB_PATH` doesn't survive a container recreate; see `handoff.md`'s Known
Limitations and its own `[NEXT]` for the full problem statement and smallest fix).
Nothing in that task should require a frontend change; if a future session's
implementation of it somehow does, that's the point to give this file a real
`[NEXT]` again, not before.
