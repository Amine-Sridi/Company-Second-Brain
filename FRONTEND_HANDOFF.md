# Recruiter/Admin Frontend — Handoff

Authoritative handoff for the React recruiter/admin frontend at
`services/recruitment-workflow/frontend/`. Covers frontend architecture, the
API contract it consumes, current implementation state, and the 2D/3D
knowledge graph. It does **not** cover backend implementation, system-wide
architecture, or cross-service demo readiness — those live in the repo-root
`handoff.md`; this file must never contradict it.

**Status: feature-complete.** Every planned page, the full V2 dark visual
redesign, an optional 3D graph mode, and the VoiceHR language eligibility
gate UI are built and have each been confirmed rendering correctly in a real
browser (not just build-verified) at least once. No frontend-only work is
currently queued (see `[NEXT]` at the end).

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
| Select / invite / accept / reject | `POST /v1/matches/{id}/select`, `/v1/applications/{id}/invite\|accept\|reject` | `api/applications.js`; every backend `409`/`422`/`502` rendered verbatim, no special-cased wording. `accept`'s `422` when the language eligibility gate blocks it is rendered the same generic way — the frontend also disables the button itself as a UX hint (see below), but RWS's own check is the real authority |
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

- **`LoginPage` still renders the original V1 light theme** — deliberate, a
  pre-auth screen never in scope for the V2 redesign.
- **`--v2-*` and `--color-*` are still two separate token sets** in
  `variables.css` (the additive scaffolding that made incremental V2
  migration safe). Every page is on V2 now, so this is pure cleanup with no
  visual effect once folded — see `[NEXT]`.
- **The "V2 page panel" background treatment (aurora/grid + noise
  pseudo-elements) is duplicated across ten page CSS files** rather than
  extracted into one shared class — a natural target for the same token-fold
  pass, not mandatory for it.
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

---

## [NEXT]

See `handoff.md`'s own `[NEXT]` (**Automatic VoiceHR invitation email
delivery**) — that work is backend/cross-service (VoiceHR's existing
`send_invitation_email` + Resend config), not frontend-specific, so it's
tracked there, not duplicated here. No frontend-only work is currently
queued. The previously-queued token-fold cleanup below remains optional,
low-priority detail, not an active `[NEXT]`: fold `--v2-*` into `--color-*`
in `variables.css` (delete the old light values,
rename or repoint every reference), check `LoginPage` still renders correctly
against the resulting palette (it's the one file that reads `--color-*`
directly and was never redesigned), and — while that file is already being
touched — consider extracting the ten-times-duplicated "V2 page panel"
background block into one shared class. Verification bar for this task is
"confirm nothing moved," not "confirm a new look appeared": diff a sample of
pages' resolved colors before/after, confirm the CSS bundle size doesn't shift
unexpectedly, re-run the live-route checks. After this lands, there is no
further planned work on this project.
