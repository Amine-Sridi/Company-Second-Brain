import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import CytoscapeComponent from "react-cytoscapejs";
import StatusBadge from "./StatusBadge.jsx";
import styles from "./MatchGraph.module.css";

// Namespacing node ids: candidate_id and job_id are both UUIDs from different tables,
// so a bare id could theoretically collide across the two -- and a click handler needs
// to tell a job node from a candidate node without a second lookup anyway.
const JOB_PREFIX = "job:";
const CANDIDATE_PREFIX = "candidate:";

// Cose is cytoscape core's own built-in force-directed layout -- no extra layout
// plugin dependency. Used by GraphPage (/graph) for the full, many-node dataset.
export const FULL_GRAPH_LAYOUT = {
  name: "cose",
  animate: false,
  fit: true,
  padding: 40,
  nodeRepulsion: 9000,
  idealEdgeLength: 100,
};

// A candidate's own ego graph is inherently a star: one candidate at the center,
// a handful of jobs radiating out. `concentric` (also built into cytoscape core --
// no extra dependency) places the candidate node in the middle ring and every job
// node in the outer ring deterministically, which reads far more simply than
// running cose's iterative physics simulation over 2-10 nodes.
export const EGO_GRAPH_LAYOUT = {
  name: "concentric",
  animate: false,
  fit: true,
  padding: 30,
  concentric: (node) => (node.data("kind") === "candidate" ? 2 : 1),
  levelWidth: () => 1,
  minNodeSpacing: 45,
};

/**
 * Builds Cytoscape elements from a CandidateJobGraphResponse-shaped
 * `{ jobs, edges }` object (GET /v1/graph/candidates-jobs's own response shape) --
 * used as-is by GraphPage for the whole dataset, and by CandidateDetailPage's ego
 * graph after pre-filtering `edges`/`jobs` down to one candidate client-side. Either
 * way this function never talks to the network itself and never recomputes a score
 * or a qualification decision -- it only reshapes already-persisted MatchResult
 * fields into the {data} objects Cytoscape expects.
 *
 * @param {{ jobs: Array<{job_id: string, title: string}>, edges: Array<object> }|null} rawGraph
 * @param {Map<string, string>|null} candidateNames - candidate_id -> display name
 */
export function buildGraphElements(rawGraph, candidateNames) {
  if (!rawGraph) return { elements: [], candidateCount: 0 };

  // Precomputed once per node (same approach as finalScore on edges below) so the
  // stylesheet's degree-based sizing (buildStylesheet) can use a plain mapData()
  // data-selector instead of a live topology query.
  const jobDegree = new Map();
  const candidateDegree = new Map();
  for (const edge of rawGraph.edges) {
    jobDegree.set(edge.job_id, (jobDegree.get(edge.job_id) ?? 0) + 1);
    candidateDegree.set(edge.candidate_id, (candidateDegree.get(edge.candidate_id) ?? 0) + 1);
  }

  const jobNodes = rawGraph.jobs.map((job) => ({
    data: {
      id: `${JOB_PREFIX}${job.job_id}`,
      kind: "job",
      label: job.title,
      refId: job.job_id,
      degree: jobDegree.get(job.job_id) ?? 0,
    },
  }));

  const candidateIds = [...new Set(rawGraph.edges.map((edge) => edge.candidate_id))];
  const candidateNodes = candidateIds.map((candidateId) => ({
    data: {
      id: `${CANDIDATE_PREFIX}${candidateId}`,
      kind: "candidate",
      label: candidateNames?.get(candidateId) ?? `Candidate ${candidateId.slice(0, 8)}…`,
      refId: candidateId,
      degree: candidateDegree.get(candidateId) ?? 0,
    },
  }));

  // A node with no explicit `position` defaults to the exact same (0, 0) point in
  // Cytoscape -- with every node coinciding there, cose's own force calculation
  // falls back to Math.random() to pick an arbitrary separation direction for every
  // node pair, on every layout run, producing a different final layout each time
  // (verified against live data: this is NOT the `randomize` layout option's doing --
  // FULL_GRAPH_LAYOUT never sets it, and cytoscape's own real default is already
  // `false`; the instability survives regardless of that option). Seeding a small,
  // deterministic, distinct starting point per node -- by its stable index in this
  // array, so identical input always produces identical seeds -- means no two nodes
  // ever start exactly coincident, so that random tie-break path is never triggered;
  // cose's own iterative forces (not this seed) still do all the real layout work.
  // `concentric` (the ego graph's own layout) never reads incoming position at all,
  // so this has no effect there beyond removing an already-nonexistent risk.
  const allNodes = [...jobNodes, ...candidateNodes];
  allNodes.forEach((node, index) => {
    const angle = (2 * Math.PI * index) / allNodes.length;
    node.position = { x: 50 * Math.cos(angle), y: 50 * Math.sin(angle) };
  });

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

  return { elements: [...allNodes, ...edges], candidateCount: candidateNodes.length };
}

