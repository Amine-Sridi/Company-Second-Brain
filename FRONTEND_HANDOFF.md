# Recruiter/Admin Frontend — Handoff

Authoritative handoff for the React recruiter/admin frontend at
`services/recruitment-workflow/frontend/`. Covers frontend architecture, the
API contract it consumes, current implementation state, and the 2D/3D
knowledge graph. It does **not** cover backend implementation, system-wide
architecture, or cross-service demo readiness — those live in the repo-root
`handoff.md`; this file must never contradict it.

**Status: feature-complete for everything built so far; one new backend
feature has no frontend surface yet** (see `[NEXT]` at the end). Every
planned page, the full V2 dark visual redesign, an
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
| List/create jobs | `GET`/`POST /v1/jobs` | `api/jobs.js`; create runs JD structuring + retrieval + scoring server-side, can take several seconds; both carry the optional `required_language`/`min_language_cefr` pair |
| Set/clear job language requirement | `PATCH /v1/jobs/{id}/language-requirement` | `api/jobs.js`'s `setJobLanguageRequirement`; `JobDetailPage`'s inline edit form |
| Ranked matches | `GET /v1/jobs/{id}/matches` | `explanation` lazily populated server-side; frontend never recomputes any score |
| Candidate identity | `GET /v1/candidates/{id}/identity` | via `hooks/useCandidateIdentity.js`, session-scoped in-memory cache only |
| All candidates | `GET /v1/candidates` | `CandidatesPage` |
| Candidate knowledge | `GET /v1/candidates/{id}/knowledge` | `CandidateDetailPage` |
| Select / invite / resend / accept / reject | `POST /v1/matches/{id}/select`, `/v1/applications/{id}/invite\|resend-invite\|accept\|reject` | `api/applications.js`; every backend `409`/`422`/`429`/`502` rendered verbatim, no special-cased wording. `accept`'s `422` when the language eligibility gate blocks it is rendered the same generic way — the frontend also disables the button itself as a UX hint (see below), but RWS's own check is the real authority. `resend-invite` (new) is a separate call from `invite`, only shown/enabled once `"invited"` — see below |
| Applications (scoped / global) | `GET /v1/jobs/{id}/applications`, `GET /v1/applications` | `ApplicationsPage` / `InterviewsPage` |
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

## Known limitations

- **No frontend surface for interview rounds yet** — `handoff.md`'s
  milestones 33-34 built the full backend (create/reschedule/complete/
  cancel, `interview_rounds` embedded on every `Application` read), but no
  page displays it and no page can call any of its endpoints. See `[NEXT]`.
- **`LoginPage` still renders the original V1 light theme** — deliberate, a
  pre-auth screen never in scope for the V2 redesign.
- **`--v2-*` and `--color-*` remain two separate token sets in
  `variables.css`, permanently** — not a leftover to fold, on inspection
  (milestone 17). The two dead tokens that inspection found (`--color-hover-bg`,
  `--color-success-hover`, zero references anywhere) are gone; everything
  else in `--color-*` is still genuinely load-bearing (`LoginPage`'s own
  styles plus `global.css`'s shared body/focus-ring defaults) and can't be
  renamed onto `--v2-*`'s names without breaking `LoginPage`'s light theme.
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

---

## [NEXT]

**Interview Rounds surface, first slice: display + schedule (no
reschedule/complete/cancel UI yet).**

`handoff.md`'s milestones 33-34 built the entire backend feature — every
`ApplicationResponse` (both `GET .../applications` routes, `select`,
`accept`/`reject`, `invite`, `resend-invite`) now embeds
`interview_rounds: InterviewRoundResponse[]`
(`{id, application_id, round_type, status, outcome, interviewer_name,
scheduled_at, notes, created_by, created_at, updated_at}`), and
`POST /v1/applications/{id}/interview-rounds` (body:
`{round_type: "technical"|"managerial", interviewer_name, scheduled_at,
notes?}`) schedules one — but nothing in this frontend reads or writes
either yet. `src/api/applications.js` needs a `createInterviewRound`
function alongside its existing `selectCandidate`/`inviteApplication`/etc.

Scope for this slice, deliberately smaller than the full feature (defer
reschedule/complete/cancel UI to the step after this one, matching how the
backend itself landed create before lifecycle actions):

- **Display**: on `ApplicationsPage` (where `language_gate`'s `StatusBadge`
  already renders per row) and `CandidateDetailPage`'s applications table,
  add a compact rounds summary per application — e.g. a `StatusBadge` per
  round (`variant="glass"`, tone mapping `scheduled → neutral`,
  `completed`+`pass → success`, `completed`+`fail → danger`,
  `completed`+`needs_review → warning`, `cancelled → neutral`), or a short
  "2 rounds" label that expands to the list on click. Reuse
  `describeLanguageGate`'s pattern in `constants/language.js` as the model
  for a new small `constants/interviewRounds.js` (label/tone maps,
  `round_type` → "Technical"/"Managerial").
- **Schedule action**: a small form (round type select, interviewer name
  text input, date/time picker, optional notes) — a modal or inline
  expansion, recruiter's choice of pattern already used elsewhere in this
  codebase (check `ResumeUploadPage`/`JobsListPage`'s existing forms for the
  established style before inventing a new one). Visible/enabled only when
  `language_gate.status` is `"passed"` or `"not_required"` and
  `application.status` is not `"accepted"`/`"rejected"` — mirroring the
  backend's own two gates so the UI never offers an action the API would
  422/reject, with a disabled-with-tooltip state (not hidden) otherwise,
  matching this project's existing convention for gated actions.
- **Verification bar**: real-browser Playwright pass showing the summary
  rendering for an application with zero rounds (nothing shown, no broken
  layout), one scheduled round, and a completed round with each outcome
  tone: zero new console errors, no regression to the existing
  `language_gate` badge/tooltip it sits next to.

Do **not** build reschedule/complete/cancel UI in this slice, do not add a
new route, and do not touch `InterviewsPage` (still VoiceHR-only, unrelated
to this feature — see "Deliberately not touched" precedent elsewhere in
this document for why mixing the two would blur "VoiceHR language gate
separate").
