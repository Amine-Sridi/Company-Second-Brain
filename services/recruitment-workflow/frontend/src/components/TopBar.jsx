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
        Log out
      </button>
    </header>
  );
}
