import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";
import styles from "./TopBar.module.css";

export default function TopBar() {
  const { recruiter, logout } = useAuth();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <header className={styles.topbar}>
      <span className={styles.email}>{recruiter?.email}</span>
      <button type="button" className={styles.logoutButton} onClick={handleLogout}>
        <IconLogout className={styles.logoutIcon} />
        Log out
      </button>
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
