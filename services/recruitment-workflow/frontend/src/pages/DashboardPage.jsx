import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getCandidates } from "../api/candidates.js";
import { getJobs } from "../api/jobs.js";
import { getAllApplications } from "../api/applications.js";
import { ApiError } from "../api/client.js";
import StatTile from "../components/StatTile.jsx";
import EmptyState from "../components/EmptyState.jsx";
import { STATUS_LABELS, STATUS_ORDER } from "../constants/applicationStatus.js";
import styles from "./DashboardPage.module.css";

// Applications not yet in a terminal state -- a display count, not a workflow rule.
const TERMINAL_STATUSES = new Set(["accepted", "rejected"]);
const RECENT_ACTIVITY_LIMIT = 6;
// VoiceHR outcomes where no language level was issued and a human has to look.
const NO_LEVEL_STATUSES = new Set(["insufficient_evidence", "inconclusive", "failed"]);

/**
 * Operational home: real counts from the three reads the browser is allowed to make
 * (all on RWS -- see FRONTEND_HANDOFF.md §22), computed here in render. Every number
 * on this page is a plain count of data RWS already returned; nothing is invented,
 * and the "needs your attention" panel only lists items whose count is above zero.
 */
export default function DashboardPage() {
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
  const maxStatusCount = Math.max(1, ...statusCounts.map((s) => s.count));
  const awaitingDecision = statusCounts.find((s) => s.status === "interviewed")?.count ?? 0;

  const flaggedJobs = jobs.filter((job) => job.extraction_status === "flagged").length;
  const noLevel = applications.filter((a) => NO_LEVEL_STATUSES.has(a.interview_assessment?.evaluation_status)).length;
  const attention = [
    flaggedJobs > 0 && {
      key: "flagged",
      tone: "warn",
      to: "/jobs",
      title: `${flaggedJobs} job${flaggedJobs === 1 ? "" : "s"} flagged for a second look`,
      text: "The extraction or the match outcome looked suspicious. Matching still ran.",
    },
    awaitingDecision > 0 && {
      key: "decide",
      tone: "info",
      to: "/interviews",
      title: `${awaitingDecision} interview${awaitingDecision === 1 ? "" : "s"} waiting for a decision`,
      text: "The language level and the evidence are ready to review.",
    },
    noLevel > 0 && {
      key: "nolevel",
      tone: "neutral",
      to: "/reports",
      title: `${noLevel} interview${noLevel === 1 ? "" : "s"} without a language level`,
      text: "Not enough evidence for a level. A human review is needed.",
    },
  ].filter(Boolean);

  const titles = new Map(jobs.map((job) => [job.job_id, job.title]));
  const recentActivity = [
    ...candidates.map((c) => ({
      id: `candidate-${c.candidate_id}`,
      kind: "Candidate added",
      label: c.name,
      to: `/candidates/${c.candidate_id}`,
      at: c.created_at,
    })),
    ...jobs.map((j) => ({ id: `job-${j.job_id}`, kind: "Job posted", label: j.title, to: `/jobs/${j.job_id}`, at: j.created_at })),
    ...applications
      .filter((a) => a.decided_at && TERMINAL_STATUSES.has(a.status))
      .map((a) => ({
        id: `decision-${a.application_id}`,
        kind: a.status === "accepted" ? "Candidate accepted" : "Candidate rejected",
        label: titles.get(a.job_id) ?? "a job",
        to: `/jobs/${a.job_id}/applications`,
        at: a.decided_at,
      })),
  ]
    .sort((a, b) => new Date(b.at) - new Date(a.at))
    .slice(0, RECENT_ACTIVITY_LIMIT);

  const kpis = [
    { label: "Candidates in the pool", value: candidates.length },
    { label: "Jobs posted", value: jobs.length },
    { label: "Applications in progress", value: inPipelineCount },
    { label: "VoiceHR interviews completed", value: completedInterviewsCount },
  ];

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div className={styles.headerText}>
          <span className={styles.eyebrow}>Dashboard</span>
          <h1 className={styles.title}>Recruitment at a glance</h1>
        </div>
        <div className={styles.actions}>
          <Link to="/jobs" className={styles.actionPrimary}>
            Submit a job
          </Link>
          <Link to="/upload" className={styles.actionSecondary}>
            Upload resumes
          </Link>
        </div>
      </header>

      {loading && <p className={styles.status}>Loading dashboard…</p>}

      {!loading && error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}

      {!loading && !error && (
        <>
          <section className={styles.kpis} aria-label="Key figures">
            {kpis.map((kpi, index) => (
              <StatTile key={kpi.label} label={kpi.label} value={kpi.value} delay={index * 0.05} />
            ))}
          </section>

          <div className={styles.row}>
            <section className={`${styles.panel} ${styles.wide}`} aria-label="Application pipeline">
              <div className={styles.panelHead}>
                <h2 className={styles.panelTitle}>Application pipeline</h2>
              </div>
              {applications.length === 0 ? (
                <EmptyState title="No applications yet" description="Select a qualified candidate on a job to start the pipeline." />
              ) : (
                <>
                  <div className={styles.bars}>
                    {statusCounts.map(({ status, count }) => (
                      <div key={status} className={styles.barRow}>
                        <span>{STATUS_LABELS[status]}</span>
                        <span className={styles.barTrack}>
                          <span
                            className={`${styles.barFill} ${styles[`fill_${status}`]}`}
                            style={{ width: `${(count / maxStatusCount) * 100}%` }}
                          />
                        </span>
                        <span className={styles.barCount}>{count}</span>
                      </div>
                    ))}
                  </div>
                  {awaitingDecision > 0 && (
                    <p className={styles.note}>
                      {awaitingDecision} candidate{awaitingDecision === 1 ? " is" : "s are"} waiting for a recruiter decision after
                      their VoiceHR interview.
                    </p>
                  )}
                </>
              )}
            </section>

            <section className={styles.panel} aria-label="Needs your attention">
              <h2 className={styles.panelTitle}>Needs your attention</h2>
              {attention.length === 0 ? (
                <p className={styles.note}>Nothing needs your attention right now.</p>
              ) : (
                <div className={styles.attention}>
                  {attention.map((item) => (
                    <Link key={item.key} to={item.to} className={`${styles.attentionItem} ${styles[`attention_${item.tone}`]}`}>
                      <span className={styles.attentionDot} aria-hidden="true" />
                      <span className={styles.attentionText}>
                        <span className={styles.attentionTitle}>{item.title}</span>
                        <span>{item.text}</span>
                      </span>
                    </Link>
                  ))}
                </div>
              )}
            </section>
          </div>

          <div className={styles.row}>
            <section className={`${styles.panel} ${styles.wide}`} aria-label="Recent activity">
              <h2 className={styles.panelTitle}>Recent activity</h2>
              {recentActivity.length === 0 ? (
                <p className={styles.note}>Upload a resume or post a job to get started.</p>
              ) : (
                <ul className={styles.activity}>
                  {recentActivity.map((event) => (
                    <li key={event.id}>
                      <span className={styles.activityLabel}>
                        <span className={styles.activityKind}>{event.kind} · </span>
                        <Link to={event.to}>{event.label}</Link>
                      </span>
                      <span className={styles.activityDate}>
                        {new Date(event.at).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <Link to="/graph" className={styles.graphCard}>
              <span className={styles.graphTitle}>Knowledge graph</span>
              <span className={styles.graphText}>See who fits each job, and which required skills are missing.</span>
              <span className={styles.graphLink}>Open the graph</span>
              <svg className={styles.graphGlyph} viewBox="0 0 120 120" aria-hidden="true">
                <circle cx="60" cy="60" r="56" fill="none" stroke="#3b4150" strokeDasharray="3 4" />
                <circle cx="60" cy="60" r="30" fill="none" stroke="#3b4150" strokeDasharray="3 4" />
                <rect x="47" y="52" width="26" height="16" rx="4" fill="#ffffff" />
                <circle cx="60" cy="27" r="7" fill="#5b74cf" />
                <circle cx="88" cy="74" r="6" fill="#5b74cf" />
                <circle cx="12" cy="66" r="6" fill="none" stroke="#e08a3c" strokeWidth="3" />
                <circle cx="100" cy="24" r="4" fill="#3b4150" />
              </svg>
            </Link>
          </div>
        </>
      )}
    </div>
  );
}
