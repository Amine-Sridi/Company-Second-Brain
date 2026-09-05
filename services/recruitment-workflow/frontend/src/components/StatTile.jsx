import styles from "./StatTile.module.css";

/**
 * A single labeled number in a summary grid (currently only DashboardPage's
 * overview row). Purely presentational -- callers decide what to pass, including
 * a literal "—" placeholder before a page has real data to show (see
 * DashboardPage, which does not fetch anything yet).
 */
export default function StatTile({ label, value, hint }) {
  return (
    <div className={styles.tile}>
      <span className={styles.label}>{label}</span>
      <span className={styles.value}>{value}</span>
      {hint && <span className={styles.hint}>{hint}</span>}
    </div>
  );
}
