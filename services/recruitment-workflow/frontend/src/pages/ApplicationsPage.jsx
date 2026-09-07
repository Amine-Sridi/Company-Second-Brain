import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getJobs } from "../api/jobs.js";
import { getApplications, invite, accept, reject } from "../api/applications.js";
import { ApiError } from "../api/client.js";
import { useCandidateIdentity } from "../hooks/useCandidateIdentity.js";
import { usePolling } from "../hooks/usePolling.js";
import PageHeader from "../components/PageHeader.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import EmptyState from "../components/EmptyState.jsx";
import AssessmentPanel from "../components/AssessmentPanel.jsx";
import { STATUS_LABELS, STATUS_TONE } from "../constants/applicationStatus.js";
import styles from "./ApplicationsPage.module.css";

const POLL_INTERVAL_MS = 7000;

const INTERVIEW_STATUS_LABELS = {
  completed: "Completed",
  inconclusive: "Inconclusive",
  failed: "Failed",
};

// Statuses RWS itself accepts an invite/resend-invite call for (main.py:
// invite_application). Showing the button only here is a UX hint that mirrors this
// rule -- the backend's own check is what's actually authoritative (§22).
const INVITABLE_STATUSES = new Set(["selected", "invited"]);

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
      <PageHeader title={job ? `Applications — ${job.title}` : "Applications"} />

      {loading && <p className={styles.status}>Loading applications…</p>}

      {!loading && error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}

      {!loading && !error && applications.length === 0 && (
        <EmptyState
          title="No applications yet"
          description="Select a candidate from the ranking table to get started."
        />
      )}

      {!loading && !error && applications.length > 0 && (
        <div className={styles.tableWrapper}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Candidate</th>
                <th>Status</th>
                <th>Interview</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {applications.map((application) => (
                <ApplicationRow
                  key={application.application_id}
                  application={application}
                  onUpdated={handleApplicationUpdated}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ApplicationRow({ application, onUpdated }) {
  const identity = useCandidateIdentity(application.candidate_id);
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState(null);
  const [deciding, setDeciding] = useState(null); // null | "accept" | "reject"
  const [decideError, setDecideError] = useState(null);
  const [expanded, setExpanded] = useState(false);

  const displayName =
    identity.status === "ready"
      ? identity.name
      : identity.status === "loading"
        ? "Loading…"
        : `Candidate ${application.candidate_id.slice(0, 8)}…`;

  const canInvite = INVITABLE_STATUSES.has(application.status);
  const inviteLabel = application.status === "invited" ? "Resend Invite" : "Invite";
  const canDecide = DECIDABLE_STATUSES.has(application.status);
  const hasAssessment = application.interview_assessment != null;
  // A decision (accept/reject) and an invite both mutate the same application row --
  // disabling every action while any one is in flight avoids firing two conflicting
  // requests for the same row at once. This is a UX safeguard only; RWS's own
  // per-endpoint checks (409s) remain the real authority regardless.
  const rowBusy = inviting || deciding !== null;

  async function handleInvite() {
    if (rowBusy || !canInvite) return;
    setInviting(true);
    setInviteError(null);
    try {
      // Real POST /v1/applications/{id}/invite. RWS itself gates this on the
      // application's current status (409 otherwise), looks up the candidate's
      // contact server-side, and calls VoiceHR -- none of that is duplicated here.
      const updated = await invite(application.application_id);
      onUpdated(updated);
    } catch (err) {
      setInviteError(err instanceof ApiError ? err.message : "Failed to send invitation.");
    } finally {
      setInviting(false);
    }
  }

  async function handleDecide(decision) {
    if (rowBusy || !canDecide) return;
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

  return (
    <>
      <tr
        className={hasAssessment ? styles.row : undefined}
        onClick={hasAssessment ? () => setExpanded((value) => !value) : undefined}
        onKeyDown={
          hasAssessment
            ? (event) => {
                if (event.key === "Enter" || event.key === " ") {
                  if (event.key === " ") event.preventDefault();
                  setExpanded((value) => !value);
                }
              }
            : undefined
        }
        role={hasAssessment ? "button" : undefined}
        tabIndex={hasAssessment ? 0 : undefined}
      >
        <td>
          <Link
            to={`/candidates/${application.candidate_id}`}
            className={styles.candidateName}
            onClick={(event) => event.stopPropagation()}
          >
            {displayName}
          </Link>
          {identity.status === "error" && (
            <span className={styles.identityWarning}> (name unavailable)</span>
          )}
        </td>
        <td>
          <StatusBadge tone={STATUS_TONE[application.status] ?? "neutral"}>
            {STATUS_LABELS[application.status] ?? application.status}
          </StatusBadge>
        </td>
        <td>
          {application.interview_status ? (
            <span className={styles.interviewStatus}>
              {INTERVIEW_STATUS_LABELS[application.interview_status] ?? application.interview_status}
              {hasAssessment && (
                <span className={styles.expandHint}>{expanded ? " ▾" : " ▸"}</span>
              )}
            </span>
          ) : (
            <span className={styles.interviewStatusEmpty}>—</span>
          )}
        </td>
        <td onClick={(event) => event.stopPropagation()}>
          <div className={styles.actions}>
            {canInvite && (
              <button
                type="button"
                className={styles.inviteButton}
                onClick={handleInvite}
                disabled={rowBusy}
              >
                {inviting ? "Sending…" : inviteLabel}
              </button>
            )}
            {canDecide && (
              <>
                <button
                  type="button"
                  className={styles.acceptButton}
                  onClick={() => handleDecide("accept")}
                  disabled={rowBusy}
                >
                  {deciding === "accept" ? "Accepting…" : "Accept"}
                </button>
                <button
                  type="button"
                  className={styles.rejectButton}
                  onClick={() => handleDecide("reject")}
                  disabled={rowBusy}
                >
                  {deciding === "reject" ? "Rejecting…" : "Reject"}
                </button>
              </>
            )}
            {application.status === "accepted" && (
              <span className={styles.decidedLabelAccepted}>Accepted ✓</span>
            )}
            {application.status === "rejected" && (
              <span className={styles.decidedLabelRejected}>Rejected</span>
            )}
          </div>
        </td>
      </tr>

      {inviteError && (
        <tr>
          <td colSpan={4}>
            <div className={styles.error} role="alert">
              {inviteError}
            </div>
          </td>
        </tr>
      )}

      {decideError && (
        <tr>
          <td colSpan={4}>
            <div className={styles.error} role="alert">
              {decideError}
            </div>
          </td>
        </tr>
      )}

      {expanded && hasAssessment && (
        <tr>
          <td colSpan={4}>
            <AssessmentPanel assessment={application.interview_assessment} />
          </td>
        </tr>
      )}
    </>
  );
}
