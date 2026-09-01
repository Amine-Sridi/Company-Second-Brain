import { NavLink } from "react-router-dom";
import styles from "./Sidebar.module.css";

export default function Sidebar() {
  const linkClass = ({ isActive }) => (isActive ? styles.linkActive : styles.link);

  return (
    <nav className={styles.sidebar}>
      <div className={styles.brand}>Company Second Brain</div>
      <NavLink to="/jobs" className={linkClass}>
        Jobs
      </NavLink>
      <NavLink to="/upload" className={linkClass}>
        Upload Resume
      </NavLink>
    </nav>
  );
}
