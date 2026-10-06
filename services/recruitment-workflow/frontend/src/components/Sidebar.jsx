import { NavLink, useNavigate } from "react-router-dom";
import { motion, useReducedMotion } from "motion/react";
import { useAuth } from "../context/AuthContext.jsx";
import styles from "./Sidebar.module.css";

// Only routes that actually exist get a nav entry (see FRONTEND_HANDOFF.md).
const NAV_ITEMS = [
  { to: "/", label: "Dashboard", icon: IconDashboard, end: true },
  { to: "/jobs", label: "Jobs", icon: IconJobs },
  { to: "/candidates", label: "Candidates", icon: IconCandidates },
  { to: "/upload", label: "Upload resumes", icon: IconUpload },
  { to: "/interviews", label: "VoiceHR interviews", icon: IconVoiceHR },
  { to: "/reports", label: "Reports", icon: IconReports },
  { to: "/graph", label: "Knowledge graph", icon: IconGraph },
];

/**
 * The persistent shell navigation: the one dark surface in the light theme. It also
 * carries the signed-in account and the log-out action, which used to live in a
 * separate top bar that held nothing else.
 *
 * The active item is a single shared `motion.span` (layoutId) that slides between
 * nav entries instead of each link drawing its own static highlight.
 */
export default function Sidebar() {
  const shouldReduceMotion = useReducedMotion();
  const { recruiter, logout } = useAuth();
  const navigate = useNavigate();
  const pillTransition = shouldReduceMotion
    ? { duration: 0 }
    : { type: "spring", stiffness: 420, damping: 34 };

  function handleLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <nav className={styles.sidebar} aria-label="Main">
      <div className={styles.brand}>
        <span className={styles.brandMark} aria-hidden="true">
          <IconGraph className={styles.brandMarkIcon} />
        </span>
        <span className={styles.brandText}>
          Company
          <br />
          Second Brain
        </span>
      </div>

      <div className={styles.nav}>
        {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) => (isActive ? `${styles.link} ${styles.linkActive}` : styles.link)}
          >
            {({ isActive }) => (
              <>
                {isActive && (
                  <motion.span
                    layoutId="sidebarActivePill"
                    className={styles.activePill}
                    transition={pillTransition}
                  />
                )}
                <Icon className={isActive ? styles.iconActive : styles.icon} />
                <span className={styles.linkLabel}>{label}</span>
              </>
            )}
          </NavLink>
        ))}
      </div>

      <div className={styles.account}>
        <span className={styles.accountEmail} title={recruiter?.email}>
          {recruiter?.email}
        </span>
        <button type="button" className={styles.logoutButton} onClick={handleLogout}>
          <IconLogout className={styles.icon} />
          Log out
        </button>
      </div>
    </nav>
  );
}

function IconLogout(props) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" {...props}>
      <path d="M8 3.5H5a1.5 1.5 0 0 0-1.5 1.5v10A1.5 1.5 0 0 0 5 16.5h3" />
      <path d="M13 13.5 17 10l-4-3.5" />
      <path d="M17 10H8" />
    </svg>
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

function IconVoiceHR(props) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" {...props}>
      <path d="M4 10a6 6 0 0 1 12 0v3a2 2 0 0 1-2 2h-1v-5h3" />
      <path d="M4 10v3a2 2 0 0 0 2 2h1v-5H4" />
      <path d="M8.5 17.5h3" />
    </svg>
  );
}

function IconReports(props) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" {...props}>
      <path d="M5 3h7l3 3v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" />
      <path d="M7 9h6" />
      <path d="M7 12h6" />
      <path d="M7 15h3" />
    </svg>
  );
}

function IconGraph(props) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" {...props}>
      <circle cx="5" cy="6" r="2" />
      <circle cx="5" cy="14" r="2" />
      <circle cx="14" cy="10" r="2.6" />
      <path d="M6.8 6.9 11.7 9" />
      <path d="M6.8 13.1 11.7 11" />
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
