import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import CytoscapeComponent from "react-cytoscapejs";
import { getCandidatesJobsGraph } from "../api/graph.js";
import { getCandidates } from "../api/candidates.js";
import { ApiError } from "../api/client.js";
import PageHeader from "../components/PageHeader.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import EmptyState from "../components/EmptyState.jsx";
import styles from "./GraphPage.module.css";

// Namespacing node ids: candidate_id and job_id are both UUIDs from different tables,
// so a bare id could theoretically collide across the two -- and a click handler needs
// to tell a job node from a candidate node without a second lookup anyway.
const JOB_PREFIX = "job:";
const CANDIDATE_PREFIX = "candidate:";

// Cose is cytoscape core's own built-in force-directed layout -- no extra layout
// plugin dependency for the "2D force-directed layout first, no 3D yet" requirement.
// animate: false renders the settled layout immediately rather than animating into
// it, which matters more than it sounds at a few hundred elements.
const LAYOUT = {
  name: "cose",
  animate: false,
  fit: true,
  padding: 40,
  nodeRepulsion: 9000,
  idealEdgeLength: 100,
};

/**
 * Recruiter-facing candidate <-> job knowledge graph -- read-only exploration of
 * already-persisted MatchResults. Built from exactly two existing RWS calls
 * (GET /v1/graph/candidates-jobs, GET /v1/candidates) and nothing else: no direct
 * call to Second Brain/Resume Intelligence/VoiceHR, no scoring recomputation, no
 * qualification re-derivation -- every value rendered is copied straight off a
 * persisted MatchResult (see handoff.md's graph-endpoint entry and the design audit
 * it followed). Candidate nodes are not returned by the backend; they're derived
 * here from the unique candidate_id values on edges, with display names resolved
 * against the candidate list, exactly as the backend's own schema docstring expects.
 */
