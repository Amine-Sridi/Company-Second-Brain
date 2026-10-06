import { Outlet, useLocation } from "react-router-dom";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import Sidebar from "../components/Sidebar.jsx";
import styles from "./Layout.module.css";

/**
 * Shell for every authenticated page: sidebar + routed content. Only ever rendered
 * inside ProtectedRoute (see AppRouter), so nothing here needs its own auth check.
 * The account and log-out action live in the sidebar footer.
 *
 * The one "page entrance" motion moment applies here, at the shell level, so every
 * route gets it: a plain opacity/8px-offset fade keyed on the pathname.
 */
export default function Layout() {
  const location = useLocation();
  const shouldReduceMotion = useReducedMotion();

  return (
    <div className={styles.shell}>
      <Sidebar />
      <div className={styles.main}>
        <div className={styles.content}>
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={location.pathname}
              initial={shouldReduceMotion ? false : { opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={shouldReduceMotion ? undefined : { opacity: 0, y: -8 }}
              transition={{ duration: 0.22, ease: "easeOut" }}
            >
              <Outlet />
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}
