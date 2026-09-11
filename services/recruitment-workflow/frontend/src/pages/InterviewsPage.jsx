import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getAllApplications } from "../api/applications.js";
import { ApiError } from "../api/client.js";
import { useCandidateIdentity } from "../hooks/useCandidateIdentity.js";
import { usePolling } from "../hooks/usePolling.js";
import PageHeader from "../components/PageHeader.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import EmptyState from "../components/EmptyState.jsx";
import AssessmentPanel from "../components/AssessmentPanel.jsx";
import { STATUS_LABELS, STATUS_TONE } from "../constants/applicationStatus.js";
import styles from "./InterviewsPage.module.css";

const POLL_INTERVAL_MS = 7000;

const INTERVIEW_STATUS_LABELS = {
  completed: "Completed",
  inconclusive: "Inconclusive",
  failed: "Failed",
};

/**
 * Global, unscoped view of every application's interview progress across every job
 * -- the same underlying data ApplicationsPage shows per job, via GET
 * /v1/applications instead of GET /v1/jobs/{job_id}/applications. Read-only by
 * design: no invite/accept/reject actions here (see FRONTEND_HANDOFF.md §21) --
 * ApplicationsPage remains the one place a recruiter actually acts on an
 * application; this page exists to see interview status/progress across the whole
 * pipeline at a glance, not to duplicate that mutation surface for no real benefit.
 */
export default function InterviewsPage() {
  const [applications, setApplications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    loadApplications();
  }, []);

  async function loadApplications() {
    setLoading(true);
    setError(null);
    try {
      // Always fetched fresh from RWS -- never trusts stale state, so a hard
      // browser refresh on this URL reflects real backend state (same rule every
      // other page in this app follows).
      const response = await getAllApplications();
      setApplications(response);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load interviews.");
    } finally {
      setLoading(false);
    }
  }

  // Silent background refresh for polling -- same pattern as ApplicationsPage's
  // pollApplications: never touches loading/error state, and a thrown error is
  // swallowed by usePolling itself, so a transient poll failure never blanks out
  // already-rendered data.
  async function pollApplications() {
    const response = await getAllApplications();
    setApplications(response);
  }

  // Polling is only meaningful while some application, anywhere, is actually
  // waiting on an interview -- the same "invited" check ApplicationsPage uses,
  // just across every job instead of one.
  const hasInvitedApplication = applications.some((application) => application.status === "invited");

  usePolling(pollApplications, { intervalMs: POLL_INTERVAL_MS, enabled: hasInvitedApplication });

  return (
    <div className={styles.page}>
      <PageHeader
        title="VoiceHR Interviews"
        subtitle="Every application's interview progress, across every job."
        variant="glass"
      />

      {loading && <p className={styles.status}>Loading interviews…</p>}

      {!loading && error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}

      {!loading && !error && applications.length === 0 && (
        <EmptyState
          variant="glass"
          title="No applications yet"
          description="Select a candidate from a job's ranked list to get started."
        />
      )}

      {!loading && !error && applications.length > 0 && (
        <div className={styles.tableWrapper}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Candidate</th>
                <th>Job</th>
                <th>Status</th>
                <th>Interview</th>
                <th>Score</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {applications.map((application) => (
                <InterviewRow key={application.application_id} application={application} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function InterviewRow({ application }) {
  const identity = useCandidateIdentity(application.candidate_id);
  const [expanded, setExpanded] = useState(false);

  const displayName =
    identity.status === "ready"
      ? identity.name
      : identity.status === "loading"
        ? "Loading…"
        : `Candidate ${application.candidate_id.slice(0, 8)}…`;

  const hasAssessment = application.interview_assessment != null;
  // Most-recent relevant timestamp for this row -- interview completion if it
  // happened, otherwise when the candidate was invited, otherwise when they were
  // selected. Always something to show; never a fabricated value.
  const updatedAt = application.interview_completed_at ?? application.invited_at ?? application.selected_at;

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
          <Link
            to={`/jobs/${application.job_id}`}
            className={styles.inlineLink}
            onClick={(event) => event.stopPropagation()}
          >
            {application.job_title}
          </Link>
        </td>
        <td>
          <StatusBadge variant="glass" tone={STATUS_TONE[application.status] ?? "neutral"}>
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
        <td className={styles.scoreCell}>{formatScoreSummary(application.interview_assessment)}</td>
        <td className={styles.dateCell}>
          {updatedAt
            ? new Date(updatedAt).toLocaleDateString(undefined, {
                year: "numeric",
                month: "short",
                day: "numeric",
              })
            : "—"}
        </td>
      </tr>

      {expanded && hasAssessment && (
        <tr>
          <td colSpan={6}>
            <AssessmentPanel assessment={application.interview_assessment} />
          </td>
        </tr>
      )}
    </>
  );
}

// A compact "CEFR · overall/100" summary for the table row -- the full breakdown
// (fluency/vocabulary/grammar/pronunciation/comprehension) is only in the expanded
// AssessmentPanel, reached by clicking the row, same as ApplicationsPage.
function formatScoreSummary(assessment) {
  if (!assessment) return "—";
  if (assessment.evaluation_status === "failed") return "Failed";
  const parts = [];
  if (assessment.cefr_level) parts.push(assessment.cefr_level);
  if (assessment.overall_score != null) parts.push(`${assessment.overall_score}/100`);
  return parts.length > 0 ? parts.join(" · ") : "—";
}
