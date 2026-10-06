import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getJobs } from "../api/jobs.js";
import {
  getApplications,
  invite,
  resendInvite,
  accept,
  reject,
  createInterviewRound,
  rescheduleInterviewRound,
  completeInterviewRound,
  cancelInterviewRound,
  resendInterviewRoundNotification,
} from "../api/applications.js";
import { ApiError } from "../api/client.js";
import { useCandidateIdentity } from "../hooks/useCandidateIdentity.js";
import { usePolling } from "../hooks/usePolling.js";
import PageHeader from "../components/PageHeader.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import EmptyState from "../components/EmptyState.jsx";
import AssessmentPanel from "../components/AssessmentPanel.jsx";
import { STATUS_LABELS } from "../constants/applicationStatus.js";
import {
  EVALUATION_STATUS_LABELS,
  LANGUAGE_GATE_LABELS,
  LANGUAGE_GATE_TONE,
  describeLanguageGate,
  formatLanguageRequirement,
} from "../constants/language.js";
import {
  ROUND_TYPE_LABELS,
  ROUND_OUTCOME_LABELS,
  NOTIFICATION_STATUS_LABELS,
  roundStatusLabel,
  roundStatusTone,
  notificationStatusTone,
  describeRound,
  describeScheduleBlockedReason,
  toDatetimeLocalInputValue,
} from "../constants/interviewRounds.js";
import styles from "./ApplicationsPage.module.css";

const POLL_INTERVAL_MS = 7000;

// application.interview_status is RWS's own coarse field (db.py: only ever
// "completed"/"inconclusive"/"failed", never "insufficient_evidence" -- that finer
// distinction lives one level down, inside interview_assessment.evaluation_status,
// shown via AssessmentPanel). Reuses the same label wording as
// EVALUATION_STATUS_LABELS for the three values both fields share, rather than a
// second, separately-maintained copy of identical text.
const INTERVIEW_STATUS_LABELS = EVALUATION_STATUS_LABELS;

// Status RWS itself accepts an initial invite/retry call for (main.py:
// invite_application, "selected" branch). Once "invited", further re-sends go
// through the separate explicit resend endpoint instead (RESENDABLE_STATUSES below)
// -- showing the right button for the right status is a UX hint that mirrors the
// backend's own rules; the backend's own check is what's actually authoritative (§22).
const INVITABLE_STATUSES = new Set(["selected"]);

// Status RWS itself accepts an explicit resend call for (main.py: resend_invite).
const RESENDABLE_STATUSES = new Set(["invited"]);

// Statuses RWS itself accepts an accept/reject decision for (db.py:
// _DECIDABLE_STATUSES). "accepted"/"rejected" are terminal -- once a status leaves
// this set, no decision button reappears for that row; the real backstop if this
// frontend rule and the backend's ever drift is RWS's own 409 ("Application is not in
// a decidable state").
const DECIDABLE_STATUSES = new Set(["selected", "invited", "interviewed"]);

/**
 * Tracks the candidates selected for a job through invitation, interview completion,
 * and the recruiter's final accept/reject decision. Polls while an interview is
 * pending so a completed evaluation shows up without a manual reload; renders the
 * real interview_assessment once one lands.
 */