function readToken(style, name, fallback) {
  return style.getPropertyValue(name).trim() || fallback;
}

function buildStylesheet() {
  // Cytoscape can't read CSS custom properties itself (it draws to canvas, not the
  // DOM) -- read the real values out of variables.css once so node/edge colors stay
  // the app's actual design tokens instead of a second, drifting set of hex literals.
  //
  // V2 design system, final slice (see FRONTEND_HANDOFF.md): reads --v2-* tokens
  // directly rather than taking a theme/variant param, because both of this
  // component's real call sites (GraphPage and CandidateDetailPage's ego graph)
  // are migrated to V2 together in this same pass -- there is no remaining V1
  // consumer left needing the old --color-* values, so a toggle would be
  // unused-branch complexity, not a real requirement. If a future V1 consumer
  // ever appears, that's the point to add one back, not before.
  const style = getComputedStyle(document.documentElement);
  const color = {
    primary: readToken(style, "--v2-cyan", "#3fd8e8"),
    accent: readToken(style, "--v2-violet", "#a78bfa"),
    success: readToken(style, "--v2-success", "#4ade80"),
    danger: readToken(style, "--v2-danger", "#fb7185"),
    warning: readToken(style, "--v2-warning", "#fbbf24"),
    surface: readToken(style, "--v2-bg-elevated", "#10131e"),
    text: readToken(style, "--v2-text", "#eef0f7"),
    textMuted: readToken(style, "--v2-text-muted", "#9297ab"),
    fontSans: readToken(style, "--font-sans", "sans-serif"),
  };

  // "Subtle" per this task's own wording: long enough to read as an intentional
  // emphasis, short enough to never feel sluggish on a click. Listed once here and
  // reused by both node/edge base selectors below.
  const focusTransition = {
    "transition-duration": 150,
    "transition-timing-function": "ease-out",
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
        // Applies to any style change on the node -- highlight/fade (border-width,
        // border-color, opacity) triggered by this component's click handlers, and
        // degree-based size below -- without touching the click/highlight/reset
        // logic itself, which still just adds/removes plain classes.
        "transition-property": "border-width, border-color, opacity, width, height",
        ...focusTransition,
      },
    },
    {
      // Size still scales by data(degree) (how many persisted matches touch this
      // job), not a fixed constant -- a job matched to many candidates now reads as
      // visually more central than one with a single match. Job vs. candidate stays
      // distinguished primarily by shape + color (unchanged); the two size bands
      // below (34-58 vs. 18-32) never overlap, so size reinforces that distinction
      // rather than competing with it. Degree is clamped to a fixed 1-10 assumed
      // range via mapData, the same fixed-domain approach already used for
      // finalScore below -- consistent with this codebase's existing convention,
      // not a new one.
      selector: "node[kind='job']",
      style: {
        shape: "round-rectangle",
        "background-color": color.primary,
        width: "mapData(degree, 1, 10, 34, 58)",
        height: "mapData(degree, 1, 10, 34, 58)",
      },
    },
    {
      selector: "node[kind='candidate']",
      style: {
        shape: "ellipse",
        "background-color": color.accent,
        width: "mapData(degree, 1, 10, 18, 32)",
        height: "mapData(degree, 1, 10, 18, 32)",
      },
    },
    {
      selector: "edge",
      style: {
        "curve-style": "bezier",
        // Score/qualification encoding below is byte-for-byte unchanged from
        // before this pass -- only the transition-* lines are new.
        width: "mapData(finalScore, 0, 1, 1, 7)",
        opacity: "mapData(finalScore, 0, 1, 0.35, 0.95)",
        "line-color": color.textMuted,
        "target-arrow-shape": "none",
        "transition-property": "width, opacity, line-color",
        ...focusTransition,
      },
    },
    // Boolean data selectors: `?` matches a truthy field value, `!` matches a falsy
    // one -- `qualified` is always present (true/false), so this is a clean split,
    // not a truthy/missing-field ambiguity.
    { selector: "edge[?qualified]", style: { "line-color": color.success } },
    { selector: "edge[!qualified]", style: { "line-color": color.danger, "line-style": "dashed" } },
    // Applied/removed by this component's own legend-toggle/score-filter controls
    // (full graph only) -- a purely client-side display concern. Nothing here ever
    // touches `qualified`/`finalScore` themselves; a filtered-out edge's data is
    // completely unchanged, it just isn't drawn.
    { selector: "edge.filteredOut", style: { display: "none" } },
    // Selection/highlight classes, applied by this component's own click handlers --
    // kept last so they win over the data-driven rules above for elements they match.
    { selector: "node.faded, edge.faded", style: { opacity: 0.12 } },
    { selector: "node.highlighted", style: { "border-width": 3, "border-color": color.warning } },
    { selector: "edge.highlighted", style: { width: 8, opacity: 1 } },
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

