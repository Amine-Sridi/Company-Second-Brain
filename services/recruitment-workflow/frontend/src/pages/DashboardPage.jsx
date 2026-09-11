import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { motion, useReducedMotion } from "motion/react";
import { useAuth } from "../context/AuthContext.jsx";
import { getCandidates } from "../api/candidates.js";
import { getJobs } from "../api/jobs.js";
import { getAllApplications } from "../api/applications.js";
import { ApiError } from "../api/client.js";
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

// Bento-grid accent alternation for the four KPI tiles -- purely decorative,
// ties each tile to one of the two restrained brand colors (see StatTile.jsx).
const STAT_ACCENTS = ["cyan", "violet", "cyan", "violet"];

/**
 * Operational home for the admin shell: real counts from the three read endpoints
 * the browser is allowed to call (all on RWS -- see FRONTEND_HANDOFF.md §22),
 * computed here in render, not duplicated backend logic. No charts, no analytics --
 * every number is a plain count already implied by data RWS already returns.
 *
 * V2 design system, Phase 1 (see FRONTEND_HANDOFF.md): this page is the first,
 * and so far only, full V2 redesign -- a bespoke hero (not the shared PageHeader,
 * which every other still-light page still needs unchanged) and an asymmetric
 * bento layout, all built from data this page already fetched before this pass;
 * nothing here adds a new API call or invents a number that isn't a real count.
 */
