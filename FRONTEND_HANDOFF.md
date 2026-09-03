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
│   │                   # RWS resource -- auth.js/resumes.js/jobs.js/candidates.js/
│   │                   # applications.js (selectMatch/getApplications/invite) all
│   │                   # exist; accept()/reject() land with Phase 7, see §20/§24
│   ├── auth/           # Plain JS (no React) JWT storage + decode -- callable from
│   │                    # anywhere, including api/client.js, without a hook
│   ├── context/          # AuthContext -- the one global store this app has
│   ├── router/            # AppRouter (route table) + ProtectedRoute (auth guard)
│   ├── hooks/               # Reusable custom hooks -- useCandidateIdentity exists;
│   │                          # usePolling remains proposed (Phase 6)
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
| Upload a resume | `api/resumes.js: uploadResume(file)` *(implemented)* | `POST /v1/resumes` | `candidate_id`, `is_new` | `401` no/invalid JWT · `422` unsupported file type · `502` Resume Intelligence unreachable/failed |
| List jobs | `api/jobs.js: getJobs()` *(implemented)* | `GET /v1/jobs` **IMPLEMENTED** | `job_id`, `title`, `created_at`, `match_count` (`JobSummaryResponse`, newest-first) | `401` no/invalid JWT |
| Create a job from a JD | `api/jobs.js: createJob({ title, raw_description, threshold?, top_k? })` *(implemented)* | `POST /v1/jobs` | `job_id`, `job_description_id`, `version`, `title`, `required_skills`, `preferred_skills`, `min_years_experience`, `titles`, `threshold`, `scoring_config_version`, `matches: MatchResultResponse[]` (matches here have `explanation: null` — see next row) | `502` JD structuring / Second Brain retrieval or lookup failed · `422` unknown `scoring_config_version` · `500` no scoring config exists |
| View ranked candidates | `api/jobs.js: getJobMatches(jobId)` *(implemented)* | `GET /v1/jobs/{job_id}/matches` | `match_id`, `candidate_id`, `semantic_score`, `skill_score`, `experience_score`, `title_score`, `deterministic_score`, `final_score`, `hard_gate_passed`, `matched_requirements`, `missing_requirements`, `qualified`, `explanation` (populated lazily here, unlike the `POST /v1/jobs` response) | `404` job not found |
| Resolve a candidate's display name | `api/candidates.js: getCandidateIdentity(candidateId)` *(implemented)* | `GET /v1/candidates/{candidate_id}/identity` **IMPLEMENTED** | `candidate_id`, `name`, `email` (`CandidateIdentityResponse` — no `phone`, no other fields) | `401` no/invalid JWT · `404` unknown candidate · `502` Resume Intelligence unavailable |
| Select a qualified candidate | `api/applications.js: selectMatch(matchId)` *(implemented)* | `POST /v1/matches/{match_id}/select` | `ApplicationResponse` (see below) | `404` match not found · `422` not qualified · `409` already selected |
| Track applications for a job | `api/applications.js: getApplications(jobId)` *(implemented)* | `GET /v1/jobs/{job_id}/applications` | `ApplicationResponse[]` | `404` job not found |
| Invite to VoiceHR | `api/applications.js: invite(applicationId, language?)` *(implemented)* | `POST /v1/applications/{application_id}/invite` | `ApplicationResponse` (now `status: "invited"`) | `404` not found · `409` not invitable in current status · `422` candidate contact not found · `502` contact lookup or VoiceHR call failed |
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

**Cache** (module-level singleton, outside React state — `hooks/useCandidateIdentity.js`,
**implemented**):
- Candidate identity, keyed by `candidate_id`, shared read access across every
  `CandidateRow` on `JobDetailPage` and every `ApplicationRow` on `ApplicationsPage` so
  the same candidate's name isn't re-fetched on every row re-render or page revisit
  within a session. A failed lookup is cached too (`status: "error"`), so a broken
  `candidate_id` degrades once per session rather than being hammered on every render.

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
  back to `/jobs` via the sidebar, or forward to `/jobs/:jobId/applications` via a new
  "View Applications" link in the header (added this pass, alongside
  `ApplicationsPage` itself — see below).
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

### ApplicationsPage (`/jobs/:jobId/applications`) — **implemented (partial — see §24)**
- **Purpose**: track selected candidates through invitation. Polling, VoiceHR
  assessment rendering, and Accept/Reject are explicitly out of scope for this pass
  (Phase 6/7 — see §24); this page currently shows a static snapshot, refreshed only on
  navigation/reload.
