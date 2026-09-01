import { Outlet } from "react-router-dom";
import Sidebar from "../components/Sidebar.jsx";
import TopBar from "../components/TopBar.jsx";
import styles from "./Layout.module.css";

/**
 * Shell for every authenticated page: sidebar + top bar + routed content. Only ever
 * rendered inside ProtectedRoute (see AppRouter), so nothing here needs its own auth
 * check.
 */
export default function Layout() {
  return (
    <div className={styles.shell}>
      <Sidebar />
      <div className={styles.main}>
        <TopBar />
        <div className={styles.content}>
          <Outlet />
        </div>
      </div>
    </div>
  );
}