/**
 * The Cytoscape canvas + detail panel widget shared by GraphPage (`/graph`, the
 * whole candidate/job dataset) and CandidateDetailPage's embedded ego graph (one
 * candidate's own connections). Both call sites pass an already-network-fetched,
 * already-filtered `{ jobs, edges }` object -- this component makes no API call of
 * its own and recomputes no score/qualification value; every rendered field is
 * copied straight off a persisted MatchResult.
 *
 * `ref.resetView()` is exposed so a caller that puts its own "Reset view" button
 * elsewhere (GraphPage's PageHeader) can still trigger it; a caller that's fine with
 * the button living inside the widget itself can pass `showResetButton` instead and
 * skip the ref entirely (the ego graph embed does this).
 *
 * `size` picks a fixed CSS size variant ("full" for GraphPage's whole-dataset view,
 * "ego" for the smaller embedded candidate-detail view) rather than taking an
 * arbitrary height prop, so each variant's own mobile-breakpoint shrink stays a
 * plain CSS media query instead of fighting inline-style specificity.
 *
 * `showScoreFilter`/`showSearch` add the full graph's interactive controls (legend
 * click-to-toggle is always available whenever `showLegend` is on -- there's no
 * separate flag for it, the legend IS the control per this feature's own spec).
 * Both default to `false` so the ego graph embed (which passes neither) is
 * completely unaffected -- these controls exist only where GraphPage opts in.
 */
