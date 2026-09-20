# Recruiter/Admin Frontend — Handoff

Authoritative handoff for the React recruiter/admin frontend at
`services/recruitment-workflow/frontend/`. Covers frontend architecture, the
API contract it consumes, current implementation state, and the 2D/3D
knowledge graph. It does **not** cover backend implementation, system-wide
architecture, or cross-service demo readiness — those live in the repo-root
`handoff.md`; this file must never contradict it.

**Status: feature-complete, no outstanding frontend task** (see `[NEXT]` at
the end — phase 2 of the interview-round notification feature milestone 25
started, ICS/reschedule/cancel notifications, is backend-first per
`handoff.md`'s own scoping; milestone 22 added a small, real-browser-
verified badge/banner for JD-extraction validation, milestone 23 added a
distinct "Auto-repaired" badge/note on top of that for the backend's
automatic JD-repair pass, milestone 24 corrected that messaging's wording
after a backend reliability audit found it overclaiming "extraction" as the
cause for every flagged job, and milestone 25 added the interviewer-email/
location scheduling fields and a notification-status badge + resend action
for the backend's new interview-round email notifications). Every planned
page, the full V2 dark visual redesign, an
optional 3D graph mode, and the VoiceHR language eligibility gate/assessment
UI are built and have each been confirmed rendering correctly in a real
browser (not just build-verified) at least once.

---

## Current state

All 12 routes below are implemented, wired to real RWS endpoints (no mocked
data anywhere), and cross-linked (every candidate/job/report reference in one
page's data links to that entity's own detail page). The entire authenticated
app shell + all eleven post-login screens use the V2 dark "AI recruitment
command center" design system; `LoginPage` alone stays on the original light
theme (pre-auth, deliberately out of scope for the redesign).

Verified in a real browser (Playwright against a Chromium binary already
cached on this machine — no browser had ever been available in earlier
sessions, so every claim below is a genuine upgrade over "build succeeded"):
full login → Dashboard → Candidates → Candidate Detail → Jobs → Job Detail →
Applications → Report Detail → Reports → Interviews → Knowledge Graph 2D
(search + filter) → 3D → back to 2D → logout → re-login walkthrough, desktop
and mobile (390px), zero unexplained console errors, no broken cross-link.

Production build: `npm run build` → `dist/`, served same-origin by RWS's own
FastAPI app (no separate frontend server, no CORS). The 3D graph's dependency
(`3d-force-graph` + `three`, ~337 kB gzipped) is lazy-loaded — confirmed via
built-chunk inspection that it never enters the app's normal bundle; only
`/graph` even has the option to load it, and only once a user picks "3D".

---

## Architecture / invariants

**Stack**: React + Vite, `react-router-dom` (`BrowserRouter`), native `fetch`
(one wrapper, `api/client.js`), CSS Modules + a shared `variables.css` token
file, React Context for auth only (the one piece of state genuinely shared
across the tree), local component state everywhere else, `sessionStorage` for
the JWT. No Redux/Zustand/React Query/Axios/UI component library/WebSockets —
each was considered and rejected for a stated reason (see git history if the
reasoning is ever needed again; not repeated here).

**Auth boundary**: the browser holds exactly one credential, the recruiter's
JWT (`sessionStorage`, attached as `Authorization: Bearer` by `api/client.js`
to every call except login itself). A 401 on any call clears the token and
hard-redirects to `/login`. No service API key, and no VoiceHR host/port, ever
reaches frontend code — confirmed by construction (no file under `src/`
references either).

**PII rule**: RWS's own responses (`MatchResult`, `Application`) never carry a
candidate name/email — only `candidate_id`. Display names are resolved
transiently per request via `GET /v1/candidates/{id}/identity` and cached
client-side only in an in-memory `Map` (`hooks/useCandidateIdentity.js`), never
in `sessionStorage`/`localStorage` — the frontend must never become a third
place candidate PII is retained, even transiently. A failed identity lookup
degrades to a short id prefix + "(name unavailable)," never breaks the row.

**State ownership**: `AuthContext` is the only global store. Every page fetches
and holds its own data fresh on mount (no page trusts navigation state, so a
hard refresh on any detail route — `/candidates/:id`, `/jobs/:id`,
`/reports/:id` — always re-fetches and renders correctly from backend state
alone; verified live). `hooks/usePolling.js` drives the one exception:
`ApplicationsPage` polls every ~7s only while an application is `"invited"`,
to surface the webhook-driven `invited → interviewed` transition without a
manual reload.

**Hard rule, unchanged since day one**: the frontend never computes, adjusts,
or second-guesses `final_score`/`qualified`/any component score/`hard_gate_passed`.
Every one of those is rendered exactly as RWS returns it.

**Folder layout**: `api/` (only code allowed to call `fetch`), `auth/`
(framework-agnostic JWT storage/decode), `context/` (`AuthContext`, the only
context), `router/` (route table + `ProtectedRoute`), `hooks/`, `constants/`
(shared label/tone maps, promoted only once two pages need the same one),
`components/` (reusable across ≥2 pages), `pages/` (one per route, owns its
own state), `styles/` (`variables.css` tokens + `global.css` reset).

---

## Frontend routes

Verified directly against `src/router/AppRouter.jsx`.

| Route | Access | Page |
|---|---|---|
| `/login` | Public | `LoginPage` (still V1 light theme) |
| `/` | Protected | `DashboardPage` |
| `/candidates` | Protected | `CandidatesPage` |
| `/candidates/:candidateId` | Protected | `CandidateDetailPage` (knowledge, applications, ego graph) |
| `/jobs` | Protected | `JobsListPage` |
| `/upload` | Protected | `ResumeUploadPage` |
| `/jobs/:jobId` | Protected | `JobDetailPage` (ranked matches) |
| `/jobs/:jobId/applications` | Protected | `ApplicationsPage` |
| `/interviews` | Protected | `InterviewsPage` (global, unscoped) |
| `/reports` | Protected | `ReportsPage` |
| `/reports/:applicationId` | Protected | `ReportDetailPage` |
| `/graph` | Protected | `GraphPage` (2D/3D knowledge graph) |
| `*` | Protected fallback | redirects to `/` (or `/login` first if unauthenticated) |

All protected routes nest under one `ProtectedRoute` → `Layout` wrapper; no
page re-checks auth itself.

---

## API surface consumed

Every endpoint below is on RWS only (full method/auth table in `handoff.md`);
this table adds only the frontend-specific consumption notes.

| Action | Endpoint | Frontend notes |
|---|---|---|
| Login | `POST /v1/auth/login` | `api/auth.js` |
| Upload resume | `POST /v1/resumes` | `api/resumes.js` |
| List/create jobs | `GET`/`POST /v1/jobs` | `api/jobs.js`; create runs JD structuring + retrieval + scoring server-side, can take several seconds; both carry the optional `required_language`/`min_language_cefr` pair, and (milestone 36) `extraction_status`/`extraction_warnings` — `extraction_status` is `"clean"`/`"flagged"`, or (milestone 37) `"repaired"` — `JobsListPage`'s badges and `JobDetailPage`'s banner/note both read this same shape, no field mapping needed |
| Set/clear job language requirement | `PATCH /v1/jobs/{id}/language-requirement` | `api/jobs.js`'s `setJobLanguageRequirement`; `JobDetailPage`'s inline edit form |
| Ranked matches | `GET /v1/jobs/{id}/matches` | `explanation` lazily populated server-side; frontend never recomputes any score |
| Candidate identity | `GET /v1/candidates/{id}/identity` | via `hooks/useCandidateIdentity.js`, session-scoped in-memory cache only |
| All candidates | `GET /v1/candidates` | `CandidatesPage` |
| Candidate knowledge | `GET /v1/candidates/{id}/knowledge` | `CandidateDetailPage` |
| Select / invite / resend / accept / reject | `POST /v1/matches/{id}/select`, `/v1/applications/{id}/invite\|resend-invite\|accept\|reject` | `api/applications.js`; every backend `409`/`422`/`429`/`502` rendered verbatim, no special-cased wording. `accept`'s `422` when the language eligibility gate blocks it is rendered the same generic way — the frontend also disables the button itself as a UX hint (see below), but RWS's own check is the real authority. `resend-invite` (new) is a separate call from `invite`, only shown/enabled once `"invited"` — see below |
| Applications (scoped / global) | `GET /v1/jobs/{id}/applications`, `GET /v1/applications` | `ApplicationsPage` / `InterviewsPage`; each row's `interview_rounds` array is rendered by `ApplicationsPage` (new — see below), unused elsewhere |
| Schedule / reschedule / complete / cancel interview round | `POST /v1/applications/{id}/interview-rounds`, `.../{round_id}/reschedule\|complete\|cancel` | `api/applications.js`'s `createInterviewRound`/`rescheduleInterviewRound`/`completeInterviewRound`/`cancelInterviewRound`; `ApplicationsPage` only — see below. **Milestone 25**: `createInterviewRound` now also sends required `interviewerEmail`/optional `location`, and every round response carries `notification_status`/`notification_error` (phase 1 of automating human interview scheduling, see `handoff.md` milestone 41) |
| Resend interview-round notification | `POST /v1/applications/{id}/interview-rounds/{round_id}/resend-notification` | **Milestone 25.** `api/applications.js`'s `resendInterviewRoundNotification`; `ApplicationsPage` only, shown solely when a round's `notification_status === "failed"`. Unlike every other action here, a `502` is a real failure outcome to render, not a bug — resending has no other value if delivery itself fails |
| Interview report / recording | `GET /v1/interviews/{id}/report\|recording` | `ReportDetailPage`; recording played via a same-origin blob URL, RWS's proxy rewrite confirmed live — never a direct VoiceHR URL |
| Knowledge graph | `GET /v1/graph/candidates-jobs` | `GraphPage`/`MatchGraph`/`MatchGraph3D`; see below |

**Never called from this frontend**: `POST /v1/scoring-configs` (service-key
only) and `POST /v1/webhooks/voicehr` (HMAC-only, VoiceHR→RWS).

---

## 2D + 3D knowledge graph architecture

**Data**: one call to `GET /v1/graph/candidates-jobs` (jobs + edges, every edge
field copied straight off a persisted `MatchResult` — `final_score`,
`qualified`, `hard_gate_passed`, matched/missing requirements) merged
client-side with `GET /v1/candidates` for display names. Candidate nodes are
derived from the unique `candidate_id`s on edges — the backend intentionally
returns no `candidates[]` array. Both qualified and disqualified edges render
(color-coded, not filtered) — the graph is a full audit-trail view.

**2D (default view)** — `components/MatchGraph.jsx`, Cytoscape.js
(`react-cytoscapejs`), `cose` force layout for the full graph / `concentric`
for a candidate's own ego graph (embedded in `CandidateDetailPage`). Legend
click-to-toggle qualified/disqualified, a minimum-score slider, and search all
live in the sibling `components/GraphControls.jsx` (lifted out of `MatchGraph`
so the same filter state survives a 2D↔3D switch). Click-to-select renders
into the shared `components/GraphDetailPanel.jsx`.

**3D (explicit, optional mode)** — `components/MatchGraph3D.jsx`, built
directly on `3d-force-graph` (chosen over `react-force-graph`: that library
pulled in unnecessary AR/VR dependencies and a git+SSH transitive dependency;
`3d-force-graph`'s real dependency tree has neither, confirmed before
installing). Reached only via `React.lazy()` — WebGL initializes only once a
user actually clicks "3D." Reuses the same node/edge id scheme and the same
`GraphDetailPanel` as 2D (via a plain-object selection shape both renderers
produce identically), so the two views can never drift into different data
semantics or a different detail experience. Real-browser-tuned (fog density,
camera framing, node radii, glow-shell blending) after an initial pass looked
washed-out at the default camera distance — the fix and before/after
comparison are the kind of empirical tuning that doesn't need re-deriving
unless the visual result changes again.

**Shared, both views**: filters (qualified/disqualified toggle, min score) and
search are owned by `GraphPage` and passed as controlled props to whichever
renderer is mounted, so switching views never resets them. Only one renderer
is ever mounted at a time.

---

## VoiceHR language eligibility gate UI

Renders RWS's own verdict (`language_gate.py`, full detail in `handoff.md`) —
nothing here recomputes pass/fail or CEFR ordering; `constants/language.js`
holds only display vocabulary (`LANGUAGE_LABELS`, `CEFR_LEVELS`,
`LANGUAGE_GATE_LABELS`/`_TONE`, `describeLanguageGate`/`formatLanguageRequirement`
helpers), never scoring logic.

- **`JobsListPage`**: the "New Job" form's advanced options gain an opt-in
  checkbox + language/CEFR selects, submitted as `POST /v1/jobs`'s optional
  `required_language`/`min_language_cefr`. Existing job rows show `· Requires
  English · B2` when set.
- **`JobDetailPage`**: a `LanguageRequirementSection` shows `Required
  language: English · B2` (or "No language requirement configured") with an
  inline Edit/Set-requirement form that calls the new `PATCH
  .../language-requirement` — Save, Clear (both fields null), and Cancel.
  Never touches any already-selected `Application`'s own snapshot.
- **`ApplicationsPage`**: a new "Language screening" column renders
  `application.language_gate.status` via `StatusBadge` (now accepts an
  optional `title` prop, used here for a one-line explanation on hover — a
  small, additive change to the shared component, not a redesign of it). The
  Accept button is disabled (with the same explanation as its `title`) when
  the gate is `pending`/`failed`/`needs_review`; Reject is never affected.
- **`ApplicationsPage` invite/resend UX, reworked this session**: the single
  Invite/"Resend Invite" button is now two distinct, status-driven actions.
  While `"selected"`: a plain "Invite" button, or — after a failed attempt —
  "Invitation not sent" text + a "Retry" button (both call the same existing
  `invite()`/`POST .../invite`; "Retry" is not a new endpoint, just the same
  call again, which RWS/VoiceHR's own idempotency key makes reuse the
  already-created interview). This "failed" flag (`lastInviteFailed`) is
  **ephemeral component state only** — it is not part of `ApplicationResponse`
  and deliberately reverts to a plain "Invite" button on reload, since RWS
  itself has no persisted field for "attempted but failed" (a transient email
  failure is not a workflow status; see `handoff.md` milestone 29). While
  `"invited"`: "Invitation sent" text + a "Resend invitation" button, calling
  the new `resendInvite()` / `POST /v1/applications/{id}/resend-invite` — a
  genuinely different call from `invite()`, intentionally re-sending on the
  same VoiceHR interview/token rather than creating a new attempt. A resend
  failure shows an error banner (same `role="alert"` pattern as the existing
  invite/decide errors) but never reverts the row away from "Invitation
  sent." Neither state ever renders a token, link, or provider id — only
  RWS's own plain status text and error messages, verbatim.
- **`ReportDetailPage`**: a `LanguageGateSummary` block (only rendered when
  the gate isn't `not_required`) shows `Required: B2 English` / `VoiceHR: C1
  English` / `Result: <badge>`. Its `language_gate` comes from the same
  `GET /v1/applications` row `ReportDetailPage` already fetches to resolve
  the candidate/job cross-links — no new network call. One real bug found and
  fixed during real-browser verification here: when VoiceHR's assessed CEFR
  is known but the assessed language is missing (an old pre-existing report
  from before the webhook carried a `language` field), the naive template
  literal rendered the literal text "null" next to the CEFR level — fixed to
  join only the defined parts.
- **Deliberately not touched**: `InterviewsPage` (global, read-only list) does
  not show gate status — out of this task's explicitly scoped surface
  (Job Detail / Applications / Report Detail only).

---

## Interview rounds UI (full lifecycle)

Renders RWS's own post-VoiceHR interview-rounds feature (`handoff.md`
milestones 33-34) — a round's `status`/`outcome` are RWS's recorded facts,
never computed or second-guessed here; `constants/interviewRounds.js` holds
only display vocabulary (`ROUND_TYPE_LABELS`, `ROUND_OUTCOME_LABELS`,
`roundStatusLabel`/`roundStatusTone`/`describeRound`/
`describeScheduleBlockedReason`/`toDatetimeLocalInputValue`), the same
"labels and tones only" rule `constants/language.js` already follows for
the language gate. Covered by 18 Node-test-runner cases in
`constants/interviewRounds.test.js` (same zero-new-dependency convention as
`language.test.js`).

- **`ApplicationsPage` only** (per this task's explicit scope — not
  `CandidateDetailPage`, unlike the original `[NEXT]` draft's broader
  suggestion; see `[NEXT]` for what's still deferred). A new "Interview
  rounds" column sits between "Language screening" and the actions column,
  bumping every expandable row's `colSpan` from 5 to 6 accordingly.
- **Display**: each existing round renders as a `StatusBadge`
  (`variant="glass"`, matching every other badge on this page) labeled
  `"<Type>: <Status or Outcome>"` (e.g. `"Technical: Scheduled"`,
  `"Managerial: Pass"`), toned `scheduled → primary`, `cancelled →
  neutral`, completed `pass/fail/needs_review → success/danger/warning` —
  the same tone vocabulary the language gate already uses. Hovering a badge
  shows `describeRound()`'s one-line summary (type, interviewer, and —
  only while still scheduled — when). Zero rounds renders a plain `—`, the
  same empty-cell convention `Interview`/`Language screening` already use.
- **Schedule action**: a "Schedule interview" button, always visible,
  toggling an inline form (round type select, interviewer name, a native
  `datetime-local` input, optional notes) in the same cell — no modal, no
  new route, matching this page's own existing inline-actions-in-a-table-
  cell pattern (invite/resend/accept/reject all work the same way already).
  On submit, `POST /v1/applications/{id}/interview-rounds` returns only the
  new `InterviewRoundResponse` (not the whole updated application) — the
  frontend appends it onto the row's already-loaded `interview_rounds`
  array locally rather than re-fetching, since nothing else about the
  application changes when a round is created. Verified this is real
  persistence, not just optimistic local state, via a hard page reload
  after scheduling.
- **Gating**: `describeScheduleBlockedReason()` mirrors
  `create_interview_round_endpoint`'s own two checks exactly (application
  not `accepted`/`rejected`; language gate not `pending`/`failed`/
  `needs_review`) — the button stays visible but `disabled`, with the
  blocking reason as its `title` (reusing `describeLanguageGate()` for the
  gate case), the same disabled-with-tooltip convention the Accept button
  already uses for the identical gate check. RWS's own `422` remains the
  real authority regardless.
- **Live-verified** (real browser, real backend, two fresh "Smoketest"
  jobs — never the pre-existing demo applications, since this project has
  no delete endpoint for any resource): a real `POST /v1/jobs` → real
  qualified match → real `select` → real `POST .../interview-rounds` round
  trip on an application with no language requirement (button enabled,
  schedule succeeds, badge appears, survives a hard reload); a second job
  with a language requirement produces a `pending` gate and a real
  `disabled` button with the exact `describeLanguageGate()` text as its
  title. Desktop (1400px) and mobile (390px) both screenshotted — the table
  scrolls horizontally on narrow viewports exactly as it already did before
  this column existed (no new overflow). Zero new console errors; the only
  console/network errors seen (`/v1/candidates/0a699701-.../identity` 404s)
  are the same pre-existing, already-documented orphaned-candidate lookup
  this document and `handoff.md` both call out repeatedly — confirmed by
  reproducing them independently of this change (visiting Reports/
  Interviews with no interview-rounds interaction at all still produces
  them). Dashboard/Candidates/Jobs/Reports/Interviews all screenshotted
  and visually unaffected.
- **Lifecycle actions, only ever shown for a `status === "scheduled"`
  round** — a `completed`/`cancelled` round is a closed record with zero
  action buttons, matching RWS's own "terminal" framing exactly (`db.py`'s
  own state machine has no path back to `scheduled`). Each round manages
  its own action/form/loading/error state independently
  (`InterviewRoundItem`, a new sub-component of `ApplicationRow`) — acting
  on one round never disturbs a sibling round, an in-flight "Schedule
  interview" for a new round, or accept/reject/invite on the same
  application, since these all target different sub-resources (a specific
  round vs. the application itself) with nothing to corrupt by running
  independently.
  - **Reschedule**: reveals a `datetime-local` input pre-filled with the
    round's current `scheduled_at` (`toDatetimeLocalInputValue()`, the
    exact inverse of the `new Date(value).toISOString()` conversion the
    schedule form's own submit already does) + Save/Cancel, same inline-
    form visual pattern the schedule form uses. Calls
    `rescheduleInterviewRound`; the round's `status` stays `"scheduled"`.
  - **Complete**: reveals an outcome select (`pass`/`fail`/`needs_review`),
    optional notes, and Save/Cancel. Calls `completeInterviewRound`; the
    round's badge updates to the recorded outcome (e.g. `"Managerial: Needs
    review"`) and every action button disappears for it, permanently.
  - **Cancel**: a single button, guarded by the same native
    `window.confirm()` this page's own Reject button already uses (both
    terminal, both irreversible). Calls `cancelInterviewRound`; the badge
    updates to `"<Type>: Cancelled"` and every action disappears.
  - **Error handling**: RWS's `409`/`404`/`422` render verbatim in a
    `role="alert"` banner scoped to that specific round (not the whole
    row), the same no-special-casing convention every other action on this
    page already follows.
- **Live-verified** (real browser, real backend, one fresh "Smoketest"
  application across two passes — never the pre-existing demo data):
  scheduled three rounds via the existing (unaffected) schedule UI, then
  rescheduled one (form correctly pre-filled with its current
  `scheduled_at`, badge stayed `"Scheduled"`, survived a hard reload),
  completed a second with outcome `needs_review` (badge became `"Managerial:
  Needs review"`, notes persisted, zero action buttons remained, survived a
  hard reload), and cancelled a third (badge became `"Technical:
  Cancelled"`, zero action buttons remained, survived a hard reload) — the
  one round left `"scheduled"` throughout kept its full set of three action
  buttons the entire time. A direct API call confirmed RWS still rejects
  (`409`, naming the round's real current status) a second `complete` call
  on an already-completed round, proving the backend contract independent
  of whatever the UI currently shows. Mobile (390px) screenshotted — same
  horizontal-scroll behavior the table already had, no new overflow. Zero
  new console errors (the only ones seen are the same pre-existing
  `/v1/candidates/0a699701-.../identity` 404, reproduced independently of
  this change in the prior slice's own verification).
- **`InterviewsPage` untouched** — same "VoiceHR language gate separate"
  precedent as above.
- **`CandidateDetailPage` also shows interview rounds, read-only (milestone
  20)** — its Applications table already fetched `interview_rounds` on
  every row (the same `GET /v1/applications` `ApplicationsPage` uses), just
  never rendered it; a recruiter reviewing one candidate's full history
  across every job they've applied to had no way to see interview activity
  without opening each job's own Applications page. Reuses
  `ROUND_TYPE_LABELS`/`roundStatusTone`/`roundStatusLabel`/`describeRound`
  exactly as `ApplicationsPage` does — same badges, same tones, same
  tooltip — with zero action buttons (no Schedule/Reschedule/Complete/
  Cancel here; those stay `ApplicationsPage`-only, by design, matching this
  page's own existing read-only relationship to every other application
  field it shows). Zero new API calls.
- **Milestone 25: notification fields/badge, phase 1 of automating human
  interview scheduling (`handoff.md` milestone 41).** The schedule form
  gained a required "Interviewer email" input (`type="email"`) and an
  optional "Meeting link or location" input, both sent straight through to
  `POST .../interview-rounds`. Each round now renders a *second*
  `StatusBadge` next to its existing type/status badge —
  `NOTIFICATION_STATUS_LABELS`/`notificationStatusTone`
  (`constants/interviewRounds.js`, same "labels/tones only" rule) toned
  `sent → success`, `failed → danger`, `pending → neutral` — with a
  "Resend notification" action button that appears only once
  `notification_status === "failed"` (calls the new
  `resendInterviewRoundNotification`; its `502` on failure renders in the
  same per-round `role="alert"` banner every other round action already
  uses, since a resend's own failure is a real outcome to show, not
  swallowed as a partial success). `CandidateDetailPage`'s read-only
  rendering is unaffected (it never showed round actions to begin with, and
  the notification badge is deliberately kept `ApplicationsPage`-only too,
  same reasoning as every other round action).

  **Live-verified** (real browser, real backend, the same already-curated
  `"Email Delivery Smoke Test Engineer"` job's Applications page): scheduled
  a real round with a real interviewer email and location — round created,
  "Notification failed" badge rendered (no `RESEND_API_KEY` configured live
  either, exactly the designed contract), "Resend notification" button
  appeared next to Reschedule/Complete/Cancel. Zero new console errors
  beyond the same pre-existing, already-documented `/v1/candidates/
  0a699701-.../identity` 404 this section already calls out twice above.

---

## Known limitations

- **Interview-round *actions* (schedule/reschedule/complete/cancel) only
  exist on `ApplicationsPage`** — `CandidateDetailPage` shows the same
  rounds read-only (milestone 20) but has no form/button for any of the
  four actions, by design: this page has never had any application-mutating
  action of its own (not even Accept/Reject), only cross-links and display,
  so adding one action just for rounds would be inconsistent with how every
  other field on this page already works. Revisit only if a real recruiter
  workflow need for acting on a round from this specific page shows up.
- **`JobDetailPage` has no interview-round summary, correctly** —
  confirmed during the post-VoiceHR UX audit (milestone 20), not a silent
  gap: this page never fetches `Application`s at all (only `MatchResult`s;
  a match isn't an application until selected), and its existing "View
  Applications" link already goes straight to the page with full round
  detail. Adding a summary here would need a new API call plus match/
  application reconciliation logic — not a small fix, and no concrete need
  for it surfaced during the audit's real-browser walkthrough.
- **`DashboardPage` doesn't reflect interview-round activity, correctly** —
  also confirmed during milestone 20: rounds are advisory, not a pipeline
  stage (the same reason they never gate `accept`/`reject`), so a
  dashboard-level count or recent-activity entry for them wouldn't
  correspond to any real funnel state worth surfacing at that level.
- **`LoginPage` still renders the original V1 light theme** — deliberate, a
  pre-auth screen never in scope for the V2 redesign.
- **`--v2-*` and `--color-*` remain two separate token sets in
  `variables.css`, permanently** — not a leftover to fold, on inspection
  (milestone 17). The two dead tokens that inspection found (`--color-hover-bg`,
  `--color-success-hover`, zero references anywhere) are gone; everything
  else in `--color-*` is still genuinely load-bearing (`LoginPage`'s own
  styles, `global.css`'s shared body defaults for the pre-paint/no-JS case,
  and — since milestone 21 — `LoginPage.module.css`'s own scoped revert of
  `global.css`'s shared `:focus-visible` rules, which default to `--v2-cyan`
  now that every *other* page is V2) and can't be renamed onto `--v2-*`'s
  names without breaking `LoginPage`'s light theme.
  `--v2-*` can't be renamed onto `--color-*`'s names either: those exact
  names are read live via `getComputedStyle()` in `MatchGraph.jsx` and
  `MatchGraph3D.jsx`, so they're a public API, not free-floating internal
  names. No pair of tokens across the two sets shares an actual color value
  (`--v2-*`'s status colors were deliberately re-tuned brighter for AA
  contrast on dark surfaces — see `variables.css`'s own comment), so there
  was no genuine duplication to consolidate beyond the two dead tokens
  removed. Treat this as closed, not deferred.
- **The "V2 page panel" background treatment (aurora/grid + noise
  pseudo-elements) is duplicated across ten page CSS files** rather than
  extracted into one shared class — still a valid, purely-optional cleanup
  target, deliberately skipped in milestone 17 to keep that pass's risk to
  zero (see milestone 17).
- **`StatusBadge`, `EmptyState`, and `PageHeader` each ship an unused
  non-`glass` (`--color-*`-based) style branch** — every current call site
  passes `variant="glass"`, so those rules never render, but the component
  itself still exposes a non-glass default as part of its public contract.
  Deliberately left alone (not "proven dead" in the same unambiguous sense
  as an unreferenced token — it's a designed fallback, not forgotten code).
- **Report audio uses the native `<audio controls>` element**, not a custom
  player — a deliberate choice (zero maintenance, fully accessible) over a
  cosmetic-only custom player.
- **No pagination** on any list view — matches the backend's own "no infra
  ahead of demonstrated need" rule.
- Two VoiceHR/RWS-side gaps that affect what this frontend can demo (full
  detail in `handoff.md`): no in-product way to retrieve a fresh VoiceHR
  invite link, and one candidate with no Resume Intelligence identity record
  that still shows as qualified on a live job — both are backend/data facts,
  not frontend bugs, and both are already handled gracefully in the UI (a
  visible "(name unavailable)" fallback, correct 404/422 rendering).

---

## Demo readiness

The full whole-system readiness audit (verdict, findings, demo sequence,
presenter guidance) lives in `handoff.md` — this is cross-service scope, not
frontend-only, so it is not duplicated here. Frontend-specific fact worth
keeping close at hand: the demo sequence's entire frontend leg (Dashboard →
Candidates → Candidate Detail → Jobs → Job Detail → Applications → Report
Detail → Reports → Knowledge Graph 2D → 3D) was walked end to end in a real
browser with zero broken cross-link and zero new console error.

---

## Historical milestones

Chronological, one line each — the full phase-by-phase narrative (exact
screenshots, before/after comparisons, per-phase test/verification detail)
has been compressed out. Nothing here should be treated as current without
checking the actual source; use this only to orient, not to cite.

1. Scaffold: Vite + React app, routing, auth context, API client wrapper,
   `LoginPage`.
2. Core recruiter flow: `JobsListPage`, `ResumeUploadPage`, `JobDetailPage`
   (ranked matches + select), `ApplicationsPage` (invite/accept/reject +
   polling), VoiceHR assessment rendering.
3. Production static serving (RWS serves the built frontend same-origin, SPA
   fallback for client-side routes).
4. Admin-shell redesign: `Sidebar`/`TopBar`/`PageHeader`/`StatusBadge`/
   `EmptyState`/`StatTile`, real `DashboardPage` data.
5. `CandidatesPage`, `InterviewsPage` (global), `ReportsPage` +
   `ReportDetailPage` (VoiceHR report/recording proxy through RWS),
   `CandidateDetailPage` (professional knowledge + cross-links).
6. A cross-service UX audit closed the remaining cross-link gaps between all
   eleven screens.
7. 2D knowledge graph: backend endpoint + design audit (Cytoscape.js chosen
   over alternatives), the `/graph` page itself, interactive filters/search,
   then a per-candidate ego graph embedded in `CandidateDetailPage`.
8. **V2 visual redesign**, in slices: design system + shell + Dashboard, then
   each remaining operational page group, ending with the knowledge graph
   itself — closing the dark/light seam across every screen.
9. **3D knowledge graph**: built on `3d-force-graph`, lazy-loaded; a
   dedicated visual-tuning pass fixed a washed-out default camera
   framing/fog/glow after real-browser screenshots (the first real-browser
   access this project had) showed the initial version wasn't legible.
10. A real-browser audit of the shell/Dashboard found and fixed three
    concrete defects invisible to build-only verification (a light gap below
    the dark panel on any page taller than its content, a mismatched
    light-theme focus ring on the dark chrome, a mobile grid-overflow bug) —
    fixed once at the shared shell level, benefiting every page at once.
11. A real-browser audit of the remaining nine pages confirmed seven were
    already solid and polished two that weren't (`CandidateDetailPage`'s
    accent colors, `ResumeUploadPage`'s drop-zone icon + a missing style
    variant).
12. The whole-system final production/demo readiness audit (see `handoff.md`)
    confirmed the entire frontend end to end in a real browser one more time,
    found no defect, and closed out the project's planned work.
13. **VoiceHR language eligibility gate UI**: the "VoiceHR language
    eligibility gate UI" section above (job creation/edit, Applications'
    language-screening column + blocked Accept, Report Detail's summary) —
    real backend work driving this UI, tracked in `handoff.md`'s milestone
    26. `StatusBadge` gained an optional `title` prop (additive) so the
    Applications column could show a one-line explanation on hover. One real
    rendering bug (a literal "null" next to a CEFR level when the assessed
    language was missing) found and fixed via real-browser verification.
14. **`ApplicationsPage` invite delivery UX + explicit resend**: see the
    "`ApplicationsPage` invite/resend UX, reworked this session" bullet
    above — real cross-service work tracked in `handoff.md`'s milestone 29
    (new RWS `POST .../resend-invite` + VoiceHR `POST
    /api/rws/interviews/{id}/resend-invite`). Live-verified in a real
    browser against the actual (unmocked) `RESEND_API_KEY`-unset failure:
    "Invitation not sent"/"Retry" rendered correctly, the real error banner
    text showed through verbatim, and the application/interview state was
    confirmed unchanged after Retry via direct DB queries (see `handoff.md`
    milestone 29 for the full trace). The "Resend invitation" (`"invited"`)
    state itself was not exercised live in this same pass, since no
    application in this environment has ever reached `"invited"` (email
    delivery has never succeeded here) and forcing that status directly in
    the database would misrepresent an invite that never actually
    succeeded — it's covered instead by the mocked-provider test suites
    `handoff.md` describes.
15. **`AssessmentPanel`/`ReportDetailPage`: new "Interaction" score row and
    "Insufficient evidence" status label** — driven by real backend work
    tracked in `handoff.md`'s milestone 31 (VoiceHR's interview rubric
    now scores interaction/spontaneity as its own dimension, separate from
    comprehension, and can report a transcript as not having enough usable
    language evidence to grade at all). Both are additive: `AssessmentPanel`
    gained one more `AssessmentScoreRow` ("Interaction") and one more
    status label/tone pair, using the exact same graceful-null rendering
    (`value == null ? "—"`) pronunciation already relied on — the two call
    sites fed from the narrower webhook-derived `interview_assessment`
    (ApplicationsPage/InterviewsPage) simply render "—" for it, since that
    object never carries this new score (a deliberate backend-side choice,
    see `handoff.md`, not a frontend gap); only `ReportDetailPage`'s richer
    VoiceHR-report-backed view ever shows a real number there. Live-verified:
    the one existing real stored report in current demo data renders
    correctly post-change (screenshotted) — that specific report's own
    `evaluation_status` is `"failed"` (pre-existing, unrelated to this
    change), so it correctly shows the pre-existing "no scores available"
    text rather than the new row, which is the expected branch, not a
    defect. The new "Insufficient evidence" status label/row has not been
    seen live end-to-end (no completed interview with that outcome exists
    in this environment — VoiceHR's Gemini access is still denied, see
    `handoff.md`'s Known Limitations); verified instead by direct component
    inspection (the same null-tolerant pattern used everywhere else here).
16. **Report Detail redesign + report-wide label consistency** — the real
    frontend work `handoff.md`'s milestone 32 predicted would land here.
    `ReportDetailPage.jsx` restructured into an explicit hierarchy (Language
    Screening → Assessment Quality → Language Profile → Strengths/Concerns →
    Transcript, see `handoff.md`'s own milestone 32 for the full reasoning)
    so a recruiter can see *why* a candidate received their language result
    without reading raw JSON or the full transcript — the plain generic
    `<AssessmentPanel>` this page used to render is gone from it entirely,
    superseded by three new purpose-built sections (`AssessmentQualitySection`,
    `LanguageProfileSection`, and a renamed `LanguageScreeningSection`);
    `AssessmentPanel` itself is untouched in *role*, still used exactly as
    before by ApplicationsPage/InterviewsPage's own denser row-expansion
    context, which this task never asked to redesign. Found and fixed a
    real duplicate-label bug while consolidating shared vocabulary: this
    page's own local `LANGUAGE_GATE_RESULT_TEXT` said `passed → "Meets
    requirement"` while `constants/language.js`'s shared
    `LANGUAGE_GATE_LABELS` (the one ApplicationsPage's own column already
    used) said `passed → "Passed"` — two different words, on two different
    pages, for the exact same gate state. Fixed at the one shared source;
    the duplicate is deleted. Also consolidated three separately-defined
    copies of `EVALUATION_STATUS_LABELS`/`TONE` (`AssessmentPanel.jsx`,
    `ApplicationsPage.jsx`, `InterviewsPage.jsx`) into one shared export,
    reused by all three plus a new `ReportsPage` "Status" column. New
    `describeDimensionScore()` helper (constants/language.js) is
    display-only sugar (Excellent/Strong/Good/Fair/Limited bands over a
    0-100 score) — never used to compute or influence anything a backend
    already decided. First-ever frontend test file in this project,
    `src/constants/language.test.js` (13 cases, Node's built-in test
    runner, zero new dependencies) — see `handoff.md` for why a JS
    framework (Jest/Vitest/RTL) was deliberately not introduced for this.
    Live-verified: all six Report Detail states this task's own spec named
    (passed, below-requirement, insufficient-evidence, pending,
    not-required, missing-optional-dimension-data) rendered correctly via
    Playwright `page.route()`-injected deterministic fixtures (screenshotted,
    zero console errors); the one real stored `"failed"`-evaluation demo
    report still renders correctly post-redesign; a 375px mobile viewport
    showed no horizontal overflow; `ApplicationsPage`'s language-gate
    column confirmed to consistently say "Meets requirement", never
    "Passed", anywhere in the app.
17. **CSS token-fold cleanup — closed the queued item, but not the way it was
    planned.** Inspected every `--v2-*`/`--color-*` reference across `src/`
    before changing anything (25 files consume `--v2-*`, 7 consume
    `--color-*`) and found the premise in the old `[NEXT]` entry didn't
    survive contact with the actual code: there is no pair of tokens across
    the two sets with an identical value to "consolidate" (`--v2-*`'s status
    colors are deliberately re-tuned brighter than `--color-*`'s, per
    `variables.css`'s own long-standing comment), and a wholesale rename of
    `--v2-*` onto `--color-*`'s names is actively unsafe: `LoginPage` still
    needs those exact `--color-*` names to resolve to their original light
    values, and `MatchGraph.jsx`/`MatchGraph3D.jsx` read `--v2-*` names live
    via `getComputedStyle()`, making every one of those names a public API
    rather than a renameable internal detail. Did the safe, real part
    instead: grepped for zero-reference tokens and found exactly two,
    `--color-hover-bg` and `--color-success-hover` — deleted, confirmed by
    re-grepping and by reading them back as empty strings from a live page's
    `getComputedStyle(document.documentElement)`. Rewrote `variables.css`'s
    stale "this can all become `--color-*` once migration finishes" comment
    (written before `LoginPage`'s permanent-light-theme decision and the
    graphs' live token reads existed) to state the actual, permanent
    architecture instead. Left the ten-times-duplicated "V2 page panel"
    background block and the three shared components' unused non-`glass`
    style branches alone — both real, both optional, neither a token-level
    change, and touching either would have added risk to a pass whose whole
    point was zero visual risk. Net: `variables.css` 147 → 136 lines, built
    CSS bundle 68.48 kB → 68.42 kB (9.19 kB → 9.17 kB gzipped) — a small,
    expected shift, not a redesign. Verified: `npm run build` clean;
    `node --test src/constants/language.test.js` 13/13 passing (unrelated to
    this change, re-run per the task's own bar); Docker image rebuilt and
    the stack redeployed; real-browser Playwright pass logged in unchanged
    for every main route (`/login`, `/`, `/candidates`, `/jobs`, `/upload`,
    `/interviews`, `/reports`, `/graph`) plus both 2D and 3D graph modes,
    screenshotted and eyeballed against known-good prior screenshots —
    `LoginPage`'s resolved colors matched `--color-bg`/`--color-text`
    exactly, sampled `--v2-*`/`--color-*` root values were all unchanged,
    the two removed tokens correctly resolved empty; zero new console
    errors (the one console/network error seen, a `/v1/candidates/.../identity`
    404, is the same pre-existing, already-documented gap `handoff.md` and
    milestone 16 both call out — not caused by this change).
18. **Interview rounds UI, first slice: display + schedule.** Real frontend
    work driving the "Interview rounds UI (display + schedule)" section
    above — backend milestones 33-34 in `handoff.md` landed first, this
    closes the resulting frontend gap the same way the language gate's own
    UI followed its backend (milestone 13). New `constants/
    interviewRounds.js` (label/tone maps + `describeScheduleBlockedReason`,
    15 new Node-test-runner cases) and `api/applications.js`'s
    `createInterviewRound`, both consumed only by `ApplicationsPage` — a
    deliberate scope narrowing from the original `[NEXT]` draft, which also
    suggested `CandidateDetailPage`; this task's own instructions scoped it
    to `ApplicationsPage` alone. Live-verified against two freshly-created
    "Smoketest" jobs (never the pre-existing demo applications — this
    project has no delete endpoint for any resource, so touching real demo
    rows would have left a permanent, unrequested change): a real schedule
    on a `not_required`-gate application succeeded, persisted across a hard
    reload, and rendered the correct badge; a second job with a language
    requirement produced a real `pending` gate and a real disabled button
    with the correct tooltip. One harmless byproduct: an initial job-
    creation attempt used a longer, more natural-sounding job description
    than `scripts/seed_demo_data.py`'s own proven-safe phrasing and got
    zero qualified matches for the same reason that script's own comment
    already documents (Gemini extracted a requirement — "Backend
    Development" — nothing in the candidate pool's skill lists literally
    has); the corrected retry used that script's exact working phrasing
    instead. Both smoketest job rows (the failed attempt and the working
    one) remain in the Jobs list as harmless clutter, the same precedent
    already set by the pre-existing "Backend Engineer (Demo)" row — no
    delete capability exists, by design.
19. **Interview rounds UI, second slice: reschedule/complete/cancel.**
    Closes the lifecycle gap milestone 18 deliberately left open. New
    `InterviewRoundItem` sub-component (`ApplicationsPage.jsx`) gives each
    `"scheduled"` round its own independent Reschedule/Complete/Cancel
    actions and form/loading/error state — a `completed`/`cancelled` round
    renders with zero action buttons, permanently, matching RWS's own
    terminal state machine. Three new `api/applications.js` functions
    (`rescheduleInterviewRound`/`completeInterviewRound`/
    `cancelInterviewRound`) and one new `constants/interviewRounds.js`
    helper (`toDatetimeLocalInputValue`, the exact inverse of the
    conversion the schedule form's submit already used, now needed to
    pre-fill the Reschedule form with a round's current `scheduled_at`) —
    18 Node-test-runner cases total in that file now (was 15). Live-
    verified end to end against one fresh "Smoketest" application (three
    rounds scheduled through the already-existing, unaffected schedule UI,
    then one of each: rescheduled, completed with a `needs_review` outcome
    and notes, and cancelled with the confirm-guard) — every transition
    rendered the correct badge, left zero action buttons behind for the
    round that changed, left the still-`"scheduled"` round's three buttons
    untouched, and survived a hard reload each time (real persistence, not
    optimistic local state). A direct API call independently confirmed RWS
    still 409s a `complete` call on an already-completed round regardless
    of what the UI currently shows. The interview-rounds feature (backend
    milestones 33-34, frontend milestones 18-19) is now complete end to end.
20. **Post-VoiceHR interview rounds UX audit + final workflow
    integration.** Not a feature slice — a real-browser audit of the whole
    chain milestones 18-19 only ever verified in pieces: resume match →
    select → VoiceHR invite → VoiceHR language result → language gate
    passes → schedule a round → reschedule/complete/cancel → recruiter
    accept/reject, run start to finish in one continuous session on one
    application. Since VoiceHR's Gemini access is still denied and
    `RESEND_API_KEY` is still unset (both pre-existing, unrelated to this
    audit), a truly live invite+interview still can't be demonstrated —
    confirmed live (a real "Invite" click on a freshly-selected application
    correctly 422'd: "Candidate contact information not found; cannot
    invite to interview," the already-documented orphaned-candidate path,
    not the `RESEND_API_KEY` 502 path this session expected going in — both
    are pre-existing, gracefully-handled failure modes, and which one fires
    just depends on which candidate a given job happens to match). Advanced
    past that using this project's own already-established, sanctioned
    technique for exactly this situation (`scripts/seed_demo_data.py`'s
    `reconnect_voicehr_report`: a direct workflow-state-only DB write to
    reach `"invited"`, then a real, correctly-HMAC-signed webhook replay) —
    not a new technique invented for this audit, and applied only to a
    fresh "Smoketest" application, never real demo data.

    **Findings, classified, on the interview-rounds workflow specifically**
    (full whole-app scope was not re-audited — see the one broader item
    below):
    - **MEDIUM, fixed**: `ApplicationsPage`'s own inner content (candidate
      name link, Invite/Accept/Reject/Resend, and all four round-lifecycle
      buttons) inherited `global.css`'s light-theme `:focus-visible` ring —
      confirmed live via real keyboard Tab navigation (not just a
      `.focus()` call, which gave a misleading initial reading). Same class
      of bug already found and fixed for Sidebar's own links; fixed the
      same way, a self-contained `ApplicationsPage.module.css` override.
    - **MEDIUM, fixed**: `CandidateDetailPage`'s Applications table never
      rendered `interview_rounds`, despite already fetching it on every
      row — a recruiter reviewing one candidate across every job they've
      applied to had no visibility into interview activity without opening
      each job's own Applications page. Added read-only, reusing the exact
      same badge/tone/tooltip helpers `ApplicationsPage` uses, zero new API
      calls, zero action buttons (this page has never had a mutating
      action of its own).
    - **LOW, no change**: `JobDetailPage` correctly has no round summary
      (never fetches `Application`s at all; "View Applications" already
      links to full detail). `DashboardPage` correctly has no round
      activity (rounds are advisory, not a pipeline stage). Terminology
      (`"Interview"` vs `"Interview rounds"` columns, `"Pass"`/`"Fail"` vs
      `"Meets requirement"` wording) read clearly during the live
      walkthrough — no confusion observed, no change made. Mobile layout
      and `prefers-reduced-motion` both confirmed correctly inherited, not
      assumed (a real 390px screenshot, and a real computed-style check
      showing `transition-duration` collapsing under reduced motion).
    - **No workflow contradiction found**: `accept` is still blocked only
      by the language gate, exactly as before this feature existed; round
      outcomes never gate `accept` or `reject`; `reject` remains unblocked
      by anything; scheduling itself is correctly blocked once
      `accepted`/`rejected` (verified live — the button renders `disabled`
      with the exact `describeScheduleBlockedReason()` text as its title,
      confirmed via Playwright's own "element is not enabled" click-guard
      rather than a successful click).
    - **One broader item found, out of this audit's own scope**: the
      focus-ring gap above is not specific to `ApplicationsPage` — its root
      cause (`global.css`'s shared rule) is app-wide, and every other V2
      page's own inner content has never actually been Tab-tested for it.
      Logged as `[NEXT]` rather than fixed here, since fixing every page
      would be a real, separate piece of work, not a small fix incidental
      to this feature.
    - **Demo-sequence finding, documented not executed**: the real curated
      demo application `a2769e7b-f63b-4d73-8ac3-a2e443b3d46b` is already
      schedule-ready today — `status: "interviewed"`,
      `language_gate.status: "not_required"`, zero existing rounds —
      confirmed via a live API check. The interview-rounds feature could be
      added to the actual jury demo with zero setup cost (no Gemini/Resend
      dependency at all), inserted between the existing "Report Detail" and
      "Accept" steps. Deliberately **not executed** against this real,
      curated application during this audit — populating what the jury
      literally sees is a presentation decision for whoever runs the demo,
      not a code fix; see `handoff.md`'s updated demo sequence text for the
      exact recommendation.

    **Verification**: `npm run build` clean; frontend tests 31/31 passing
    (unchanged by this pass; no new test file — the audit's findings were
    either UI-code fixes covered by the same real-browser pass, or
    documentation-only decisions). RWS unit suite 135/135, safe integration
    suite 113/113 (`TEST_DATABASE_URL` → `company_second_brain_test`, never
    the live database) — both unchanged, since this audit made no backend
    changes at all (none were needed; no real defect was found). Confirmed
    via direct `psql` that the two specific curated demo applications
    (`a2769e7b-...`, `3a6df887-...`) are exactly as they were before this
    session — only this session's own disposable "Smoketest" data grew.
    **Scope note**: this pass audited the interview-rounds workflow
    specifically (the task actually given), not a full milestone-12-style
    whole-app regression sweep of every route and both graph modes — the
    walkthrough did pass through Dashboard/Candidates/Jobs/Reports/
    Interviews incidentally with zero errors, but that's partial coverage,
    not a claim of having re-verified everything milestone 12 once did.
21. **App-wide dark-theme focus-ring audit — closed, via one shared token,
    not ten scoped overrides.** Milestone 20 fixed the light-theme
    `:focus-visible` ring on `ApplicationsPage` alone with a scoped
    per-component override, the same pattern Sidebar's own earlier fix
    used. Before repeating that pattern on the ten remaining pages, checked
    whether a single shared fix was now safe instead — it was: every
    post-login page runs V2 (`FRONTEND_HANDOFF.md`'s own "Current state"),
    so the light-theme ring was never really the right *default* anymore,
    just the one `global.css` had shipped with since before V2 existed.
    Flipped both of `global.css`'s shared `:focus-visible` rules (the plain
    `outline` rule for buttons/links/table-rows, and the `input`/
    `textarea`/`select`-specific border+box-shadow rule) from
    `--color-primary`/`--color-primary-bg` to `--v2-cyan`/`--v2-cyan-bg`,
    then added one small, self-contained revert in `LoginPage.module.css`
    (`.field input:focus-visible`, `.submitButton:focus-visible`) back to
    the light-theme colors for exactly its own three focusable elements —
    the same "scoped override, cannot affect any other page" reasoning
    every previous fix used, just inverted: now it's the one *light* page
    overriding a *dark* default, not the other way around. Removed the
    three now-redundant per-component overrides this made obsolete
    (`Sidebar.module.css`'s `.link:focus-visible`, `TopBar.module.css`'s
    `.logoutButton:focus-visible` — an identical Phase-28-era fix this
    session hadn't previously noticed, found only by grepping every
    `:focus-visible` rule in the codebase before touching anything — and
    `ApplicationsPage.module.css`'s milestone-20 override) rather than
    leave them duplicating the new shared default. Left two other
    already-correct, pre-existing overrides alone
    (`MatchGraph.module.css`'s `.searchInput`, `JobsListPage.module.css`'s
    `.field input`/`.field textarea`) since both are deliberately more
    minimal than the new global default (border-color only, no glow
    box-shadow) — matching them would be a visual change beyond fixing the
    color, which this task's own "no layout/behavior changes" rule ruled
    out.

    **Verification**: `npm run build` clean (CSS bundle 71.77 kB → 71.38 kB,
    a small reduction, not a redesign); frontend tests 31/31 unchanged (pure
    CSS, no logic touched); Docker image rebuilt and the stack redeployed.
    Real-browser Playwright pass, real `page.keyboard.press("Tab")` (not
    `.focus()`, which milestone 20 already found gives a misleading
    `:focus-visible` reading) through all 11 post-login routes plus a
    second 2D→3D pass on `GraphPage` — Dashboard, Candidates, Candidate
    Detail, Jobs, Job Detail, Resume Upload, Applications, Interviews,
    Reports, Report Detail, Graph 2D, Graph 3D — every one of ~190 tabbed
    elements across every page now resolves to `--v2-cyan`
    (`rgb(63, 216, 232)`), zero remaining light-theme-blue occurrences
    outside `LoginPage`. `LoginPage` itself independently confirmed still
    exactly light-theme: its email/password inputs' `border-color` and
    `box-shadow` and its submit button's `outline` all still resolve to
    `--color-primary`/`--color-primary-bg` (`rgb(47, 95, 219)` /
    `rgb(234, 240, 253)`), screenshotted for a visual check, not just a
    computed-style assertion. Explicitly covered every category this task
    named: buttons, links, a table-row interaction (`InterviewsPage`'s
    expandable row, screenshotted with a visible cyan ring), form controls
    (text/date/select/textarea inputs across `ApplicationsPage`/
    `JobsListPage`/`ResumeUploadPage`), and 2D/3D graph controls
    (`GraphControls`'s search input, screenshotted with a visible cyan
    ring). Mobile (390px) `ApplicationsPage` re-checked — same pre-existing
    horizontal-scroll behavior, no new overflow, cyan ring still correct at
    that viewport. `prefers-reduced-motion` reconfirmed unaffected (a real
    `reducedMotion: "reduce"` browser context, `transition-duration`
    collapsing to ~0 on `LoginPage`'s own submit button) — this change never
    touched `global.css`'s separate reduced-motion block. No backend
    involvement at all (pure frontend CSS), so no backend regression suite
    was re-run this session.
22. **JD-extraction-validation UI: a badge and a banner, no new page or
    confirmation step.** Backend companion to `handoff.md`'s milestone 36 —
    `GET /v1/jobs` now carries `extraction_status`/`extraction_warnings` on
    every job (see that file's API surface entry). Added a `StatusBadge`
    (`variant="glass"`, `tone="warning"`, reusing the exact shared component
    every other status pill in this app already uses) next to the job title
    on `JobsListPage`, shown only when `extraction_status === "flagged"`;
    `.jobTitle` picked up `display: flex` to lay the badge out inline with
    the title text. Added a non-blocking warning banner to `JobDetailPage`,
    shown under `LanguageRequirementSection` when a job is flagged, listing
    every `extraction_warnings[].message` — styled with the same structural
    pattern as the page's existing `.error` block, restyled with
    `--v2-warning`/`--v2-warning-bg` tokens `StatusBadge`'s own warning tone
    already uses elsewhere. No new route, no confirmation dialog, no change
    to how a job is created — exactly this feature's own "minimal recruiter
    intervention" design goal. `api/jobs.js` needed no change at all (its
    `getJobs()`/`createJob()` already pass RWS's JSON straight through with
    no field mapping).

    **Verification**: `npm run build` clean; frontend's 31 Node tests
    unchanged (pure logic tests, not touched by this UI-only change).
    Real-browser Playwright pass against the actual running stack (not just
    build-verified): logged in, submitted a real job with a deliberately
    vague description through the real Gemini pipeline, confirmed it came
    back `extraction_status: "flagged"`, then confirmed in the browser that
    its Jobs-list card shows the "Needs review" badge while every other
    (clean) job's card shows none, clicked into it, and confirmed the
    warning banner rendered on `/jobs/{id}` with the exact warning message
    from the API — screenshotted both, zero console/page errors either way.
23. **JD-repair-pass UI: a distinct "Auto-repaired" badge/note, `"flagged"`
    completely unchanged.** Backend companion to `handoff.md`'s milestone
    37 — `extraction_status` now also carries `"repaired"` (a flagged
    extraction that one automatic Gemini repair call genuinely fixed).
    Added a second `StatusBadge` on `JobsListPage` (`tone="accent"`, the
    same violet tone `StatusBadge.jsx`'s `glass.accent` already provides,
    deliberately not `warning` — a repaired job needs no recruiter
    attention, so it shouldn't look like one that does), shown only when
    `extraction_status === "repaired"`, alongside (never replacing) the
    existing `"flagged"` badge condition. Added a small, non-alert
    informational note to `JobDetailPage` for the same status — styled with
    the same violet tokens (`--v2-violet`/`--v2-violet-bg`), not the
    `.error`-derived warning-banner pattern `"flagged"` uses, since this
    needs no action and shouldn't visually compete with an actual warning.
    No new route, no new component, no change to `"flagged"`'s own badge or
    banner at all. `api/jobs.js` needed no change (same pass-through as
    milestone 22).

    **Verification**: `npm run build` clean; frontend's 31 Node tests
    unchanged. A real-browser Playwright regression pass (not a fresh
    feature check — no job in this environment's data actually has
    `extraction_status: "repaired"` yet, see `handoff.md`'s Known
    Limitations for why) confirmed the pre-existing `"flagged"`/`"clean"`
    badge and banner behavior is completely unaffected by adding the new
    conditional branches: the same flagged job still shows exactly "Needs
    review" (never "Auto-repaired"), the same clean job still shows
    neither, zero console/page errors. The "Auto-repaired" badge/note
    themselves were verified by direct code review against `StatusBadge`'s
    existing tone API and this page's existing CSS patterns, not an actual
    browser render of that specific state.
24. **Warning messaging fix: `"flagged"` no longer overclaims "extraction."**
    Found by `handoff.md`'s milestone 39 reliability audit, not a new
    feature. Both `JobDetailPage`'s warning banner ("This job description's
    extraction looks suspicious") and `JobsListPage`'s "Needs review" badge
    tooltip ("Gemini's extraction... looked suspicious") hardcoded a claim
    that stopped being universally true the moment milestone 38's
    `match_anomaly.py` gave `"flagged"` a *second*, unrelated cause: a
    perfectly clean extraction whose match outcome looks wrong. A job
    flagged only by that path would show a banner asserting something false
    about its own extraction. Fixed with a minimal wording change in both
    places — banner intro now reads "This job needs a second look," badge
    tooltip now names both possible causes generically — no new component,
    no new conditional structure; the real, specific cause is still always
    named by the actual `extraction_warnings[].message` list, already
    rendered verbatim underneath.

    **Verification**: `npm run build` clean; frontend's 31 Node tests
    unchanged (pure copy change, no logic touched). Real-browser Playwright
    pass against the actual running stack confirmed the corrected wording
    renders for a real, pre-existing flagged job (old inaccurate text gone,
    new generic text present, the underlying specific warning message still
    shown beneath it), a clean job still shows neither badge, and zero
    console/page errors.
25. **Interview-round notification fields + status badge/resend action —
    see "Interview rounds UI" above for the full detail.** Backend companion
    to `handoff.md`'s milestone 41 (phase 1 of automating human interview
    scheduling): the schedule form gained a required interviewer-email input
    and an optional location input; each round gained a second badge for
    `notification_status` and a "Resend notification" action shown only
    once it's `"failed"`. `handoff.md`'s own milestone 40 (atomic job
    submission), closing the partial-write risk milestone 39 found, also
    landed this session and confirmed the frontend-impact question milestone
    24 left open: that fix needed zero frontend changes (a mid-request
    failure still just returns the same `502` this page already handled,
    with no orphaned job needing a "resume" affordance).

    **Verification**: `npm run build` clean; frontend's 34 Node tests (+3:
    `notificationStatusTone`'s tone mapping and `NOTIFICATION_STATUS_LABELS`'
    coverage) passed. Real-browser Playwright pass against the actual
    running stack, the same already-curated demo job used throughout this
    section: scheduled a real round with a real interviewer email and
    location, confirmed the round creates successfully, the "Notification
    failed" badge renders (no `RESEND_API_KEY` configured live, exactly the
    designed contract), and "Resend notification" appears alongside the
    existing Reschedule/Complete/Cancel actions — zero new console errors
    beyond the same pre-existing orphaned-candidate 404 this section already
    calls out repeatedly.

---

## [NEXT]

**No outstanding frontend task.** Milestone 25 (interview-round notification
fields/badge/resend) is the most recent frontend change, real-browser-
verified per above.

The next backend-driven frontend need will come from `handoff.md`'s own
`[NEXT]` (interview-round ICS invitations + reschedule/cancel
notifications — phase 2 of the same feature milestone 25 just started the
frontend half of). That work is scoped as backend-only (a real ICS
attachment on the existing notification email, plus wiring the same
best-effort notification logic into `reschedule`/`cancel`, which currently
send none) — no new frontend surface is anticipated (the existing
notification badge/resend button milestone 25 just built should already
cover whatever reschedule/cancel notifications look like from the
recruiter's side, since they reuse the same `notification_status`/
`notification_error` fields), but re-read `handoff.md`'s `[NEXT]` once that
work actually starts rather than assuming this holds.

Everything else on record is either a deliberate, already-documented,
closed decision (the "V2 page panel" background duplication, the three
shared components' unused non-`glass` branches, no pagination, native
`<audio controls>` — see Known Limitations above, none of these are gaps
waiting on a next session) or external and not implementable from this
side: VoiceHR's Gemini access needs Google support/billing action, and both
automatic invite emails and interview-round notifications need a real
`RESEND_API_KEY` configured (each service's own key — see `handoff.md`).
Before starting new work here, re-audit both handoff
documents' Known Limitations for anything that became real since this was
written — don't treat this section's silence alone as proof there's
nothing left, the same discipline this file has asked of itself before.
