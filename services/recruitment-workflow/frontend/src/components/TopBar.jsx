import { useNavigate } from "react-router-dom";
import { motion, useReducedMotion } from "motion/react";
import { useAuth } from "../context/AuthContext.jsx";
import styles from "./TopBar.module.css";

/** V2 design system, Phase 1 -- see Sidebar.jsx's own comment for why this
 * component's dark-glass restyle is safe to apply unconditionally (self-contained,
 * no --color-* tokens touched). The logout button gets one small hover/focus
 * micro-interaction, this phase's one requested example of that motion category
 * outside of Dashboard's cards. */
export default function TopBar() {
  const { recruiter, logout } = useAuth();
  const navigate = useNavigate();
  const shouldReduceMotion = useReducedMotion();

  function handleLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <header className={styles.topbar}>
      <span className={styles.email}>{recruiter?.email}</span>
      <motion.button
        type="button"
        className={styles.logoutButton}
        onClick={handleLogout}
        whileHover={shouldReduceMotion ? undefined : { scale: 1.03 }}
        whileTap={shouldReduceMotion ? undefined : { scale: 0.97 }}
        transition={{ duration: 0.12 }}
      >
        <IconLogout className={styles.logoutIcon} />
        Log out
      </motion.button>
    </header>
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
