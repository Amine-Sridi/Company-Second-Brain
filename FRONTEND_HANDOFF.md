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
- [x] **Phase 16 — Knowledge graph React page** *(new `GraphPage` at `/graph`,
  Cytoscape.js + `react-cytoscapejs`, consuming the backend prerequisite added last
  session (`GET /v1/graph/candidates-jobs`, tracked in `handoff.md`) plus the
  already-existing `GET /v1/candidates` for name resolution — no new backend call;
  read-only/exploratory, per the knowledge-graph design audit's own recommendations
  — see §20/§21/§24)*
- [x] **Phase 17 — Candidate-detail ego graph reuse** *(the Phase 15 "Knowledge
  Graph" placeholder on `CandidateDetailPage` now renders a real, scoped graph of
  just that candidate and its own matched jobs; Phase 16's Cytoscape engine was
  extracted into a new shared `MatchGraph` component so `GraphPage` and this embed
  are two call sites of the same code, not two implementations — no new backend
  call, no new dependency — see §20/§21/§24)*
- [x] **Phase 18 — Knowledge graph 2D visual polish** *(fixed a real, verified
  layout-instability bug in the full graph's `cose` layout — root cause was never
  the `randomize` option, which was already at its correct default — plus a subtle
  canvas dot-grid, degree-based node sizing, and subtle focus/reset transitions in
  the shared `MatchGraph` component; no new dependency, no backend change, edge
  score/qualification encoding untouched — see §20/§21/§24)*
- [x] **Phase 19 — Interactive graph controls (legend filtering + score filter +
  node search)** *(full `/graph` only — the legend's qualified/disqualified
  swatches are themselves now a display-only toggle, a client-side minimum-score
  slider, and a candidate/job name search that reuses the existing click-highlight
  path; the `CandidateDetailPage` ego graph is unaffected by construction (it never
  passes the new opt-in props) — no backend call, no new dependency — see
  §20/§21/§24)*
- [x] **Phase 20 — Final navigation polish (cross-link audit fixes)** *(the four
  concrete gaps named by the whole-system UX audit: Resume Upload's success screen
  now links the created/matched candidate; `ApplicationsPage` rows with an
  assessment now link the full report; `InterviewsPage`/`ReportsPage`'s Job column
  is now a real link; `ReportDetailPage` now links the job too, reusing the
  applications call it already makes — no new API call anywhere, no backend
  change — see §20/§24)*
- [x] **Phase 21 — V2 design system, app shell, and Dashboard (visual direction
  change)** *(a deliberate, explicit product decision to move away from the
  classical light SaaS-admin look toward a dark "AI recruitment command center"
  aesthetic — new `motion` dependency, new additive `--v2-*` token layer, full
  redesign of `Sidebar`/`TopBar`/`Layout`'s page-transition wiring, an opt-in
  dark variant on `StatusBadge`/`EmptyState`, a direct redesign of `StatTile`
  (its only consumer), and a full rebuild of `DashboardPage` into a bento
  layout — no backend change, every other page's own styling is completely
  untouched and still renders exactly as before — see §20/§21/§24)*

