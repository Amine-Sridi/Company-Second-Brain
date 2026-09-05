import styles from "./PageHeader.module.css";

/**
 * Shared page-header layout: title + optional subtitle on the left, optional
 * actions (a button, a link, a group of both) on the right. Purely presentational
 * -- every page still owns its own title/subtitle text and action elements; this
 * only replaces the header markup/CSS that JobsListPage, JobDetailPage,
 * ApplicationsPage, and ResumeUploadPage each used to duplicate individually.
 */
export default function PageHeader({ title, subtitle, actions }) {
  return (
    <div className={styles.header}>
      <div>
        <h1 className={styles.title}>{title}</h1>
        {subtitle && <p className={styles.subtitle}>{subtitle}</p>}
      </div>
      {actions && <div className={styles.actions}>{actions}</div>}
    </div>
  );
}