export default function DashboardPage() {
  const { recruiter } = useAuth();
  const shouldReduceMotion = useReducedMotion();

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
      // rule every other page in this app follows.
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

  const kpis = [
    { label: "Total Candidates", value: candidates.length },
    { label: "Total Jobs", value: jobs.length },
    { label: "In Pipeline", value: inPipelineCount },
    { label: "Interviews Completed", value: completedInterviewsCount },
  ];

  return (
    <div className={styles.page}>
      <Hero
        recruiter={recruiter}
        candidateCount={candidates.length}
        jobCount={jobs.length}
        loading={loading}
        shouldReduceMotion={shouldReduceMotion}
      />

      {loading && <p className={styles.status}>Loading dashboard…</p>}

      {!loading && error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}

      {!loading && !error && (
        <div className={styles.bento}>
          {kpis.map((kpi, index) => (
            <StatTile
              key={kpi.label}
              label={kpi.label}
              value={kpi.value}
              accent={STAT_ACCENTS[index]}
              delay={index * 0.06}
            />
          ))}

          <FadeInSection
            className={styles.pipelinePanel}
            delay={0.24}
            shouldReduceMotion={shouldReduceMotion}
          >
            <h2 className={styles.panelTitle}>Pipeline by status</h2>
            {applications.length === 0 ? (
              <EmptyState
                variant="glass"
                title="No applications yet"
                description="Select a candidate from a job's ranked list to start the pipeline."
              />
            ) : (
              <>
                <StatusDistributionBar statusCounts={statusCounts} total={applications.length} />
                <div className={styles.statusLegend}>
                  {statusCounts.map(({ status, count }) => (
                    <div key={status} className={styles.statusLegendRow}>
                      <StatusBadge tone={STATUS_TONE[status]} variant="glass">
                        {STATUS_LABELS[status]}
                      </StatusBadge>
                      <span className={styles.statusCount}>{count}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </FadeInSection>

          <FadeInSection
            className={styles.activityPanel}
            delay={0.3}
            shouldReduceMotion={shouldReduceMotion}
          >
            <h2 className={styles.panelTitle}>Recent activity</h2>
            {recentActivity.length === 0 ? (
              <EmptyState
                variant="glass"
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
          </FadeInSection>

          <GraphTeaser delay={0.36} shouldReduceMotion={shouldReduceMotion} />
        </div>
      )}
    </div>
  );
}

function Hero({ recruiter, candidateCount, jobCount, loading, shouldReduceMotion }) {
  // A real, derived one-line summary -- built only from counts this page already
  // fetched, never a placeholder/fabricated figure (§22, and this phase's own "no
  // fake metrics" requirement). Omitted entirely while loading rather than
  // guessing at zero.
  const summary =
    !loading &&
    `${candidateCount} candidate${candidateCount === 1 ? "" : "s"} in the pool · ${jobCount} job${
      jobCount === 1 ? "" : "s"
    } posted`;

  return (
    <motion.div
      className={styles.hero}
      initial={shouldReduceMotion ? false : { opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, ease: "easeOut" }}
    >
      <div className={styles.heroText}>
        <span className={styles.heroEyebrow}>Command Center</span>
        <h1 className={styles.heroTitle}>
          Welcome back{recruiter?.email ? "," : ""}
          {recruiter?.email && <span className={styles.heroName}> {recruiter.email}</span>}
        </h1>
        {summary && <p className={styles.heroSubtitle}>{summary}</p>}
      </div>

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
    </motion.div>
  );
}

/** A horizontal segmented bar, one segment per application status, width
 * proportional to its real share of `total` -- the "pipeline visualization" this
 * phase's brief asks for, built entirely from `statusCounts` (the exact numbers
 * already rendered as text in the legend below it, never a separate/divergent
 * calculation). Segment widths animate in on mount; a zero-count status renders no
 * segment at all rather than a zero-width sliver. */
function StatusDistributionBar({ statusCounts, total }) {
  const shouldReduceMotion = useReducedMotion();

  return (
    <div className={styles.distributionBar} role="img" aria-label="Application status distribution">
      {statusCounts.map(({ status, count }) => {
        if (count === 0) return null;
        const percent = (count / total) * 100;
        return (
          <motion.div
            key={status}
            className={`${styles.distributionSegment} ${styles[`segment-${status}`]}`}
            initial={shouldReduceMotion ? false : { width: 0 }}
            animate={{ width: `${percent}%` }}
            transition={{ duration: 0.6, ease: "easeOut" }}
            title={`${STATUS_LABELS[status]}: ${count}`}
          />
        );
      })}
    </div>
  );
}

/** Plain fade/slide-in wrapper shared by the two bento panels -- a small, local,
 * two-consumer helper (not promoted to components/, per this project's own
 * "duplication over premature abstraction" rule) rather than repeating the same
 * five motion props twice. */
function FadeInSection({ className, delay, shouldReduceMotion, children }) {
  return (
    <motion.section
      className={className}
      initial={shouldReduceMotion ? false : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay, ease: "easeOut" }}
    >
      {children}
    </motion.section>
  );
}

/** Purely decorative + navigational -- "subtle visual connection to the graph
 * feature" per this phase's brief. Deliberately shows no number of its own: this
 * page does not fetch graph data (no new API call, per this phase's own
 * requirement), so there is nothing real to count here without one. The three-dot
 * glyph echoes MatchGraph's own candidate/job node styling (see
 * components/MatchGraph.jsx) at a glance, not a literal graph render. */
function GraphTeaser({ delay, shouldReduceMotion }) {
  return (
    <motion.div
      initial={shouldReduceMotion ? false : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay, ease: "easeOut" }}
      whileHover={shouldReduceMotion ? undefined : { y: -2 }}
      className={styles.graphTeaser}
    >
      <svg className={styles.graphGlyph} viewBox="0 0 64 40" fill="none" aria-hidden="true">
        <circle cx="12" cy="12" r="4" className={styles.glyphViolet} />
        <circle cx="12" cy="28" r="4" className={styles.glyphViolet} />
        <circle cx="46" cy="20" r="5.5" className={styles.glyphCyan} />
        <path d="M16 14 42 19M16 26 42 21" stroke="currentColor" strokeWidth="1.2" className={styles.glyphLine} />
      </svg>
      <div>
        <h2 className={styles.panelTitle}>Knowledge Graph</h2>
        <p className={styles.graphTeaserText}>
          Explore how every candidate connects to every role, scored and qualified.
        </p>
      </div>
      <Link to="/graph" className={styles.graphTeaserLink}>
        Open graph →
      </Link>
    </motion.div>
  );
}
