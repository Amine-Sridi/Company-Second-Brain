import { Outlet, useLocation } from "react-router-dom";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import Sidebar from "../components/Sidebar.jsx";
import TopBar from "../components/TopBar.jsx";
import styles from "./Layout.module.css";

/**
 * Shell for every authenticated page: sidebar + top bar + routed content. Only ever
 * rendered inside ProtectedRoute (see AppRouter), so nothing here needs its own auth
 * check.
 *
 * V2 design system, Phase 1 (see FRONTEND_HANDOFF.md): the one "page entrance"
 * motion moment applies here, at the shell level, so every route gets it for free
 * -- including the ten pages this phase does not otherwise touch. It is a plain
 * opacity/8px-offset fade keyed on the pathname, nothing that depends on a page's
 * own light-or-dark styling. `mode="wait"` (old page fully exits before the new
 * one enters) is deliberate while pages still have wildly different layouts/
 * widths (a light 760px page next to Dashboard's dark full-bleed hero) --
 * overlapping enter/exit would visibly jump; revisit once more pages share V2's
 * layout language. `.content` itself is intentionally untouched by this phase --
 * see Layout.module.css.
 */
export default function Layout() {
  const location = useLocation();
  const shouldReduceMotion = useReducedMotion();

  return (
    <div className={styles.shell}>
      <Sidebar />
      <div className={styles.main}>
        <TopBar />
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