- **API calls**: `GET /v1/jobs/{job_id}/applications` via
  `api/applications.js: getApplications()` (always re-fetched on mount, keyed off the
  `:jobId` route param — same "never trust navigation state" pattern as
  `JobDetailPage`, so a hard refresh on this URL reflects real backend state); `GET
  /v1/jobs` via `getJobs()` as a **best-effort** header source (same pattern as
  `JobDetailPage` — no dedicated `GET /v1/jobs/{id}` exists on RWS); `GET
  /v1/candidates/{id}/identity` per row via the existing `useCandidateIdentity` hook;
  `POST /v1/applications/{id}/invite` via `api/applications.js: invite()` on
  Invite/Resend Invite.
- **Primary components**: a table with one `ApplicationRow` per application
  (resolved/fallback name, a status badge, an interview-status cell showing `—` when
  `interview_status` is `null`, and an Invite/Resend-Invite button). `ApplicationRow`
  is a page-local function inside `ApplicationsPage.jsx` (not `components/`), same
  reasoning as `JobDetailPage`'s `CandidateRow` — promote only once genuinely shared.
  No `AssessmentPanel` and no Accept/Reject buttons this pass (Phase 6/7).
- **State**: `job` (best-effort header), `applications`, `loading`, `error` at the page
  level; per-row `inviting`, `inviteError` inside each `ApplicationRow`.
- **Actions**: Invite/Resend Invite — shown **only** when `status` is `"selected"` or
  `"invited"` (`INVITABLE_STATUSES`, mirroring `main.py: invite_application`'s own
  gate; a UX hint, not a substitute for RWS's real `409` check — §22), labeled "Invite"
  for `selected` and "Resend Invite" for `invited`, disabled while the request is in
  flight. On success, the row is updated in place from the real returned
  `ApplicationResponse` (no local status guessing); on failure, an inline error banner
  shows the backend's own `detail` (`409`/`422`/`502` all rendered verbatim via
  `ApiError.message`, per §14 — no special-cased wording for any one status).
- **Loading/empty/error**: "Loading applications…" / "No applications yet — select a
  candidate from the ranking table." / error banner with the backend's own `detail`. A
  per-row candidate-identity failure never blocks the table (same
  `useCandidateIdentity` fallback as `JobDetailPage`: a short `candidate_id` prefix
  with an inline "(name unavailable)" note).
- **Navigation behavior**: reachable from `JobDetailPage` via a new "View Applications"
  link in its header (`JobDetailPage.jsx`, this pass — the only route to this page,
  since it's job-scoped and the sidebar's nav is global); no navigation forced from
  this page itself — a recruiter goes back to `/jobs/:jobId` or `/jobs` via the
  sidebar.

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
- [x] **Phase 2 — Resume upload**
- [x] **Phase 3 — Jobs list + job creation**
- [x] **Phase 4 — Candidate ranking / identity / selection**
- [x] **Phase 5 — Applications + invitation** *(list + invite/resend-invite only, by
  explicit scope — polling and assessment display are Phase 6, accept/reject is Phase
  7)*
- [ ] **Phase 6 — Polling + VoiceHR assessment display** *(next — see §24)*
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
- `src/pages/ResumeUploadPage.jsx` + `.module.css` — fully functional: click-or-drop
  file selection, upload via `api/resumes.js`, loading/success/error states, "Upload
  another resume" reset.
- `src/api/resumes.js` (`uploadResume(file)`).
- `src/api/client.js`'s `request()` — accepts a `FormData` body (used by
  `uploadResume`) alongside the existing JSON-body path; no `Content-Type` is set when
  the body is `FormData`, so the browser supplies the correct multipart boundary
  itself. The JSON path (used by `login()`) is unchanged.
- `src/pages/JobsListPage.jsx` + `.module.css` — fully functional: fetches and renders
  the real jobs list, and a "New Job" creation form.
- `src/pages/JobDetailPage.jsx` + `.module.css` — fully functional: fetches and renders
  real ranked matches, with per-row candidate-identity resolution, expandable
  score/requirements/explanation detail, candidate selection, and (**new this pass**) a
  "View Applications" header link to `/jobs/:jobId/applications`.
- `src/pages/ApplicationsPage.jsx` + `.module.css` — **fully functional as of this
  pass** for its current scope: fetches and renders the real applications list for a
  job, per-row candidate-identity resolution, status + interview-status display, and
  Invite/Resend Invite. Polling, VoiceHR assessment rendering, and Accept/Reject are
  explicitly not built this pass (Phase 6/7 — see §24).