**Phase 21 is a deliberate mid-project pivot, not a continuation of Phase 20's
own `[NEXT]`** ("Final production/demo readiness audit — whole system, no feature
work" — see the previous revision of this section). The user explicitly redirected
toward a new visual-direction redesign instead; that readiness audit has not been
done and is not implied to be cancelled, only postponed — a future session should
still expect to run it once the V2 migration (this phase plus however many
"operational pages" phases follow) is complete, per this file's own §23 workflow,
not skip it.

- [x] **Phase 22 — V2 visual migration, slice 1 (Applications / Interviews /
  Reports / Report Detail)** *(the first of the "operational pages" migration
  `[NEXT]` named — one cohesive group chosen specifically because `ReportsPage`
  links directly into `ReportDetailPage`, so migrating one without the other
  would create a jarring dark→light drill-down; `AssessmentPanel` (sole consumer:
  exactly these three pages) redesigned directly in place, same reasoning as
  `StatTile` in Phase 1; `PageHeader` gained the same opt-in `variant="glass"`
  pattern already proven on `StatusBadge`/`EmptyState` — no backend change, the
  other six un-migrated pages are untouched — see §20/§21/§24)*

- [x] **Phase 23 — V2 visual migration, slice 2 (Job Detail / Candidate Detail)**
  *(picked up on a bare "continue" using the exact guidance Phase 22 itself left
  in §24 — these two specifically, not the list pages, because Phase 22 had
  left them each linking into a now-dark page (`JobDetailPage` → `Applications
  Page`, `CandidateDetailPage` → `ReportDetailPage`), a new drill-down seam Phase
  22 itself flagged; `CandidateDetailPage`'s embedded `MatchGraph` ego graph is
  deliberately **not** themed (still reads V1 tokens, same as `GraphPage`) — a
  disclosed, bounded exception, not an oversight — see §20/§21/§24)*

- [x] **Phase 24 — V2 visual migration, slice 3 (Jobs List / Candidates / Resume
  Upload)** *(picked up on a bare "continue" using the exact guidance Phase 23
  left in §24 — `JobsListPage` and `CandidatesPage` chosen because their rows/
  cards link directly into `JobDetailPage`/`CandidateDetailPage`, both V2 since
  Phase 23, the same "list is the light side of an already-dark seam" pattern
  that motivated Phase 23's own scope; `ResumeUploadPage` folded in as a small,
  standalone, low-risk third page rather than its own separate slice; a
  pre-existing CSS bug (`.resetButton`'s leftover standalone `margin-top`
  misaligning it against `.uploadButton` once both share `.actions` as a flex
  row, dating to the earlier navigation-polish pass) was fixed as part of
  rewriting this file's CSS, not as a separate change — no backend change, only
  `GraphPage`/`MatchGraph` remain on V1 — see §20/§21/§24)*

- [x] **Phase 25 — V2 visual migration, final slice (`GraphPage` / `MatchGraph`)**
  *(picked up on a bare "continue" using the guidance Phase 24 left in §24 — the
  last piece. `MatchGraph.jsx`'s `buildStylesheet()` reads real computed CSS
  custom properties via `getComputedStyle()` since Cytoscape draws to a
  `<canvas>`, not the DOM, and cannot use CSS classes for fill colors; that
  function now reads `--v2-*` token names directly instead of `--color-*`.
  **Deviation from this phase's own prior `[NEXT]` guidance, made and justified
  during implementation, not carried over unexamined**: that guidance speculated
  a `theme`/`variant` param on `buildStylesheet()` so `GraphPage` and
  `CandidateDetailPage`'s ego graph could independently pick V1 or V2 colors.
  That speculation assumed one call site might stay V1 — but this same pass
  migrates `GraphPage` itself, so both of `MatchGraph`'s real call sites land on
  V2 together and no V1 consumer is left needing the old token names. Adding a
  toggle for a branch nothing would ever take is exactly the unnecessary
  abstraction this project's own CLAUDE.md warns against, so `buildStylesheet()`
  was redesigned directly in place instead — the same "direct redesign, not an
  opt-in variant" pattern already used for `StatTile`/`AssessmentPanel` once all
  real consumers were confirmed migrating together. `MatchGraph.module.css`
  (canvas chrome, legend, filter row, search, detail panel) got the same direct
  `--v2-*` redesign; all 4 internal `<StatusBadge>` calls flipped to
  `variant="glass"`. `GraphPage.jsx`/`.module.css` got the same `PageHeader`/
  `EmptyState` `variant="glass"` flip and "V2 page panel" treatment as every
  other migrated page. `CandidateDetailPage.jsx`'s `<MatchGraph>` call site
  needed no prop change (no toggle exists) — only its explanatory comment was
  updated to say why. No backend change, no new dependency, no logic in
  `buildGraphElements()`/click-highlight/layout touched — only the token names
  `buildStylesheet()` reads and 4 `StatusBadge` props — see §20/§21/§24)*

- [x] **Phase 26 — 3D Knowledge Graph presentation mode (`MatchGraph3D`)** *(the
  user explicitly reversed the "Optional 3D presentation mode" NO-GO recorded
  between Phase 19 and 20 — see the note in §24 documenting that reversal — by
  naming the specific library that resolves the original blocker:
  `3d-force-graph` used directly, not `react-force-graph`, which is what the
  original disposable prototype actually measured and rejected for its AR/VR
  dependency weight and a git+SSH transitive dependency. `3d-force-graph`'s own
  real dependency tree (`npm view 3d-force-graph dependencies`) has neither —
  confirmed before installing, not assumed. 2D (`MatchGraph`/Cytoscape) is
  untouched as the default/analytical view; 3D is an explicit, lazy-loaded,
  opt-in `2D | 3D` switch on `/graph` only — `CandidateDetailPage`'s ego graph
  keeps 2D-only, unchanged, out of this task's scope. New files:
  `components/MatchGraph3D.jsx` + `.module.css` (the immersive Three.js scene,
  isolated so no Three.js import reaches `MatchGraph.jsx`),
  `components/GraphDetailPanel.jsx` (the click/selection detail experience,
  extracted from `MatchGraph.jsx` so both renderers share one implementation
  instead of two drifting copies), `components/GraphControls.jsx` (the legend/
  score-filter/search bar, extracted from `MatchGraph.jsx` and lifted to
  `GraphPage` so that shared state survives a 2D↔3D switch instead of resetting
  — the "small refactor" this task's own spec anticipated might be needed).
  `MatchGraph.jsx` gained one new pure export, `buildGraph3DElements()`, reusing
  the exact same id-prefixing/degree-precomputation logic as the existing
  `buildGraphElements()` so both renderers derive nodes/edges from one place.
  No backend change, no new score/qualification computation, no new API call —
  see §20/§21/§24 for the full verification record and the honest
  browser-verification gap.)*

- [x] **Phase 27 — 3D graph visual tuning + real-browser validation** *(this
  session found that Playwright's Chromium browser, already cached on this
  machine from unrelated prior use, could actually be launched here —
  `playwright-core` installed into an isolated scratchpad directory, never
  added to the project itself, driving the already-cached
  `chromium-1228` binary directly by `executablePath`. This overturns every
  earlier phase's standing "no browser available" disclosure **for this one
  session** — real screenshots of the actual rendered WebGL scene were taken
  and inspected, not just code/API reasoning. See §20/§21 for exactly what was
  seen and what was fixed as a direct, evidenced result: the initial
  `zoomToFit` padding and fog density were both too aggressive for this small
  dataset, washing every node/link out to near-invisibility at the default
  framing (confirmed via three near-identical screenshots — initial load,
  "Fit graph," and remount-after-switch — all showing the same dim, distant
  cluster); node radii were too small to read clearly once actually visible;
  and the glow-shell rendered as a dull dark ring rather than a glow, because
  its default alpha blending darkens against a dark background instead of
  brightening it. All four were corrected with targeted, conservative numeric/
  material changes — no architecture change, no new dependency, no backend
  touch, 2D untouched. A second real-browser pass after the fix confirmed the
  improvement directly (screenshots compared side by side, not assumed) and
  also incidentally proved several interaction paths end-to-end for the first
  time with real data on screen: click-to-select population of the shared
  `GraphDetailPanel` with real match percentages/qualified badges, the
  qualified/disqualified filter toggle correctly hiding/showing only its edge
  category without disturbing node positions, and search-triggered camera
  focus landing on the correct node with its real connections listed. Not
  every interaction in this task's own checklist could be exercised this way
  (a scripted browser can click coordinates and read text, but can't judge
  whether an animation "feels smooth" or hand-verify a real mouse-drag orbit)
  — see §21 for exactly what remains a human-only judgment call.)*

- [x] **Phase 28 — V2 design system / shell / Dashboard: real-browser audit +
  targeted fixes** *(the `[NEXT]` text handed to this session was "V2 frontend
  visual redesign — design system + shell + dashboard" — but Phase 21 had
  already built exactly that, and its own §20 entry disclosed it was never
  seen in a real browser. Rather than redesign already-working components on
  stylistic preference, this session used the same real cached-Chromium
  technique Phase 27 introduced to actually look at `variables.css`'s
  `--v2-*` system, `Sidebar`/`TopBar`/`Layout`, and `DashboardPage` rendered
  live, compare them against the intended direction, and fix only what a real
  screenshot proved was actually wrong. Verdict: the visual language itself
  (dark cinematic bento layout, cyan/violet accents, glass panels, restrained
  motion, a graph-teaser glyph whose colors already match the 3D graph's own
  job=cyan/candidate=violet convention) is genuinely strong and was **not**
  rewritten. Three real, screenshot-confirmed defects were found and fixed
  with small, targeted changes — see §20 for the full before/after record:
  a light void below the Dashboard panel whenever the viewport is taller than
  its content (fixed via a scoped body-background toggle in
  `DashboardPage.jsx`'s own mount lifecycle, reverted on unmount — confirmed
  live that navigating away still renders the next page's light background
  correctly, no leak); a mismatched blue `:focus-visible` ring (the shared
  `global.css` rule, tuned for light pages) appearing on the dark Sidebar/
  TopBar chrome (fixed with a self-contained `--v2-cyan` override in each
  component's own `.module.css`, the same "self-contained, can't leak to
  light pages" pattern Phase 21 already established); and a classic CSS
  grid/flex "min-width: auto" shrink-blowout causing real horizontal overflow
  and clipped (not ellipsis-truncated) text on the Dashboard's bento grid at
  phone width (fixed with `min-width: 0` at the two points in the shrink
  chain that needed it). No backend change, no new dependency, no routing
  change, no fake metric, no other page touched.)*

- [x] **Phase 29 — Real-browser visual audit of all nine remaining operational
  pages, benchmarked against Dashboard/3D Graph** *(the `[NEXT]` text handed
  to this session was "V2 visual migration — operational pages," but this
  session was explicitly told to ignore that wording until verifying repo
  history — `grep -rn "var(--color-" src/pages/*.module.css` confirmed every
  page except `LoginPage` was already token-clean, meaning Phases 22-25 had
  already finished the literal migration. So this session's real work was the
  audit Phase 28 modeled: load every page in the real cached-Chromium browser
  (Candidates, Candidate Detail, Upload, Jobs, Job Detail, Applications,
  VoiceHR/Interviews, Reports, Report Detail — desktop and mobile, 18
  screenshots), classify each against Dashboard/the 3D graph, and touch only
  what a real screenshot proved was actually weak. Seven of nine pages
  classified **already V2-quality** (Candidates, Jobs, Job Detail,
  Applications, Interviews, Reports, Report Detail) and were **not**
  touched — solid tables, working hover states, consistent badge/link colors,
  no rewrite applied on stylistic preference. Two classified **minor polish
  needed** (Candidate Detail: zero cyan/violet accent anywhere, reading as
  visually disconnected from the rest of the app; Resume Upload: a bland
  text-only drop zone, plus one real bug — its default-state `PageHeader` was
  missing `variant="glass"`, entirely unlike its own success-state
  `PageHeader` two states later in the same file, so its subtitle rendered in
  the light-theme muted gray against a dark panel).
  **The single highest-value finding was systemic, not page-specific**: the
  "light void below the dark panel" bug Phase 28 fixed for `DashboardPage`
  alone turned out to be a property of every page using the shared "V2 page
  panel" pattern, not something specific to Dashboard — confirmed by the very
  first screenshot taken this session (`CandidatesPage`, unmodified since
  Phase 24, showing the identical gap). Since Phase 21's original reason for
  never touching `Layout.module.css`'s `.content` no longer holds (every page
  reachable through the shell now paints its own dark panel; `LoginPage`
  isn't rendered through this shell at all), this session fixed it once at
  the root instead of repeating Phase 28's per-page workaround, and removed
  that now-redundant `DashboardPage.jsx` `useEffect` as part of the same
  change. See §20/§21 for the full before/after record, including the two
  fresh cyan/violet accent touches added to `CandidateDetailPage` and the
  upload-icon glyph added to `ResumeUploadPage`'s drop zone. No backend
  change, no new dependency, no routing change, no fake data.)*

- [x] **Phase 30 — Final production/demo readiness audit (whole system)** *(run
  from the repo root, full findings/verdict/demo-sequence recorded in
  `handoff.md` per this project's own "cross-service facts go there" rule —
  not restated in full here. Frontend-relevant summary: a real-browser,
  real-data end-to-end walkthrough — login → Dashboard → Candidates →
  Candidate Detail (survived a hard refresh) → Jobs → Job Detail →
  Applications → Report Detail (audio resolved via a same-origin `blob:` URL)
  → Reports → Interviews → Knowledge Graph 2D (search + filter) → 3D → back
  to 2D → logout → protected-route redirect → re-login → mobile sanity —
  completed with **zero new console errors** and no broken cross-link,
  confirming Phases 21-29's V2 redesign and 2D/3D graph work hold up under a
  full, deliberately unscripted click-through, not just the per-phase checks
  that built them. No frontend code was changed by this audit; the one real
  fix it found (missing container `restart` policies) was infrastructure,
  recorded in `handoff.md`. Overall system verdict: **DEMO READY**, no
  `CRITICAL`/`HIGH` defect found.)*

**The frontend now has every screen named in the original admin-dashboard redesign
proposal, the candidate-detail cross-linking hub, the whole-dataset knowledge graph
with interactive filtering/search — now presentable in either a default 2D
analytical view or an explicit, optional, real-browser-tuned 3D immersive view
(Phases 26-27) — a per-candidate ego graph reusing the same 2D engine, a
visually-polished layout-stable version of all of it, no remaining dead-end/
missing cross-links from the final UX audit, and the V2 dark "command center"
visual direction applied to every one of the eleven production screens, **every
one of which has now been checked in a real browser at least once** (Phase 28
for the shell/Dashboard, Phase 29 for the other nine, Phase 30 for a final
full-system click-through): the app shell, `DashboardPage` (Phase 21,
audited+fixed Phase 28), Applications/Interviews/Reports/Report Detail (Phase
22, audited Phase 29 — no change needed), Job Detail/Candidate Detail (Phase
23, audited Phase 29 — Candidate Detail polished), Jobs List/Candidates/Resume
Upload (Phase 24, audited Phase 29 — Resume Upload polished), and the
Knowledge Graph (Phase 25, audited Phase 29 — no change needed).** The
dark/light visual seam that every phase since 22 tracked opening and closing
is fully closed, **and the separate "light void below every panel" defect
Phase 28/29 found is now fixed once, at the shell level, for every page at
once.** No page anywhere in the app still renders the original V1 light
theme. All thirty phases done. §24 names the next bounded task.

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

**Phase 16 — Knowledge graph React page (`/graph`)**: **DONE**. Implements the design
audit's own recommendation (chat-delivered proposal, tracked in `handoff.md`) plus the
backend prerequisite it named (`GET /v1/graph/candidates-jobs`, also tracked in
`handoff.md` from last session) — this session was frontend-only.

- **New dependency**: `cytoscape` (^3.34.3) + `react-cytoscapejs` (^2.0.0, the thin
  official React wrapper — a `<div>` ref plus declarative elements/stylesheet/layout
  props, no extra state management or abstraction of its own). Chosen over react-force-
  graph/three.js (3D, unneeded per the design audit's own "no 3D yet" scope) and raw
  D3-force (more code to own for the same result). No other new dependency.
- **`src/api/graph.js`** (new): one function, `getCandidatesJobsGraph()`, calling
  `GET /v1/graph/candidates-jobs` through the existing `request()` wrapper — same
  pattern as every other `api/*.js` module, so it's covered by `client.js`'s existing
  JWT-attachment/401-handling/error-normalization with no new code.
- **`src/pages/GraphPage.jsx` + `.module.css`** (new), routed at `/graph`
  (`AppRouter.jsx`):
  - **Data**: one `Promise.all` of `getCandidatesJobsGraph()` (jobs + edges) and the
    already-existing `getCandidates()` (for display names) on mount — no new backend
    call beyond the one endpoint added last session, and no direct call to any
    service other than RWS.
  - **Graph construction**: job nodes from `jobs[]`; candidate nodes derived from the
    *unique* `candidate_id` values across `edges[]` (the backend deliberately returns
    no `candidates[]` array — see `handoff.md`'s endpoint entry — so this is exactly
    the client-side derivation its own schema docstring expects); one Cytoscape edge
    per `MatchResult`, both qualified and disqualified, never filtered or deduplicated.
  - **Visuals**: job nodes are primary-colored rounded squares, candidate nodes are
    accent-colored circles (`--color-primary`/`--color-accent`, read from the real
    computed CSS custom properties at stylesheet-build time — Cytoscape renders to
    canvas, so it cannot reference `var(--color-x)` itself the way DOM CSS can; this
    keeps node colors tied to the one real token source instead of a second, driftable
    set of hex literals). Edge width and opacity scale with `final_score` via
    Cytoscape's own `mapData()`; qualified edges are solid `--color-success`,
    disqualified edges are dashed `--color-danger` (color *and* line-style, not color
    alone, so the distinction doesn't depend on color perception). A small legend
    (plain DOM/CSS, not canvas) explains all four visual encodings.
  - **Interactions**: click a node → `closedNeighborhood()` highlights it and its
    edges/neighbors, everything else fades (`.faded`/`.highlighted` CSS classes on the
    Cytoscape elements, added/removed by the click handlers — no extra state machine);
    click an edge → the edge and its two endpoint nodes highlight the same way; click
    the background → clears the highlight and the detail panel. A persistent detail
    panel (plain DOM, not canvas) shows, for a node, its name/kind and a sorted list of
    its connections (other party, score, qualified badge); for an edge, both
    endpoints (candidate/job, each a real `<Link>` into their existing detail page —
    the "separate Open action" requirement), final score, hard-gate result, and
    matched/missing requirement chips (visually identical to `JobDetailPage`'s own
    chips — a small deliberate duplication rather than a new shared component for one
    reuse, matching this project's own duplication-over-premature-abstraction rule).
    Zoom/pan are Cytoscape's own default mouse/touch handling; a "Reset view" button in
    the page header re-fits the graph and clears any selection/highlight.
  - **Layout**: Cytoscape core's own built-in `cose` force-directed layout (no extra
    layout plugin/dependency) — 2D only, as scoped; `animate: false` so the graph
    appears already-settled rather than visibly animating into place.
  - **No scoring/qualification logic of any kind lives in this page** — `qualified`,
    `hard_gate_passed`, and `final_score` are rendered exactly as the backend returns
    them, never recomputed or re-derived.
  - **Sidebar**: a "Knowledge Graph" nav entry was added to `Sidebar.jsx` (new inline
    SVG icon, same pattern as every other nav icon) — added last, only once the page
    itself was built, verified, and working, matching this task's own explicit
    ordering.
- **Verification**:
  - `npm run build`: 98 modules, 0 errors, both on host and inside a real Docker
    multi-stage build of the `recruitment-workflow` image (the same two-stage
    Dockerfile from Phase 9, unchanged). The production JS bundle grew to ~675 kB
    (~215 kB gzipped) from cytoscape's own size — a real, disclosed trade-off of
    picking a graph-rendering library at all, not a regression in this page's own
    code; noted in §21, not hidden.
  - Full RWS backend suite (`pytest tests/`, unaffected by a frontend-only change):
    **69/69 passed**, host and inside the rebuilt/recreated `csb-recruitment-workflow`
    container.
  - **Live production-serving verification**, same technique as Phase 9: hard-refresh
    equivalents (`curl`) against the real port 8003 for `/graph`, `/`, `/jobs`,
    `/candidates`, `/interviews`, `/reports`, `/upload`, and `/health` all returned
    real `200`s with the correct content types; the served bundle was fetched and
    confirmed to actually contain `cytoscape`, the `/v1/graph/candidates-jobs` path
    literal, the `/graph` route, and the `"Knowledge Graph"` sidebar label — not just
    a build that happened to succeed.
  - **Live data correctness, against the real running stack** (real recruiter login,
    real JWT): `GET /v1/graph/candidates-jobs` returned **4 real jobs, 26 real edges
    (17 qualified / 9 disqualified)**; `GET /v1/candidates` returned 6 real candidates.
    Rather than asserting the page would render this correctly, this exact live JSON
    was fed into a small headless-Cytoscape script (the real `cytoscape` package
    version this app bundles, run with `headless: true` — no DOM/canvas required)
    that reconstructs `GraphPage.jsx`'s own `buildElements()` logic and then exercises
    the *actual Cytoscape APIs the page's click handlers call* against it: job/
    candidate/edge counts matched the live payload exactly; every resolvable
    candidate node's label matched its real `GET /v1/candidates` name; every job
    node's label matched its real persisted title; the `edge[?qualified]`/
    `edge[!qualified]` boolean data-selectors used by the actual stylesheet selected
    exactly the 17/9 real qualified/disqualified edges with no overlap or gap;
    `closedNeighborhood()` on a real job node returned exactly that job's own real
    edges plus its distinct candidates (proving the click-to-highlight logic is
    correct against real data, not just plausible); `connectedEdges()` on a real
    candidate node returned exactly that candidate's real matches; a real edge's
    `source()`/`target()` correctly resolved to its candidate/job endpoints (what the
    detail panel's edge view relies on); and the `cose` layout ran headlessly without
    throwing and assigned every node a finite position. All 14 checks passed. This is
    the honest substitute for a literal browser click-through: **no browser is
    available in this environment** (the same gap Phase 9 already disclosed) — every
    piece of interactive logic that a click-through would exercise was instead run
    for real, against real data, through the real library, just without a rendered
    canvas/pixels to look at.
  - `git status` after the pass showed only the expected files changed: `api/graph.js`
    (new), `pages/GraphPage.jsx` + `.module.css` (new), `router/AppRouter.jsx` (one
    route added), `components/Sidebar.jsx` (one nav entry + icon added),
    `package.json`/`package-lock.json` (the two new dependencies) — no existing page's
    own file was touched.

---

**Phase 17 — Candidate-detail ego graph reuse**: **DONE**. Replaces the Phase 15
static `EmptyState` placeholder in `CandidateDetailPage`'s "Knowledge Graph" section
with a real graph scoped to that one candidate: the candidate's own node, only the
jobs it has a persisted `MatchResult` against, and those edges — no skills, no 3D,
no scoring recomputation, exactly as scoped.

- **Extraction (the "do only the minimum refactor" requirement)**: Phase 16's
  `GraphPage.jsx` held all of the Cytoscape element-building, stylesheet, click/
  highlight interaction, and detail-panel rendering inline. All of that moved,
  unchanged in behavior, into a new shared component, **`src/components/
  MatchGraph.jsx`** (+ `.module.css`) — the only refactor this task made, done
  because there are now genuinely two call sites, not in anticipation of a third.
  `MatchGraph` takes an already-fetched, already-filtered `{ jobs, edges }` object
  (`CandidateJobGraphResponse`'s own shape) and a `candidateNames` map; it makes no
  API call and recomputes no score/qualification value itself — identical to
  Phase 16's own rule, just relocated. Two props let each call site differ without
  forking the code: `size` (`"full"` | `"ego"`, a fixed CSS size variant — see
  below) and `showResetButton` (GraphPage instead calls `ref.resetView()` from its
  own `PageHeader` button, via `forwardRef`/`useImperativeHandle`, to keep that
  button in exactly the place it already was; the ego embed has no page-header
  action to reuse, so it renders its own small internal button).
- **`GraphPage.jsx`** (rewritten, much shorter): now owns only data-fetching
  (unchanged: `GET /v1/graph/candidates-jobs` + `GET /v1/candidates`) and page
  chrome (header, reset button, loading/error/empty states), rendering
  `<MatchGraph size="full" showLegend layout={FULL_GRAPH_LAYOUT} ... />`. Its own
  `.module.css` was trimmed to just page-level rules (`.page`, `.resetButton`,
  `.status`, `.error`) — every rule `MatchGraph` now owns (`.legend*`, `.panel*`,
  `.connectionList*`, `.chipGroup*`, etc.) was moved to `MatchGraph.module.css`
  verbatim.
- **`CandidateDetailPage.jsx`**: added `loadEgoGraph()` — calls the *same*
  `getCandidatesJobsGraph()` `GraphPage` already calls (no new backend endpoint, as
  required), then filters client-side: `edges.filter(e => e.candidate_id ===
  candidateId)`, then `jobs.filter(j => matchedJobIds.has(j.job_id))` — the same
  "filter an existing unscoped read" pattern this page already uses for `GET
  /v1/applications`. Candidate name resolution reuses the identity this page
  already fetches via `useCandidateIdentity` (`new Map([[candidateId,
  displayName]])`) rather than a second `GET /v1/candidates` call — a graph of one
  candidate only ever needs that one candidate's name. Renders
  `<MatchGraph size="ego" layout={EGO_GRAPH_LAYOUT} showResetButton ... />` in
  place of the old placeholder, with its own loading/error/empty ("No job matches
  yet") states matching the page's existing per-section pattern.
- **Simpler layout, not just a smaller canvas**: an ego graph is inherently a star
  (one candidate, a handful of jobs) — rather than reuse `cose`'s iterative
  force simulation at that trivial scale, `MatchGraph.jsx` exports a second layout
  constant, `EGO_GRAPH_LAYOUT`, using Cytoscape core's built-in `concentric` layout
  (no extra dependency) with the candidate node pinned to the center ring and job
  nodes to the outer ring — deterministic, and visually a clean star rather than a
  physics simulation settling over 2–4 nodes. `GraphPage`'s own `FULL_GRAPH_LAYOUT`
  (`cose`) is untouched.
- **Size variants over an arbitrary height prop**: `MatchGraph` takes `size="full"`
  (70vh/480px, 60vh/360px under 768px — identical numbers to Phase 16's original
  CSS) or `size="ego"` (a fixed 320px/220px, 260px/200px under 768px) as CSS
  classes, not an inline height. An inline style would have silently defeated the
  existing mobile-breakpoint media query (inline styles beat media queries
  regardless of specificity) — named size variants keep that responsive behavior a
  plain CSS rule for both call sites.
- **Verification**:
  - `npm run build`: 100 modules, 0 errors, both on host and inside a real Docker
    multi-stage rebuild of `recruitment-workflow` (bundle now ~677 kB / ~216 kB
    gzipped — a small further increase from `MatchGraph`'s own two exported layout
    constants and the `forwardRef`/`useImperativeHandle` wiring, not a new
    dependency).
  - Full RWS backend suite: **69/69 passed**, host and inside the rebuilt/recreated
    `csb-recruitment-workflow` container (unaffected — frontend-only change).
  - **Live production-serving verification** (same `curl`-against-port-8003
    technique as Phase 9/16): `/`, `/jobs`, `/candidates`, `/interviews`,
    `/reports`, `/upload`, `/graph`, and a real candidate-detail URL
    (`/candidates/a90600f8-...`) all returned real `200`s with the built bundle
    referenced correctly — confirming `/graph`'s own hard-refresh behavior is
    unchanged by the extraction.
  - **Live data correctness** (real recruiter JWT, real running stack): pulled the
    real `GET /v1/graph/candidates-jobs` (4 jobs, 26 edges) and `GET /v1/candidates`
    payloads and, as with Phase 16, fed them into a headless-Cytoscape script (the
    real bundled `cytoscape` package, `headless: true`) that reconstructs both
    `CandidateDetailPage.jsx`'s `loadEgoGraph()` filter and `MatchGraph.jsx`'s
    `buildGraphElements()` and runs them against a real candidate ("Amine Sridi")
    with **3 of the system's 4 real jobs matched** — chosen specifically because it
    has multiple matches *and* at least one job correctly excluded, proving the
    filter both includes and excludes correctly rather than trivially passing on a
    candidate matched to everything. All 12 checks passed: exactly that candidate's
    3 real edges and no others; exactly the 3 jobs those edges reference (the 4th
    real job correctly absent); the candidate node's label resolved from the
    identity-hook-backed map with no second `GET /v1/candidates` call; every edge's
    score/qualified/hard-gate value passed through unchanged from its real
    persisted `MatchResult`; the real `concentric` layout ran headlessly and placed
    the candidate node at a real, non-zero distance from every job node (a genuine
    star shape, not a degenerate stack); and a candidate with zero real matches
    produced a correctly empty `{ jobs: [], edges: [] }` (the `EmptyState` path).
    As with Phase 16, **no browser is available in this environment** — this is
    the same honest, real-library/real-data substitute for a literal click-through,
    not a claim that pixels were inspected.
  - `git status` after the pass showed exactly the expected files: `components/
    MatchGraph.jsx` + `.module.css` (new), `pages/GraphPage.jsx` +
    `.module.css` (trimmed to page-only chrome), `pages/CandidateDetailPage.jsx`
    (ego-graph section) — no route, no sidebar entry, no `package.json` change (no
    new dependency was needed), and no other existing page touched.

---

**Phase 18 — Knowledge graph 2D visual polish**: **DONE**. Everything below lives
entirely in `components/MatchGraph.jsx` + `.module.css` — no other file changed, no
new dependency, no backend call, no change to `GraphPage.jsx`/`CandidateDetailPage.jsx`.

- **Layout-stability investigation (done first, before any fix, per this task's own
  instruction)**: inspected `FULL_GRAPH_LAYOUT`/`EGO_GRAPH_LAYOUT` directly — neither
  sets `randomize`, and the installed `cytoscape` package's own `cose.mjs` source
  confirms its real default is already `randomize: false` (grepped, not assumed from
  docs). So the task's own hint was right to be skeptical of touching that option.
  **Reproduced the instability for real** with a headless-Cytoscape script (the real
  bundled `cytoscape` package) against real live `/v1/graph/candidates-jobs` data,
  using the *exact* construction sequence `react-cytoscapejs` itself uses in the
  browser (`cytoscape({headless:true})` with no elements, then `cy.add(elements)`,
  then `cy.layout(FULL_GRAPH_LAYOUT).run()` — not the `cytoscape({elements})`
  constructor shortcut, which turned out to have its own unrelated quirk and would
  have produced a misleading result). Five runs on identical real data produced five
  different final layouts.
  - **Real root cause, found by reading `cytoscape`'s own `cose.mjs` and
    `element.mjs` source, not guessed**: `buildGraphElements()` never set an initial
    `position` on any node, so every node defaulted to Cytoscape's own hardcoded
    `{x: 0, y: 0}` — every node in the graph starts at the exact same point. Cose's
    force-repulsion calculation (`nodeRepulsion()`) has its own internal fallback,
    entirely separate from the `randomize` option: whenever two nodes' centers are
    found to be exactly coincident, it calls `Math.random()` to pick an arbitrary
    separation direction so the physics simulation isn't stuck computing a
    force from a zero-length vector. With *every* node pair coincident on the first
    iteration, this random tie-break fires for all of them, on every run — a real
    bug, and specifically **not** the `randomize` option's doing, confirming the
    task's premise.
  - **Smallest supported fix, verified before landing**: `buildGraphElements()` now
    assigns each node a small, deterministic, distinct seed position (a point on a
    fixed-radius circle, placed by the node's own stable index in the array) before
    handing elements to Cytoscape. No two nodes are ever exactly coincident, so
    `nodeRepulsion()`'s random fallback is never triggered; cose's own iterative
    forces (unchanged) still do all the real layout work from there. **No layout
    *option* was touched** — `FULL_GRAPH_LAYOUT`/`EGO_GRAPH_LAYOUT` are byte-for-byte
    identical to before this pass. `concentric` (the ego graph's layout) was
    confirmed, by reading its source, to never read incoming node position at all,
    so this change has no effect there beyond removing an already-nonexistent risk.
  - **Verified fixed**, same real-data/real-construction-path method as the repro:
    5 runs on identical real live data now produce identical positions (within
    0.01px) for every node, every time.
- **Canvas surface**: `.canvasWrapper` (the actual Cytoscape canvas area, not the
  legend/toolbar rows above it) gets a subtle dot-grid via a plain CSS
  `radial-gradient` background — Cytoscape's own canvas is transparent, so this
  shows through behind it. Uses the existing `--color-border` token (the app's own
  subtlest visible-line color) rather than a new one-off value.
- **Degree-based node sizing**: each node's `data.degree` (its real persisted-match
  count, precomputed once in `buildGraphElements()` the same way `finalScore`
  already is for edges) drives its size via `mapData`, replacing the old fixed
  42×42/26×26 constants. A job matched to many candidates now visibly reads as more
  central than one with a single match. **Kind distinction preserved and
  reinforced, not replaced**: shape (round-rectangle vs. ellipse) and color
  (primary vs. accent) are completely unchanged, and the two size bands
  (jobs 34–58px, candidates 18–32px) were deliberately chosen not to overlap, so
  size is a third, always-consistent signal for "job vs. candidate," never a
  competing one. Degree is clamped to a fixed assumed 1–10 range via `mapData`, the
  same fixed-domain convention `finalScore` (0–1) already established — not a new
  pattern.
- **Subtle focus/reset transitions**: added `transition-property`/
  `transition-duration` (150ms)/`transition-timing-function` (`ease-out`) to the
  base `node`/`edge` stylesheet rules. This is a pure stylesheet addition — the
  `applyHighlight`/`resetView`/click-handler JS is completely untouched, still just
  adding/removing the same `.faded`/`.highlighted` classes; Cytoscape now animates
  *any* resulting style change (opacity, border, width) instead of snapping it
  instantly. As a side effect, this also gives the graph a brief, subtle settle-in
  on first paint — not asked for specifically, but a harmless byproduct of the same
  one-line stylesheet mechanism.
- **Edge score/qualification encoding**: untouched by design — the `finalScore` →
  width/opacity `mapData` expressions and the `edge[?qualified]`/`edge[!qualified]`
  boolean selectors are byte-for-byte the same as before this pass; only
  `transition-property` was added alongside them.
- **Verification**:
  - `npm run build`: 100 modules, 0 errors, both on host and inside a real Docker
    multi-stage rebuild of `recruitment-workflow` (bundle ~678 kB / ~216 kB gzipped
    — a ~0.6 kB increase from this pass's own code, not a new dependency).
  - Full RWS backend suite: **69/69 passed**, host and inside the rebuilt/recreated
    `csb-recruitment-workflow` container (unaffected — frontend-only change).
  - **Live production-serving verification** (real `curl` against port 8003, same
    technique as every prior graph phase): `/`, `/jobs`, `/candidates`,
    `/interviews`, `/reports`, `/upload`, `/graph`, and a real candidate-detail URL
    all returned real `200`s; the served JS/CSS bundle was fetched and confirmed to
    actually contain `mapData(degree` and the dot-grid's `radial-gradient` rule —
    not just a build that happened to succeed. Container logs showed only clean
    access-log lines for every request, no errors.
  - **Real-data headless Cytoscape checks** (no browser available in this
    environment, same honest substitute used for every prior graph phase): against
    the real live `/v1/graph/candidates-jobs` payload (4 jobs, 26 edges, real
    degrees `5, 7, 7, 7`) and a real ego graph (candidate "Amine Sridi," 3 of 4 real
    jobs), using the real bundled `cytoscape` package through the real
    `react-cytoscapejs` construction sequence: full-graph layout deterministic
    across 5 runs; ego-graph (`concentric`) layout still deterministic and
    unaffected by the new seed positions; higher-degree job nodes never rendered
    smaller than lower-degree ones; job/candidate size bands never overlapped
    (max candidate width 22.7px < min job width 44.7px); shapes unchanged
    (round-rectangle / ellipse); the qualified/disqualified boolean selectors still
    split the real 17/9 edges exactly; edge width still tracked the exact same
    `finalScore` mapping as before; and `closedNeighborhood()` on a real job node
    still returned exactly that job's real connected edges — the click/highlight
    machinery is unaffected by the stylesheet-only transition addition. 11/11
    checks passed.
  - `git status` after the pass showed exactly two files changed: `components/
    MatchGraph.jsx` and `components/MatchGraph.module.css` — no other file
    (including `GraphPage.jsx`, `CandidateDetailPage.jsx`, `package.json`, the
    router, or the sidebar) was touched.
- **Not done this pass, deliberately** (per the task's own explicit exclusions): no
  filters, no search, no 3D, no skill nodes, no backend/persistence change, no new
  dependency.

---

**Phase 19 — Interactive graph controls (legend filtering + score filter + node
search)**: **DONE**. Everything below lives in `components/MatchGraph.jsx` +
`.module.css`, plus three new props passed from `pages/GraphPage.jsx` — no other
file changed, no new dependency, no backend call, no new API call.

- **Interactive legend (full graph only)**: the existing Qualified/Disqualified
  legend swatches are now `<button>`s — clicking one hides that whole edge category.
  Per this task's own instruction ("keep legend as the control itself"), there are
  no separate checkboxes anywhere; the legend *is* the toggle. A toggled-off swatch
  dims (`opacity: 0.45`) so its current state is visible at a glance. The Job/
  Candidate swatches stay plain labels — there's no filter for node kind.
- **Minimum-score filter**: a native `<input type="range">` (0–100, step 5, no new
  dependency) hides edges below the chosen threshold. **Explicitly a display filter
  only** — it reads each edge's already-persisted `final_score` and never writes to,
  recomputes, or re-derives it; `qualified` is completely untouched by this control.
  This is stated in the code's own comments, not just here, precisely because
  CLAUDE.md's "the LLM/UI never decides qualified" boundary deserves to be
  unambiguous even for a client-side display concern that's obviously safe.
- **Node search (full graph only)**: a text input matching against the same
  `label` every node already carries (job title / candidate display name) — no new
  data, no new call. Selecting a result calls the *exact same* `applyHighlight()`
  function a click already uses (not a second, parallel implementation), populates
  the detail panel via the existing `buildNodeSelection()`, and additionally calls
  `cy.fit(node.closedNeighborhood(), 50)` to center the view on it — the one thing
  a plain click doesn't already do. Not present on the ego graph at all (see below).
- **How filters/search/click all combine**: `hiddenCategories` (legend) and
  `minScore` (slider) are read together in one `useEffect` that re-syncs a single
  `.filteredOut` class on every edge — an edge is hidden if it's in a toggled-off
  category **or** below the score threshold (a set union, verified against real
  data, not an accidental intersection). This effect only ever toggles a class on
  already-existing elements; it never calls `cy.add()` or `cy.layout()`, so it
  cannot disturb the Phase 18 deterministic layout. Search and click-to-highlight
  are unaffected by the filter state and vice versa — a filtered-out edge stays
  hidden even if its endpoint gets highlighted (nothing sets `display` back), and
  search can still find/focus a node whose edges are currently filtered out (search
  finds *nodes*, the filter hides *edges* — two independent concerns, deliberately
  not cross-gated).
- **Reset view — the explicit decision this task asked for**: "Reset view" resets
  **focus** (the current click/search selection, all highlight/fade classes, and
  the viewport) back to the whole graph. It **deliberately does not** clear the
  qualified/disqualified toggles or the score filter — those are treated as a
  sticky viewing preference the user set up on purpose, not part of "what am I
  currently looking at." This is stated explicitly in `resetView`'s own comment,
  not left implicit.
- **Ego graph unaffected, by construction, not by a special case**: `showScoreFilter`
  and `showSearch` both default to `false` on `MatchGraph`, and
  `CandidateDetailPage`'s real `<MatchGraph>` call passes neither (confirmed by
  reading that call site directly, and by an automated check — see Verification).
  There was no need to touch `CandidateDetailPage.jsx` at all for this task.
- **Verification**:
  - `npm run build`: 100 modules, 0 errors, both on host and inside a real Docker
    multi-stage rebuild of `recruitment-workflow` (bundle ~681 kB / ~217 kB gzipped
    — a ~2.8 kB increase from this pass's own code, not a new dependency).
  - Full RWS backend suite: **69/69 passed**, host and inside the rebuilt/recreated
    `csb-recruitment-workflow` container (unaffected — frontend-only change).
  - **Live production-serving verification** (real `curl` against port 8003, same
    technique as every prior graph phase): `/`, `/jobs`, `/candidates`,
    `/interviews`, `/reports`, `/upload`, `/graph`, and `/health` all returned real
    `200`s; the served bundle was fetched and confirmed to actually contain the
    `filteredOut` class name and the `searchResults` CSS class — not just a build
    that happened to succeed. Container logs showed only clean access-log lines.
  - **Real-data headless Cytoscape checks** (no browser available in this
    environment, same honest substitute used for every prior graph phase): pulled
    a fresh real `GET /v1/graph/candidates-jobs` (4 jobs, 26 edges — 17 qualified /
    9 disqualified — a different live count than earlier phases' fixture, refetched
    for this pass rather than reused stale) and `GET /v1/candidates` (6 real
    candidates), then ran the actual `buildGraphElements`/`applyHighlight`/filter-
    sync/search logic verbatim against them through the real bundled `cytoscape`
    package: toggling the legend's "qualified" swatch off hid exactly the real 17
    qualified edges and left all 9 disqualified ones visible; toggling both off hid
    all 26, toggling back on restored all 26; a real median score threshold (83%)
    hid exactly the 14 real edges below it and none above it; combining "hide
    disqualified" with that same threshold hid exactly the expected union (14, not
    a smaller intersection); searching real substrings of a real job title and a
    real candidate name ("Amine Sridi") found exactly those nodes, case-
    insensitively, with zero false matches on a nonsense term; selecting a search
    result highlighted exactly that job's real 5 connected edges via the identical
    `applyHighlight()` a click uses, then fit-to-neighborhood completed without
    error; simulating Reset view cleared all highlight/fade state while a still-
    active legend filter's hidden edges correctly stayed hidden (confirming the
    explicit reset-focus-not-filters decision); the full-graph layout remained
    deterministic across 3 runs (unchanged from Phase 18, confirming this pass
    didn't reintroduce instability); and a direct read of
    `CandidateDetailPage.jsx`'s own `<MatchGraph>` call confirmed neither
    `showScoreFilter` nor `showSearch` is passed there. **17/17 checks passed.**
  - `git status` after the pass showed exactly the expected files: `components/
    MatchGraph.jsx` + `.module.css` (the controls themselves) and
    `pages/GraphPage.jsx` (three new props on its existing `<MatchGraph>` call) —
    `CandidateDetailPage.jsx`, `package.json`, the router, and the sidebar were
    all untouched.
- **Not done this pass, deliberately** (out of scope per the task): no skill nodes,
  no 3D, no backend/persistence change, no new dependency, no "clear filters"
  button (Reset view intentionally leaves filters alone — see above), no filter-
  aware search (search and the edge filter are independent, as documented above).

---

**Phase 20 — Final navigation polish (cross-link audit fixes)**: **DONE**. Between
Phase 19 and this pass, a chat-response-only "final unified admin-dashboard UX
audit" (no code, no handoff update, per its own explicit scope) reviewed all eleven
production screens and found four concrete, cheap, real cross-link gaps — every
one of them a case of a page already holding the exact field needed (`job_id`,
`application_id`, `candidate_id`) and simply not linking it. This pass implements
those four, exactly as scoped, and nothing else from that audit's longer polish
list (the rest was explicitly lower-priority/optional, or explicitly recommended
*not* to change — see that audit's own findings if revisiting them later).

- **`ResumeUploadPage`**: the success screen's "Candidate ID" field was previously
  the only outcome shown — a dead-end UUID. Added a **"View Candidate"** link to
  `/candidates/{candidate_id}` (reusing `result.candidate_id`, already in state
  from the real `POST /v1/resumes` response — no new call) styled with the
  existing `.uploadButton` class, placed alongside "Upload another resume" in the
  existing `.actions` row. `.uploadButton` gained `text-decoration: none` +
  `display: inline-block` so the same class renders identically on a `<Link>` as
  it already did on a `<button>` — the only CSS behavior change, and harmless to
  the button's own existing look.
- **`ApplicationsPage`**: rows with a non-null `interview_assessment` now show a
  **"View Report"** link to `/reports/{application_id}` inside the same actions
  `<td>` that already holds Invite/Accept/Reject and already calls
  `event.stopPropagation()` at the cell level — so the new link needed no extra
  propagation handling of its own to avoid triggering the row's expand toggle.
  Brings this page to parity with `CandidateDetailPage`'s applications table,
  which already had this exact link.
- **`InterviewsPage` + `ReportsPage`**: the "Job" column, previously plain
  `{application.job_title}` text, is now a `<Link to={/jobs/${application.job_id}}>`
  — the same `job_id` `GlobalApplicationResponse` already carries, just not
  rendered as a link before. Each gets its own `onClick={(event) =>
  event.stopPropagation()}`, matching the exact pattern already used by the
  candidate-name link in the same row (both pages' rows are click-to-expand/
  click-to-navigate, so this was required to preserve that behavior, not optional).
- **`ReportDetailPage`**: already called `getAllApplications()` once, purely to
  resolve `candidateId` for the header link — it now also captures `job_id`/
  `job_title` off that *same* matched row into one more `useState` (`job`, holding
  `{jobId, jobTitle} | null`) and renders it as a link in the `.meta` row. Zero new
  API calls, per the task's explicit requirement — the data was already being
  fetched and simply not fully used.
- **New shared CSS class per file**: `ApplicationsPage.module.css`,
  `InterviewsPage.module.css`, `ReportsPage.module.css`, and
  `ReportDetailPage.module.css` each gained one `.inlineLink` rule — a byte-for-byte
  copy of `CandidateDetailPage.module.css`'s existing `.inlineLink` (primary color,
  no underline until hover). CSS Modules are scoped per file in this codebase (no
  cross-file style imports anywhere), so matching an existing visual language
  exactly, file-by-file, is what "reuse existing link/button styles" means in this
  architecture — not a new visual style, the same one already established.
- **Verification**:
  - `npm run build`: 100 modules, 0 errors, both on host and inside a real Docker
    multi-stage rebuild of `recruitment-workflow` (bundle ~682 kB / ~217 kB
    gzipped — a negligible increase from four small `<Link>`s and their CSS, no
    new dependency).
  - Full RWS backend suite: **69/69 passed**, host and inside the rebuilt/recreated
    `csb-recruitment-workflow` container (unaffected — frontend-only change, no
    backend file touched).
  - **Live verification against the real running stack**: real recruiter login,
    real `GET /v1/applications` (7 applications, 5 with a real
    `interview_assessment`) — confirmed a real sample row's `job_id`,
    `application_id`, and `candidate_id` are all present and match the exact
    template-literal field names used in each new `<Link>` (grepped the source
    directly: `` `/jobs/${application.job_id}` ``, `` `/jobs/${job.jobId}` ``,
    `` `/reports/${application.application_id}` ``,
    `` `/candidates/${result.candidate_id}` `` — all correct). Hard-refresh
    equivalents (`curl`) against the real production port 8003 for `/reports/
    {a real application_id}`, `/jobs/{a real job_id}`, `/candidates/{a real
    candidate_id}`, and every pre-existing route all returned real `200`s. The
    served bundle was confirmed to actually contain the new "View Report"/"View
    Candidate" strings. Container logs showed only clean access-log lines.
  - **Row click/expand behavior preserved**: confirmed by reading the actual
    `stopPropagation` placement in each file — `InterviewsPage`/`ReportsPage`'s new
    Job links each carry their own `onClick={(event) => event.stopPropagation()}`,
    identical to the pre-existing candidate-name link in the same row;
    `ApplicationsPage`'s new "View Report" link sits inside the actions cell whose
    `onClick` already stops propagation at the `<td>` level, the same reason the
    pre-existing Invite/Accept/Reject buttons never needed their own handler.
  - `git status` after the pass showed exactly the expected ten files: the JSX and
    `.module.css` for `ResumeUploadPage`, `ApplicationsPage`, `InterviewsPage`,
    `ReportsPage`, and `ReportDetailPage` — no other page, no shared component, no
    API module, no router, no sidebar, and no backend file touched.

---

**Phase 21 — V2 design system, app shell, and Dashboard**: **DONE**. A deliberate
visual-direction change (the user's own explicit instruction, not a continuation
of the prior audit trail) — a dark "AI recruitment command center" aesthetic,
scoped this pass to the persistent shell chrome and Dashboard only.

- **The one load-bearing architectural decision this whole phase depends on**:
  every un-migrated page (everything except Dashboard) renders its own plain text
  directly onto the implicit light body background, using `--color-text` (a dark
  color) for contrast — there is no wrapping "light surface" most of that text
  sits on. Repointing `--color-bg`/`--color-text`/etc. to dark values, or giving
  `Layout.module.css`'s `.content` its own dark background, would have made every
  one of those pages' plain headings/labels instantly illegible (dark-on-dark).
  So `styles/variables.css` gained a **second, fully additive `--v2-*` token
  set** — nothing under the original `--color-*` names was touched, removed, or
  repointed. `Layout.module.css`'s `.content` was **deliberately left alone**
  (still no background of its own) for the same reason. The result is an
  intentional, visible seam for now: dark `Sidebar`/`TopBar` chrome framing a
  light content area for every page except Dashboard (which paints its own dark
  panel over that same light area). This is the expected, disclosed state of a
  phased visual migration, not a bug — closing that seam page-by-page is exactly
  what "V2 visual migration — operational pages" (this phase's own `[NEXT]`) is
  for.
- **New dependency**: `motion` (^13.2.0, the current name/successor of
  `framer-motion`, same team/API family), imported via its `motion/react` entry
  point. Used for: the sidebar's shared-layout active-nav indicator (`layoutId`),
  page-entrance transitions (`Layout.jsx`, `AnimatePresence`), card entrance/hover
  (`StatTile`, Dashboard's panels), the animated KPI count-up (`animate()`), and
  small hover/tap feedback (`TopBar`'s logout button). No other new dependency.
- **`styles/variables.css`**: additive `--v2-*` block only (deep charcoal/navy
  surfaces, glass panel colors, a restrained cyan/violet accent pair, dark-tuned
  status colors, radii/blur/shadow/transition tokens) — see the file's own long
  comment for the full "why additive" reasoning, condensed above.
- **`styles/global.css`**: one new `@media (prefers-reduced-motion: reduce)` rule
  collapsing all CSS `transition`/`animation` durations app-wide — the safety net
  for CSS-driven motion (hover states, the aurora backdrop). `motion`-driven
  animations are JS, not CSS, so they don't get caught by this rule and instead
  each check `useReducedMotion()` individually (see below) — both layers exist
  because one media query can't reach into an animation library's own
  requestAnimationFrame loop.
- **`Sidebar.jsx`/`.module.css`**: full redesign, safe to do unconditionally
  because this component is entirely self-contained (own `--v2-*` colors, own
  classes, no page depends on its internals). Dark glass surface, a faint
  grid + single aurora bloom (`::before`, pure CSS, no image asset), a small
  gradient brand mark reusing the graph nav icon's own glyph (the app's one
  signature branding moment), and the active nav item is now a single shared
  `motion.span` (`layoutId="sidebarActivePill"`) that slides between items on
  navigation instead of each link drawing its own static highlight — the "nav
  active state" motion this phase's brief named specifically. Reduced-motion:
  the pill's spring transition collapses to `{ duration: 0 }` via
  `useReducedMotion()`.
- **`TopBar.jsx`/`.module.css`**: same self-contained redesign; the logout button
  is a `motion.button` with a small `whileHover`/`whileTap` scale, gated the same
  way.
- **`Layout.jsx`**: `.content`'s only change is what's *inside* it — `<Outlet />`
  is now wrapped in `<AnimatePresence mode="wait"><motion.div key={location.
  pathname}>` for a plain opacity/8px-offset page-entrance fade, applied at the
  shell level so **every route gets it for free**, including the ten pages this
  phase doesn't otherwise touch. `mode="wait"` (full exit before the new page
  enters) is deliberate while pages have such different layouts (a light 760px
  page next to Dashboard's dark full-bleed panel) — simultaneous enter/exit would
  visibly jump. One disclosed side effect, industry-standard for animated route
  transitions (Next.js App Router, Astro view transitions, etc. all do the same):
  keying on `location.pathname` means navigating between two different dynamic
  IDs on the same route (e.g. one job to another) now fully remounts that page
  instead of relying on its own `useEffect([id])` to refetch in place — strictly
  safer (guarantees no leftover state from the previous ID), at the cost of a
  brief loading flash on that specific navigation pattern, which this app's
  actual flows rarely exercise directly (job-to-job or candidate-to-candidate
  navigation always goes through an intermediate list page today).
- **`StatusBadge.jsx`/`.module.css` and `EmptyState.jsx`/`.module.css`**: both
  heavily reused by still-light pages (`ApplicationsPage`, `InterviewsPage`,
  `JobDetailPage`, `CandidateDetailPage`, `MatchGraph`, `AssessmentPanel`,
  `CandidatesPage`, `JobsListPage`, `GraphPage`, `ReportsPage` — grepped to
  confirm every call site before touching either file), so neither got its
  default look changed. Both gained one **additive, opt-in `variant="glass"`
  prop**, defaulting to `undefined` (renders exactly as before); Dashboard is the
  only caller that passes it. Verified every existing call site passes no
  `variant` at all, so every one of those ten pages renders pixel-identical to
  before this phase.
- **`StatTile.jsx`/`.module.css`**: grepped and confirmed `DashboardPage` is its
  *only* consumer, so — unlike StatusBadge/EmptyState — this one was redesigned
  directly in place, no variant needed. Now a glass card with a restrained
  cyan/violet top accent line (alternated per tile), a staggered entrance
  (`delay` prop, one `motion` moment), a hover lift, and an animated count-up
  number (`motion`'s imperative `animate()` tweening from the previous value to
  the new one) — the "KPI/stat transitions" this phase's brief named. Reduced
  motion skips straight to the final value instead of tweening.
- **`PageHeader.jsx` was not touched** — Dashboard no longer uses it (see below),
  and every other page still needs its current light-theme look unchanged.
- **`DashboardPage.jsx`/`.module.css`**: full rebuild into an asymmetric bento
  layout, using **only data this page already fetched before this pass** — no
  new API call, no invented number:
  - A bespoke hero (not the shared `PageHeader`) with a real one-line summary
    derived from already-fetched counts ("N candidates in the pool · M jobs
    posted"), omitted entirely while loading rather than showing a guessed zero.
  - Four `StatTile`s (unchanged counts: total candidates, total jobs, in
    pipeline, interviews completed) filling row 1 of a 4-column grid.
  - A "Pipeline by status" panel: a new segmented distribution bar
    (`StatusDistributionBar`) whose segment widths are computed directly from the
    same `statusCounts` array the legend below it renders as text — one
    calculation, two presentations, never a second/divergent number. A
    zero-count status renders no segment (not a zero-width sliver); an empty
    pipeline shows `EmptyState variant="glass"` instead of a blank bar.
  - "Recent activity", restyled, same real derivation as before (unchanged logic,
    only the visual treatment and its `EmptyState variant="glass"` empty case are
    new).
  - A "Knowledge Graph" teaser card (`GraphTeaser`): the "subtle visual
    connection to the graph feature" this phase's brief asked for — a small
    decorative node/edge glyph (echoing `MatchGraph`'s own candidate/job node
    styling) and a link to `/graph`. Deliberately shows **no number of its own**:
    Dashboard does not fetch graph data (no new API call), so there is nothing
    real to count here without one — the honest choice given this phase's own "no
    fake metrics" requirement, over fabricating or mis-scoping a figure.
  - Quick actions (Upload Resume / New Job / View Candidates) restyled as a
    gradient-filled primary + two glass secondary buttons, same three links as
    before, unchanged destinations.
- **Verification**:
  - `npm run build`: 504 modules (up from ~100 — `motion`'s own internal module
    graph, not new page count), 0 errors, both on host and inside a real Docker
    multi-stage rebuild of `recruitment-workflow`. Bundle grew from ~682 kB
    (~217 kB gzip) to **~823 kB (~264 kB gzip)** — a real, disclosed cost of
    adding a full animation library, not hidden; a legitimate trade for the
    motion this phase explicitly asked for, not incidental bloat.
  - Full RWS backend suite: **69/69 passed**, host and inside the rebuilt/
    recreated `csb-recruitment-workflow` container (unaffected — no backend file
    touched).
  - **Live production-serving verification** (real `curl` against port 8003,
    same technique as every prior phase): `/`, `/candidates`, `/upload`, `/jobs`,
    `/interviews`, `/reports`, `/graph`, and `/health` all returned real `200`s.
    The served JS bundle was fetched directly and confirmed to contain this
    phase's own authored strings (`sidebarActivePill`, `"Command Center"`, the
    graph-teaser copy) — not just a build that happened to succeed. The served
    CSS was confirmed to contain the new `--v2-*` tokens, the `feTurbulence`
    noise data-URI, the `prefers-reduced-motion` rule, and `backdrop-filter`.
  - **Live data correctness** (real recruiter login against the real running
    stack): real `GET /v1/candidates` (6), `GET /v1/jobs` (4), `GET
    /v1/applications` (7 — 3 still in pipeline, 2 interviews completed) — the
    exact same three calls Dashboard has always made, confirming this pass
    changed presentation only, not data. Container logs showed only clean
    access-log/external-call lines, no errors, no PII.
  - **Every existing `StatusBadge`/`EmptyState` call site** across the app was
    grepped and confirmed to pass no `variant` prop, so the "additive, not
    breaking" claim above is verified, not asserted.
  - **No visual/pixel inspection was possible** — no browser is available in this
    environment, the same disclosed gap named throughout this project's history
    (Phase 9, the knowledge-graph phases, etc.). Verification here is therefore
    build success + live data correctness + direct source confirmation of every
    class name referenced in JSX actually being defined in its `.module.css` +
    confirmation that no existing call site's behavior changed — not a claim that
    the redesign was seen rendered.
  - `git status` after the pass showed exactly the expected files: `package.json`/
    `package-lock.json` (the one new dependency), `components/{Sidebar,TopBar,
    StatTile,StatusBadge,EmptyState}.jsx` + their `.module.css`,
    `pages/{Layout,DashboardPage}.jsx` + `DashboardPage.module.css`, and
    `styles/{variables,global}.css` — no other page, no router, no API module,
    and no backend file touched.
- **Not done this pass, deliberately** (per the task's own scope): no other page
  redesigned, no custom webfont added (a well-tuned system-font type scale was
  judged sufficient for "stronger typography" without adding a network dependency
  a live demo venue might not have reliably — see §21), no light/dark toggle (this
  is a one-way visual-direction change, not a themeable mode), no change to
  `--sidebar-width` or the shell's grid structure.

---

**Phase 22 — V2 visual migration, slice 1 (Applications / Interviews / Reports /
Report Detail)**: **DONE**. First slice of Phase 21's own `[NEXT]`
("V2 visual migration — operational pages"), picked up on a bare "continue" with
no further instruction — the scope decision below is this session's own, made
using the guidance Phase 21 already left in §24, not a re-litigation of it.

- **Why these four pages, together, as the first slice**: `ReportsPage` links
  directly into `ReportDetailPage` (`/reports/:id`) — migrating one without the
  other would swap *which* page has the dark/light seam rather than closing it,
  and specifically make it worse in the one case that's an actual drill-down
  (list → detail) rather than a sidebar jump between unrelated sections.
  `ApplicationsPage` and `InterviewsPage` were folded into the same slice because
  they already share nearly all of their visual language with `ReportsPage`
  (identical table/row/status-badge structure) and both use `AssessmentPanel`,
  whose only three consumers are exactly these three pages — migrating them
  separately would have meant restyling `AssessmentPanel` (or worse, giving it an
  opt-in variant it will never need a light-theme fallback for) more than once.
- **`PageHeader.jsx`/`.module.css`**: gained the same additive, opt-in
  `variant="glass"` pattern Phase 21 already proved on `StatusBadge`/
  `EmptyState` — every page not in this slice passes no `variant` and is
  unaffected. `.title` itself needed no override at all: it has never set its own
  color, only ever inherited it from whatever page container it sits in, which is
  exactly why it "just works" once a migrated page's own `.page` class sets
  `color: var(--v2-text)` — only `.subtitle` (which does hard-code a color)
  needed the variant's own rule.
- **`AssessmentPanel.jsx`/`.module.css`**: grepped and confirmed its *only* three
  consumers (`ApplicationsPage`, `InterviewsPage`, `ReportDetailPage`) are all
  migrated in this same pass, so — like `StatTile` in Phase 1 — this is a direct
  redesign in place, not a variant; there is no remaining light-theme call site
  to preserve. Its own internal `StatusBadge` call now also passes
  `variant="glass"`.
- **`ApplicationsPage`/`InterviewsPage`/`ReportsPage`/`ReportDetailPage`**: each
  gained the same "V2 page panel" treatment already established by
  `DashboardPage.module.css` in Phase 1 (an opaque dark background, border,
  radius, and shadow, `color: var(--v2-text)`, plus the identical aurora/grid/
  noise `::before`/`::after` backdrop) — safe for the same reason as Dashboard: each
  page paints its own dark surface *over* the still-untouched light area
  `Layout.module.css`'s `.content` would otherwise show, rather than requiring
  `.content` itself to change. **This exact block is now duplicated across five
  files** (Dashboard + these four) — flagged explicitly in each file's own
  comment as the point a shared global utility class would normally get
  extracted, per this project's "promote once real duplication is observed"
  rule; deliberately not done yet in this pass (see Not done, below). Every table,
  status badge, link, and action button in all four pages was re-tinted to
  `--v2-*` tokens, preserving every existing semantic distinction (invite=
  gradient primary, accept=success, reject=danger-outlined, candidate/job links=
  cyan) — grepped afterward to confirm zero remaining `var(--color-*)`
  references in any of the five touched CSS files.
  - `ReportDetailPage`'s native `<audio>` recording player gained
    `color-scheme: dark`, hinting supporting browsers (Chromium/Firefox) to
    render its own native chrome (play button, scrubber, volume) in a
    dark-appropriate palette — a one-line, zero-risk improvement, not a full
    custom-player rebuild (which would be well past this pass's own scope for
    one `<audio>` element).
- **Verification**:
  - `npm run build`: 504 modules, 0 errors, both on host and inside a real Docker
    multi-stage rebuild of `recruitment-workflow`. Bundle grew only in CSS
    (~50 kB → ~55 kB / ~8.4 kB gzipped) — JS was essentially unchanged, as
    expected: this pass only added a `variant` prop and retinted existing CSS,
    it introduced no new component logic or dependency.
  - Full RWS backend suite: **69/69 passed**, host and inside the rebuilt/
    recreated `csb-recruitment-workflow` container (unaffected — no backend file
    touched).
  - **Live production-serving verification** (real `curl` against port 8003,
    same technique as every prior phase): `/`, `/candidates`, `/upload`, `/jobs`,
    `/interviews`, `/reports`, `/graph`, and `/health` all returned real `200`s.
    The served CSS was confirmed to contain `color-scheme:dark` and 35 real
    usages of `--v2-cyan`; the served JS was confirmed to contain
    `InterviewsPage`'s own real subtitle text.
  - **Live data correctness** (real recruiter login against the real running
    stack): real `GET /v1/applications` returned 7 applications, 5 with a real
    `interview_assessment` (reportable); a real `/reports/{a real
    application_id}` hard-refresh returned a real `200`. Container logs showed
    only clean access-log lines, no errors, no PII.
  - `git status` after the pass showed exactly the expected files:
    `components/{AssessmentPanel,PageHeader}.jsx` + their `.module.css`, and
    `pages/{ApplicationsPage,InterviewsPage,ReportsPage,ReportDetailPage}.jsx` +
    their `.module.css` — no other page, no router, no API module, no shared
    component beyond the two named above, and no backend file touched.
  - **No visual/pixel inspection was possible**, same disclosed environment gap
    as Phase 21 and every graph phase before it — verification here is build
    success + live data correctness + a direct grep confirming zero leftover
    `--color-*` references in any touched CSS file, not a claim that the
    redesign was seen rendered.
- **Not done this pass, deliberately**: the six remaining pages
  (`CandidatesPage`/`CandidateDetailPage`/`ResumeUploadPage`/`JobsListPage`/
  `JobDetailPage`/`GraphPage`) — left for later slices, per Phase 21's own "one
  page or small related group at a time" guidance; the "V2 page panel"
  background block was **not** extracted into a shared utility despite now
  appearing five times — explicitly flagged as the point that would normally
  happen, not done reflexively in the same pass as using it a fifth time; no
  change to `--v2-*` token values themselves.

---

**Phase 23 — V2 visual migration, slice 2 (Job Detail / Candidate Detail)**:
**DONE**. Second slice of Phase 21's own `[NEXT]`, picked up on a bare
"continue" — the scope decision below follows the guidance Phase 22 itself left
in §24, not a fresh re-derivation.

- **Why these two pages, specifically, next**: Phase 22 migrated Applications/
  Reports without their still-light parent pages, which meant `JobDetailPage`
  (still V1) linked into the now-V2 `ApplicationsPage`, and `CandidateDetailPage`
  (still V1) linked into the now-V2 `ReportDetailPage` — two *new* dark/light
  drill-down seams, called out explicitly in Phase 22's own §24 entry. This
  slice closes both by migrating the two pages that were the light side of each
  seam, rather than starting with the list pages that don't yet have this
  specific problem.
- **`JobDetailPage`/`.module.css`**: same "V2 page panel" treatment as every
  Phase 22 page, `PageHeader`/`StatusBadge`/`EmptyState` all flipped to
  `variant="glass"`. The Select button's solid primary color became the same
  cyan→violet gradient treatment `DashboardPage`'s primary quick-action already
  uses, and the score bar's fill became the same gradient — the one place this
  slice introduced a new visual choice rather than a straight re-tint, chosen to
  keep the gradient accent consistent with its other "primary/affirmative
  action" appearances elsewhere in V2 rather than inventing a third color
  treatment for the same semantic role.
- **`CandidateDetailPage`/`.module.css`**: same page-panel treatment; the
  Applications table (with its Job/Report links) and Professional Knowledge
  section fully re-tinted. **Its embedded `MatchGraph` ego graph (the "Knowledge
  Graph" section) was deliberately left untouched** — `MatchGraph.jsx`'s
  `buildStylesheet()` reads real computed `--color-*` values, and `GraphPage`
  (its other call site) is still V1/light; giving `MatchGraph` a V2 theme here
  without also handling `GraphPage` would need the same opt-in-variant pattern
  already used for `StatusBadge`/`EmptyState`/`PageHeader`, and doing that
  properly is exactly the "`MatchGraph`/`GraphPage` deserve extra care" work
  Phase 22's own §24 entry already named as its own future slice — not
  appropriate to do piecemeal, half inside this page's own migration. A comment
  at the actual `<MatchGraph>` call site in `CandidateDetailPage.jsx` explains
  this at the point a future reader would actually wonder about it. Net visible
  effect: that one embedded widget currently renders as a light card inside an
  otherwise-dark section — a small, contained, consciously-accepted extension
  of the project's existing disclosed seam, not an oversight.
- **Verification**:
  - `npm run build`: 504 modules, 0 errors, both on host and inside a real Docker
    multi-stage rebuild of `recruitment-workflow`. Bundle grew only in CSS
    (~55 kB → ~58 kB / ~8.4 kB gzipped) — JS was essentially unchanged, same
    reasoning as Phase 22 (prop/CSS changes only, no new component logic).
  - Full RWS backend suite: **69/69 passed**, host and inside the rebuilt/
    recreated `csb-recruitment-workflow` container (unaffected — no backend file
    touched).
  - **Live production-serving verification**: `/`, `/candidates`, `/upload`,
    `/jobs`, `/interviews`, `/reports`, `/graph`, and `/health` all returned real
    `200`s against the real production port 8003. The served CSS was confirmed
    to contain 43 real usages of `--v2-cyan` (up from 35 after Phase 22).
  - **Live data correctness** (real recruiter login against the real running
    stack): a real `GET /v1/jobs` + `GET /v1/jobs/{id}/matches` (7 real matches
    for a real job) and a real `GET /v1/candidates`, followed by real
    hard-refresh `200`s on `/jobs/{that real job_id}` and `/candidates/{a real
    candidate_id}`. Container logs showed only clean access-log/external-call
    lines, no errors, no PII.
  - `git status` after the pass showed exactly the expected files:
    `pages/{JobDetailPage,CandidateDetailPage}.jsx` + their `.module.css` — no
    other page, no shared component, no router, no API module, and no backend
    file touched.
  - **No visual/pixel inspection was possible**, same disclosed environment gap
    as every prior V2 phase — verification here is build success + live data
    correctness + a direct grep confirming zero leftover `--color-*` references
    in either touched CSS file, not a claim that the redesign was seen rendered.
- **Not done this pass, deliberately**: `MatchGraph`'s own theming (see above —
  explicitly deferred to its own future slice, not started here); the four
  remaining pages (`CandidatesPage`/`ResumeUploadPage`/`JobsListPage`/
  `GraphPage`); the "V2 page panel" background block, now duplicated across
  seven files, was still not extracted into a shared utility — flagged again,
  still deliberately not done (see §24 for the actual threshold judgment call).

---

**Phase 24 — V2 visual migration, slice 3 (Jobs List / Candidates / Resume
Upload)**: **DONE**. Third slice of Phase 21's own `[NEXT]`, picked up on a bare
"continue" — the scope decision below follows the guidance Phase 23 itself left
in §24.

- **Why these three pages**: `JobsListPage`'s job cards link into
  `JobDetailPage`, and `CandidatesPage`'s rows link into `CandidateDetailPage` —
  both already V2/dark since Phase 23, so both list pages were the light side of
  the exact same "list → already-dark detail" seam pattern that motivated Phase
  23's own scope choice. `ResumeUploadPage` doesn't have that seam pressure
  either way (it's a standalone dead-end-free flow, not a list linking
  anywhere) but was small enough to fold into the same pass as a third page
  rather than spend a whole separate slice on it.
- **`JobsListPage`/`CandidatesPage`**: same "V2 page panel" treatment and
  `PageHeader`/`EmptyState` `variant="glass"` flip as every prior slice. The
  "New Job" button and form's submit button adopted the same cyan→violet
  gradient already used for every other primary/affirmative action in V2
  (Dashboard's quick action, Job Detail's Select button) — consistent
  treatment, not a new one invented for this page.
- **`ResumeUploadPage`**: same treatment on both of its render paths (the
  drop-zone view and the post-upload success view). While rewriting this file's
  CSS, a **real pre-existing bug** was fixed as a byproduct, not a separate
  task: `.resetButton` ("Upload another resume") still carried a standalone
  `margin-top` left over from before the navigation-polish pass (a few sessions
  ago) moved it into the same `.actions` flex row as the "View Candidate"
  button — with both as flex-row siblings, that leftover margin was pushing
  `.resetButton` down and out of alignment with `.uploadButton` beside it. Fixed
  by removing the stray margin (the row's own `.actions { margin-top }` already
  provides the correct spacing above the whole row) — this was a real, visible,
  latent bug independent of the V2 migration itself, caught only because this
  file's CSS was being read in full anyway.
- **Verification**:
  - `npm run build`: 504 modules, 0 errors, both on host and inside a real Docker
    multi-stage rebuild of `recruitment-workflow`. Bundle grew only in CSS
    (~58 kB → ~62 kB / ~8.5 kB gzipped) — JS unchanged, same reasoning as every
    prior V2 slice (prop/CSS changes only).
  - Full RWS backend suite: **69/69 passed**, host and inside the rebuilt/
    recreated `csb-recruitment-workflow` container (unaffected — no backend file
    touched).
  - **Live production-serving verification**: `/`, `/candidates`, `/upload`,
    `/jobs`, `/interviews`, `/reports`, `/graph`, and `/health` all returned real
    `200`s against the real production port 8003. The served CSS was confirmed
    to contain 57 real usages of `--v2-cyan` (up from 43 after Phase 23).
  - **Live data correctness** (real recruiter login against the real running
    stack): real `GET /v1/jobs` (4 real jobs) and `GET /v1/candidates` (7 real
    candidates) both returned real `200`s. Container logs showed only clean
    access-log/external-call lines, no errors, no PII.
  - `git status` after the pass showed exactly the expected files:
    `pages/{JobsListPage,CandidatesPage,ResumeUploadPage}.jsx` + their
    `.module.css` — no other page, no shared component, no router, no API
    module, and no backend file touched.
  - **No visual/pixel inspection was possible**, same disclosed environment gap
    as every prior V2 phase — verification here is build success + live data
    correctness + a direct grep confirming zero leftover `--color-*` references
    in any of the three touched CSS files, not a claim that the redesign was
    seen rendered. The `.resetButton` alignment fix in particular could not be
    visually confirmed either — it's a correct-by-inspection CSS reasoning fix
    (the exact mechanism of the misalignment was traced through the cascade),
    not something seen rendered before or after.
- **Not done this pass, deliberately**: `GraphPage`/`MatchGraph` (the one
  remaining piece — its own real design decision, named in §24, not started
  here); the "V2 page panel" background block, now duplicated across ten files,
  was still not extracted into a shared utility.

**Phase 25 (V2 visual migration, final slice — `GraphPage`/`MatchGraph`)**:
**DONE**. Picked up on a bare "continue" using the guidance Phase 24 left in
§24 — the last remaining piece of the V2 migration.

- **Why this needed a real design decision, not just a CSS re-tint**:
  `MatchGraph.jsx` draws to a Cytoscape `<canvas>`, which cannot read CSS
  classes — its `buildStylesheet()` function reads real computed CSS custom
  property values via `getComputedStyle(document.documentElement)` (through a
  local `readToken(style, name, fallback)` helper) at stylesheet-build time.
  Re-theming it means changing *which token names* that function reads, not
  adding a class.
- **Simpler approach used than the prior `[NEXT]` guidance speculated**: that
  guidance (written at the end of Phase 24) proposed adding a `theme`/`variant`
  param to `buildStylesheet()` so `GraphPage` and `CandidateDetailPage`'s ego
  graph could each independently choose V1 or V2 colors — modeled on the
  `StatusBadge`/`EmptyState`/`PageHeader` `variant="glass"` opt-in pattern.
  During implementation this turned out to be unnecessary complexity: this same
  pass migrates `GraphPage` itself, so both of `MatchGraph`'s real call sites
  (`GraphPage`, `CandidateDetailPage`'s ego graph) land on V2 together in this
  one pass, leaving no V1 consumer that would ever take the toggle's other
  branch. `buildStylesheet()` was instead redesigned directly in place to read
  `--v2-*` token names unconditionally — the same "direct redesign, not an
  opt-in variant" pattern already established for `StatTile`/`AssessmentPanel`
  once all real consumers were confirmed migrating together (see Phase
  21/22). This reasoning is recorded in a code comment at `buildStylesheet()`
  itself, not just here.
- **`MatchGraph.jsx`**: `buildStylesheet()`'s `color` object now reads 8
  `--v2-*` tokens (`--v2-cyan`, `--v2-violet`, `--v2-success`, `--v2-danger`,
  `--v2-warning`, `--v2-bg-elevated`, `--v2-text`, `--v2-text-muted`) instead of
  their `--color-*` equivalents; all 4 internal `<StatusBadge>` call sites
  (the search-result button, 3 in `DetailPanel`) now pass `variant="glass"`.
  `buildGraphElements()`, `buildNodeSelection()`/`buildEdgeSelection()`, the
  position-seeding logic, `FULL_GRAPH_LAYOUT`/`EGO_GRAPH_LAYOUT`, and every
  click/highlight/reset/filter/search handler are byte-for-byte unchanged —
  only the token names read for color and the `StatusBadge` props changed.
- **`MatchGraph.module.css`**: full direct redesign to `--v2-*` tokens (canvas
  dot-grid background, legend/legend-toggle, score filter/search box and
  results dropdown, toolbar/reset button, detail panel and its labels/values/
  connection list/matched-missing chips). Structure, class names, and the
  mobile media query are unchanged — only colors/tokens.
- **`GraphPage.jsx`**: `<PageHeader>` and `<EmptyState>` now pass
  `variant="glass"`, matching every other migrated page. No change to
  `load()`/data-fetching (`getCandidatesJobsGraph()`, `getCandidates()`) or any
  other logic.
- **`GraphPage.module.css`**: full "V2 page panel" treatment identical in
  structure to the other nine migrated pages (`.page` background/border/
  shadow/radius, `::before` aurora+grid pseudo-element, `::after` noise
  pseudo-element, `.resetButton`/`.status`/`.error` re-tinted).
- **`CandidateDetailPage.jsx`**: the `<MatchGraph>` call site's old "deliberately
  NOT restyled" comment (from Phase 23, explaining why the ego graph stayed on
  V1 colors while the rest of the page was V2) was replaced with a short comment
  noting the theme now lives entirely inside `MatchGraph.jsx` itself and this
  call site needed no prop change. No prop changes to the actual `<MatchGraph>`
  call.
- **Verification**:
  - `npm run build`: 504 modules, 0 errors, CSS 63.22 kB (8.50 kB gzip), JS
    823.50 kB (263.98 kB gzip) — JS size unchanged from Phase 24 (no new
    dependency; CSS/prop/token changes only).
  - `grep -n "var(--color-" src/pages/GraphPage.module.css
    src/components/MatchGraph.module.css src/components/MatchGraph.jsx` →
    zero matches, confirming no leftover V1 token references anywhere in the
    three touched files.
  - Cross-checked all 8 `--v2-*` token names referenced in `MatchGraph.jsx`'s
    `readToken()` calls against `variables.css` — each confirmed defined
    exactly once (catches a silent-fallback-to-hardcoded-default bug that a
    misspelled token name would otherwise hide, since `readToken()` has no
    other way to signal a miss).
  - Full RWS backend suite, inside a real rebuilt/recreated
    `csb-recruitment-workflow` container: **69/69 passed** (unaffected — no
    backend file touched).
  - **Live production-serving verification**: `/`, `/candidates`, `/upload`,
    `/jobs`, `/interviews`, `/reports`, `/graph`, and `/health` all returned
    real `200`s against the real production port 8003. The served CSS was
    confirmed to contain 65 real usages of `--v2-cyan` (up from 57 after Phase
    24) and 6 of `--v2-bg-elevated`; the one remaining `var(--color-*)` line in
    the served CSS belongs to `LoginPage`, the pre-auth screen, which was never
    in scope for this migration (only the eleven post-login operational
    screens were) — confirmed by inspecting that line's actual selector
    context, not assumed.
  - **Live data correctness** (real recruiter login against the real running
    stack): real `GET /v1/graph/candidates-jobs` and `GET /v1/candidates` both
    returned real `200`s with real data — 4 jobs, 26 edges, 7 candidates, 17
    qualified / 9 disqualified matches. Container logs showed only clean
    access-log lines, no errors, no PII.
  - **Headless Cytoscape structural re-verification**, using the real bundled
    `cytoscape` package against this same real live data (mirroring the exact
    unchanged `buildGraphElements()` logic and the post-slice
    `buildStylesheet()`'s selector/mapping logic, with literal hex values
    standing in for the `getComputedStyle()` read that itself needs a real
    browser): full-graph `cose` layout still deterministic across 3 runs;
    `edge[?qualified]`/`edge[!qualified]` still split the real 17/9 edges
    exactly; job/candidate node shapes and non-overlapping degree-based size
    bands intact; a real job's `closedNeighborhood()` still returns exactly its
    real edges (5, for the sampled job); a real multi-job candidate's ego
    (`concentric`) layout still deterministic; edge width still tracks the same
    `finalScore` → `[1,7]` mapping. All 8 checks passed — this logic was never
    touched by this slice, but re-verifying closes the loop honestly rather
    than assuming.
  - `git status --porcelain` after the pass showed exactly the expected files
    touched for this slice: `components/MatchGraph.jsx` +
    `MatchGraph.module.css`, `pages/GraphPage.jsx` + `GraphPage.module.css`,
    `pages/CandidateDetailPage.jsx` — no other page, no backend file. (The
    working tree also still shows every other un-committed V2 phase's files
    modified, per this project's practice of not committing until the user
    asks.)
  - **No visual/pixel inspection was possible**, same disclosed environment gap
    as every prior V2 phase — verification here is build success + live data
    correctness + headless structural proof + direct token-existence
    cross-checks, not a claim that the dark Cytoscape canvas colors were seen
    rendered.
- **Milestone**: this closes the dark/light seam across the **entire**
  application — all eleven production screens are now on the V2 "AI
  recruitment command center" look; no page anywhere still renders the
  original V1 light theme. The "V2 page panel" background block (the
  aurora/grid `::before` + noise `::after` pseudo-element pair) is now
  duplicated across all ten migrated page CSS files (`DashboardPage` plus the
  nine operational pages; `DashboardPage.module.css` in fact has it twice, once
  per bento panel) — still not extracted, now explicitly the token-fold task's
  job (see §24), not a new deferral.

**Phase 26 (3D Knowledge Graph presentation mode)**: **DONE, with an explicit
manual-browser-verification gap** (see §21). Reverses the "Optional 3D
presentation mode — NO-GO for now" decision recorded between Phase 19 and 20
(§24 still preserves that original note, now annotated as superseded) — the
user's own instruction named the specific fix for the reason it was rejected:
use `3d-force-graph` directly instead of `react-force-graph`.

- **Dependency check, before writing any code**: `npm view 3d-force-graph
  dependencies` → `{ accessor-fn, kapsule, three, three-forcegraph,
  three-render-objects }` — no AR/VR package, no git+SSH transitive dependency
  (the two specific problems the original prototype found in
  `react-force-graph`). Installed `3d-force-graph@^1.80.0` and `three@^0.180.0`
  (added as an explicit direct dependency, not left implicit, so npm hoists one
  single copy of `three` — required for `nodeThreeObject`'s custom `THREE.Mesh`
  instances to be recognized by 3d-force-graph's own internal `instanceof
  THREE.Object3D` checks; confirmed only one `node_modules/three` directory
  exists after install). `npm audit` after install showed 4 pre-existing
  vulnerabilities (`esbuild`/`vite` dev-server, `react-router`) — none
  attributable to the two new packages, which appear nowhere in the audit
  output.
- **A real, install-time API surface check, not assumed from memory**: read
  the installed package's own `.d.ts` files directly
  (`node_modules/3d-force-graph/dist/3d-force-graph.d.ts` and
  `node_modules/three-forcegraph`'s) before writing `MatchGraph3D.jsx`. This
  caught one real mistake before it shipped: v1.x's actual constructor is
  `new ForceGraph3D(domElement)` — not the older Kapsule-style
  `ForceGraph3D()(domElement)` double-call some pre-1.0 tutorials still show —
  confirmed against the installed package's own README usage example, not a
  changelog assumption. Also directly confirmed (not assumed) against the
  bundled `d3-force-3d@3.0.6` source: `forceManyBody().strength()` and
  `forceLink().distance()` both accept a per-node/per-link accessor function
  (not just a constant), and `forceCenter().strength()` exists in this bundled
  version — all three are used by this component's own force-tuning (see
  below), and a wrong assumption here would have thrown at runtime in a
  browser with no build-time signal at all.
- **Architecture, per this task's own instructions**:
  - `components/MatchGraph3D.jsx` (new) — the entire Three.js/3d-force-graph
    scene, reached only via `React.lazy()` from `GraphPage.jsx`. No Three.js
    import exists anywhere in `MatchGraph.jsx`.
  - `components/GraphDetailPanel.jsx` (new) — `MatchGraph.jsx`'s previously
    inline `DetailPanel`/`RequirementChips` extracted verbatim (byte-for-byte
    same JSX/logic, same `MatchGraph.module.css` classes) so both the 2D and
    3D renderer share one detail-panel implementation. It renders a plain
    `{type: "node"|"edge", ...}` object — it has never touched Cytoscape
    itself — so 3D's own selection builders (`buildNode3DSelection`/
    `buildEdge3DSelection` in `MatchGraph3D.jsx`) just need to produce the same
    shape from plain force-graph node/link objects instead of Cytoscape
    elements.
  - `components/GraphControls.jsx` (new) — the legend/score-filter/search bar,
    extracted from `MatchGraph.jsx` (previously gated behind
    `showLegend`/`showScoreFilter`/`showSearch` props only `GraphPage` ever
    set — `CandidateDetailPage`'s ego graph never did, so removing them from
    `MatchGraph.jsx` changes nothing for that call site) and lifted to
    `GraphPage`, which now owns `hiddenCategories`/`minScore`/`searchTerm`
    state directly. This is the "small extraction from GraphPage" the task's
    own spec allowed for — without it, switching from 2D to 3D would unmount
    the 2D `MatchGraph` (and its internal filter state with it), silently
    resetting "qualified only"/the score floor/search on every view switch.
  - `MatchGraph.jsx` changes: removed the legend/filter/search props, state,
    and JSX (moved above); `hiddenCategories`/`minScore` are now controlled
    props with safe defaults (`{qualified: false, disqualified: false}` / `0`)
    so `CandidateDetailPage`'s ego-graph call site needs zero changes; the
    imperative handle gained `focusNode(nodeId)` (renamed from the old
    internal `handleSelectSearchResult`, same logic) alongside the existing
    `resetView()`, so `GraphPage`'s search box can drive either renderer
    identically; added the new pure export `buildGraph3DElements()`, which
    reshapes `buildGraphElements()`'s own output into 3d-force-graph's
    `{nodes, links}` shape — same id-prefixing (`job:`/`candidate:`), same
    precomputed `degree`, same field names on edges — so the two renderers can
    never drift into different data semantics. `buildGraphElements()`,
    `buildNodeSelection()`/`buildEdgeSelection()`, `buildStylesheet()`, and
    every Cytoscape click/highlight/layout handler are byte-for-byte
    unchanged.
  - `GraphPage.jsx`: added `viewMode` state (`"2d"` default), the `2D | 3D`
    segmented switch in the header actions, lifted filter/search state (above),
    and `const MatchGraph3D = lazy(() => import("../components/
    MatchGraph3D.jsx"))` wrapped in `<Suspense>` — only mounted when
    `viewMode === "3d"`. Both renderers are given the `key="2d"`/`key="3d"`
    React key so switching between them is a real unmount/mount (not a prop
    update), which is what makes `MatchGraph3D`'s own cleanup effect
    (`Graph._destructor()`) actually run when leaving 3D, and guarantees 3D's
    `useEffect`-based scene setup always starts fresh rather than trying to
    reuse a stale WebGL context.
- **3D scene design** (`MatchGraph3D.jsx`), reusing real design tokens read via
  the same `getComputedStyle()` pattern `MatchGraph.jsx`'s `buildStylesheet()`
  already established (not a second, hardcoded palette):
  - Candidate nodes: small `THREE.SphereGeometry` spheres (`--v2-violet`). Job
    nodes: larger, visually distinct `THREE.IcosahedronGeometry` (`--v2-cyan`)
    — deliberately a different geometry, not just a bigger sphere, so job vs.
    candidate reads at a glance. Both use `MeshPhongMaterial` with an
    `emissive` channel plus a second, larger, transparent
    `MeshBasicMaterial` shell behind each mesh as a cheap glow/bloom stand-in
    — no post-processing pipeline added, per the task's own constraint.
  - `Graph.scene().fog = new THREE.FogExp2(...)` (atmospheric depth) and two
    plain lights (`AmbientLight`, `PointLight`) added directly via `Graph.
    scene()` — all stock three.js primitives already present as a
    3d-force-graph dependency, nothing extra installed for this.
  - Qualified links render brighter/more saturated (`--v2-success` at higher
    alpha) with 2 flowing `linkDirectionalParticles`; disqualified links render
    muted/red-tinted (`--v2-danger` at low alpha), no particles. Link width and
    both links' base opacity are driven by the persisted `final_score` via the
    same `mapRange()` helper used for node radius — never recomputed, only
    reshaped for the accessor `3d-force-graph` expects.
  - Force tuning for the small real dataset (a handful of jobs, a few dozen
    edges): `d3Force('charge')` gives job nodes far stronger repulsion
    (`-260`) than candidates (`-90`) so jobs spread out and act as anchors;
    `d3Force('link').distance()` pulls a candidate closer to a job the higher
    their `final_score`; `cooldownTicks(220)` gives the small graph a bounded
    settle time, after which `onEngineStop` auto-frames the camera
    (`zoomToFit`) to whatever composition resulted. This is a first,
    reasoned-but-unverified pass — see the `[NEXT]` in §24 for the actual
    visual tuning this needs once someone can see it render.
  - Hover uses the library's built-in `nodeLabel`/`linkLabel` tooltip plus a
    cursor swap; click focuses the camera via the library's own documented
    "move camera toward a node" recipe (`cameraPosition` with a
    distance-ratio offset) and dims everything outside the clicked node's
    neighborhood by reassigning `nodeThreeObject`/`linkColor` with the current
    focus set baked in; clicking the background clears it. Search (via the
    shared `GraphControls`) calls the exact same `focusNode(id)` path a click
    does, through the component's own imperative handle.
  - Filtering (`hiddenCategories`/`minScore`) re-derives a filtered `links`
    array and calls `.graphData({nodes, links})` again rather than relying on
    the library's `linkVisibility` accessor — chosen because `3d-force-graph`
    merges incoming data by node `id` across `.graphData()` calls and keeps
    each surviving node's current `x/y/z` (confirmed from the underlying
    `three-forcegraph` update-diffing behavior, though — like every dynamic
    interaction in this phase — the *visible smoothness* of that merge still
    needs eyes-on confirmation, not just architectural confidence); the node
    array itself is never filtered, so this can't move nodes around, only
    toggle which edges draw.
  - `prefers-reduced-motion`: checked once via `window.matchMedia(...)` at
    mount; when set, every `cameraPosition`/`zoomToFit` call uses a `0`ms
    transition instead of the normal ~600-1000ms, per the task's own
    accessibility requirement.
  - WebGL failure fallback: the entire scene-setup effect is wrapped in
    `try/catch`; a thrown error sets a `status: "error"` state that renders a
    plain "3D preview isn't available in this browser" message with a button
    back to 2D, instead of a blank pane.
- **Verification — what was actually possible in this environment, stated
  plainly rather than implied**:
  - `npm run build` (local): 905 modules, 0 errors. Main bundle
    `index-*.js` **826.21 kB / 265.14 kB gzip** — a ~3 kB increase over Phase
    25's 823.50 kB, from the new small components/JSX/CSS, **not** from
    Three.js. `MatchGraph3D-*.js` landed in its own chunk: **1,251.16 kB /
    337.69 kB gzip** — this is where `three`/`3d-force-graph` actually live.
  - **Lazy-load proof, inspected directly in the built output** (not assumed
    from `React.lazy()` alone): `dist/index.html` references only the main
    bundle — no `<link rel="modulepreload">` for the 3D chunk. The main
    bundle's own source contains exactly one reference to it:
    `import("./MatchGraph3D-<hash>.js")` — a real dynamic `import()` call
    site, only reachable once `GraphPage` actually renders the 3D branch.
    `grep -c "THREE.Scene\|IcosahedronGeometry\|ForceGraph3D"` against the
    **main bundle alone** returned `0` — confirmed zero Three.js/3d-force-graph
    code reaches the normal bundle.
  - **Docker**: real `docker compose build recruitment-workflow` (multi-stage
    build runs the same `vite build` inside the container — identical output
    sizes/hashes to the local build) + `up -d --no-deps --force-recreate`.
  - Full RWS backend suite, inside the freshly recreated container: **69/69
    passed** (no backend file touched by this phase at all).
  - **Live route check**: `/`, `/candidates`, `/upload`, `/jobs`,
    `/interviews`, `/reports`, `/graph`, `/health` all returned real `200`s
    against production port 8003 post-rebuild.
  - **Served-bundle checks against the real running container** (not the local
    `dist/` copy): fetched the real served main bundle and the real served 3D
    chunk directly over HTTP — both `200`, sizes matching the local build
    exactly; confirmed the same "3D chunk only reachable via one dynamic
    `import()` in the main bundle, zero Three.js symbols in the main bundle"
    result against what the container actually serves, not just what Vite
    produced locally. Grepped the served main bundle for
    `resume-intelligence`/`second-brain`/API-key-shaped strings — none found,
    consistent with every prior phase's same check.
  - **Live data correctness** (real recruiter login against the real running
    stack, same dataset every phase since 25 has used): `GET
    /v1/graph/candidates-jobs` and `GET /v1/candidates` both `200`, 4 jobs, 26
    edges, 7 candidates, 17 qualified / 9 disqualified. Container logs clean —
    no errors, no PII, only expected access-log lines.
  - **Headless structural re-verification of the new data adapter** (not the
    3D rendering itself, which needs a real browser — see §21): a Node script
    against the real bundled data above confirmed `buildGraph3DElements()`
    produces exactly 11 plain nodes (4 jobs + 7 candidates) and 26 plain links
    with no Cytoscape `{data:}` wrapper; the qualified/disqualified split
    matches the real 17/9; every link's `source`/`target` id resolves to a
    real node in the same array (no dangling reference); every node's
    precomputed `degree` matches its real edge count; and the `mapRange()`
    helper used for node radius/link width clamps and interpolates correctly
    at its domain boundaries and midpoint. **11/11 checks passed.**
  - `git status --porcelain` after the pass showed exactly the expected files:
    4 new (`MatchGraph3D.jsx`/`.module.css`, `GraphControls.jsx`,
    `GraphDetailPanel.jsx`), `MatchGraph.jsx`/`GraphPage.jsx`/
    `GraphPage.module.css` modified, `package.json`/`package-lock.json`
    modified (the two new dependencies) — no backend file, no other page.
  - **What was explicitly NOT verified, and cannot be from this environment**:
    the actual WebGL scene rendering, orbit/zoom camera controls, the
    click-to-focus camera animation, hover tooltips, the highlight/dim visual
    effect, the fog/glow/lighting appearance, filter/search actually updating
    the visible scene, the 2D↔3D switch's visual transition, and the WebGL
    failure fallback's actual trigger conditions. `3d-force-graph` requires a
    real `window`/WebGL context and cannot run in Node — there is no headless
    path for this the way Cytoscape has one. See §21/§24's explicit
    manual-verification checklist; do not treat the checks above as a
    substitute for it.

**Phase 27 (3D graph visual tuning + real-browser validation)**: **DONE** —
and, unusually for this project's frontend history, with a **real browser
actually used**, not just build/data-level reasoning. Recorded here in detail
because it changes what future sessions can trust about this feature.

- **How real browser access happened**: this machine already had Playwright's
  Chromium binary cached at `AppData/Local/ms-playwright/chromium-1228` from
  unrelated prior use (confirmed with a plain `find` before assuming
  anything). `playwright-core@1.63.0` (the lightweight driver package, no
  bundled browser download) was installed into an **isolated scratchpad
  directory** (`npm init -y` + `npm install`, entirely outside
  `services/recruitment-workflow/frontend/`) and pointed at that cached
  binary via `executablePath` — **the project's own `package.json` was never
  touched for this**, this is a verification tool, not a product dependency.
  Headless Chromium's default configuration on this machine already exposes
  real GPU-backed WebGL (`ANGLE (Intel, Intel(R) UHD Graphics ...) Direct3D11`
  — confirmed via a `canvas.getContext('webgl')` + `WEBGL_debug_renderer_info`
  probe before writing the real test) — no `--use-gl`/`--ignore-gpu-blocklist`
  flags were needed, and one specific combination of those flags actually
  broke screenshot capture outright (`Page.captureScreenshot` protocol error)
  during an early attempt, so they were deliberately left off.
- **What the script did**: logged in via the real `POST /v1/auth/login` API,
  injected the resulting JWT into `sessionStorage` under the real key
  (`rws_jwt`, per `src/auth/token.js`) the same way the app itself reads it,
  navigated to `/graph` against the real running container, then drove the
  page exactly as a recruiter would: switch to 3D, wait for the force
  simulation to settle, screenshot; hover and click the canvas; click legend/
  filter/search/camera controls; switch back to 2D and re-enter 3D. Console
  and page-error listeners were attached for the whole run.
- **Round 1 (before any tuning change) — real problems found, not assumed**:
  - The initial post-settle composition, the "Fit graph" button's result, and
    a fresh remount after switching back to 3D **all produced the same
    result**: a tiny, dim, barely-distinguishable cluster near the bottom of
    an otherwise empty dark canvas. Nodes were visible only as faint gray-teal
    smudges — none of the intended cyan/violet color, no readable size
    distinction between job and candidate nodes.
  - A comparison screenshot of the unchanged 2D view (same data, same design
    tokens) showed vivid, saturated colors — proving the token *values* were
    never the problem, only how the 3D scene presented them at its default
    camera distance.
  - An incidental camera move (triggered while probing for a clickable node)
    landed much closer to the cluster and, at that closer distance, the
    color/shape/link design actually looked reasonable — this was the key
    clue that the real defect was **framing distance + fog interacting
    badly**, not the color/material choices themselves.
  - At that same closer distance, the glow-shell effect was visible clearly
    enough to see it was wrong in a different way: each node showed a dull,
    slightly-lighter-than-background **dark ring**, not a glow — the
    telltale look of a translucent shell alpha-blended over a very dark
    background (which can only ever move a pixel *toward* the shell's flat
    color, never brighten it), not a genuine light-emitting effect.
- **Fixes applied, each directly tied to one of the observations above** (all
  in `MatchGraph3D.jsx`, `buildNodeObject`/the scene-setup effect only —
  code comments at each site cross-reference this phase for future sessions):
  - `Graph.scene().fog` density: `0.0055` → `0.0016`. This alone was expected
    to be the single biggest fix, since three near-identical "washed out"
    screenshots all shared the same distant default framing.
  - `zoomToFit` padding, centralized into one new `ZOOM_FIT_PADDING` constant
    and applied consistently to the initial auto-fit, "Reset camera," and
    "Fit graph" (previously each passed its own literal `70`): `70` → `26`,
    bringing the default camera meaningfully closer on this dataset's actual
    size.
  - Node radii: jobs `7-12` → `9-15`, candidates `3.2-5.6` → `4.5-7.5`.
  - Ambient/point light intensity: `0.65`/`0.5` → `0.85`/`0.75` (the
    `MeshPhongMaterial` nodes use needs real light to shade its diffuse
    component; emissive alone wasn't enough at the corrected distance).
  - Glow shell: switched from the default alpha blend to
    `THREE.AdditiveBlending` (brightens the pixels behind it instead of just
    alpha-compositing a dark translucent color), size ratio `radius * 2` →
    `radius * 1.6`, opacity `0.16` → `0.28` (default) / `0.03` → `0.05`
    (dimmed) — tuned to read as a soft halo, deliberately not pushed further
    given this task's own explicit "avoid excessive glow" constraint.
- **Round 2 (after the fixes) — re-verified with the same real-browser script
  against a real rebuilt Docker container, screenshots compared directly, not
  assumed improved**:
  - The initial composition now clearly shows 4 distinct bright-cyan job
    icosahedra of visibly different sizes (degree-based), 7 smaller violet
    candidate spheres, green (qualified) and red (disqualified) links with
    visible flowing particles on qualified links, and a soft cyan/violet glow
    around each node — the whole graph fits the frame as one intentional,
    readable composition, not a washed-out smudge.
  - **Click-to-select, confirmed working end to end with real data on
    screen**: clicking a job node populated the shared `GraphDetailPanel`
    with its real title, an "Open job →" link, "7 CONNECTIONS," and 7 rows of
    real persisted `final_score` percentages and qualified/not-qualified
    badges (e.g. `75% Not qualified`) — proving `MatchGraph3D`'s own
    `buildNode3DSelection` produces a shape `GraphDetailPanel` renders
    correctly, with real data, not just a structurally-plausible shape.
  - **The highlight/dim neighborhood effect is visibly correct**: nodes/links
    outside the clicked node's neighborhood clearly dim relative to the
    focused cluster in the same screenshot.
  - **The qualified/disqualified filter toggle, re-tested live**: toggling
    "Qualified match" off in the legend left only red (disqualified) links
    visible on screen, with node positions visibly unchanged from the
    unfiltered screenshot — real visual confirmation (not just architectural
    inference) that the `.graphData()` re-filter approach doesn't jump the
    layout.
  - **Search-triggered camera focus, confirmed working end to end**:
    searching and selecting a job moved the camera to frame it with its real
    5 qualified connections and percentages shown in the panel.
  - **2D↔3D switching and remount, confirmed clean**: the 2D screenshot taken
    after returning from 3D was pixel-identical in structure to 2D's own
    baseline screenshot (same data, same colors, same layout) — no visual
    regression; re-entering 3D afterward produced the same clean composition
    with no console/page errors on either transition.
  - **Console/page-error listener across the entire scripted run**: zero page
    errors; the only console output was one pre-existing Cytoscape
    "custom wheel sensitivity" advisory warning (present before this task,
    unrelated to 3D, harmless) — no WebGL/Three.js error, no React error, no
    uncaught exception.
- **Standard build/Docker verification, same as every phase**: `npm run
  build` (905 modules, 0 errors; bundle sizes effectively unchanged —
  `index-*.js` still 826.21 kB / `MatchGraph3D-*.js` still ~1,251 kB, since
  this pass only changed numeric constants and one blending mode, not
  imports); the served main bundle still contains exactly one
  `import("./MatchGraph3D-<hash>.js")` dynamic import and zero Three.js
  symbols, confirming lazy-loading is still intact after the tuning changes;
  real `docker compose build` + `up -d --no-deps --force-recreate`; full RWS
  suite **69/69 passed**; `/graph` route `200`. No backend file touched, no
  new API call added (confirmed by diffing `MatchGraph3D.jsx` for any new
  `fetch`/`api/` reference — none).
- **What this phase still could NOT verify, honestly**: whether the camera
  motion/orbit *feels* smooth (a scripted click can measure "did the camera
  move to roughly the right place," not "does this feel good to a human
  hand on a mouse"); real mouse-drag orbit/pan interaction (the script only
  ever moved the mouse to fixed coordinates and clicked, never performed a
  drag); WebGL performance under real sustained interaction; the
  `prefers-reduced-motion` branch (not exercised — would need a browser
  context launched with that media feature forced); and the WebGL-failure
  fallback UI (still never triggered by a real failure, only reasoned about
  via its `try/catch`). See §21 for the precise, narrowed-down remaining
  checklist — it is considerably shorter than before this phase, but not
  empty.
- Screenshots from both rounds were saved to this session's scratchpad
  directory only (`.../scratchpad/pwverify/*.png`) — not committed to the
  repository and not embedded in this document; a future session without
  the same cached Chromium binary should not assume they still exist.

**Phase 28 (V2 design system / shell / Dashboard: real-browser audit + targeted
fixes)**: **DONE**. Same real cached-Chromium technique as Phase 27, this time
pointed at `/` (Dashboard), `Sidebar`, `TopBar`, and a page-to-page navigation,
across desktop/tablet/phone viewports, before touching any code.

- **Why an audit first, not a rewrite**: the `[NEXT]` text for this session
  asked for a "V2 frontend visual redesign — design system + shell +
  dashboard," but Phase 21 (see its own §19/§20 entries) already built exactly
  that — a dark cinematic bento dashboard, glass `Sidebar`/`TopBar`, a
  restrained cyan/violet accent pair, subtle grid/aurora/noise texture, and
  `motion`-driven entrance/hover/count-up animations, all gated behind
  `prefers-reduced-motion`. Phase 21's own verification, however, was
  explicitly build/data-level only ("no browser is available in this
  environment" — its exact words). This session had a real browser (the same
  cached Playwright Chromium Phase 27 found), so the honest first step was to
  actually look, not assume Phase 21's code-level confidence was equivalent to
  visual confirmation, and not rebuild something that turned out to be
  already good.
- **What the audit found matched the intended direction well**: the bento
  layout, gradient hero text, glass stat tiles with alternating cyan/violet
  accent lines, the pipeline distribution bar, and the graph-teaser card all
  rendered as intended — genuinely reading as a "premium AI command center,"
  not a generic template. One specific consistency point was checked directly
  and confirmed already correct: the graph-teaser glyph's color mapping
  (violet = candidate, cyan = job) already matches the 3D graph's own
  convention from Phase 26/27 — no fix needed there. Hover states on the
  quick-action buttons and the graph-teaser card were confirmed subtle and
  working via direct screenshot comparison.
- **Three real, screenshot-confirmed defects were found and fixed**:
  1. **A light void below the Dashboard panel whenever the viewport is taller
     than the page's own content.** `DashboardPage.module.css`'s `.page` is a
     dark panel sitting inside `Layout.module.css`'s still-light `.content`
     wrapper (deliberately untouched since Phase 21 — see §21's historical
     seam note); with no explicit height on either, a tall viewport simply
     showed `body`'s own light background in the gap below the panel — a
     screenshot at 1440×900 showed roughly a third of the viewport as plain
     light void beneath the dark content. **Fix**: `DashboardPage.jsx` now
     toggles `document.body.style.backgroundColor` to `var(--v2-bg)` in a
     `useEffect` on mount, restoring whatever value was there before on
     unmount. This is scoped entirely to Dashboard's own mount lifecycle —
     `Layout.module.css` and every other page's own styling are untouched.
     **Verified live, not just reasoned**: a screenshot taken immediately
     after navigating away to `/candidates` showed that page's normal light
     background rendering correctly, confirming the toggle reverts cleanly
     and doesn't leak into other (still-light) pages.
  2. **A mismatched blue keyboard-focus ring on the dark chrome.**
     `global.css`'s shared `:focus-visible` rule uses `--color-primary` (a
     light-theme blue, `#2f5fdb`) — correct for every still-light page, but a
     screenshot of a keyboard-focused Sidebar nav link showed this blue ring
     looking visibly out of place against the dark `--v2-*` chrome, undercutting
     the "coherent visual relationship" this session was asked to check for.
     **Fix**: added a `:focus-visible` override using `--v2-cyan` directly
     inside `Sidebar.module.css` (on `.link`) and `TopBar.module.css` (on
     `.logoutButton`) — the same "self-contained, `--v2-*`-only, cannot affect
     any light page" pattern Phase 21 already established for every rule in
     both files. `global.css` itself was not touched, so no light page's own
     focus ring changed.
  3. **A real mobile horizontal-overflow bug on the Dashboard's bento grid.**
     A phone-width (390px) screenshot showed the pipeline distribution bar,
     activity-list dates, and the graph-teaser's description text all clipped
     abruptly at the right edge of the viewport, instead of the ellipsis
     truncation `.activityLabel` was already styled for
     (`overflow: hidden; text-overflow: ellipsis; white-space: nowrap`) — the
     classic CSS "a `1fr` grid track's implicit `min-width` is `auto`, not
     `0`" shrink-blowout, compounded by `.activityLabel` itself missing
     `min-width: 0` (a flex item's own implicit `min-width: auto` prevents
     `text-overflow: ellipsis` from ever engaging, independent of the grid
     issue). **Fix**: added `.bento > * { min-width: 0; }` (one rule,
     covering every grid item regardless of which file defines its class) and
     `min-width: 0` on `.activityLabel` directly, both in
     `DashboardPage.module.css` only.
- **Round 2, real-browser re-verification after the fixes, same script,
  rebuilt Docker container**:
  - Desktop (1440×900): the dark panel now reaches the bottom of the
    viewport with no light gap, confirmed by direct screenshot comparison
    against the "before" round.
  - The activity list's longer labels now visibly ellipsis-truncate
    (e.g. "Job posted: Senior Ba…") instead of silently overflowing their own
    row without visual consequence at this width — this is the fix
    *working as designed*, not a regression: the truncation behavior was
    always coded, just silently unable to engage before `min-width: 0` was
    added.
  - Phone (390px): every element — KPI tiles, the distribution bar, activity
    rows, the graph-teaser's full two-line description — now stays fully
    within the viewport with correct ellipsis truncation where labels are
    long; the "Knowledge Graph" description that was cut mid-word before now
    wraps cleanly across two lines in full.
  - Keyboard focus on the "Knowledge Graph" nav link now shows a cyan ring
    that reads as part of the same design language as the rest of the chrome.
  - Tablet (768px) and the page-transition (Dashboard → Candidates,
    mid-transition and settled) screenshots were re-checked and are
    unaffected by any of the three fixes, as expected — confirmed, not
    assumed.
  - Console/page-error listener across the entire scripted run, both rounds:
    zero errors, zero warnings, in either round.
- **Standard build/Docker verification, same as every phase**: `npm run
  build` (905 modules, 0 errors; main bundle `826.38 kB` / 3D chunk unchanged
  at `~1,251 kB` — this pass changed only a handful of CSS rules and one
  small `useEffect`, no new import); real `docker compose build` +
  `up -d --no-deps --force-recreate`; full RWS suite **69/69 passed**; `/`
  route `200`. The served main bundle's dynamic `import("./MatchGraph3D-
  <hash>.js")` (Phase 26/27's lazy-loading) was re-confirmed intact and
  unaffected by this pass. No backend file touched, no new API call (checked
  by diffing every changed file for `fetch`/`api/`/`axios` references — none
  beyond what already existed).
- `git status --porcelain` after the pass showed exactly the expected files:
  `pages/DashboardPage.jsx` + `DashboardPage.module.css`,
  `components/Sidebar.module.css` + `TopBar.module.css` — no other page, no
  `Layout.jsx`/`.module.css`, no `variables.css`/`global.css`, no router, no
  API module, no backend file.
- **What this phase deliberately did not do**: rewrite or redesign any part
  of the shell/dashboard that the audit found already working (per this
  task's own explicit "do not redo working components for stylistic
  preference alone"); touch `variables.css`'s `--v2-*`/`--color-*` token
  split (that remains the still-postponed token-fold task, §24); add any new
  gradient, glow, or motion beyond what Phase 21 already had; or extend this
  audit to any of the still-light operational pages (`CandidatesPage`,
  `JobsListPage`, etc.) — those pages' own light/dark seam is real, disclosed,
  and is exactly what "V2 visual migration — operational pages" (this
  phase's own `[NEXT]`, see §24) is for, not something to fix incidentally
  here.

**Phase 29 (real-browser visual audit of the nine remaining operational
pages)**: **DONE**. Same real cached-Chromium technique as Phases 27/28, this
time systematically across every page this handoff's own history had marked
"V2-migrated" but never actually screenshotted.

- **Ignoring the literal `[NEXT]` wording, as instructed, and checking repo
  history first**: `grep -rn "var(--color-" services/recruitment-workflow/
  frontend/src/pages/*.module.css` returned zero matches for all nine target
  pages (only `LoginPage.module.css`, out of scope, still had any) —
  confirming directly, not by trusting this document's own prose, that
  Phases 22-25's token migration really is complete. This meant the honest
  scope of "V2 visual migration — operational pages" was a *visual audit*,
  the same category of work Phase 28 had just modeled for Dashboard, not a
  fresh migration.
- **Audit method**: one Playwright script logged in once, then navigated to
  all nine pages (`/candidates`, `/candidates/:id`, `/upload`, `/jobs`,
  `/jobs/:id`, `/jobs/:id/applications`, `/interviews`, `/reports`,
  `/reports/:id`) at both a desktop (1440×900) and phone (390×844) viewport,
  taking a full-page screenshot of each — 18 screenshots total, reviewed
  directly, before any code change.
- **Classification** (per this task's own three-tier scheme):
  - **Already V2-quality, untouched**: `CandidatesPage`, `JobsListPage`,
    `JobDetailPage`, `ApplicationsPage`, `InterviewsPage`, `ReportsPage`,
    `ReportDetailPage`. All seven showed solid, consistent tables, working
    row-hover states, correctly color-coded status/qualified badges, cyan
    links, and gradient score bars/buttons matching Dashboard's own
    cyan-violet gradient exactly (`selectButton`'s
    `linear-gradient(120deg, var(--v2-cyan), var(--v2-violet))` — checked
    directly against the source, not assumed from a screenshot's apparent
    color, since a short button can make a diagonal two-color gradient read
    as a single flatter blue at a glance). No mobile overflow found on any of
    the seven (their tables already use the established
    `.tableWrapper { overflow-x: auto }` + `min-width` pattern from Phase 15,
    which correctly becomes a horizontal-scroll region rather than
    overflowing the page — an intentional, pre-existing, consistent pattern
    across every tabular page in the app, not something this phase touched).
  - **Minor polish needed, fixed**: `CandidateDetailPage` and
    `ResumeUploadPage` — see below.
  - **Meaningful redesign needed**: none. No page warranted it.
- **The one systemic (not page-specific) defect, found and fixed once**:
  the very first screenshot this phase took (`CandidatesPage`, desktop) showed
  the identical "light void below the dark panel" defect Phase 28 had found
  and fixed *only for `DashboardPage`* — proving it was never a
  Dashboard-specific bug, it was a property of the shared "V2 page panel"
  pattern every one of these pages uses, just never noticed before because no
  prior phase had a real browser to see it in. Root cause (see
  `Layout.module.css`'s own updated comment): `.content` had never been given
  either a background or a height, because Phase 21's original justification
  for leaving it alone — most pages still rendered plain text directly onto
  it, no dark panel of their own yet — stopped being true once Phase 25
  finished migrating every page to its own dark panel. **Fix**: `.content`
  in `Layout.module.css` now gets `flex: 1 1 auto; min-height: 0; background:
  var(--v2-bg-inset);` — the `flex`/`min-height` pair makes it actually reach
  the bottom of `.main`'s height (a plain block box without them just sizes
  to its own content, which was the actual root cause, not merely a missing
  background color), and the background then has something to paint. This
  single change fixes the defect on **every** page reached through the shell
  at once, not just the nine audited this phase — confirmed live on
  `DashboardPage`, `GraphPage` (both 2D and re-checked after this change),
  and all nine audited pages, desktop and mobile, with zero regression.
  `DashboardPage.jsx`'s own Phase 28 `useEffect` body-background toggle
  (a correct but page-scoped workaround at the time) was removed as part of
  this same change — it is now fully superseded by the shell-level fix and
  would otherwise be redundant, dead complexity.
- **`CandidateDetailPage` (minor polish)**: the audit's clearest finding —
  this page used zero cyan/violet accent color anywhere, an all-gray palette
  that read as visually disconnected from Dashboard, the 3D graph, and every
  other migrated page. Fixed with two small, restrained additions, not a
  redesign: (1) each of the page's three sections (`Professional knowledge`,
  `Applications`, `Knowledge Graph`) gained a 2px top accent line, alternating
  cyan/violet — the exact same device `StatTile` already uses on Dashboard,
  applied via `:nth-of-type(odd)`/`:nth-of-type(even)` rather than a new prop;
  (2) `years_of_experience`'s value now renders like a StatTile value (larger,
  bold, cyan) instead of plain body text, and the `job_titles` chip list (a
  handful of entries) now renders with a violet tint, while the (potentially
  dozens-long) `skills` chip list stays neutral gray — deliberately not
  tinting every skill chip too, since that would read as busy/loud on a
  candidate with a large skill list, the opposite of "restrained."
- **`ResumeUploadPage` (minor polish + one real bug)**: the audit found the
  drop zone read as a bland, text-only box next to the rest of the app's
  polish. Fixed by adding a small hand-authored inline-SVG upload-arrow icon
  above the drop-zone text (same style convention as `Sidebar.jsx`'s own nav
  icons — no icon font/library added), tinted `--v2-text-faint` at rest and
  `--v2-cyan` on hover/drag-active, matching the drop zone's own existing
  hover-state color change. Separately, the audit found a **real, if subtle,
  bug**: this page's default-state `<PageHeader>` call was missing
  `variant="glass"` — its own success-state `<PageHeader>` two states later in
  the same file already had it, an inconsistency within one file. Without it,
  `PageHeader`'s subtitle rendered in `--color-text-muted` (a light-theme
  gray, `#6b7280`) against this page's dark panel instead of the intended
  `--v2-text-muted` — lower contrast than every other migrated page's
  subtitle. **Fix**: added the missing `variant="glass"`, matching the
  pattern already correct everywhere else in this same file.
- **Verification — real browser, both rounds**:
  - Round 1 (before) and round 2 (after), same 18-screenshot script, real
    rebuilt Docker container both times. Round 2 confirmed: the dark
    background now reaches the bottom of the viewport on every page checked
    (Candidates, Candidate Detail, Upload, Job Detail, Reports, the 2D
    `GraphPage`, and re-confirmed on Dashboard); `CandidateDetailPage` now
    shows alternating cyan/violet top accents, a cyan "0.3" years-of-experience
    value, and violet job-title chips, with skill chips correctly left
    unchanged (still neutral gray); `ResumeUploadPage` now shows an upload
    icon and a properly `--v2-text-muted`-colored subtitle; every page
    re-checked at phone width (390px) showed no overflow or regression from
    the shell-level `.content` change.
  - **Console/page-error listener across the entire scripted run, both
    rounds**: the only errors seen, identically in both rounds (proving
    they predate this phase, not introduced by it), were two `404`s per
    viewport on `GET /v1/candidates/{id}/identity` — traced directly (not
    assumed) to the exact same test-data hygiene condition §21 already
    documents from the Phase 10 audit (a candidate present in Second Brain's
    scoring index with no matching Resume Intelligence contact record); the
    frontend's own existing, correct handling of that condition is what's
    visible in `JobDetailPage`'s "Candidate 0a699701… (name unavailable)"
    row. Not a regression, not a new limitation, not touched by this phase.
  - **Standard build/Docker verification**: `npm run build` (905 modules, 0
    errors; main bundle `826.78 kB` gzip `265.35 kB`, 3D chunk unchanged at
    `~1,251 kB` — this pass changed CSS and a handful of small JSX pieces
    only, no new import); real `docker compose build` +
    `up -d --no-deps --force-recreate`; full RWS suite **69/69 passed**; all
    eight routes checked (`/`, `/candidates`, `/upload`, `/jobs`,
    `/interviews`, `/reports`, `/graph`, `/health`) returned real `200`s
    against the rebuilt container. The served main bundle's dynamic
    `import("./MatchGraph3D-<hash>.js")` (Phase 26/27's lazy-loading) was
    re-confirmed intact. No backend file touched, no new API call (checked by
    diffing every changed file for `fetch`/`api/`/`axios` references — none
    beyond what already existed).
- `git status --porcelain` after the pass showed exactly the expected files:
  `pages/Layout.module.css` (the shell-level fix), `pages/DashboardPage.jsx`
  (the now-superseded `useEffect` removed — this brought the file back to
  byte-identical with the last commit, since nothing else in it changed this
  phase, hence it doesn't even show as modified), `pages/
  CandidateDetailPage.jsx` + `.module.css`, `pages/ResumeUploadPage.jsx` +
  `.module.css` — no other page, no router, no API module, no backend file.
- **What this phase deliberately did not do**: touch any of the seven pages
  classified already-V2-quality; redesign `CandidateDetailPage`'s or
  `ResumeUploadPage`'s actual layout/structure (only color/icon accents were
  added — every section, field, table, and empty state is exactly where and
  what it was); add a custom audio player to `ReportDetailPage` (the native
  `<audio controls>` element was considered and deliberately left alone — a
  custom player is real, disproportionate complexity/risk for a cosmetic
  gain the task's own "boring over clever" project convention doesn't
  support); or fold `--v2-*`/`--color-*` (still the separately-queued
  token-fold task, §24).

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
- **Deliberate scope decisions from the Phase 16 (Knowledge graph) pass**: no
  hover-triggered tooltip was added alongside click-to-select — the task's own click/
  highlight/reset behavior is fully specified and click-based, and a second,
  independent hover interaction would be extra state for no clearly-asked-for benefit;
  the detail panel already gives the same information a hover tooltip would, on a
  click. The graph canvas uses a fixed `70vh` height rather than a precise `calc()`
  against the topbar/padding chrome — simpler and robust across window sizes, at the
  cost of not filling the viewport to the exact pixel; `Layout.module.css`'s own
  `.content` has no fixed-height convention for any other page to follow here. No
  node search/filter/pagination was added — consistent with this project's existing
  "no infra ahead of demonstrated need" rule for every other list page — click-to-
  highlight is the only way to find a specific node's connections for now; revisit
  only if a real dataset's node count makes that impractical. `CandidateDetailPage`'s
  "Knowledge Graph" section (Phase 15) was, at the time of this Phase 16 pass, still
  a static `EmptyState` placeholder — **resolved in Phase 17** (see above; embedding
  a per-candidate ego graph into it was the very next task named here, and is now
  done, not a remaining gap).

  The production JS bundle is now ~675 kB (~215 kB gzipped, per Vite's own build
  output) — noticeably larger than before Cytoscape was added, and now past Vite's
  500 kB warning threshold. This is a real, disclosed cost of adding any graph-
  rendering library at all, not something Phase 16 could avoid while still meeting
  the task's own "use Cytoscape.js" requirement; code-splitting `/graph` into its own
  chunk (`React.lazy`) would reclaim this weight for every other route but was judged
  premature ahead of an actual demo-load complaint, per this project's own "no infra
  ahead of demonstrated need" rule. (Still true after Phase 17 — no new dependency
  was added, so the bundle only grew by the small amount of new code itself; see the
  §20 Phase 17 entry for the exact number.)
- **Deliberate scope decisions from the Phase 17 (ego graph reuse) pass**: the ego
  graph shows only jobs this candidate has an actual persisted `MatchResult`
  against — a candidate matched to every job in the current (small, smoke-test-
  seeded) dataset will see a graph that looks identical in shape to a slice of the
  full `/graph` explorer; this is correct behavior, not a bug, and was specifically
  verified against a candidate whose matches *don't* span every job to prove the
  exclusion path too (see §20). No legend was added to the embedded ego graph
  (`showLegend` defaults to `false` on `MatchGraph`) — two node shapes/colors and a
  handful of elements read clearly without one in a small embedded section; the
  full `/graph` page, with far more elements and less surrounding context, keeps
  its legend. The embedded graph keeps its own "Reset view" button (rendered
  inside `MatchGraph` itself via `showResetButton`) rather than adding one to
  `CandidateDetailPage`'s own page header, since that header is the candidate's
  name/email, not a toolbar for one section among three.
- **Phase 21 (V2 design system) introduced a dark/light seam; Phase 25 closed
  it — recorded here for history, no longer a live limitation.** Sidebar/TopBar
  went dark in Phase 21 while every page's own content area except Dashboard
  stayed on the original light theme (see Phase 21's own §20 entry for exactly
  why `--color-*`/`.content` couldn't be touched that pass), and each following
  slice (22, 23, 24, 25) migrated more pages until, as of Phase 25, **every
  production page is on V2 and the seam no longer exists anywhere** (the one
  deliberate exception is `LoginPage`, a pre-auth screen never in scope for
  this redesign). The old guidance not to "fix" the seam by touching
  `--color-*`/`.content` in isolation no longer applies now that no page still
  depends on the light values for contrast — but see §24's token-fold `[NEXT]`
  task before actually deleting `--color-*`'s old values, since `LoginPage`
  still reads them directly and needs to be checked against the new palette
  first.
- **Phase 21 — no custom webfont was added for "stronger typography."** A
  well-tuned system-font type scale (size/weight/letter-spacing/tabular-nums)
  was judged sufficient, deliberately avoiding a Google-Fonts-style network
  dependency a live demo venue's network might not load reliably or quickly. If a
  future session judges a real display font is worth that risk, that's a
  reasonable call to revisit — it wasn't an oversight here, it was weighed and
  declined for this pass.
- **The "Optional 3D presentation mode — NO-GO for now" decision recorded
  between Phase 19 and 20 (still preserved verbatim in §24 for history) is
  superseded by Phase 26.** That decision was specifically about
  `react-force-graph`'s AR/VR dependency weight and git+SSH transitive
  dependency, plus the small dataset — the user's own Phase 26 instruction
  resolved the first two by naming `3d-force-graph` directly instead (a
  materially different, clean dependency tree — confirmed, not assumed, in
  §20's Phase 26 entry) and accepted the small-dataset tradeoff explicitly by
  asking for a *tuned, curated* composition rather than large-graph defaults.
  Do not cite the old NO-GO note as still-current guidance.
- **Phase 26's browser-verification gap was substantially, but not entirely,
  closed by Phase 27 — read this note before either of the other two.** Phase
  27 found that Playwright's Chromium browser was already cached on this
  machine and used it directly (real GPU-backed WebGL, not a mock) to drive
  `/graph`'s 3D mode and screenshot the result — see §20's Phase 27 entry for
  the full before/after record. **Confirmed by an actual rendered screenshot,
  not just code reasoning**: the WebGL scene renders with correct job/
  candidate color and size distinction, the glow-shell reads as a soft halo
  (after a fix — the original version read as a dull dark ring, a real defect
  this pass caught and corrected), qualified links render brighter/greener
  and disqualified links render muted/red, click-to-select populates the
  shared `GraphDetailPanel` with real persisted match data, the highlight/dim
  neighborhood effect is visibly correct, the qualified/disqualified filter
  toggle visibly changes the scene without disturbing node positions,
  search-triggered camera focus lands on the right node with real data
  shown, the `2D ↔ 3D` switch and a remount both produce a clean scene with
  zero console/page errors, and the overall composition (after this pass's
  fog/zoomToFit/radius/light tuning) reads as an intentional graph with jobs
  as visual anchors, not a washed-out cloud.
  **Still not verified by anything more than code/API reasoning** (a
  scripted browser can click a fixed coordinate and read rendered text, but
  cannot judge a "feel," perform a real mouse-drag orbit, or force a browser
  media-feature preference): whether the click-to-focus camera animation and
  general orbit/pan/zoom controls *feel* smooth and pleasant under a real
  human hand on a mouse/trackpad (only "did the camera arrive roughly where
  intended" was checked); WebGL performance under sustained real interaction
  (panning, rapid clicking, resizing) rather than a handful of scripted,
  paused actions; the `prefers-reduced-motion` code path (the scripted run
  never launched a browser context with that media feature forced); the
  WebGL-failure fallback UI actually appearing under a real failure condition
  (still only exercised via its `try/catch` existing, never a real GPU/driver
  failure); and real hover-tooltip legibility/positioning at a human's normal
  viewing distance and font-rendering (a screenshot was taken during a
  scripted hover, but reading it critically as a human still hasn't happened
  the way it would in an actual demo run-through). Treat this narrowed list,
  not the original Phase 26 list, as the remaining gap — it is real but
  considerably smaller than before.

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

**Phases 1–30 are all done** — every screen from the original admin-dashboard
redesign proposal, the candidate-detail cross-linking hub, the whole-dataset
knowledge graph at `/graph` (interactive legend filtering, a minimum-score filter,
candidate/job search — now presentable in a default 2D analytical view or an
explicit, optional, lazy-loaded, **real-browser-tuned-and-validated** 3D immersive
view, Phases 26-27), the candidate-detail ego graph reusing the same 2D
`MatchGraph` engine, a visually-polished layout-stable version of both, no
remaining dead-end/missing cross-links between any of the eleven production
screens, and the dark "AI recruitment command center" (V2) visual direction
applied to **every one of the eleven production screens — every one of which
has now been checked in a real browser at least once** (Phase 28 for the
shell/Dashboard, Phase 29 for the other nine, including a shell-level fix that
corrected a defect present on all of them, not just Dashboard): the persistent
shell, `DashboardPage` (Phase 21, audited+fixed Phase 28), Applications/
Interviews/Reports/Report Detail (Phase 22, audited Phase 29), Job Detail/
Candidate Detail (Phase 23, audited Phase 29 — Candidate Detail polished), Jobs
List/Candidates/Resume Upload (Phase 24, audited Phase 29 — Resume Upload
polished), and the Knowledge Graph / `MatchGraph` (Phase 25, audited Phase 29).
**No page anywhere in the app still renders the original V1 light theme** (the
one exception is `LoginPage`, a pre-auth screen that was never in scope for
that redesign). Full detail for each phase is in §19/§20, not restated here.

**Two chat-response-only audits happened between Phase 19 and Phase 20, neither
touched code or this file at the time** (both explicitly scoped that way), and are
recorded here since one conclusion is now superseded and the other still matters:

- **Optional 3D presentation mode — original decision**: **NO-GO for now** *(superseded
  by Phase 26 — kept verbatim below for history, do not treat as current guidance;
  see §21's own note on exactly what changed).* A real disposable prototype
  (outside the repo, never integrated) measured `react-force-graph`'s
  `ForceGraph3D` at a real **+512 kB gzipped** (confirmed lazy-loadable, so a normal
  2D visit wouldn't pay it), but also found the library **cannot run at all outside
  a real browser** (`window is not defined` at import time in plain Node) — unlike
  Cytoscape, there is no headless verification path for it in this environment at
  all. Combined with the current dataset (4 jobs, a handful of candidates) being
  too small for 3D to show anything 2D doesn't already show clearly, and a live
  demo being the wrong place to introduce a first WebGL dependency, the conclusion
  was to keep 2D as the only mode.
  **What changed in Phase 26**: the user's own instruction named the specific fix
  for the *dependency* half of this concern — use `3d-force-graph` directly, not
  `react-force-graph` — which resolved the AR/VR-weight and git+SSH-dependency
  problems this note was actually about (confirmed via a real `npm view
  3d-force-graph dependencies` check before installing anything, not assumed);
  the "cannot run headlessly" fact is still true (it's a WebGL library, that's
  inherent) and is exactly why Phase 26 carries the honest, disclosed
  manual-verification gap this section's own `[NEXT]` below exists to close; and
  the small-dataset concern was addressed head-on by this task's own explicit
  request for a *tuned, curated* composition (job nodes as strong anchors,
  candidates clustering around them) rather than generic large-graph force
  defaults — whether that tuning actually reads well is exactly what still needs
  eyes on it, see `[NEXT]`.
- **Final unified admin-dashboard UX audit**: reviewed all eleven production
  screens end-to-end. Overall verdict was "solid and demo-ready as-is" — no
  structural or navigational confusion found. It named four concrete, cheap
  cross-link gaps (each a page already holding the field it needed and simply not
  linking it) as the one worthwhile polish pass; **Phase 20 (above) implemented all
  four.** The audit's other, lower-priority observations (terminology nits like
  "talent pool" vs. "candidate pool", inconsistent page max-widths, no explicit
  "back to list" link on a few detail pages) were deliberately left alone as not
  worth demo-prep time — do not "clean these up" speculatively; they were
  considered and explicitly deprioritized, not missed.

**A note on sequencing**: Phase 20's own `[NEXT]` was "Final production/demo
readiness audit — whole system, no feature work" (its full scope is preserved
below for the historical record) — the user then explicitly redirected to the
V2 redesign instead (Phase 21). **That readiness audit has now run, as Phase
30** (see `handoff.md` for its full findings/verdict — this project's
"cross-service facts go there" rule applies, since the audit's scope was
whole-system, not frontend-only): verdict **DEMO READY**, no `CRITICAL`/`HIGH`
defect found, one real infra fix applied (missing container restart
policies). The block below is preserved as a historical record of what Phase
20 originally asked for, not as still-pending work — it should still run once
the V2 migration reaches a stable point (either fully
migrated, or a deliberate decision to demo the mixed dark/light state as-is), not
be silently dropped.

**[NEXT] — derived from actual repo state, not copied from an old `[NEXT]`
(see below for why).** Three consecutive sessions now (Phases 27, 28, 29) have
each been handed `[NEXT]` wording that named work already finished by the time
the session actually inspected the repo — a real pattern, not a coincidence,
worth naming plainly so a future session doesn't repeat it a fourth time:
**trust a live `grep`/real-browser check over this document's own prose every
time, on every task, not only when explicitly told to.**

**The actual, current, verified state of the frontend**: every one of the
eleven production screens is on the V2 dark design system, every one has now
been seen rendering in a real browser at least once (Phases 27-29), the one
cross-cutting rendering defect found (the shell-level light-void bug) is fixed
for all of them at once, and the two pages found needing polish
(`CandidateDetailPage`, `ResumeUploadPage`) have been fixed and re-verified.
There is no remaining V2 migration or V2 visual-quality work waiting to be
discovered — the next three sessions' worth of "audit further" would very
likely just keep confirming this. Two tasks were already queued behind the
visual-redesign work, from Phase 20 and Phase 25 respectively (full scope
preserved below); given the *actual* current state, here is the honest
priority order between them, decided now rather than left for a future
session to guess at:

**The readiness audit named above has now run, as Phase 30** (full findings in
`handoff.md`, per this project's own "cross-service facts go there" rule —
the audit's scope was whole-system, not frontend-only). Verdict: **DEMO
READY**, no `CRITICAL`/`HIGH` defect found anywhere in the system; the one
real fix it applied (missing container `restart` policies) was
infrastructure, not frontend. Nothing in this repo's frontend code changed as
a result of that audit — the real-browser walkthrough it ran confirmed
Phases 21-30's work holds up, it didn't find anything here to fix.

**[NEXT] Token-fold / documentation cleanup only.** This is now the only
remaining queued frontend task — the `--v2-*` → `--color-*` fold below, full
scope preserved so it doesn't need re-deriving. No feature work, no visual
redesign, no new audit: every page is confirmed V2 and confirmed working by
three independent real-browser passes (Phases 27-29) plus the whole-system
readiness audit (Phase 30) — this task is pure internal cleanup + doc
tidying, not a place to find or invent new scope.

---

**`--v2-*` → `--color-*` token fold (cleanup, no visual change).** Every production page is now on V2 (Phase 25 closed the last
seam, Phases 27-29 real-browser-confirmed it) and `LoginPage` is deliberately
out of scope, so the additive-dual-token-set scaffolding that made incremental
migration safe has now done its job and should be folded away rather than
carried indefinitely. Concrete guidance, carried over so it doesn't need
re-deriving:

- **The fold itself**: in `variables.css`, delete the old light `--color-*`
  values and rename/merge the `--v2-*` block into `--color-*` (or, if a literal
  rename touches too many call sites at once, repoint every `--color-*`
  definition to its `--v2-*` equivalent and then delete the now-redundant
  `--v2-*` names) — `variables.css`'s own comment on the `--v2-*` block already
  describes this as the eventual plan, written at the time that block was
  added. `LoginPage` is the one file that still reads `--color-*` directly, so
  after the fold it will simply pick up the new dark values — check
  `LoginPage.module.css`/`LoginPage.jsx` render correctly against the new
  palette as part of this task (it was never redesigned, only automatically
  repainted by the token rename), and if it doesn't read cleanly, treat that as
  this task's finding, not a reason to keep two token sets alive.
- **Remove the now-redundant explanatory comments** every migrated file
  accumulated during the incremental migration (the "why this is additive"
  comment in `variables.css`, the `variant="glass"`/direct-redesign rationale
  comments scattered across `StatusBadge`, `EmptyState`, `PageHeader`,
  `StatTile`, `AssessmentPanel`, `MatchGraph.jsx`/`.module.css`, and each
  page's "V2 design system" CSS header comment) — they explained why two token
  sets coexisted; once there is only one token set, they document a historical
  decision, not a live constraint, and should either be deleted or condensed to
  a single line if genuinely still useful context.
- **`variant="glass"` props become unconditional/removable** on `StatusBadge`,
  `EmptyState`, and `PageHeader` once there is no more default (light) render
  path left for any of them to preserve — a judgment call on whether to
  actually remove the prop plumbing now or leave it as an inert no-op prop;
  removing it is more thorough but touches every call site again, so weigh
  that against how much of this task's time budget is left.
- **The "V2 page panel" background block, now duplicated across ten page CSS
  files** (Dashboard + nine operational pages, `DashboardPage.module.css`
  twice), is a natural thing to finally extract into a shared class (in
  `global.css` or a small new plain-CSS file, applied via className
  composition) while this file is already being touched for the rename — not
  mandatory for the fold itself, but the cheapest time to do it is now, not a
  twelfth duplication later.
- **Verification for this task is different in kind from every prior slice**:
  since no page's visible colors should change (only which token name produces
  them), the bar is "confirm nothing moved," not "confirm a new look appeared"
  — diff the resolved CSS custom property values before/after for a sample of
  pages if possible, confirm the build's CSS bundle size doesn't shift
  unexpectedly, and re-run the full live-route + live-data checks every prior
  phase has used.
- **This is now the only queued frontend task** — the readiness audit that
  used to sit ahead of it in the queue (see §24's own history above) ran as
  Phase 30 and passed (**DEMO READY**, `handoff.md` has the full record). This
  fold can proceed now — nothing else is blocking it.

---

*(Preserved for historical reference — this task is DONE, as Phase 30; see
§19/§20 and `handoff.md` for the full record. Kept here only so a future
session can see exactly what was asked and confirm none of it was missed.)*
**Final production/demo readiness audit — whole system, no feature work.**
With every planned feature done, this task is a last
end-to-end pass across the *whole* system — both backends and the frontend
together — treating it as "would this survive a live 4–6 minute demo and a
jury's questions right now," not as a place to design anything new. Concretely:

- **Live, from-scratch verification**: a real `docker compose down` + `up` of the
  entire stack (all four services + Postgres), then walk the actual demo sequence
  the UX audit itself proposed (Dashboard → Upload → Candidate Detail → New Job →
  Job Detail → Applications → Interviews → Reports → Report Detail → Knowledge
  Graph) end to end against the freshly-started stack, confirming every step still
  works from cold — not from an environment that's accumulated hours of prior
  sessions' state.
- **Check `handoff.md`'s own Known Limitations** for anything still marked
  unresolved that a jury could plausibly hit live (e.g. any lingering demo-data
  hygiene notes) and confirm which of them still apply against real current data.
- **A final `.env`/secrets/config sanity check** — nothing demo-breaking left
  half-configured, no stale seeded credentials that changed, no service left
  pointing at the wrong port after all the container rebuilds this project's own
  history has been through.
- **Confirm test counts and green status one more time, across every service**,
  not just the frontend's own — a true "is everything actually still green"
  snapshot to close out the project, not a repeat of any single phase's own
  verification.
- Explicitly **not** in scope: any new page, any new endpoint, any further visual
  redesign, any of the UX audit's deprioritized items, 3D. This task finds and
  fixes only what would genuinely embarrass the demo — it does not invent new work.

`handoff.md` should be updated by this next task if (and only if) it finds a real
cross-service issue; if it finds nothing beyond frontend-only observations, this
file (`FRONTEND_HANDOFF.md`) is the right place, per §23's own rule.
