# VoiceHR

VoiceHR is a pre-existing, working AI voice-interview application. Its interview engine
(`backend/services/gemini_live.py`) and SQLite persistence are **not** owned by this monorepo
and are not rewritten or redesigned here — per `CLAUDE.md`'s standing rule, only its API
surface is extended for integration with the Recruitment Workflow Service (RWS). VoiceHR
originally shipped with more than this needed: an alternate local Whisper/Ollama/Kokoro
interview-conduct pipeline, a Vapi phone-call integration, Anthropic/Ollama evaluator options,
and an admin-UI interview-creation flow that duplicated (and could not safely replicate) RWS's
own service-key-protected `POST /api/invite` call. None of these were part of the Company
Second Brain architecture (Gemini is this project's sole AI provider; RWS is the sole trigger
for interview creation), and all were removed as dead weight across two repository cleanup
passes — see `handoff.md` for that history. Everything documented below
reflects the actual current implementation in `backend/`, not a plan.

## Role inside Company Second Brain

RWS invites a selected, qualified candidate to a language assessment by calling VoiceHR's
`POST /api/invite`. VoiceHR conducts the interview independently (its own frontend, its own
SQLite database, its own evaluation), and when the interview reaches a terminal outcome it
signs and sends an assessment webhook back to RWS. RWS never sees the transcript or recording
— only the numeric assessment. VoiceHR never sees anything about the job, the match, or the
recruiter's decision.

## Candidate interview flow

1. **Invitation** — RWS calls `POST /api/invite` with the candidate's name, email, and
   language. VoiceHR creates an `interviews` row in `pending` status and returns an interview
   link (`{BASE_URL}/interview?token=...`) valid for 7 days. RWS is the only caller of this
   endpoint in the current architecture — see Known limitations for how the candidate actually
   receives that link today.
2. **Landing** — the candidate opens the link; the frontend calls `GET /api/interview/{token}`
   to fetch their name/language, then `POST /api/interview/{token}/prepare` (a cheap
   token-validity check — Gemini Live needs no separate warm-up step), then
   `POST /api/interview/{token}/start`, which marks the interview `active` and returns a
   WebSocket URL (`ws_url`).
3. **The interview itself** happens entirely over a WebSocket connection
   (WebSocket `/ws/interview/{token}`, `backend/api/routes/session.py`) — see below.
4. **Completion** — when the WebSocket pipeline finishes, `backend/services/processing.py`'s
   `process_interview()` writes the transcript, runs evaluation, persists the report and
   recording, and — if the interview was created with an `external_reference_id` — notifies
   RWS (see below). The interview's final status is one of `completed`, `inconclusive`, or
   `terminated`.
5. **Interruption handling** — if the WebSocket connection drops or the pipeline fails once
   (`retry_count < 1`), the interview is reset to `pending` so the candidate can reopen the
   same link and continue; a second failure, or an explicit `candidate_refused`/
   `abusive_language` outcome, ends the interview as `terminated`.
6. **Report** — a report becomes available at `{BASE_URL}/report?token={report_token}`, a
   separate unguessable token generated at completion time. Per the candidate invitation
   email's own wording, this link is intended for the recruiter/admin, not shown to the
   candidate directly.

## The Gemini Live interview path