- `src/api/jobs.js` (`getJobs()`, `createJob()`, `getJobMatches()`).
- `src/api/candidates.js` (`getCandidateIdentity()`).
- `src/api/applications.js` — `selectMatch()` (prior pass); `getApplications(jobId)`
  and `invite(applicationId, language?)` (**new this pass**). `accept()`/`reject()`
  remain unbuilt (Phase 7).
- `src/hooks/useCandidateIdentity.js` — module-level in-memory cache (§8, §9) plus the
  resolving hook itself; a lookup failure resolves to a distinct `"error"` status
  rather than throwing, so it can never break the page it's used on. Reused as-is by
  `ApplicationsPage` this pass — no changes needed to the hook itself.
- `src/styles/variables.css`, `src/styles/global.css` — unchanged.
- **Not yet created**: `src/api/applications.js`'s `accept()`/`reject()` (Phase 7);
  `src/hooks/usePolling.js` (Phase 6); `AssessmentPanel` / any VoiceHR-assessment
  rendering on `ApplicationsPage` (Phase 6); Accept/Reject buttons (Phase 7) — see §24.

**Build verified**: `npm install` (66 packages, 0 errors) and `npm run build` both
succeeded before this pass (61 modules) and after adding the Applications +
invitation workflow (62 modules, built cleanly in ~0.9s, `dist/` produced, 0
errors/warnings).

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

**Live-tested — Applications + invitation workflow (this pass)**, through the exact
dev-proxy path, issuing the same requests `ApplicationsPage`/`api/applications.js`
issue, against real applications already present from prior live-testing (one
`"selected"` application from the Jobs-experience pass, and one pre-existing
`"interviewed"` application — with a real `interview_assessment` — from an even
earlier end-to-end pass):
- `GET /v1/jobs/{job_id}/applications`: unauthenticated → real `401`; authenticated →
  real `200` with both real applications, exactly the shape `ApplicationRow` consumes
  (`status`, `interview_status`, `interview_assessment` present but — correctly for
  this pass's scope — not read by any component).
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

**Applications + invitation workflow** is now **done and verified for its scoped
slice** (§19, §20): `ApplicationsPage` fetches and renders the real applications list
for a job (`GET /v1/jobs/{job_id}/applications`), resolves each candidate's name via
the existing `useCandidateIdentity` hook, shows current `status` and `interview_status`,
and supports Invite/Resend Invite (`POST /v1/applications/{id}/invite`) gated on the
same statuses RWS itself accepts. `JobDetailPage` gained a "View Applications" link so
the page is reachable from a selected candidate. All of it was exercised live against
the real running stack, including a real invite, a real resend, a real `409` on a
non-invitable application, a real `404` on an unknown one, and confirmation that a
fresh reload reflects true backend state — see §20 for the full results. Explicitly
**not** built this pass, by the task's own scope: polling, VoiceHR assessment
rendering, and Accept/Reject.

**Polling + VoiceHR assessment display** (Phase 6 — extends `ApplicationsPage`,
`/jobs/:jobId/applications`):

- Build `hooks/usePolling.js` (§12): polls `GET /v1/jobs/{job_id}/applications` every
  ~6–8s, enabled only while at least one visible application is `status: "invited"`,
  paused on `document.hidden`, resumed on `visibilitychange`, stopped entirely once
  nothing is pending — no manual "stop polling" control needed. Wire it into
  `ApplicationsPage` so an interview's completion (`invited → interviewed`, arriving
  server-side via RWS's `POST /v1/webhooks/voicehr`) becomes visible without a manual
  reload.
- Once an interview completes, render `interview_assessment` per §13's exact field
  list (`AssessmentPanel`, part of `ApplicationsPage`) — render only while
  `interview_assessment` is non-null, and branch its layout on `evaluation_status`
  rather than assuming every score field is present (e.g. on `"failed"`, scores may be
  null). Real test data already exists to verify against: the pre-existing
  `"interviewed"` application exercised this pass carries a real, complete
  `interview_assessment` (`overall_score: 93`, `cefr_level: "C1"`, `evaluation_status:
  "completed"`, etc.) — a real fixture, not one that needs to be fabricated.
- Do not implement Accept/Reject in this pass — that is Phase 7, a separate bounded
  task.
- Do not implement VoiceHR-side anything (interview conduct, candidate-facing flow) —
  entirely out of this frontend's scope, per §1/§2.

Follow §11's/§13's hard rule throughout: render `ApplicationResponse`/
`interview_assessment` fields exactly as RWS returns them — never compute, adjust, or
second-guess an evaluation, a score, or a workflow-state transition.
