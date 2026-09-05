import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";
import { getCandidates } from "../api/candidates.js";
import { getJobs } from "../api/jobs.js";
import { getAllApplications } from "../api/applications.js";
import { ApiError } from "../api/client.js";
import PageHeader from "../components/PageHeader.jsx";
import StatTile from "../components/StatTile.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import EmptyState from "../components/EmptyState.jsx";
import { STATUS_LABELS, STATUS_TONE, STATUS_ORDER } from "../constants/applicationStatus.js";
import styles from "./DashboardPage.module.css";

// Applications not yet in a terminal state -- the same "not decided" concept
// ApplicationsPage's DECIDABLE_STATUSES gates buttons on, read here purely as a
// display count, not a workflow rule (§22: no frontend business logic).
const TERMINAL_STATUSES = new Set(["accepted", "rejected"]);

// How many recent-activity rows to show -- an operational glance, not a full log.
const RECENT_ACTIVITY_LIMIT = 5;

/**
 * Operational home for the admin shell: real counts from the three read endpoints
 * the browser is allowed to call (all on RWS -- see FRONTEND_HANDOFF.md §22),
 * computed here in render, not duplicated backend logic. No charts, no analytics --
 * every number is a plain count already implied by data RWS already returns.
 */
export default function DashboardPage() {
  const { recruiter } = useAuth();

  const [candidates, setCandidates] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [applications, setApplications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    loadDashboard();
  }, []);

  async function loadDashboard() {
    setLoading(true);
    setError(null);
    try {
      // Three independent RWS reads, fetched fresh on every mount (never cached
      // across visits) so a hard refresh always reflects real backend state, same
      // rule every other page in this app already follows.
      const [candidatesResponse, jobsResponse, applicationsResponse] = await Promise.all([
        getCandidates(),
        getJobs(),
        getAllApplications(),
      ]);
      setCandidates(candidatesResponse);
      setJobs(jobsResponse);
      setApplications(applicationsResponse);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load dashboard data.");
    } finally {
      setLoading(false);
    }
  }

  const inPipelineCount = applications.filter((a) => !TERMINAL_STATUSES.has(a.status)).length;
  const completedInterviewsCount = applications.filter((a) => a.interview_status === "completed").length;
  const statusCounts = STATUS_ORDER.map((status) => ({
    status,
    count: applications.filter((a) => a.status === status).length,
  }));

  // Recent activity is deliberately built only from events that already carry a
  // human-readable label (a candidate's name, a job's title) -- an application-
  // derived event (selected/invited/decided) only has an opaque candidate_id
  // attached, which would need an extra per-row identity lookup to be readable and
  // fails the "cleanly derived from existing timestamps" bar this section is
  // scoped to (see FRONTEND_HANDOFF.md).
  const recentActivity = [
    ...candidates.map((c) => ({
      id: `candidate-${c.candidate_id}`,
      label: `${c.name} added to the candidate pool`,
      at: c.created_at,
    })),
    ...jobs.map((j) => ({
      id: `job-${j.job_id}`,
      label: `Job posted: ${j.title}`,
      at: j.created_at,
    })),
  ]
    .sort((a, b) => new Date(b.at) - new Date(a.at))
    .slice(0, RECENT_ACTIVITY_LIMIT);

  return (
    <div className={styles.page}>
      <PageHeader
        title="Dashboard"
        subtitle={recruiter?.email ? `Welcome back, ${recruiter.email}` : "Welcome back"}
      />

      <div className={styles.quickActions}>
        <Link to="/upload" className={styles.actionPrimary}>
          Upload Resume
        </Link>
        <Link to="/jobs" className={styles.actionSecondary}>
          New Job
        </Link>
        <Link to="/candidates" className={styles.actionSecondary}>
          View Candidates
        </Link>
      </div>

      {loading && <p className={styles.status}>Loading dashboard…</p>}

      {!loading && error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}

      {!loading && !error && (
        <>
          <div className={styles.statGrid}>
            <StatTile label="Total Candidates" value={candidates.length} />
            <StatTile label="Total Jobs" value={jobs.length} />
            <StatTile label="In Pipeline" value={inPipelineCount} />
            <StatTile label="Interviews Completed" value={completedInterviewsCount} />
          </div>

          <div className={styles.columns}>
            <section className={styles.section}>
              <h2 className={styles.sectionTitle}>Pipeline by status</h2>
              <div className={styles.statusBreakdown}>
                {statusCounts.map(({ status, count }) => (
                  <div key={status} className={styles.statusRow}>
                    <StatusBadge tone={STATUS_TONE[status]}>{STATUS_LABELS[status]}</StatusBadge>
                    <span className={styles.statusCount}>{count}</span>
                  </div>
                ))}
              </div>
            </section>

            <section className={styles.section}>
              <h2 className={styles.sectionTitle}>Recent activity</h2>
              {recentActivity.length === 0 ? (
                <EmptyState
                  title="No activity yet"
                  description="Upload a resume or post a job to get started."
                />
              ) : (
                <ul className={styles.activityList}>
                  {recentActivity.map((event) => (
                    <li key={event.id} className={styles.activityRow}>
                      <span className={styles.activityLabel}>{event.label}</span>
                      <span className={styles.activityDate}>
                        {new Date(event.at).toLocaleDateString(undefined, {
                          year: "numeric",
                          month: "short",
                          day: "numeric",
                        })}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}