const MatchGraph = forwardRef(function MatchGraph(
  {
    rawGraph,
    candidateNames,
    layout,
    showLegend = false,
    showResetButton = false,
    showScoreFilter = false,
    showSearch = false,
    size = "full",
  },
  ref
) {
  const [selection, setSelection] = useState(null);
  const cyRef = useRef(null);

  // Display-only filter state -- never sent anywhere, never touches a persisted
  // MatchResult field. `hiddenCategories` toggles whole qualified/disqualified
  // groups (the legend IS this control, per this task's own spec); `minScore` (a
  // 0-100 percent, matching how scores are shown everywhere else in this app)
  // hides edges below that persisted `final_score`. Both apply as a Cytoscape class
  // toggle in the effect below -- `qualified`/`final_score` themselves are never
  // read back, recomputed, or reassigned by either control.
  const [hiddenCategories, setHiddenCategories] = useState({ qualified: false, disqualified: false });
  const [minScore, setMinScore] = useState(0);
  const [searchTerm, setSearchTerm] = useState("");

  const { elements } = useMemo(() => buildGraphElements(rawGraph, candidateNames), [rawGraph, candidateNames]);
  const stylesheet = useMemo(() => buildStylesheet(), []);

  // Re-syncs the `.filteredOut` class on every real edge whenever a filter control
  // changes -- a plain class toggle on already-laid-out elements, not a re-fetch,
  // not a new `cy.add()`/`cy.layout()` call, so this can never disturb the
  // deterministic layout those two calls are responsible for.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const minScoreFraction = minScore / 100;
    cy.edges().forEach((edge) => {
      const qualified = edge.data("qualified");
      const hiddenByCategory =
        (qualified && hiddenCategories.qualified) || (!qualified && hiddenCategories.disqualified);
      const hiddenByScore = edge.data("finalScore") < minScoreFraction;
      edge.toggleClass("filteredOut", hiddenByCategory || hiddenByScore);
    });
  }, [hiddenCategories, minScore, elements]);

  const handleToggleCategory = useCallback((category) => {
    setHiddenCategories((prev) => ({ ...prev, [category]: !prev[category] }));
  }, []);

  // Search matches against the same `label` already carried on every node (job
  // title / candidate display name) -- no new data, no new call, just a client-side
  // substring filter over what buildGraphElements already produced.
  const searchResults = useMemo(() => {
    if (!showSearch) return [];
    const term = searchTerm.trim().toLowerCase();
    if (!term) return [];
    return elements
      .filter((el) => el.data.kind && el.data.label.toLowerCase().includes(term))
      .slice(0, 8)
      .map((el) => ({ id: el.data.id, label: el.data.label, kind: el.data.kind }));
  }, [elements, searchTerm, showSearch]);

  const applyHighlight = useCallback((cy, focusElement) => {
    cy.elements().removeClass("faded highlighted");
    const neighborhood = focusElement.closedNeighborhood();
    cy.elements().difference(neighborhood).addClass("faded");
    neighborhood.addClass("highlighted");
  }, []);

  // Selecting a search result reuses the exact same highlight/selection path a
  // click on that node already takes -- not a second, parallel implementation --
  // then additionally fits the view to that node's neighborhood ("center the
  // selected node", the one thing a plain click doesn't already do).
  const handleSelectSearchResult = useCallback(
    (nodeId) => {
      const cy = cyRef.current;
      if (!cy) return;
      const node = cy.getElementById(nodeId);
      if (node.empty()) return;
      applyHighlight(cy, node);
      setSelection(buildNodeSelection(node));
      cy.fit(node.closedNeighborhood(), 50);
      setSearchTerm("");
    },
    [applyHighlight]
  );

  // "Reset view" resets *focus* -- the current selection/highlight (from a click or
  // a search result) and the viewport -- back to the whole graph. It deliberately
  // does NOT clear the qualified/disqualified toggles or the score filter: those
  // are a sticky viewing preference the user set up on purpose, not part of "which
  // node am I currently looking at" -- an explicit choice, not an oversight (see
  // FRONTEND_HANDOFF.md).
  const resetView = useCallback(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass("faded highlighted");
    cy.fit(undefined, 30);
    setSelection(null);
    setSearchTerm("");
  }, []);

  useImperativeHandle(ref, () => ({ resetView }), [resetView]);

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

  const sizeClass = size === "ego" ? styles.sizeEgo : styles.sizeFull;

  return (
    <div className={styles.layout}>
      <div className={`${styles.card} ${sizeClass}`}>
        {showLegend && <Legend hiddenCategories={hiddenCategories} onToggleCategory={handleToggleCategory} />}
        {(showScoreFilter || showSearch) && (
          <div className={styles.filterRow}>
            {showScoreFilter && (
              <label className={styles.scoreFilter}>
                <span className={styles.scoreFilterLabel}>Min score: {minScore}%+</span>
                <input
                  type="range"
                  min={0}
                  max={100}
                  step={5}
                  value={minScore}
                  onChange={(event) => setMinScore(Number(event.target.value))}
                  className={styles.scoreFilterInput}
                />
              </label>
            )}
            {showSearch && (
              <div className={styles.searchBox}>
                <input
                  type="text"
                  className={styles.searchInput}
                  placeholder="Search candidates or jobs…"
                  value={searchTerm}
                  onChange={(event) => setSearchTerm(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") setSearchTerm("");
                  }}
                />
                {searchTerm.trim() && (
                  <ul className={styles.searchResults}>
                    {searchResults.length > 0 ? (
                      searchResults.map((result) => (
                        <li key={result.id}>
                          <button
                            type="button"
                            className={styles.searchResultButton}
                            onClick={() => handleSelectSearchResult(result.id)}
                          >
                            <StatusBadge variant="glass" tone={result.kind === "job" ? "primary" : "accent"}>
                              {result.kind === "job" ? "Job" : "Candidate"}
                            </StatusBadge>
                            <span className={styles.searchResultLabel}>{result.label}</span>
                          </button>
                        </li>
                      ))
                    ) : (
                      <li className={styles.searchNoResults}>No matches</li>
                    )}
                  </ul>
                )}
              </div>
            )}
          </div>
        )}
        {showResetButton && (
          <div className={styles.toolbar}>
            <button type="button" className={styles.resetButton} onClick={resetView}>
              Reset view
            </button>
          </div>
        )}
        <div className={styles.canvasWrapper}>
          <CytoscapeComponent
            elements={elements}
            stylesheet={stylesheet}
            layout={layout}
            cy={handleCyInit}
            style={{ width: "100%", height: "100%" }}
            wheelSensitivity={0.2}
          />
        </div>
      </div>
      <DetailPanel selection={selection} sizeClass={sizeClass} />
    </div>
  );
});

