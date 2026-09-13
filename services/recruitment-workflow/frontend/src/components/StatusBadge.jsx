import styles from "./StatusBadge.module.css";

const TONES = new Set(["neutral", "primary", "accent", "success", "warning", "danger"]);

/**
 * Small pill used for every status/qualification/evaluation label in the app --
 * job-match qualification, application status, interview-evaluation status.
 * Replaces each page's own badge classes with one shared visual vocabulary so
 * "green"/"red"/etc. mean the same thing everywhere (matches FRONTEND_HANDOFF.md
 * §15's existing rule; this only consolidates the CSS that already followed it).
 * An unrecognized tone falls back to "neutral" rather than rendering unstyled.
 *
 * `variant="glass"` (V2 design system, Phase 1 -- see FRONTEND_HANDOFF.md) is a
 * dark-surface-tuned restyle used only by DashboardPage today. Every other
 * consumer of this component (ApplicationsPage, InterviewsPage, JobDetailPage,
 * CandidateDetailPage, MatchGraph, AssessmentPanel) calls it with no `variant`,
 * which renders exactly as before -- this prop is additive, not a replacement,
 * specifically so this one shared component doesn't have to wait for every page
 * that uses it to be migrated before Dashboard can use its own look.
 */
export default function StatusBadge({ tone = "neutral", variant, title, children }) {
  const resolvedTone = TONES.has(tone) ? tone : "neutral";
  const variantClass = variant === "glass" ? styles.glass : "";
  return (
    <span className={`${styles.badge} ${styles[resolvedTone]} ${variantClass}`} title={title}>
      {children}
    </span>
  );
}
