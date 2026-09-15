import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { getAllApplications } from "../api/applications.js";
import { ApiError } from "../api/client.js";
import { useCandidateIdentity } from "../hooks/useCandidateIdentity.js";
import PageHeader from "../components/PageHeader.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import EmptyState from "../components/EmptyState.jsx";
import { EVALUATION_STATUS_LABELS, EVALUATION_STATUS_TONE } from "../constants/language.js";
import styles from "./ReportsPage.module.css";

/**
 * Every application with a completed (or otherwise scored) interview assessment,
 * across every job -- built entirely from the already-fetched GET /v1/applications,
 * per this task's own scope; no new backend call for the list itself. Only
 * ReportDetailPage calls the new VoiceHR-report proxy, for one report at a time.
 * Read-only, same as InterviewsPage -- no invite/accept/reject actions here.
 */
export default function ReportsPage() {
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
      const response = await getAllApplications();
      setApplications(response);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load reports.");
    } finally {
      setLoading(false);
    }
  }

  // Reportable = has a scored interview assessment, regardless of the
  // application's current status -- an already-accepted/rejected application's
  // report is still worth viewing.
  const reportable = applications.filter((application) => application.interview_assessment != null);

  return (
    <div className={styles.page}>
      <PageHeader
        title="Reports"
        subtitle="Every application with a completed interview assessment."
        variant="glass"
      />

      {loading && <p className={styles.status}>Loading reports…</p>}

      {!loading && error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}

      {!loading && !error && reportable.length === 0 && (
        <EmptyState
          variant="glass"
          title="No reports yet"
          description="Reports appear here once a candidate completes an interview."
        />
      )}

      {!loading && !error && reportable.length > 0 && (
        <div className={styles.tableWrapper}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Candidate</th>
                <th>Job</th>
                <th>CEFR</th>
                <th>Overall score</th>
                <th>Status</th>
                <th>Completed</th>
              </tr>
            </thead>
            <tbody>
              {reportable.map((application) => (
                <ReportRow key={application.application_id} application={application} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ReportRow({ application }) {
  const identity = useCandidateIdentity(application.candidate_id);
  const navigate = useNavigate();

  const displayName =
    identity.status === "ready"
      ? identity.name
      : identity.status === "loading"
        ? "Loading…"
        : `Candidate ${application.candidate_id.slice(0, 8)}…`;

  const assessment = application.interview_assessment;

  function goToReport() {
    navigate(`/reports/${application.application_id}`);
  }

  return (
    <tr
      className={styles.row}
      onClick={goToReport}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          if (event.key === " ") event.preventDefault();
          goToReport();
        }
      }}
      role="button"
      tabIndex={0}
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
      <td>{assessment.cefr_level ?? "—"}</td>
      <td>{assessment.overall_score != null ? `${assessment.overall_score}/100` : "—"}</td>
      <td>
        <StatusBadge variant="glass" tone={EVALUATION_STATUS_TONE[assessment.evaluation_status] ?? "neutral"}>
          {EVALUATION_STATUS_LABELS[assessment.evaluation_status] ?? assessment.evaluation_status}
        </StatusBadge>
      </td>
      <td className={styles.dateCell}>
        {application.interview_completed_at
          ? new Date(application.interview_completed_at).toLocaleDateString(undefined, {
              year: "numeric",
              month: "short",
              day: "numeric",
            })
          : "—"}
      </td>
    </tr>
  );
}
