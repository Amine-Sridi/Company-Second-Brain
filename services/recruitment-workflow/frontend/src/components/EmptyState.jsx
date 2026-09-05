import styles from "./EmptyState.module.css";

/**
 * Shared "nothing here yet" card -- replaces each page's own bare
 * <p className={styles.status}>No X yet...</p> empty-list message with one
 * consistent look. Only for genuinely-empty results; loading/error text stays as
 * each page's own plain status/error line (a transient state, not an empty one).
 */
export default function EmptyState({ title, description }) {
  return (
    <div className={styles.emptyState}>
      <p className={styles.title}>{title}</p>
      {description && <p className={styles.description}>{description}</p>}
    </div>
  );
}
