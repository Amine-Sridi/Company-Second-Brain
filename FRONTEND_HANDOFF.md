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
| **react-router-dom (BrowserRouter)** | Clean URLs (`/jobs/:jobId`) rather than hash fragments — the conventional choice for a real React app. Requires one small, well-understood FastAPI serving change later (§17); not a reason to prefer `HashRouter`. |
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
│   │                   # RWS resource (auth.js exists; jobs.js/applications.js/
│   │                   # candidates.js/resumes.js are proposed, see §20)
│   ├── auth/           # Plain JS (no React) JWT storage + decode -- callable from
│   │                    # anywhere, including api/client.js, without a hook
│   ├── context/          # AuthContext -- the one global store this app has
│   ├── router/            # AppRouter (route table) + ProtectedRoute (auth guard)
│   ├── hooks/               # Reusable custom hooks (useAsync, usePolling,
│   │                          # useCandidateIdentity) -- PROPOSED, not yet created
│   ├── components/           # Reusable, presentational pieces shared across pages
│   │                           # (StatusBadge, ScoreBar, Sidebar, TopBar, ...)
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
| `/` | Protected | redirects to `/jobs` |
| `/jobs` | Protected | `JobsListPage` |
| `/upload` | Protected | `ResumeUploadPage` |
| `/jobs/:jobId` | Protected | `JobDetailPage` |
| `/jobs/:jobId/applications` | Protected | `ApplicationsPage` |
| `*` | Protected fallback | redirects to `/jobs` (or `/login` if unauthenticated, since `ProtectedRoute` runs first) |

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
| Upload a resume | `api/resumes.js: uploadResume(file)` *(proposed)* | `POST /v1/resumes` | `candidate_id`, `is_new` | `401` no/invalid JWT · `422` unsupported file type · `502` Resume Intelligence unreachable/failed |
| List jobs | `api/jobs.js: getJobs()` *(proposed, needs backend work)* | `GET /v1/jobs` **PROPOSED** | not yet defined — likely `job_id`, `title`, `created_at` | n/a until implemented |
| Create a job from a JD | `api/jobs.js: createJob({ title, raw_description, threshold?, top_k?, scoring_config_version? })` *(proposed)* | `POST /v1/jobs` | `job_id`, `job_description_id`, `version`, `title`, `required_skills`, `preferred_skills`, `min_years_experience`, `titles`, `threshold`, `scoring_config_version`, `matches: MatchResultResponse[]` (matches here have `explanation: null` — see next row) | `502` JD structuring / Second Brain retrieval or lookup failed · `422` unknown `scoring_config_version` · `500` no scoring config exists |
| View ranked candidates | `api/jobs.js: getJobMatches(jobId)` *(proposed)* | `GET /v1/jobs/{job_id}/matches` | `match_id`, `candidate_id`, `semantic_score`, `skill_score`, `experience_score`, `title_score`, `deterministic_score`, `final_score`, `hard_gate_passed`, `matched_requirements`, `missing_requirements`, `qualified`, `explanation` (populated lazily here, unlike the `POST /v1/jobs` response) | `404` job not found |
| Resolve a candidate's display name | `api/candidates.js: getCandidateIdentity(candidateId)` *(proposed, needs backend work)* | `GET /v1/candidates/{candidate_id}/identity` **PROPOSED** | not yet defined — see §9 for the expected minimal shape | n/a until implemented |
| Select a qualified candidate | `api/applications.js: selectMatch(matchId)` *(proposed)* | `POST /v1/matches/{match_id}/select` | `ApplicationResponse` (see below) | `404` match not found · `422` not qualified · `409` already selected |
| Track applications for a job | `api/applications.js: getApplications(jobId)` *(proposed)* | `GET /v1/jobs/{job_id}/applications` | `ApplicationResponse[]` | `404` job not found |
| Invite to VoiceHR | `api/applications.js: invite(applicationId, language)` *(proposed)* | `POST /v1/applications/{application_id}/invite` | `ApplicationResponse` (now `status: "invited"`) | `404` not found · `409` not invitable in current status · `422` candidate contact not found · `502` contact lookup or VoiceHR call failed |
| Accept | `api/applications.js: accept(applicationId)` *(proposed)* | `POST /v1/applications/{application_id}/accept` | `ApplicationResponse` (now `status: "accepted"`) | `404` not found · `409` not in a decidable state |
| Reject | `api/applications.js: reject(applicationId)` *(proposed)* | `POST /v1/applications/{application_id}/reject` | `ApplicationResponse` (now `status: "rejected"`) | `404` not found · `409` not in a decidable state |

