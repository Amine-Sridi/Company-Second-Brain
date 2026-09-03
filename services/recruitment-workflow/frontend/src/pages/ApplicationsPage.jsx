import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { getJobs } from "../api/jobs.js";
import { getApplications, invite } from "../api/applications.js";
import { ApiError } from "../api/client.js";
import { useCandidateIdentity } from "../hooks/useCandidateIdentity.js";
import styles from "./ApplicationsPage.module.css";

const STATUS_LABELS = {
  selected: "Selected",
  invited: "Invited",
  interviewed: "Interviewed",
  accepted: "Accepted",
  rejected: "Rejected",
};

const STATUS_STYLES = {
  selected: "statusSelected",
  invited: "statusInvited",
  interviewed: "statusInterviewed",
  accepted: "statusAccepted",
  rejected: "statusRejected",
};

const INTERVIEW_STATUS_LABELS = {
  completed: "Completed",
  inconclusive: "Inconclusive",
  failed: "Failed",
};

// Statuses RWS itself accepts an invite/resend-invite call for (main.py:
// invite_application). Showing the button only here is a UX hint that mirrors this
// rule -- the backend's own check is what's actually authoritative (§22).
const INVITABLE_STATUSES = new Set(["selected", "invited"]);

/**
 * Tracks the candidates selected for a job through invitation. Polling, VoiceHR
 * assessment rendering, and accept/reject land in later passes (§24) -- this page only
 * shows current backend state and lets a recruiter invite/resend-invite.
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

  function handleApplicationUpdated(updated) {
    setApplications((current) =>
      current.map((application) =>
        application.application_id === updated.application_id ? updated : application
      )
    );
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>{job ? `Applications — ${job.title}` : "Applications"}</h1>
      </div>

      {loading && <p className={styles.status}>Loading applications…</p>}

      {!loading && error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}

      {!loading && !error && applications.length === 0 && (
        <p className={styles.status}>
          No applications yet — select a candidate from the ranking table.
        </p>
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

  const displayName =
    identity.status === "ready"
      ? identity.name
      : identity.status === "loading"
        ? "Loading…"
        : `Candidate ${application.candidate_id.slice(0, 8)}…`;

  const canInvite = INVITABLE_STATUSES.has(application.status);
  const inviteLabel = application.status === "invited" ? "Resend Invite" : "Invite";

  async function handleInvite() {
    if (inviting || !canInvite) return;
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

  return (
    <>
      <tr>
        <td>
          <span className={styles.candidateName}>{displayName}</span>
          {identity.status === "error" && (
            <span className={styles.identityWarning}> (name unavailable)</span>
          )}
        </td>
        <td>
          <span className={`${styles.statusBadge} ${styles[STATUS_STYLES[application.status]]}`}>
            {STATUS_LABELS[application.status] ?? application.status}
          </span>
        </td>
        <td>
          {application.interview_status ? (
            <span className={styles.interviewStatus}>
              {INTERVIEW_STATUS_LABELS[application.interview_status] ?? application.interview_status}
            </span>
          ) : (
            <span className={styles.interviewStatusEmpty}>—</span>
          )}
        </td>
        <td>
          {canInvite && (
            <button
              type="button"
              className={styles.inviteButton}
              onClick={handleInvite}
              disabled={inviting}
            >
              {inviting ? "Sending…" : inviteLabel}
            </button>
          )}
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
    </>
  );
}