export default MatchGraph;

// ---------------------------------------------------------------------------
// Presentational helpers
// ---------------------------------------------------------------------------

/**
 * The Job/Candidate swatches stay plain labels (no filter exists for node kind).
 * The Qualified/Disqualified swatches are themselves the toggle control this task
 * asked for -- no separate checkboxes anywhere else -- clicking one hides that
 * whole edge category via MatchGraph's `hiddenCategories` state/effect above.
 */
function Legend({ hiddenCategories, onToggleCategory }) {
  return (
    <div className={styles.legend}>
      <span className={styles.legendItem}>
        <span className={`${styles.legendSwatch} ${styles.legendJob}`} /> Job
      </span>
      <span className={styles.legendItem}>
        <span className={`${styles.legendSwatch} ${styles.legendCandidate}`} /> Candidate
      </span>
      <LegendToggle
        active={!hiddenCategories.qualified}
        onClick={() => onToggleCategory("qualified")}
        lineClassName={styles.legendQualified}
        label="Qualified match"
      />
      <LegendToggle
        active={!hiddenCategories.disqualified}
        onClick={() => onToggleCategory("disqualified")}
        lineClassName={styles.legendDisqualified}
        label="Disqualified match"
      />
    </div>
  );
}

function LegendToggle({ active, onClick, lineClassName, label }) {
  return (
    <button
      type="button"
      className={`${styles.legendItem} ${styles.legendToggle} ${active ? "" : styles.legendToggleOff}`}
      onClick={onClick}
      aria-pressed={active}
      title={active ? `Click to hide ${label.toLowerCase()}es` : `Click to show ${label.toLowerCase()}es`}
    >
      <span className={`${styles.legendLine} ${lineClassName}`} /> {label}
    </button>
  );
}

function DetailPanel({ selection, sizeClass }) {
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
