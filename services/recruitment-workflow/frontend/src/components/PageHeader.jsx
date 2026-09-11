import styles from "./PageHeader.module.css";

/**
 * Shared page-header layout: title + optional subtitle on the left, optional
 * actions (a button, a link, a group of both) on the right. Purely presentational
 * -- every page still owns its own title/subtitle text and action elements; this
 * only replaces the header markup/CSS that JobsListPage, JobDetailPage,
 * ApplicationsPage, and ResumeUploadPage each used to duplicate individually.
 *
 * `variant="glass"` (V2 design system, Phase 2 -- see FRONTEND_HANDOFF.md) is an
 * additive, opt-in dark-surface restyle -- every page migrated so far
 * (Applications, Interviews, Reports, Report Detail) passes it; every page not
 * yet migrated passes no `variant` and renders exactly as before. `.title` itself
 * needs no override either way: it has never set its own color, only ever
 * inherited it from whatever page container it sits in -- true on the original
 * light pages and still true once a page's own `.page` class sets
 * `color: var(--v2-text)` (see e.g. DashboardPage.module.css). Only `.subtitle`
 * (which does hard-code a color) needs the variant's own rule.
 */
export default function PageHeader({ title, subtitle, actions, variant }) {
  const variantClass = variant === "glass" ? styles.glass : "";
  return (
    <div className={`${styles.header} ${variantClass}`}>
      <div>
        <h1 className={styles.title}>{title}</h1>
        {subtitle && <p className={styles.subtitle}>{subtitle}</p>}
      </div>
      {actions && <div className={styles.actions}>{actions}</div>}
    </div>
  );
}