export default function GraphPage() {
  const [rawGraph, setRawGraph] = useState(null); // { jobs, edges } from the backend
  const [candidateNames, setCandidateNames] = useState(null); // Map<candidate_id, name>
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selection, setSelection] = useState(null);
  const cyRef = useRef(null);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      // Two independent RWS reads, not a graph-specific proxy -- the graph endpoint
      // deliberately has no candidates[] array (see api/graph.js), so name
      // resolution reuses the same GET /v1/candidates call CandidatesPage already
      // makes, rather than the backend fetching Resume Intelligence a second time.
      const [graph, candidates] = await Promise.all([getCandidatesJobsGraph(), getCandidates()]);
      setRawGraph(graph);
      setCandidateNames(new Map(candidates.map((candidate) => [candidate.candidate_id, candidate.name])));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load the graph.");
    } finally {
      setLoading(false);
    }
  }

  const { elements, candidateCount } = useMemo(
    () => buildElements(rawGraph, candidateNames),
    [rawGraph, candidateNames]
  );

  // Cytoscape can't read CSS custom properties itself (it draws to canvas, not the
  // DOM) -- read the real values out of variables.css once so node/edge colors stay
  // the app's actual design tokens instead of a second, drifting set of hex literals.
  const stylesheet = useMemo(() => buildStylesheet(), []);

  const applyHighlight = useCallback((cy, focusElement) => {
    cy.elements().removeClass("faded highlighted");
    const neighborhood = focusElement.closedNeighborhood();
    cy.elements().difference(neighborhood).addClass("faded");
    neighborhood.addClass("highlighted");
  }, []);

  // react-cytoscapejs invokes the `cy` prop on every prop update, not just the first
  // mount -- but componentDidMount only ever constructs one real Cytoscape instance
  // for this component's lifetime, so guarding on cyRef.current keeps these
  // listeners bound exactly once instead of stacking duplicates on every re-render.
  const handleCyInit = useCallback(
    (cy) => {
      if (cyRef.current) return;
      cyRef.current = cy;

      cy.on("tap", (event) => {
        if (event.target !== cy) return;
        cy.elements().removeClass("faded highlighted");
        setSelection(null);
      });

      cy.on("tap", "node", (event) => {
        const node = event.target;
        applyHighlight(cy, node);
        setSelection(buildNodeSelection(node));
      });

      cy.on("tap", "edge", (event) => {
        const edge = event.target;
        applyHighlight(cy, edge);
        setSelection(buildEdgeSelection(edge));
      });
    },
    [applyHighlight]
  );

  function handleResetView() {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass("faded highlighted");
    cy.fit(undefined, 30);
    setSelection(null);
  }

  return (
    <div className={styles.page}>
      <PageHeader
        title="Knowledge Graph"
        subtitle={
          rawGraph &&
          `${rawGraph.jobs.length} job${rawGraph.jobs.length === 1 ? "" : "s"} · ${candidateCount} candidate${
            candidateCount === 1 ? "" : "s"
          } · ${rawGraph.edges.length} match${rawGraph.edges.length === 1 ? "" : "es"}`
        }
        actions={
          <button
            type="button"
            className={styles.resetButton}
            onClick={handleResetView}
            disabled={!rawGraph || rawGraph.edges.length === 0}
          >
            Reset view
          </button>
        }
      />

      {loading && <p className={styles.status}>Loading graph…</p>}

      {!loading && error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}

      {!loading && !error && rawGraph && rawGraph.edges.length === 0 && (
        <EmptyState
          title="Nothing to graph yet"
          description="Submit a job description and match candidates to see connections here."
        />
      )}

      {!loading && !error && rawGraph && rawGraph.edges.length > 0 && (
        <div className={styles.graphLayout}>
          <div className={styles.graphCard}>
            <Legend />
            <div className={styles.canvasWrapper}>
              <CytoscapeComponent
                elements={elements}
                stylesheet={stylesheet}
                layout={LAYOUT}
                cy={handleCyInit}
                style={{ width: "100%", height: "100%" }}
                wheelSensitivity={0.2}
              />
            </div>
          </div>
          <DetailPanel selection={selection} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Element/stylesheet construction -- pure functions, no component state, so they're
// easy to reason about independent of React's render cycle.
// ---------------------------------------------------------------------------

function buildElements(rawGraph, candidateNames) {
  if (!rawGraph) return { elements: [], candidateCount: 0 };

  const jobNodes = rawGraph.jobs.map((job) => ({
    data: { id: `${JOB_PREFIX}${job.job_id}`, kind: "job", label: job.title, refId: job.job_id },
  }));

  const candidateIds = [...new Set(rawGraph.edges.map((edge) => edge.candidate_id))];
  const candidateNodes = candidateIds.map((candidateId) => ({
    data: {
      id: `${CANDIDATE_PREFIX}${candidateId}`,
      kind: "candidate",
      label: candidateNames?.get(candidateId) ?? `Candidate ${candidateId.slice(0, 8)}…`,
      refId: candidateId,
    },
  }));

  const edges = rawGraph.edges.map((edge) => ({
    data: {
      id: `edge:${edge.match_id}`,
      source: `${CANDIDATE_PREFIX}${edge.candidate_id}`,
      target: `${JOB_PREFIX}${edge.job_id}`,
      matchId: edge.match_id,
      candidateId: edge.candidate_id,
      jobId: edge.job_id,
      finalScore: edge.final_score,
      qualified: edge.qualified,
      hardGatePassed: edge.hard_gate_passed,
      matchedRequirements: edge.matched_requirements,
      missingRequirements: edge.missing_requirements,
    },
  }));

  return { elements: [...jobNodes, ...candidateNodes, ...edges], candidateCount: candidateNodes.length };
}

function readToken(style, name, fallback) {
  return style.getPropertyValue(name).trim() || fallback;
}

function buildStylesheet() {
  const style = getComputedStyle(document.documentElement);
  const color = {
    primary: readToken(style, "--color-primary", "#2f5fdb"),
    accent: readToken(style, "--color-accent", "#6b3fc4"),
    success: readToken(style, "--color-success", "#1f7a45"),
    danger: readToken(style, "--color-danger", "#c0392b"),
    warning: readToken(style, "--color-warning", "#9a6b0a"),
    surface: readToken(style, "--color-surface", "#ffffff"),
    text: readToken(style, "--color-text", "#1f2430"),
    textMuted: readToken(style, "--color-text-muted", "#6b7280"),
    fontSans: readToken(style, "--font-sans", "sans-serif"),
  };

  return [
    {
      selector: "node",
      style: {
        "font-family": color.fontSans,
        "font-size": 10,
        color: color.text,
        label: "data(label)",
        "text-valign": "bottom",
        "text-margin-y": 6,
        "text-wrap": "ellipsis",
        "text-max-width": "90px",
        "border-width": 2,
        "border-color": color.surface,
      },
    },
    {
      selector: "node[kind='job']",
      style: { shape: "round-rectangle", "background-color": color.primary, width: 42, height: 42 },
    },
    {
      selector: "node[kind='candidate']",
      style: { shape: "ellipse", "background-color": color.accent, width: 26, height: 26 },
    },
    {
      selector: "edge",
      style: {
        "curve-style": "bezier",
        width: "mapData(finalScore, 0, 1, 1, 7)",
        opacity: "mapData(finalScore, 0, 1, 0.35, 0.95)",
        "line-color": color.textMuted,
        "target-arrow-shape": "none",
      },
    },
    // Boolean data selectors: `?` matches a truthy field value, `!` matches a falsy
    // one -- `qualified` is always present (true/false), so this is a clean split,
    // not a truthy/missing-field ambiguity.
    { selector: "edge[?qualified]", style: { "line-color": color.success } },
    { selector: "edge[!qualified]", style: { "line-color": color.danger, "line-style": "dashed" } },
    // Selection/highlight classes, applied by GraphPage's own click handlers -- kept
    // last so they win over the data-driven rules above for the elements they match.
    { selector: "node.faded, edge.faded", style: { opacity: 0.12 } },
    {
      selector: "node.highlighted",
      style: { "border-width": 3, "border-color": color.warning },
    },
    {
      selector: "edge.highlighted",
      style: { width: 8, opacity: 1 },
    },
  ];
}

function buildNodeSelection(node) {
  const kind = node.data("kind"); // "job" | "candidate"
  const connectedEdges = node
    .connectedEdges()
    .map((edge) => {
      const otherNode = kind === "job" ? edge.source() : edge.target();
      return {
        matchId: edge.data("matchId"),
        otherLabel: otherNode.data("label"),
        finalScore: edge.data("finalScore"),
        qualified: edge.data("qualified"),
      };
    })
    .sort((a, b) => b.finalScore - a.finalScore);

  return { type: "node", kind, refId: node.data("refId"), label: node.data("label"), connectedEdges };
}

function buildEdgeSelection(edge) {
  return {
    type: "edge",
    candidateId: edge.data("candidateId"),
    candidateLabel: edge.source().data("label"),
    jobId: edge.data("jobId"),
    jobLabel: edge.target().data("label"),
    finalScore: edge.data("finalScore"),
    qualified: edge.data("qualified"),
    hardGatePassed: edge.data("hardGatePassed"),
    matchedRequirements: edge.data("matchedRequirements"),
    missingRequirements: edge.data("missingRequirements"),
  };
}

// ---------------------------------------------------------------------------
// Presentational helpers
// ---------------------------------------------------------------------------

function Legend() {
  return (
    <div className={styles.legend}>
      <span className={styles.legendItem}>
        <span className={`${styles.legendSwatch} ${styles.legendJob}`} /> Job
      </span>
      <span className={styles.legendItem}>
        <span className={`${styles.legendSwatch} ${styles.legendCandidate}`} /> Candidate
      </span>
      <span className={styles.legendItem}>
        <span className={`${styles.legendLine} ${styles.legendQualified}`} /> Qualified match
      </span>
      <span className={styles.legendItem}>
        <span className={`${styles.legendLine} ${styles.legendDisqualified}`} /> Disqualified match
      </span>
    </div>
  );
}

function DetailPanel({ selection }) {
  if (!selection) {
    return (
      <div className={styles.panel}>
        <p className={styles.panelEmpty}>
          Click a candidate, a job, or a connection line to see its details here.
        </p>
      </div>
    );
  }

  if (selection.type === "node") {
    const isJob = selection.kind === "job";
    return (
      <div className={styles.panel}>
        <StatusBadge tone={isJob ? "primary" : "accent"}>{isJob ? "Job" : "Candidate"}</StatusBadge>
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
              <StatusBadge tone={edge.qualified ? "success" : "neutral"}>
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
    <div className={styles.panel}>
      <StatusBadge tone={selection.qualified ? "success" : "danger"}>
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
