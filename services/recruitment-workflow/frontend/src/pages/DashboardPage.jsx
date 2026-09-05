import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";
import PageHeader from "../components/PageHeader.jsx";
import StatTile from "../components/StatTile.jsx";
import EmptyState from "../components/EmptyState.jsx";
import styles from "./DashboardPage.module.css";

/**
 * Visual home for the admin shell (see the admin-dashboard redesign proposal,
 * Phase 1). Deliberately not data-backed yet -- the stat tiles below are static
 * placeholders, not a fetch that was left unwired. Real counts land once the
 * backend prerequisites (a candidate list + a global applications endpoint) exist
 * -- see FRONTEND_HANDOFF.md §24.
 */
export default function DashboardPage() {
  const { recruiter } = useAuth();

  return (
    <div className={styles.page}>
      <PageHeader
        title="Dashboard"
        subtitle={recruiter?.email ? `Welcome back, ${recruiter.email}` : "Welcome back"}
      />

      <div className={styles.statGrid}>
        <StatTile label="Candidates" value="—" />
        <StatTile label="Open Jobs" value="—" />
        <StatTile label="In Pipeline" value="—" />
        <StatTile label="Interviews Completed" value="—" />
      </div>
      <p className={styles.metricsNote}>
        Live metrics land in a later phase — these tiles show the layout only.
      </p>

      <div className={styles.quickActions}>
        <Link to="/upload" className={styles.actionPrimary}>
          Upload Resume
        </Link>
        <Link to="/jobs" className={styles.actionSecondary}>
          View Jobs
        </Link>
      </div>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Recent activity</h2>
        <EmptyState
          title="No activity feed yet"
          description="This section will surface real recruiter activity once the backend supports it."
        />
      </section>
    </div>
  );
}
