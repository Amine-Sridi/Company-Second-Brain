import styles from "./StatusBadge.module.css";

const TONES = new Set(["neutral", "primary", "accent", "success", "warning", "danger"]);

/**
 * Small pill used for every status/qualification/evaluation label in the app --
 * job-match qualification, application status, interview-evaluation status.
 * Replaces each page's own badge classes with one shared visual vocabulary so
 * "green"/"red"/etc. mean the same thing everywhere (matches FRONTEND_HANDOFF.md
 * §15's existing rule; this only consolidates the CSS that already followed it).
 * An unrecognized tone falls back to "neutral" rather than rendering unstyled.
 */
export default function StatusBadge({ tone = "neutral", children }) {
  const resolvedTone = TONES.has(tone) ? tone : "neutral";
  return <span className={`${styles.badge} ${styles[resolvedTone]}`}>{children}</span>;
}
