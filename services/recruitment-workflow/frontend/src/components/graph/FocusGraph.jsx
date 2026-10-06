import { useMemo } from "react";
import { RING_LABELS, initialsOf, percent, radialLayout } from "./graphModel.js";
import styles from "./FocusGraph.module.css";

const W = 760;
const H = 640;
const CX = W / 2;
const CY = H / 2;
const RADII = { qualified: 130, gate: 215, below: 295 };

// Labels are drawn for every qualified item, the best items of the middle ring, and
// whatever is selected -- a full label on all 20 retrieved matches is what made the
// old graph unreadable.
const GATE_LABELS = 6;

/**
 * One centre (a job, or a candidate) with its matches on three rings: qualified,
 * missing required skills, below the score threshold. Plain SVG, no physics: the
 * same data always draws the same picture, which is what a recruiter (and a jury)
 * needs to compare two jobs.
 *
 * @param {{
 *   items: Array<{id, label, ring, score}>,
 *   centerTitle: string, centerSubtitle?: string,
 *   selectedId?: string|null, onSelect?: (id: string) => void,
 *   showBelow?: boolean, compact?: boolean,
 * }} props
 */
export default function FocusGraph({ items, centerTitle, centerSubtitle, selectedId, onSelect, showBelow = true, compact = false }) {
  const include = useMemo(
    () => new Set(showBelow ? ["qualified", "gate", "below"] : ["qualified", "gate"]),
    [showBelow],
  );
  const placed = useMemo(() => radialLayout(items, { cx: CX, cy: CY, radii: RADII, include }), [items, include]);
  const counts = useMemo(() => {
    const c = { qualified: 0, gate: 0, below: 0 };
    for (const item of items) c[item.ring] += 1;
    return c;
  }, [items]);

  const rings = ["qualified", "gate", "below"].filter((ring) => include.has(ring));

  return (
    <svg
      className={`${styles.svg} ${compact ? styles.compact : ""}`}
      viewBox={`0 0 ${W} ${H}`}
      role="group"
      aria-label={`${centerTitle}: ${counts.qualified} qualified, ${counts.gate} missing required skills, ${counts.below} below the threshold`}
    >
      {rings.map((ring) => (
        <circle key={ring} className={styles.ring} cx={CX} cy={CY} r={RADII[ring]} />
      ))}

      {placed.map((p) => (
        <line
          key={`edge-${p.id}`}
          className={`${styles.edge} ${styles[`edge_${p.ring}`]} ${p.id === selectedId ? styles.edgeSelected : ""}`}
          x1={CX}
          y1={CY}
          x2={p.x}
          y2={p.y}
        />
      ))}

      <g className={styles.center}>
        <rect x={CX - 100} y={CY - 34} width={200} height={68} rx={14} />
        <text x={CX} y={CY - (centerSubtitle ? 6 : 0)} textAnchor="middle" dominantBaseline="middle" className={styles.centerTitle}>
          {truncate(centerTitle, 26)}
        </text>
        {centerSubtitle && (
          <text x={CX} y={CY + 16} textAnchor="middle" dominantBaseline="middle" className={styles.centerSub}>
            {centerSubtitle}
          </text>
        )}
      </g>

      {placed.map((p) => {
        const selected = p.id === selectedId;
        const r = p.ring === "below" ? 11 : Math.max(13, Math.min(24, 13 + (p.score - 0.5) * 40));
        const showLabel = selected || p.ring === "qualified" || (p.ring === "gate" && p.rank < GATE_LABELS);
        const pct = percent(p.score);
        // Labels point away from the centre: to the side for nodes left or right of
        // it, above or below for nodes near the vertical axis.
        const dx = (p.x - CX) / RADII[p.ring];
        const side = dx > 0.35 ? "right" : dx < -0.35 ? "left" : p.y >= CY ? "below" : "above";
        const labelProps = {
          right: { x: r + 7, y: 4, textAnchor: "start" },
          left: { x: -(r + 7), y: 4, textAnchor: "end" },
          below: { x: 0, y: r + 16, textAnchor: "middle" },
          above: { x: 0, y: -(r + 9), textAnchor: "middle" },
        }[side];
        return (
          <g
            key={`node-${p.id}`}
            className={`${styles.node} ${styles[`node_${p.ring}`]} ${selected ? styles.nodeSelected : ""}`}
            transform={`translate(${p.x}, ${p.y})`}
            role="button"
            tabIndex={0}
            aria-pressed={selected}
            aria-label={`${p.label}, ${pct} percent, ${RING_LABELS[p.ring].toLowerCase()}`}
            onClick={() => onSelect?.(p.id)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onSelect?.(p.id);
              }
            }}
          >
            {selected && <circle className={styles.halo} r={r + 6} />}
            <circle className={styles.dot} r={r} />
            {p.ring !== "below" && r >= 15 && (
              <text className={styles.initials} textAnchor="middle" dominantBaseline="central">
                {initialsOf(p.label)}
              </text>
            )}
            {showLabel && (
              <text className={styles.label} {...labelProps}>
                {truncate(p.label, 20)}
                <tspan className={styles.labelScore}> {pct}%</tspan>
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

function truncate(text, max) {
  const s = String(text ?? "");
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}