`backend/api/routes/session.py`'s WebSocket handler calls `backend.services.agent.
run_sarah_pipeline()`, a one-line entry point into `backend/services/gemini_live.py`'s
`run_gemini_live_pipeline()` — the only interview-conduct implementation in this project. It's
a real-time, audio-to-audio conversation with Gemini's Live API: the candidate's microphone
audio streams to Gemini and Sarah's spoken responses stream back, with the model itself
conducting the four-task interview per a detailed persona/system-instruction prompt (candidate
name and task list are JSON-escaped into that prompt to avoid breaking it). `backend/services/
agent.py` holds only what `gemini_live.py` needs from the interview's content: the task/question
list, closing/termination messages, and `classify_candidate_text()`'s refusal/abuse detection
(used mid-conversation to end the interview early if needed).

`run_gemini_live_pipeline()` produces the same transcript shape (`role`/`text`/`timestamp`
entries) that `process_interview()` expects, so evaluation/persistence/RWS notification are
unaware of anything about how the conversation itself was conducted.

## Evaluation pipeline

`backend/services/evaluator.py`'s `evaluate()` scores the transcript against five weighted
dimensions (interaction & comprehension 25%, fluency & coherence 25%, grammar & language
control 20%, vocabulary & range 15%, pronunciation & intelligibility 15%). Gemini is the sole
evaluation provider — no other provider is configurable or supported. Gemini calls use
structured JSON output constrained to the five dimension scores, strengths/concerns/evidence,
and speaking-time/word-count metrics.

- **Retry**: if the model returns syntactically valid JSON that's missing a dimension score,
  `evaluate()` retries generation exactly once (never unbounded) before falling back. Separate,
  lower-level transport retries (rate limits/timeouts) happen inside the Gemini call itself.
- **Graceful fallback**: any unrecoverable failure returns `empty_result()` (`overall_score:
  null`, `evaluation_status: "failed"`, an `error` message) rather than raising — the interview
  is still recorded as complete, just without a computed score.
- **Prompt hardening**: the transcript is passed to the model inside `<transcript>` delimiters
  with an explicit instruction to treat it as untrusted candidate-provided data, not
  instructions — a candidate has a direct incentive to try to talk their way into a better
  score.
- `cefr_level_for_score()` maps the weighted overall score to an approximate CEFR band (A2–C1)
  — explicitly labeled everywhere as a B2-style estimate, not an official certification.

## SQLite persistence

VoiceHR keeps its own SQLite database (`voicehr.db`, `backend/database.py`) — three tables:
`interviews` (token, candidate identity, language, status, retry/attempt tracking, the RWS
integration columns below), `recordings` (audio/transcript paths, raw transcript JSON,
duration, ended reason), and `reports` (scores, dimensions, strengths/concerns/evidence,
evaluation status/error). Schema changes are additive (`ALTER TABLE ... ADD COLUMN` guarded by
a `PRAGMA table_info` check), never a migration framework — this database is not touched or
migrated by the rest of Company Second Brain.

## `POST /api/invite`

The integration entry point RWS calls to create an interview:

- **Auth**: `X-Api-Key` header, checked against `RWS_SERVICE_API_KEY` (`require_rws_service_key`
  in `backend/api/routes/invite.py`). Also usable by other callers (e.g. the admin UI) with the
  same key.
- **Body**: `candidate_name`, `candidate_email`, `language` (`en`/`fr`), and an optional
  `external_reference_id` — RWS's `application_id`, stored on the interview row and echoed
  back verbatim in the eventual completion webhook. `NULL` means the interview was created
  outside the RWS flow and will never trigger that webhook.
- **`Idempotency-Key` header** (optional): RWS always sends `"{application_id}:{attempt_number}"`.
  A repeated call with the same key reuses the existing interview (same token, same link)
  instead of creating a duplicate — this is what makes a retried invite call (e.g. after RWS
  never received VoiceHR's response to a call it actually processed) safe to repeat. A genuine
  re-invitation carries a new attempt number and therefore a new key, correctly creating a new
  interview.
- **`job_title`** (optional): RWS's `Job.title`, used only for the candidate email's greeting
  below. VoiceHR has no other concept of a job.
- **Automatically emails the candidate their interview link** as part of this same call, via
  `deliver_invitation_email()` (`backend/api/routes/interview.py`) — see "Candidate invitation
  email delivery" below. This is what actually determines success: a delivery failure makes
  this endpoint itself return a non-2xx response, not the `link`/`expires_at` body below.
- **Response** (on success): `link`/`interview_link` (same value, `link` kept for backward
  compatibility), `expires_at`, and the echoed `external_reference_id`.

## Candidate invitation email delivery

`POST /api/invite` sends the candidate their secure interview link automatically, synchronously,
in the same request — RWS never sees that link and never sends anything itself; VoiceHR owns the
whole thing because it owns the token, its expiry, and the candidate-only interview surface the
link points to.

- **Idempotent, not spammy**: `interviews.invite_email_sent_at` (NULL until the first successful
  send) is what a duplicate `POST /api/invite` on the same `Idempotency-Key` checks before
  deciding whether to (re)attempt delivery. Already delivered → the duplicate call returns the
  existing state without resending. Not yet delivered (the previous attempt failed) → delivery is
  retried on the *same* interview row, never a new one.
- **Failure is loud, not silent**: `deliver_invitation_email()` raises on any failure (provider
  rejection, network error/timeout, `RESEND_API_KEY` unset) and that exception becomes this
  endpoint's own response — RWS's caller (`voicehr_client.invite_candidate`) sees a non-2xx and
  never marks the application invited (see `handoff.md`). `invite_email_error` is persisted
  purely as VoiceHR's own internal diagnostic (cleared on the next successful send); it is never
  included in the email itself or returned to RWS/the browser.
- **One implementation, two call sites**: the same `deliver_invitation_email()` also backs the
  explicit resend route, `POST /interview/{token}/invite` (see below) — always attempts delivery
  regardless of `invite_email_sent_at`, since an explicit resend means "send it again, now."
- **Content**: candidate name, a role-specific greeting when `job_title` was provided (generic
  otherwise), the language stated directly in the greeting's own wording (the whole email is
  already in that language), the secure link, its 7-day expiry, and a short microphone/browser
  preparation note. Never: application/candidate IDs, API keys, or any resume-matching/scoring/
  CEFR-threshold information — VoiceHR is never even given a job's required CEFR level in the
  first place (only `language`), so there is nothing to accidentally disclose there.

## Automatic VoiceHR → RWS assessment notification

When an interview reaches a terminal outcome, `backend/services/processing.py`'s
`process_interview()` calls `backend/services/rws_client.py`'s
`notify_and_record_interview_result()`. It is a no-op if the interview has no
`external_reference_id`.

- **Payload shaping** (`build_assessment_payload()`): the evaluator's full result dict is
  narrowed to exactly the fields RWS's `InterviewAssessment` schema accepts — `overall_score`,
  `language` (the interview's own conducted language, threaded in from the interview row, not
  the evaluator itself — feeds RWS's language eligibility gate), `cefr_level`, the five
  dimension scores, `inconclusive`, and `evaluation_status`. No audio, no transcript, no
  strengths/concerns/evidence text ever leaves VoiceHR.
- **Interview status mapping** (`interview_status_for()`): `"failed"` if
  `evaluation_status == "failed"`; else `"inconclusive"` if the assessment's `inconclusive`
  flag is true; else `"completed"`.
- **Signing**: the JSON body is signed with HMAC-SHA256 using `RWS_WEBHOOK_HMAC_SECRET` and
  sent as the `X-Signature` header, alongside a fresh UUID `X-Delivery-Id` per delivery (RWS
  uses this for dedup on its side) and `RWS_WEBHOOK_URL` as the target.
- **Best-effort, single attempt**: `notify_rws_interview_result()` never raises. A failed
  delivery is logged and the interview's `webhook_delivered_at` column stays `NULL`; VoiceHR's
  own report/email flow is unaffected either way. There is no retry queue or reconciliation —
  consistent with this project's "no message brokers, no reconciliation polling" rule.

## Admin/report surface

- `GET /admin` and `GET /admin/reports` — the admin page: a read-only, completed-reports
  listing. Interview *creation* is exclusively RWS's responsibility in this architecture (see
  Known limitations) — this page has no invite-creation form.
- `GET /api/admin/reports` — the same listing as JSON, with each candidate's full report
  (name, email, transcript, scores, recording URL) inline.
- `POST /api/admin/reports/{report_token}/retry` — re-runs evaluation for one report (e.g.
  after a transient provider failure) without generating a new report token.
- `GET /recordings/{filename}` — serves a recorded interview's audio file.

All four require **HTTP Basic authentication** (`require_admin_auth`,
`backend/api/deps.py`) — one shared username/password pair from settings
(`ADMIN_USERNAME`/`ADMIN_PASSWORD`), not a user database or session system. An unauthenticated
or incorrect request gets `401` with `WWW-Authenticate: Basic`; browsers handle the credential
prompt and caching natively, so `admin.html`'s own JavaScript needs no auth code of any kind.
Recordings are gated the same way even though the route isn't `/admin`-prefixed, because
recording filenames are the interview's sequential integer id (`"{id}.webm"`) — trivially
enumerable — not an unguessable token.

`GET /api/report/{report_token}` and the `GET /report` page are **not** behind this gate — a
report link is a per-token shareable-capability link (the same model as the interview invite
link itself), meant for the recruiter/admin who receives it by email, and this project has not
changed that model (no expiry has been added to `report_token`; see Known limitations).

## `/health` vs `/health/detail`

- `GET /health` (`backend/main.py`) — a single cheap liveness check
  (`{"status": "ok", "service": "voicehr"}`), what the Docker healthcheck polls. No auth.
- `GET /health/detail` (`backend/api/routes/report.py`) — richer diagnostics: DB connectivity,
  which Gemini evaluation/Live models are configured, and whether `GEMINI_API_KEY` is set. No
  auth (operational status, not candidate data).

These are deliberately separate routes, not one route with a query parameter — they used to
collide (both registered at `/health`, with `main.py`'s trivial one silently shadowing the
richer one) until that was fixed; see `handoff.md`.

## Resend email configuration

- `RESEND_API_KEY` — **required** for `POST /api/invite` to succeed at all (see
  "Candidate invitation email delivery" above) as well as for the separate "report
  ready" notification to `ADMIN_EMAIL`. Without it, every invite attempt fails with a
  clean `500` ("Email delivery not configured. Set RESEND_API_KEY in .env.") and the
  report notification is silently skipped — the invite failure is a hard error by
  design (fail loud); the report notification is best-effort by design (it is not on
  the interview/report critical path — the RWS webhook is the real completion
  signal). Get a real key from the Resend dashboard (`resend.com/api-keys`); never
  commit one.
- `RESEND_FROM_ADDRESS` — the email's `From` header, e.g. `"VoiceHR <you@yourdomain.com>"`.
  Defaults to Resend's own unverified-domain sandbox sender (`VoiceHR <onboarding@resend.dev>`)
  if left unset. This must never be set to the API key itself. **Important**: Resend's
  sandbox sender can only deliver to the email address the Resend account itself is
  registered under — it cannot send to arbitrary candidate addresses. To actually
  invite real candidates by email, verify a real sending domain in the Resend
  dashboard (`resend.com/domains`) and set this to an address on that domain.
- `ADMIN_EMAIL` — recipient of the "interview report ready" notification.
- `REPORT_EMAIL_TIMEOUT_SECONDS` — bounded wait for that notification call; a timeout returns
  `False` rather than blocking or crashing the completion pipeline.

## Relevant environment variables

See `.env.example` for the full, authoritative list with defaults. Grouped by concern:

| Concern | Variables |
| --- | --- |
| Gemini (sole AI provider) | `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_EVALUATION_MODEL` (evaluation), `GEMINI_LIVE_MODEL`, `GEMINI_LIVE_VOICE` (Live conversation, active whenever `GEMINI_API_KEY` is set) |
| Email | `RESEND_API_KEY`, `RESEND_FROM_ADDRESS`, `ADMIN_EMAIL`, `REPORT_EMAIL_TIMEOUT_SECONDS` |
| Admin auth | `ADMIN_USERNAME`, `ADMIN_PASSWORD` |
| RWS integration | `RWS_SERVICE_API_KEY` (inbound `POST /api/invite` auth), `RWS_WEBHOOK_HMAC_SECRET` (outbound signature), `RWS_WEBHOOK_URL` (outbound target) |
| Base URL | `BASE_URL` — used to build interview/report links |

## Known deliberate limitations

- **`report_token` never expires.** A generated report link is retrievable indefinitely by
  anyone holding the URL. No expiry has been added.
- **`frontend/admin.html` is additionally reachable, unauthenticated, at `/admin.html`** via
  the `StaticFiles` mount at `/` (which serves every file under `frontend/`) — the `/admin`
  route's Basic-auth dependency doesn't cover that second path. The page itself carries no
  embedded data; the APIs it calls remain protected.
- **RESOLVED: candidates now receive their interview link automatically**, emailed by
  `POST /api/invite` itself the moment RWS creates the invitation — see "Candidate invitation
  email delivery" above. `admin.html`'s own "generate interview link" form (which called
  `POST /api/invite` directly from the browser with no `X-Api-Key`, which `require_rws_service_key`
  has always rejected) was removed as obsolete/broken during an earlier cleanup and was not
  restored — RWS remains the sole interview creator, and there is still no admin-UI path to
  create or resend an invitation, by design. `POST /interview/{token}/invite`
  (`backend/api/routes/interview.py`) still exists as an explicit, unauthenticated,
  always-resends primitive, now backed by the same delivery implementation as the automatic
  path above, but is not currently called from any frontend.
- **`RESEND_API_KEY` is not configured in this environment** (checked during the session that
  added automatic delivery, above). Without it, every `POST /api/invite` call now fails
  (`500`, `_send_email` raises immediately) — a real, current gap, not a hypothetical one; see
  `handoff.md`'s Known Limitations for the demo-readiness impact.
- **VoiceHR's own logging stays plain-text**, with no correlation IDs and no JSON formatting —
  unlike the other three services in this project. Deliberate: VoiceHR's architecture is kept
  unchanged, and `shared/observability.py` isn't importable here (VoiceHR's Docker image
  doesn't copy in `/shared`).
- **No retry queue or reconciliation** for the outbound RWS webhook, and no automated retry for
  a failed candidate-invitation or report-notification email — all intentionally out of scope
  per this project's "no message brokers, no background job infrastructure" rule.
- **No phone-call (Vapi) interview mode.** VoiceHR's original codebase included a Vapi-webhook-
  triggered completion path and Vapi client/assistant settings for a production phone-call
  product mode; both were unused by this project's local WebSocket/Gemini Live demo flow and
  were removed during cleanup. If phone-call interviews are ever needed again, they would need
  to be reintroduced deliberately, not restored from history as dead weight.
