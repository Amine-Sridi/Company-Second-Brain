import { NavLink } from "react-router-dom";
import styles from "./Sidebar.module.css";

// Only routes that actually exist get a nav entry -- VoiceHR/Reports are designed
// (see the admin-dashboard redesign proposal) but not built yet, so they are
// deliberately absent here rather than linking to a page that doesn't exist.
// Candidates was added once its page actually worked (see FRONTEND_HANDOFF.md).
const NAV_ITEMS = [
  { to: "/", label: "Dashboard", icon: IconDashboard, end: true },
  { to: "/candidates", label: "Candidates", icon: IconCandidates },
  { to: "/upload", label: "Upload Resume", icon: IconUpload },
  { to: "/jobs", label: "Jobs", icon: IconJobs },
];

export default function Sidebar() {
  const linkClass = ({ isActive }) => (isActive ? styles.linkActive : styles.link);

  return (
    <nav className={styles.sidebar}>
      <div className={styles.brand}>Company Second Brain</div>
      <div className={styles.nav}>
        {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end} className={linkClass}>
            <Icon className={styles.icon} />
            <span>{label}</span>
          </NavLink>
        ))}
      </div>
    </nav>
  );
}

function IconDashboard(props) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" {...props}>
      <rect x="3" y="3" width="6" height="6" rx="1.2" />
      <rect x="11" y="3" width="6" height="6" rx="1.2" />
      <rect x="3" y="11" width="6" height="6" rx="1.2" />
      <rect x="11" y="11" width="6" height="6" rx="1.2" />
    </svg>
  );
}

function IconCandidates(props) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" {...props}>
      <circle cx="10" cy="6.5" r="3" />
      <path d="M3.5 17c0-3.31 2.91-6 6.5-6s6.5 2.69 6.5 6" />
    </svg>
  );
}

function IconJobs(props) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" {...props}>
      <rect x="2.5" y="6" width="15" height="10" rx="1.5" />
      <path d="M7 6V4.5A1.5 1.5 0 0 1 8.5 3h3A1.5 1.5 0 0 1 13 4.5V6" />
      <path d="M2.5 10.5h15" />
    </svg>
  );
}

function IconUpload(props) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" {...props}>
      <path d="M10 13V3" />
      <path d="M6 6.5 10 2.5l4 4" />
      <path d="M3.5 13v2.5A1.5 1.5 0 0 0 5 17h10a1.5 1.5 0 0 0 1.5-1.5V13" />
    </svg>
  );
}