export default function ApplicationsPage() {
  const { jobId } = useParams();

  const [job, setJob] = useState(null); // best-effort header info, same pattern as JobDetailPage
  const [applications, setApplications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    loadApplications();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  async function loadApplications() {
    setLoading(true);
    setError(null);
    setJob(null);

    // Best-effort header (no dedicated GET /v1/jobs/{id} on RWS -- see
    // FRONTEND_HANDOFF.md §20); never blocks the page if it fails.
    getJobs()
      .then((jobs) => setJob(jobs.find((j) => j.job_id === jobId) ?? null))
      .catch(() => setJob(null));

    // Always fetched fresh from RWS -- never trusts navigation state, so a hard
    // browser refresh on this URL reflects real backend state.
    try {
      const response = await getApplications(jobId);
      setApplications(response);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load applications.");
    } finally {
      setLoading(false);
    }
  }

  // Silent background refresh for polling -- deliberately does not touch
  // loading/error state. If this throws, usePolling swallows it and this tick simply
  // doesn't update anything; whatever is already on screen stays exactly as it was,
  // and the next tick tries again. Only a successful response ever changes state.
  async function pollApplications() {
    const response = await getApplications(jobId);
    setApplications(response);
  }

  // Polling is only meaningful while some application is actually waiting on an
  // interview -- derived straight from the current list, so it turns itself off the
  // moment nothing is left `invited` (a webhook advanced it to interviewed, or there
  // was never anything invited in the first place). No manual "stop polling" needed.
  const hasInvitedApplication = applications.some((application) => application.status === "invited");

  usePolling(pollApplications, { intervalMs: POLL_INTERVAL_MS, enabled: hasInvitedApplication });

  function handleApplicationUpdated(updated) {
    setApplications((current) =>
      current.map((application) =>
        application.application_id === updated.application_id ? updated : application
      )
    );
  }

  return (
    <div className={styles.page}>
      <Link to={`/jobs/${jobId}`} className={styles.backLink}>
        Back to the job
      </Link>
      <PageHeader
        title={job ? job.title : "Applications"}
        subtitle={
          job &&
          `Applications · ${applications.length} candidate${applications.length === 1 ? "" : "s"}${
            job.required_language ? ` · requires ${formatLanguageRequirement(job.required_language, job.min_language_cefr)}` : ""
          }`
        }
      />

      {loading && <p className={styles.status}>Loading applications…</p>}

      {!loading && error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}

      {!loading && !error && applications.length === 0 && (
        <EmptyState
          variant="glass"
          title="No applications yet"
          description="Select a candidate from the ranking table to get started."
        />
      )}

      {!loading && !error && applications.length > 0 && (
        <div className={styles.cards}>
          {applications.map((application) => (
            <ApplicationRow
              key={application.application_id}
              application={application}
              onUpdated={handleApplicationUpdated}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ApplicationRow({ application, onUpdated }) {
  const identity = useCandidateIdentity(application.candidate_id);
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState(null);
  // Ephemeral, component-scoped only -- RWS deliberately does not persist "last
  // attempt failed" as a workflow status (a transient email failure leaves the
  // application in "selected" with no new field to distinguish "never attempted"
  // from "attempted and failed"; see handoff.md). Naturally reverts to a plain
  // "Invite" button on reload, which is correct: reload means re-reading real
  // backend state, and this label is deliberately not part of it.
  const [lastInviteFailed, setLastInviteFailed] = useState(false);
  const [resending, setResending] = useState(false);
  const [resendError, setResendError] = useState(null);
  const [resendSuccess, setResendSuccess] = useState(false);
  const [deciding, setDeciding] = useState(null); // null | "accept" | "reject"
  const [decideError, setDecideError] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const [schedulingOpen, setSchedulingOpen] = useState(false);
  const [roundType, setRoundType] = useState("technical");
  const [interviewerName, setInterviewerName] = useState("");
  const [interviewerEmail, setInterviewerEmail] = useState("");
  const [roundLocation, setRoundLocation] = useState("");
  const [scheduledAt, setScheduledAt] = useState("");
  const [roundNotes, setRoundNotes] = useState("");
  const [scheduling, setScheduling] = useState(false);
  const [scheduleError, setScheduleError] = useState(null);

  useEffect(() => {
    if (!resendSuccess) return undefined;
    const timer = setTimeout(() => setResendSuccess(false), 4000);
    return () => clearTimeout(timer);
  }, [resendSuccess]);

  const displayName =
    identity.status === "ready"
      ? identity.name
      : identity.status === "loading"
        ? "Loading…"
        : `Candidate ${application.candidate_id.slice(0, 8)}…`;

  const canInvite = INVITABLE_STATUSES.has(application.status);
  const canResend = RESENDABLE_STATUSES.has(application.status);
  const canDecide = DECIDABLE_STATUSES.has(application.status);
  const hasAssessment = application.interview_assessment != null;
  // RWS itself is the real authority (a 422 naming the reason is the backstop --
  // see main.py: accept_application); this only mirrors that rule so the button is
  // never shown as clickable when the backend would reject it anyway.
  const gate = application.language_gate ?? { status: "not_required" };
  const languageGateBlocksAccept = gate.status === "pending" || gate.status === "failed" || gate.status === "needs_review";
  const canAccept = canDecide && !languageGateBlocksAccept;
  const rounds = application.interview_rounds ?? [];
  // Mirrors main.py's create_interview_round_endpoint's own two gates (application
  // not accepted/rejected, language gate not blocking) -- see
  // constants/interviewRounds.js. RWS's own 422 is the real authority; this only
  // keeps the button from ever looking clickable when it would be rejected anyway.
  const scheduleBlockedReason = describeScheduleBlockedReason(application);
  const canSchedule = scheduleBlockedReason === null;
  // A decision (accept/reject), an invite, a resend, and scheduling a round all
  // mutate/target the same application row -- disabling every action while any one
  // is in flight avoids firing two conflicting requests for the same row at once,
  // and is the first layer (alongside RWS's own server-side cooldown on resend)
  // against double-click spam. This is a UX safeguard only; RWS's own per-endpoint
  // checks (409s/422s/429s) remain the real authority regardless.
  const rowBusy = inviting || resending || deciding !== null || scheduling;

  async function handleInvite() {
    if (rowBusy || !canInvite) return;
    setInviting(true);
    setInviteError(null);
    try {
      // Real POST /v1/applications/{id}/invite. RWS itself gates this on the
      // application's current status (409 otherwise), looks up the candidate's
      // contact server-side, and calls VoiceHR -- none of that is duplicated here.
      // While still "selected", this same call is also how the recruiter retries a
      // previously failed send: RWS/VoiceHR reuse the same interview/token via their
      // own idempotency key, so this never creates a duplicate interview.
      const updated = await invite(application.application_id);
      setLastInviteFailed(false);
      onUpdated(updated);
    } catch (err) {
      setLastInviteFailed(true);
      setInviteError(err instanceof ApiError ? err.message : "Invitation not sent.");
    } finally {
      setInviting(false);
    }
  }

  async function handleResend() {
    if (rowBusy || !canResend) return;
    setResending(true);
    setResendError(null);
    setResendSuccess(false);
    try {
      // Real POST /v1/applications/{id}/resend-invite. Intentionally sends another
      // email on the same VoiceHR interview/token -- never creates a new
      // application/interview, and a failure here never reverts this application
      // from "invited" (RWS itself enforces both; see main.py: resend_invite).
      const updated = await resendInvite(application.application_id);
      onUpdated(updated);
      setResendSuccess(true);
    } catch (err) {
      setResendError(err instanceof ApiError ? err.message : "Failed to resend invitation.");
    } finally {
      setResending(false);
    }
  }

  async function handleDecide(decision) {
    if (rowBusy || !canDecide) return;
    if (decision === "accept" && !canAccept) return;
    // Reject is the one terminal action worth a light native guard against a
    // misclick -- accept is the "positive" outcome and doesn't warrant the same
    // friction; nothing here adds a custom modal, just the browser's own confirm().
    if (decision === "reject" && !window.confirm("Reject this candidate? This cannot be undone.")) {
      return;
    }
    setDeciding(decision);
    setDecideError(null);
    try {
      // Real POST /v1/applications/{id}/accept or /reject. RWS itself enforces the
      // decidable-status rule (409 otherwise) and records the decision -- this
      // frontend only requests the transition the recruiter clicked and renders
      // whatever status comes back (§22).
      const updated = await (decision === "accept" ? accept : reject)(application.application_id);
      onUpdated(updated);
    } catch (err) {
      setDecideError(
        err instanceof ApiError ? err.message : `Failed to ${decision} candidate.`
      );
    } finally {
      setDeciding(null);
    }
  }

  function resetScheduleForm() {
    setSchedulingOpen(false);
    setRoundType("technical");
    setInterviewerName("");
    setInterviewerEmail("");
    setRoundLocation("");
    setScheduledAt("");
    setRoundNotes("");
    setScheduleError(null);
  }

  async function handleSchedule(event) {
    event.preventDefault();
    if (rowBusy || !canSchedule) return;
    setScheduling(true);
    setScheduleError(null);
    try {
      // Real POST /v1/applications/{id}/interview-rounds. RWS itself enforces both
      // preconditions (application not accepted/rejected, language gate not
      // blocking -- 422 naming which) and never touches Application.status;
      // it returns only the new InterviewRoundResponse, not the whole updated
      // application, so the round is appended onto this row's own already-loaded
      // list locally rather than re-fetching everything (nothing else about the
      // application changes when a round is created). RWS also best-effort emails
      // the candidate and interviewer as part of this same call (phase 1 of
      // automating human interview scheduling, see handoff.md) -- a delivery
      // failure never fails this request, it only shows up as the returned
      // round's own notification_status.
      const newRound = await createInterviewRound(application.application_id, {
        roundType,
        interviewerName: interviewerName.trim(),
        interviewerEmail: interviewerEmail.trim(),
        location: roundLocation.trim() || undefined,
        scheduledAt: new Date(scheduledAt).toISOString(),
        notes: roundNotes.trim() || undefined,
      });
      onUpdated({ ...application, interview_rounds: [...rounds, newRound] });
      resetScheduleForm();
    } catch (err) {
      setScheduleError(err instanceof ApiError ? err.message : "Failed to schedule interview round.");
    } finally {
      setScheduling(false);
    }
  }

  // Progress line: the three steps of the state machine before a decision, then the
  // decision itself. A step is "done" when the application is past it.
  const STEP_ORDER = ["selected", "invited", "interviewed"];
  const decided = application.status === "accepted" || application.status === "rejected";
  const currentIndex = decided ? 3 : STEP_ORDER.indexOf(application.status);
  const steps = [
    ...STEP_ORDER.map((status, index) => ({
      label: STATUS_LABELS[status],
      state: index < currentIndex ? "done" : index === currentIndex ? "current" : "todo",
    })),
    {
      label: decided ? STATUS_LABELS[application.status] : "Decision",
      state: decided ? (application.status === "accepted" ? "accepted" : "rejected") : "todo",
    },
  ];
  const assessment = application.interview_assessment;
  const initials = displayName
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

  return (
    <article className={styles.card}>
      <div className={styles.cardHead}>
        <div className={styles.who}>
          <span className={styles.avatar} aria-hidden="true">
            {initials || "?"}
          </span>
          <div className={styles.whoText}>
            <Link to={`/candidates/${application.candidate_id}`} className={styles.candidateName}>
              {displayName}
            </Link>
            {identity.status === "error" && <span className={styles.identityWarning}>(name unavailable)</span>}
            <span className={styles.whoMeta}>
              Selected {new Date(application.selected_at).toLocaleDateString(undefined, { day: "numeric", month: "short" })}
            </span>
          </div>
        </div>
        <ol className={styles.steps} aria-label="Progress">
          {steps.map((step) => (
            <li key={step.label} className={styles[`step_${step.state}`]} aria-current={step.state === "current" ? "step" : undefined}>
              {step.label}
            </li>
          ))}
        </ol>
      </div>

      <div className={styles.panels}>
        <section className={styles.panel} aria-label="VoiceHR interview">
          <span className={styles.panelTitle}>VoiceHR interview</span>
          {assessment ? (
            <>
              <span className={styles.voiceLine}>
                <span className={styles.cefr}>{assessment.cefr_level ?? "—"}</span>
                <span className={styles.voiceMeta}>
                  {assessment.overall_score != null ? `${Math.round(assessment.overall_score)}/100 · ` : ""}
                  {INTERVIEW_STATUS_LABELS[application.interview_status] ?? application.interview_status}
                </span>
              </span>
              <span className={styles.panelLinks}>
                <Link to={`/reports/${application.application_id}`}>Open report</Link>
                <button type="button" className={styles.textButton} onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
                  {expanded ? "Hide assessment" : "Show assessment"}
                </button>
              </span>
            </>
          ) : (
            <span className={styles.panelEmpty}>
              {application.status === "invited"
                ? "Invitation sent, waiting for the interview."
                : application.interview_status
                  ? INTERVIEW_STATUS_LABELS[application.interview_status] ?? application.interview_status
                  : "Not invited yet."}
            </span>
          )}
        </section>

        <section className={`${styles.panel} ${gate.status === "failed" ? styles.panelWarn : ""}`} aria-label="Language screening">
          <span className={styles.panelTitle}>Language screening</span>
          {gate.status === "not_required" ? (
            <span className={styles.panelEmpty}>No language requirement for this job.</span>
          ) : (
            <>
              <StatusBadge tone={LANGUAGE_GATE_TONE[gate.status] ?? "neutral"}>
                {LANGUAGE_GATE_LABELS[gate.status] ?? gate.status}
              </StatusBadge>
              <span className={styles.panelNote}>{describeLanguageGate(gate)}</span>
            </>
          )}
        </section>

        <section className={styles.panel} aria-label="Human interview rounds">
          <span className={styles.panelTitle}>Human interview rounds</span>
          <div className={styles.roundsList}>
            {rounds.length === 0 && !schedulingOpen && <span className={styles.panelEmpty}>No round scheduled.</span>}
            {rounds.map((round) => (
              <InterviewRoundItem
                key={round.id}
                applicationId={application.application_id}
                round={round}
                onRoundUpdated={(updatedRound) =>
                  onUpdated({
                    ...application,
                    interview_rounds: rounds.map((r) => (r.id === updatedRound.id ? updatedRound : r)),
                  })
                }
              />
            ))}
          </div>

          {!schedulingOpen ? (
            <button
              type="button"
              className={styles.scheduleRoundButton}
              onClick={() => setSchedulingOpen(true)}
              disabled={rowBusy || !canSchedule}
              title={scheduleBlockedReason ?? undefined}
            >
              Schedule a round
            </button>
          ) : (
            <form className={styles.scheduleForm} onSubmit={handleSchedule}>
              <select
                className={styles.scheduleFormSelect}
                value={roundType}
                onChange={(event) => setRoundType(event.target.value)}
                disabled={scheduling}
                aria-label="Interview type"
              >
                {Object.entries(ROUND_TYPE_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              <input
                type="text"
                className={styles.scheduleFormInput}
                placeholder="Interviewer name"
                aria-label="Interviewer name"
                value={interviewerName}
                onChange={(event) => setInterviewerName(event.target.value)}
                disabled={scheduling}
                required
              />
              <input
                type="email"
                className={styles.scheduleFormInput}
                placeholder="Interviewer email"
                aria-label="Interviewer email"
                value={interviewerEmail}
                onChange={(event) => setInterviewerEmail(event.target.value)}
                disabled={scheduling}
                required
              />
              <input
                type="datetime-local"
                className={styles.scheduleFormInput}
                value={scheduledAt}
                onChange={(event) => setScheduledAt(event.target.value)}
                disabled={scheduling}
                required
                aria-label="Scheduled date and time"
              />
              <input
                type="text"
                className={styles.scheduleFormInput}
                placeholder="Meeting link or location (optional)"
                aria-label="Meeting link or location"
                value={roundLocation}
                onChange={(event) => setRoundLocation(event.target.value)}
                disabled={scheduling}
              />
              <textarea
                className={styles.scheduleFormTextarea}
                placeholder="Notes (optional)"
                aria-label="Notes"
                value={roundNotes}
                onChange={(event) => setRoundNotes(event.target.value)}
                disabled={scheduling}
                rows={2}
              />
              <div className={styles.scheduleFormActions}>
                <button type="submit" className={styles.primaryButton} disabled={scheduling}>
                  {scheduling ? "Scheduling…" : "Save and send invitations"}
                </button>
                <button type="button" className={styles.scheduleRoundCancelButton} onClick={resetScheduleForm} disabled={scheduling}>
                  Cancel
                </button>
              </div>
              {scheduleError && (
                <div className={styles.error} role="alert">
                  {scheduleError}
                </div>
              )}
            </form>
          )}
        </section>
      </div>

      <div className={styles.cardFoot}>
        {canInvite && (
          <>
            {lastInviteFailed && !inviting && <span className={styles.inviteFailedText}>Invitation not sent</span>}
            <button type="button" className={styles.primaryButton} onClick={handleInvite} disabled={rowBusy}>
              {inviting ? "Sending…" : lastInviteFailed ? "Retry the invitation" : "Send VoiceHR invitation"}
            </button>
          </>
        )}
        {canResend && (
          <>
            <span className={styles.inviteSentText}>{resendSuccess && !resending ? "Invitation resent" : "Invitation sent"}</span>
            <button type="button" className={styles.secondaryButton} onClick={handleResend} disabled={rowBusy}>
              {resending ? "Sending…" : "Resend invitation"}
            </button>
          </>
        )}
        {canDecide && (
          <>
            <button
              type="button"
              className={styles.acceptButton}
              onClick={() => handleDecide("accept")}
              disabled={rowBusy || !canAccept}
              title={!canAccept ? describeLanguageGate(gate) ?? undefined : undefined}
            >
              {deciding === "accept" ? "Accepting…" : "Accept"}
            </button>
            <button type="button" className={styles.rejectButton} onClick={() => handleDecide("reject")} disabled={rowBusy}>
              {deciding === "reject" ? "Rejecting…" : "Reject"}
            </button>
            {!canAccept && (
              <span className={styles.blockedNote}>Acceptance is blocked by the language screening. Rejecting is always possible.</span>
            )}
          </>
        )}
        {decided && (
          <>
            <StatusBadge tone={application.status === "accepted" ? "ink" : "neutral"}>{STATUS_LABELS[application.status]}</StatusBadge>
            <span className={styles.decidedNote}>
              Decided {application.decided_at ? new Date(application.decided_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : ""}.
              The decision is recorded with the recruiter and the previous state.
            </span>
          </>
        )}
      </div>

      {inviteError && (
        <div className={styles.error} role="alert">
          {inviteError}
        </div>
      )}
      {resendError && (
        <div className={styles.error} role="alert">
          {resendError}
        </div>
      )}
      {decideError && (
        <div className={styles.error} role="alert">
          {decideError}
        </div>
      )}

      {expanded && hasAssessment && <AssessmentPanel assessment={application.interview_assessment} />}
    </article>
  );
}

/**
 * One round's badge plus its own lifecycle actions -- Reschedule/Complete/Cancel,
 * only ever shown for a "scheduled" round (db.py's own state machine: a
 * completed/cancelled round is a closed record, never editable again). Each round
 * owns its own form/loading/error state independently of its siblings and of the
 * rest of the row, so acting on one round never disturbs another round, an
 * in-flight "Schedule interview" for a new round, or accept/reject/invite --
 * these all target different sub-resources (this specific round vs. the
 * application itself), so there's nothing to corrupt by letting them run
 * independently, unlike invite/resend/decide which all mutate the same
 * Application row and do share one busy flag (see ApplicationRow's own rowBusy).
 */
function InterviewRoundItem({ applicationId, round, onRoundUpdated }) {
  const [mode, setMode] = useState(null); // null | "reschedule" | "complete"
  const [rescheduledAt, setRescheduledAt] = useState("");
  const [outcome, setOutcome] = useState("pass");
  const [completeNotes, setCompleteNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState(null);
  const [cancelling, setCancelling] = useState(false);
  const [resendingNotification, setResendingNotification] = useState(false);

  const isScheduled = round.status === "scheduled";

  function openReschedule() {
    setMode("reschedule");
    setRescheduledAt(toDatetimeLocalInputValue(round.scheduled_at));
    setActionError(null);
  }

  function openComplete() {
    setMode("complete");
    setOutcome("pass");
    setCompleteNotes("");
    setActionError(null);
  }

  function closeForm() {
    setMode(null);
    setActionError(null);
  }

  async function handleReschedule(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setActionError(null);
    try {
      // Real POST .../interview-rounds/{round_id}/reschedule. RWS itself only
      // allows this from "scheduled" (409 naming the round's current status
      // otherwise) and never touches Application.status.
      const updated = await rescheduleInterviewRound(applicationId, round.id, {
        scheduledAt: new Date(rescheduledAt).toISOString(),
      });
      onRoundUpdated(updated);
      setMode(null);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to reschedule interview round.");
    } finally {
      setBusy(false);
    }
  }

  async function handleComplete(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setActionError(null);
    try {
      // Real POST .../interview-rounds/{round_id}/complete. RWS itself requires a
      // valid outcome and only allows this from "scheduled" (409/422 otherwise);
      // never touches Application.status or the language gate -- a round's
      // outcome is informational only, it never blocks accept/reject.
      const updated = await completeInterviewRound(applicationId, round.id, {
        outcome,
        notes: completeNotes.trim() || undefined,
      });
      onRoundUpdated(updated);
      setMode(null);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to complete interview round.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCancel() {
    if (busy) return;
    // Terminal, no undo -- same native confirm() guard ApplicationsPage's own
    // Reject button already uses for the same reason.
    if (!window.confirm("Cancel this interview round? This cannot be undone.")) return;
    setCancelling(true);
    setActionError(null);
    try {
      const updated = await cancelInterviewRound(applicationId, round.id);
      onRoundUpdated(updated);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to cancel interview round.");
    } finally {
      setCancelling(false);
    }
  }

  async function handleResendNotification() {
    if (resendingNotification) return;
    setResendingNotification(true);
    setActionError(null);
    try {
      // Real POST .../interview-rounds/{round_id}/resend-notification. Unlike
      // every other action here, RWS rejects this with a 502 (not a 200) when
      // delivery itself fails -- resending has no other value if the email
      // doesn't go out, so the error is surfaced the same way as any other
      // failure below, not treated as a partial success.
      const updated = await resendInterviewRoundNotification(applicationId, round.id);
      onRoundUpdated(updated);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to resend notification.");
    } finally {
      setResendingNotification(false);
    }
  }

  return (
    <div className={styles.roundItem}>
      <StatusBadge variant="glass" tone={roundStatusTone(round)} title={describeRound(round)}>
        {ROUND_TYPE_LABELS[round.round_type] ?? round.round_type}: {roundStatusLabel(round)}
      </StatusBadge>
      <StatusBadge
        variant="glass"
        tone={notificationStatusTone(round)}
        title={round.notification_status === "failed" ? round.notification_error ?? undefined : undefined}
      >
        {NOTIFICATION_STATUS_LABELS[round.notification_status] ?? "Notification pending"}
      </StatusBadge>

      {isScheduled && mode === null && (
        <div className={styles.roundActions}>
          <button
            type="button"
            className={styles.roundActionButton}
            onClick={openReschedule}
            disabled={cancelling}
          >
            Reschedule
          </button>
          <button
            type="button"
            className={styles.roundActionButton}
            onClick={openComplete}
            disabled={cancelling}
          >
            Complete
          </button>
          {round.notification_status === "failed" && (
            <button
              type="button"
              className={styles.roundActionButton}
              onClick={handleResendNotification}
              disabled={resendingNotification}
            >
              {resendingNotification ? "Resending…" : "Resend notification"}
            </button>
          )}
          <button
            type="button"
            className={styles.roundCancelButton}
            onClick={handleCancel}
            disabled={cancelling}
          >
            {cancelling ? "Cancelling…" : "Cancel"}
          </button>
        </div>
      )}

      {mode === "reschedule" && (
        <form className={styles.scheduleForm} onSubmit={handleReschedule}>
          <input
            type="datetime-local"
            className={styles.scheduleFormInput}
            value={rescheduledAt}
            onChange={(event) => setRescheduledAt(event.target.value)}
            disabled={busy}
            required
            aria-label="New scheduled date and time"
          />
          <div className={styles.scheduleFormActions}>
            <button type="submit" className={styles.scheduleRoundButton} disabled={busy}>
              {busy ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              className={styles.scheduleRoundCancelButton}
              onClick={closeForm}
              disabled={busy}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {mode === "complete" && (
        <form className={styles.scheduleForm} onSubmit={handleComplete}>
          <select
            className={styles.scheduleFormSelect}
            value={outcome}
            onChange={(event) => setOutcome(event.target.value)}
            disabled={busy}
            aria-label="Interview outcome"
          >
            {Object.entries(ROUND_OUTCOME_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <textarea
            className={styles.scheduleFormTextarea}
            placeholder="Notes (optional)"
            value={completeNotes}
            onChange={(event) => setCompleteNotes(event.target.value)}
            disabled={busy}
            rows={2}
          />
          <div className={styles.scheduleFormActions}>
            <button type="submit" className={styles.scheduleRoundButton} disabled={busy}>
              {busy ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              className={styles.scheduleRoundCancelButton}
              onClick={closeForm}
              disabled={busy}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {actionError && (
        <div className={styles.error} role="alert">
          {actionError}
        </div>
      )}
    </div>
  );
}
