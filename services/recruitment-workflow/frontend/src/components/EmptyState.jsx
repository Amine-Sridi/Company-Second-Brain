import styles from "./EmptyState.module.css";

/**
 * Shared "nothing here yet" card -- replaces each page's own bare
 * <p className={styles.status}>No X yet...</p> empty-list message with one
 * consistent look. Only for genuinely-empty results; loading/error text stays as
 * each page's own plain status/error line (a transient state, not an empty one).
 *
 * `variant="glass"` (V2 design system, Phase 1 -- see FRONTEND_HANDOFF.md) is an
 * opt-in dark-surface restyle, used only by DashboardPage today; every other
 * consumer (CandidatesPage, JobsListPage, ApplicationsPage, InterviewsPage,
 * ReportsPage, GraphPage, CandidateDetailPage) passes no `variant` and renders
 * exactly as before -- additive, not a replacement.
 */
export default function EmptyState({ title, description, variant }) {
  const variantClass = variant === "glass" ? styles.glass : "";
  return (
    <div className={`${styles.emptyState} ${variantClass}`}>
      <p className={styles.title}>{title}</p>
      {description && <p className={styles.description}>{description}</p>}
    </div>
  );
}
