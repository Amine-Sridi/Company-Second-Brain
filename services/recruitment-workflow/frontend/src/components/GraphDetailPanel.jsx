import { Link } from "react-router-dom";
import StatusBadge from "./StatusBadge.jsx";
import styles from "./MatchGraph.module.css";

/**
 * Renders a node/edge selection produced by either graph renderer -- 2D
 * (MatchGraph.jsx, from a real Cytoscape element via buildNodeSelection/
 * buildEdgeSelection) or 3D (MatchGraph3D.jsx, from a plain force-graph node/link
 * object via its own equivalent builders). Both builders produce the exact same
 * plain-object shape (`{type: "node", kind, refId, label, connectedEdges}` /
 * `{type: "edge", ...}`), so this component itself never touches Cytoscape or
 * Three.js -- extracted here (previously inline in MatchGraph.jsx) specifically
 * so the 3D renderer can reuse the identical detail experience instead of a
 * second, drifting copy (see FRONTEND_HANDOFF.md's 3D graph phase).
 */
export default function GraphDetailPanel({ selection, sizeClass }) {
  if (!selection) {
    return (
      <div className={`${styles.panel} ${sizeClass}`}>
        <p className={styles.panelEmpty}>
          Click a candidate, a job, or a connection line to see its details here.
        </p>
      </div>
    );
  }

  if (selection.type === "node") {
    const isJob = selection.kind === "job";
    return (
      <div className={`${styles.panel} ${sizeClass}`}>
        <StatusBadge variant="glass" tone={isJob ? "primary" : "accent"}>{isJob ? "Job" : "Candidate"}</StatusBadge>
        <h2 className={styles.panelTitle}>{selection.label}</h2>
        <Link to={isJob ? `/jobs/${selection.refId}` : `/candidates/${selection.refId}`} className={styles.openLink}>
          Open {isJob ? "job" : "candidate"} →
        </Link>

        <div className={styles.connectionList}>
          <span className={styles.connectionListLabel}>
            {selection.connectedEdges.length} connection{selection.connectedEdges.length === 1 ? "" : "s"}
          </span>
          {selection.connectedEdges.map((edge) => (
            <div key={edge.matchId} className={styles.connectionRow}>
              <span className={styles.connectionName}>{edge.otherLabel}</span>
              <span className={styles.connectionScore}>{Math.round(edge.finalScore * 100)}%</span>
              <StatusBadge variant="glass" tone={edge.qualified ? "success" : "neutral"}>
                {edge.qualified ? "Qualified" : "Not qualified"}
              </StatusBadge>
            </div>
          ))}
        </div>
      </div>
    );
  }

  // selection.type === "edge"
  return (
    <div className={`${styles.panel} ${sizeClass}`}>
      <StatusBadge variant="glass" tone={selection.qualified ? "success" : "danger"}>
        {selection.qualified ? "Qualified match" : "Disqualified match"}
      </StatusBadge>

      <div className={styles.edgeParties}>
        <div>
          <span className={styles.panelLabel}>Candidate</span>
          <Link to={`/candidates/${selection.candidateId}`} className={styles.panelValueLink}>
            {selection.candidateLabel}
          </Link>
        </div>
        <div>
          <span className={styles.panelLabel}>Job</span>
          <Link to={`/jobs/${selection.jobId}`} className={styles.panelValueLink}>
            {selection.jobLabel}
          </Link>
        </div>
      </div>

      <div className={styles.scoreRow}>
        <span className={styles.panelLabel}>Final score</span>
        <span className={styles.panelValue}>{Math.round(selection.finalScore * 100)}%</span>
      </div>
      <div className={styles.scoreRow}>
        <span className={styles.panelLabel}>Hard gate</span>
        <span className={styles.panelValue}>{selection.hardGatePassed ? "Passed" : "Failed"}</span>
      </div>

      <RequirementChips label="Matched requirements" items={selection.matchedRequirements} tone="matched" />
      <RequirementChips label="Missing requirements" items={selection.missingRequirements} tone="missing" />
    </div>
  );
}

function RequirementChips({ label, items, tone }) {
  if (!items || items.length === 0) return null;
  return (
    <div className={styles.chipGroup}>
      <span className={styles.chipGroupLabel}>{label}</span>
      <div className={styles.chips}>
        {items.map((item) => (
          <span key={item} className={tone === "matched" ? styles.chipMatched : styles.chipMissing}>
            {item}
          </span>
        ))}
      </div>
    </div>
  );
}