`ApplicationResponse` fields (from `schemas.py`, used by select/invite/accept/reject/list
above): `application_id`, `job_id`, `match_result_id`, `candidate_id`, `status`
(`"selected" | "invited" | "interviewed" | "accepted" | "rejected"`), `selected_at`,
`invite_attempt_number`, `invited_at`, `interview_status`
(`"completed" | "inconclusive" | "failed" | null`), `interview_assessment` (`dict | null`
— see §13), `interview_completed_at`, `decided_at`.

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

**Cache** (module-level singleton, outside React state — proposed, `hooks/
useCandidateIdentity.js`):
- Candidate identity, keyed by `candidate_id`, shared read access across
  `JobDetailPage` and `ApplicationsPage` so the same candidate's name isn't re-fetched
  on every page visit within a session.

**Temporary** (local to `ApplicationsPage`, via `hooks/usePolling.js` — proposed):
- The interview-status poll timer. Created on mount while needed, cleared on unmount or
  once no application is left in `invited` status.

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

**The RWS identity proxy** (`GET /v1/candidates/{candidate_id}/identity`) is
**PROPOSED, not yet implemented**. The pattern it should follow already exists and
works in RWS today for a different purpose: `resume_intelligence_client.get_contact()`
(used internally, server-side only, inside `invite_application` to fetch a candidate's
name/email right before calling VoiceHR — never returned to any HTTP caller today). The
identity endpoint should be a thin, JWT-protected wrapper around that same call:
- Resume Intelligence remains the PII owner; RWS never persists what it returns.
- The call is transient — made fresh per request, not cached in RWS's own database.
- The response should carry the minimum needed for display: a `name`. Do not add
  `phone` to this endpoint or render it anywhere in this frontend unless a concrete
  screen genuinely needs it (none currently does — invitation is entirely server-side
  in RWS already, via the existing internal `get_contact()` call). `email` may be
  present in the payload (Resume Intelligence's `ContactLookupResponse` includes it),
  but the frontend should not render it by default in list views — keep the visible
  surface to `name` unless a specific screen has a stated reason to show more.

**Frontend caching rule**: `useCandidateIdentity` caches resolved names in an
in-memory `Map`, scoped to the current browser session/runtime only (a plain module
variable, not `sessionStorage`/`localStorage`). It is deliberately not persisted to any
browser storage and not sent anywhere but the identity endpoint itself — this keeps the
frontend from becoming a third place candidate PII is retained, even transiently across
reloads.

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

### JobsListPage — **placeholder only**
- **Purpose**: landing page after login; browse existing jobs, start a new one.
- **API calls (planned)**: `GET /v1/jobs` **(needs backend work first)**, `POST
  /v1/jobs`.
- **Primary components (planned)**: `JobCard` list, an inline "New Job" form (title +
  JD textarea, threshold/top_k tucked behind a collapsed "Advanced" section with the
  backend's own defaults — `threshold=0.6`, `top_k=20` — pre-filled).
- **State (planned)**: `jobs`, `loading`, `error`, new-job form fields, `creating`.
- **Actions (planned)**: create job → on success, navigate to `/jobs/{job_id}` to view
  its ranked matches immediately (the `POST /v1/jobs` response already includes them).
- **Loading/empty/error**: "Loading jobs…" / "No jobs yet — create one to get
  started." / error banner with backend `detail`.
- **Navigation behavior**: clicking a job card → `/jobs/:jobId`.

### ResumeUploadPage — **placeholder only**
- **Purpose**: add a candidate's resume to the pool (independent of any specific job).
- **API calls (planned)**: `POST /v1/resumes` via `api/resumes.js: uploadResume(file)`.
- **Primary components (planned)**: file input (`accept=".pdf,.docx,.jpg,.jpeg,.png"`
  as a UX hint only — the backend is authoritative on rejection), result panel.
- **State (planned)**: selected `file`, `uploading`, `result` (`{candidate_id, is_new}`
  or `null`), `error`.
- **Actions (planned)**: pick file → submit → show `candidate_id`/`is_new` on success.
- **Loading/empty/error**: "Uploading…" / no explicit empty state (form-only page) /
  error banner with backend `detail` (e.g. `422` for an unsupported type).
- **Navigation behavior**: none required — a standalone action; no forced redirect
  after success.

### JobDetailPage (`/jobs/:jobId`) — **placeholder only**
- **Purpose**: the ranked-candidates view — the core recruiter screen.
- **API calls (planned)**: `GET /v1/jobs/{job_id}/matches`; `GET
  /v1/candidates/{id}/identity` per visible row (via `useCandidateIdentity`); `POST
  /v1/matches/{match_id}/select` on Select.
- **Primary components (planned)**: `JobHeader` (title/requirements/threshold — held in
  page state from navigation, or re-derived once a job-detail source exists), `Candidat
  eTable` → `CandidateRow` (resolved name, `ScoreBar`, qualified badge,
  `RequirementChips` for matched/missing, expandable `ExplanationPanel`, `SelectButton`).
- **State (planned)**: `matches`, `loading`, `error`, `selectingMatchId` (per-row
  in-flight indicator).
- **Actions (planned)**: expand a row for score detail; Select (disabled once
  `!qualified` or already selected) → on success, navigate to
  `/jobs/:jobId/applications`.
- **Loading/empty/error**: "Loading candidates…" / "No candidates matched this job." /
  error banner (e.g. `404` job not found).
- **Navigation behavior**: Select → `/jobs/:jobId/applications`.

### ApplicationsPage (`/jobs/:jobId/applications`) — **placeholder only**
- **Purpose**: track selected candidates through invite → interview → decision.
- **API calls (planned)**: `GET /v1/jobs/{job_id}/applications` (initial load + polled);
  `GET /v1/candidates/{id}/identity` per row; `POST
  /v1/applications/{id}/invite`; `POST .../accept`; `POST .../reject`.
- **Primary components (planned)**: `ApplicationTable` → `ApplicationRow` (resolved
  name, `StatusBadge`, Invite/Resend-Invite button, `AssessmentPanel` once
  `interview_assessment` is non-null, Accept/Reject buttons).
- **State (planned)**: `applications`, `loading`, `error`, per-row action-in-flight
  flags.
- **Actions (planned)**: Invite (label depends on `status`: "Invite" when `selected`,
  "Resend Invite" when `invited`); Accept/Reject (available once status is `selected`,
  `invited`, or `interviewed`, per the backend's own transition rule — the frontend
  does not restrict this further than the backend already does).
- **Loading/empty/error**: "Loading applications…" / "No applications yet — select a
  candidate from the ranking table." / error banner.
- **Navigation behavior**: none forced; recruiter navigates back to `/jobs/:jobId` or
  `/jobs` via the sidebar.

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

**Polling** (PROPOSED — `hooks/usePolling.js`, not yet created):
- Runs only on `ApplicationsPage`.
- `GET /v1/jobs/{job_id}/applications` every **~6–8 seconds**.
- Only while **at least one visible application has `status: "invited"`** — the hook's
  `enabled` flag is derived from the current applications list, so it turns itself off
  automatically once nothing is left waiting.
- **Pauses when `document.hidden`** (tab not visible) and resumes via the
  `visibilitychange` event.
- **Stops entirely** once no application is in a pending-interview state — no manual
  "stop polling" button needed.
- **No WebSockets** — polling a list endpoint is sufficient for this flow's timing and
  matches the project's explicit no-new-infrastructure stance.

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

`AssessmentPanel` (planned, part of `ApplicationsPage`, §10) should render only while
`interview_assessment` is non-null, and should branch its layout on `evaluation_status`
rather than assuming all score fields are always present.

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

## 15. UX / visual direction

This should read as a real, lightweight ATS/recruiter dashboard — not an "AI product
demo." Concretely:

- Clean sidebar navigation (implemented: Jobs, Upload Resume) + a top bar showing the
  logged-in recruiter and Logout (implemented).
- Restrained color palette — one primary accent color (`--color-primary`), neutral
  surfaces/borders, and semantic colors used only for status/error, not decoration
  (already the shape of `variables.css`).
- Readable tables/cards over anything dense or cramped — this is presented on a laptop
  or projector to a jury, not optimized for information density.
- Status badges with clear, distinct colors per state
  (`selected/invited/interviewed/accepted/rejected`).
- Strong information hierarchy: `final_score` and `qualified` are the first things a
  recruiter should see per candidate; component scores, matched/missing requirements,
  and explanation are one interaction away (expand), not all shown at once.
- Score visualization without gimmicks — a plain horizontal bar or number is enough; no
  chart library, no radial gauges, no animated counters.
- Expandable match details (collapsed by default) rather than a permanently dense row.
- Minimal animation — at most a subtle loading-state transition; nothing decorative.
- Laptop/projector-first layout; basic responsive behavior only (sidebar collapses
  under `~768px` — already implemented in `Layout.module.css`), not a full mobile
  redesign.

**Where "AI" should visibly show up**: the match explanation text, the score
breakdown/insight it's attached to, and the VoiceHR assessment — i.e., in the *content*
RWS already generated, not in the frontend's own visual branding. No AI-themed iconography,
gradients, or "powered by AI" chrome — the product should look like a hiring tool that
happens to use AI internally, not an AI showcase.

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
and in the eventual production same-origin serving (§17). Never hardcode an absolute
backend URL anywhere in `src/`.

Verified working: `npm install` and `npm run build` both succeed cleanly (0 errors, 53
modules, ~1.6s build). A live login smoke test through this exact proxy path against the
real running RWS container returned a real JWT on valid credentials and the backend's
own `"Invalid email or password"` on an invalid one.

---

## 17. Production serving plan (PROPOSED — Phase 9, not yet implemented)

Intended shape: `npm run build` → `frontend/dist/` → served by RWS's own FastAPI app,
same-origin (no CORS configuration needed anywhere).

**Critical ordering rule**: FastAPI/Starlette matches routes in registration order —
first match wins. `main.py` already defines every `/v1/*` route and `/health` via
decorators. The SPA-serving routes must be added **last**, after all of those, so they
only ever catch a request that didn't match an API route first:

```python
# main.py -- appended AFTER every existing @app.get/post(...) route, never before

app.mount("/assets", StaticFiles(directory="frontend/dist/assets"), name="frontend-assets")

@app.get("/", include_in_schema=False)
@app.get("/{full_path:path}", include_in_schema=False)
async def serve_react_app(full_path: str = ""):
    return FileResponse("frontend/dist/index.html")
```

`/assets/*` (Vite's hashed JS/CSS bundle output) is served directly; every other
non-API path falls through to `index.html`, letting `BrowserRouter` take over
client-side. As long as this block stays physically after the `/v1/*` route
definitions in the file, there is no shadowing risk.

**Docker**: Node is needed only to *build* the frontend, never in the runtime image —
multi-stage `Dockerfile`:

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
COPY --from=frontend-build /frontend/dist /app/frontend/dist
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

A root-level `.dockerignore` (does not currently exist anywhere in this repo) should
exclude `**/frontend/node_modules` and `**/frontend/dist` from the Docker build
*context* before this is implemented, so `docker build` doesn't upload a large
`node_modules` tree unnecessarily.

None of this is implemented yet — it is documented here so Phase 9 doesn't need to
re-derive it.

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
- [ ] **Phase 2 — Resume upload**
- [ ] **Phase 3 — Jobs list + job creation** *(blocked on `GET /v1/jobs` backend work — see §20/§21)*
- [ ] **Phase 4 — Candidate ranking / identity / selection** *(blocked on `GET /v1/candidates/{id}/identity` backend work)*
- [ ] **Phase 5 — Applications + invitation**
- [ ] **Phase 6 — Polling + VoiceHR assessment display**
- [ ] **Phase 7 — Accept/reject**
- [ ] **Phase 8 — Visual polish / responsive states**
- [ ] **Phase 9 — Docker production build + FastAPI serving**
- [ ] **Phase 10 — Final frontend E2E/demo audit**

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
- `src/router/AppRouter.jsx`, `src/router/ProtectedRoute.jsx`.
- `src/pages/Layout.jsx` + `.module.css`, `src/components/Sidebar.jsx` + `.module.css`,
  `src/components/TopBar.jsx` + `.module.css`.
- `src/pages/LoginPage.jsx` + `.module.css` — fully functional, not a placeholder.
- `src/pages/JobsListPage.jsx`, `ResumeUploadPage.jsx`, `JobDetailPage.jsx`,
  `ApplicationsPage.jsx` — **placeholder content only** (a heading + "coming in a
  future pass"), routes wired correctly including `:jobId` param extraction.
- `src/styles/variables.css`, `src/styles/global.css`.
- **Not yet created**: `src/api/jobs.js`, `applications.js`, `candidates.js`,
  `resumes.js`; `src/hooks/` directory (does not exist yet — no `useAsync`,
  `usePolling`, or `useCandidateIdentity` implemented); any component beyond
  `Sidebar`/`TopBar` (`StatusBadge`, `ScoreBar`, `RequirementChips`, etc. do not exist
  yet).

**Build verified**: `npm install` (66 packages, 0 errors) and `npm run build` (53
modules, built cleanly in ~1.6s, `dist/` produced) both succeeded.

**Live-tested**: `POST /v1/auth/login` through the Vite dev proxy against the real
running RWS container — valid credentials returned a real JWT (`200`); invalid password
returned the real backend `401` with `{"detail": "Invalid email or password"}`. Not yet
tested: the rendered UI in an actual browser (no browser available in the environment
this was built in) — verification so far is at the network-contract level plus a clean
production build.

**Known backend prerequisites — do not build frontend features against these as if they
exist**:
- `GET /v1/jobs` (list all jobs) — **not implemented on RWS**. Required for
  `JobsListPage` to show anything beyond jobs created in the current browser session.
- `GET /v1/candidates/{candidate_id}/identity` — **not implemented on RWS**. Required
  before any candidate name can be displayed anywhere (§9). The closest existing
  precedent is `resume_intelligence_client.get_contact()`, already used internally by
  `invite_application` but never exposed over HTTP to RWS's own callers.

Confirmed via a direct route-decorator grep of `services/recruitment-workflow/main.py`
at the time of writing: `/health`, `/v1/auth/login`, `/v1/resumes`, `/v1/jobs` (POST
only), `/v1/jobs/{job_id}/matches`, `/v1/scoring-configs`, `/v1/matches/{match_id}/
select`, `/v1/applications/{application_id}/accept`, `/v1/applications/{application_id}/
reject`, `/v1/jobs/{job_id}/applications`, `/v1/applications/{application_id}/invite`,
`/v1/webhooks/voicehr` — no others exist.

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

**React/Vite frontend foundation + recruiter authentication.**

Verified against the repository at the time of writing: this is **already
implemented** (§19 Phase 1, §20) — login, JWT storage/handling, protected routing, and
the app shell all exist and were live-tested against the real RWS login endpoint.

The next bounded task, not yet started, is **Phase 2 — Resume upload**: build
`src/api/resumes.js` (`uploadResume(file)`, `POST /v1/resumes`, `multipart/form-data`)
and wire it into `ResumeUploadPage` (file picker, upload action, result panel showing
`candidate_id`/`is_new`, error banner on `422`/`502`). This has no backend prerequisite
— `POST /v1/resumes` already exists and is JWT-protected today.

Do not start Phase 3 (jobs list) in the same pass as Phase 2 — Phase 3 additionally
requires the `GET /v1/jobs` backend endpoint, which is a separate, explicit backend
task outside this frontend-only document's authority to schedule.
