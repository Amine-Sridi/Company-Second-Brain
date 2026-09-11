import { useEffect, useRef, useState } from "react";
import { animate, motion, useReducedMotion } from "motion/react";
import styles from "./StatTile.module.css";

/**
 * A single labeled number in Dashboard's KPI row -- the only current consumer, so
 * (unlike StatusBadge/EmptyState) this is a direct V2 redesign in place, not an
 * opt-in variant (see FRONTEND_HANDOFF.md, Phase 1). Purely presentational --
 * callers decide what to pass, including a non-numeric placeholder if a future
 * page ever needs one (the count-up animation below only applies to real numbers).
 *
 * `delay` staggers this tile's entrance against its siblings (Dashboard passes an
 * increasing value per tile in the grid) -- one `motion` moment; the number
 * counting up from its previous value is a second, separate one ("KPI/stat
 * transitions" in this phase's brief).
 */
export default function StatTile({ label, value, hint, accent = "cyan", delay = 0 }) {
  const shouldReduceMotion = useReducedMotion();

  return (
    <motion.div
      className={`${styles.tile} ${accent === "violet" ? styles.accentViolet : styles.accentCyan}`}
      initial={shouldReduceMotion ? false : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay, ease: "easeOut" }}
      whileHover={shouldReduceMotion ? undefined : { y: -2 }}
    >
      <span className={styles.label}>{label}</span>
      <span className={styles.value}>
        <AnimatedValue value={value} />
      </span>
      {hint && <span className={styles.hint}>{hint}</span>}
    </motion.div>
  );
}

function AnimatedValue({ value }) {
  const isNumeric = typeof value === "number" && Number.isFinite(value);
  const [display, setDisplay] = useState(isNumeric ? 0 : value);
  const previousValue = useRef(0);
  const shouldReduceMotion = useReducedMotion();

  useEffect(() => {
    if (!isNumeric || shouldReduceMotion) {
      setDisplay(value);
      previousValue.current = isNumeric ? value : 0;
      return undefined;
    }

    const controls = animate(previousValue.current, value, {
      duration: 0.7,
      ease: "easeOut",
      onUpdate: (latest) => setDisplay(Math.round(latest)),
    });
    previousValue.current = value;

    return () => controls.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, isNumeric, shouldReduceMotion]);

  return display;
}
