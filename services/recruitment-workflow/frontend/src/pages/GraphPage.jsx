import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { getCandidatesJobsGraph } from "../api/graph.js";
import { getCandidates } from "../api/candidates.js";
import { ApiError } from "../api/client.js";
import PageHeader from "../components/PageHeader.jsx";
import EmptyState from "../components/EmptyState.jsx";
import MatchGraph, { FULL_GRAPH_LAYOUT, buildGraphElements } from "../components/MatchGraph.jsx";
import GraphControls from "../components/GraphControls.jsx";
import styles from "./GraphPage.module.css";

// 3D is an explicit, optional presentation mode -- lazy-loaded so three.js/
// 3d-force-graph never enter this route's normal bundle, let alone the app's.
// WebGL itself is only initialized once MatchGraph3D actually mounts, i.e.
// once a recruiter picks "3D" below (see FRONTEND_HANDOFF.md's 3D graph phase).
const MatchGraph3D = lazy(() => import("../components/MatchGraph3D.jsx"));

/**
 * Recruiter-facing candidate <-> job knowledge graph -- read-only exploration of
 * already-persisted MatchResults. Built from exactly two existing RWS calls
 * (GET /v1/graph/candidates-jobs, GET /v1/candidates) and nothing else: no direct
 * call to Second Brain/Resume Intelligence/VoiceHR, no scoring recomputation, no
 * qualification re-derivation -- every value rendered is copied straight off a
 * persisted MatchResult (see handoff.md's graph-endpoint entry and the design audit
 * it followed). Candidate nodes are not returned by the backend; they're derived by
 * MatchGraph from the unique candidate_id values on edges, with display names
 * resolved against the candidate list, exactly as the backend's own schema
 * docstring expects.
 *
 * Two renderers share this page's data/filter/search state: the default 2D
 * Cytoscape view (<MatchGraph>) and an optional, explicit 3D immersive view
 * (<MatchGraph3D>, lazy-loaded, built on 3d-force-graph). Only one is ever
 * mounted at a time -- switching modes doesn't keep the other alive in the
 * background. The qualified/disqualified toggle, the minimum-score filter, and
 * search are all owned here (via <GraphControls>) rather than inside either
 * renderer, specifically so they survive a 2D<->3D switch instead of resetting
 * (see FRONTEND_HANDOFF.md's 3D graph phase for why this moved up from
 * MatchGraph.jsx, where it used to live for the 2D-only path).
 */
export default function GraphPage() {
  const [rawGraph, setRawGraph] = useState(null); // { jobs, edges } from the backend
  const [candidateNames, setCandidateNames] = useState(null); // Map<candidate_id, name>
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [viewMode, setViewMode] = useState("2d"); // "2d" | "3d" -- 2D is always the default
  const graphRef = useRef(null); // points at whichever renderer is currently mounted

  // Display-only filter/search state, shared by both renderers -- never sent
  // anywhere, never touches a persisted MatchResult field (see MatchGraph.jsx's
  // own comment on the equivalent effect that used to own this).
  const [hiddenCategories, setHiddenCategories] = useState({ qualified: false, disqualified: false });
  const [minScore, setMinScore] = useState(0);
  const [searchTerm, setSearchTerm] = useState("");

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

  const candidateCount = rawGraph ? new Set(rawGraph.edges.map((edge) => edge.candidate_id)).size : 0;

  // Search index -- same buildGraphElements() the 2D renderer uses internally,
  // computed independently here purely for the search box (cheap, pure, small
  // dataset); neither renderer needs to expose its own internal elements for
  // this to work.
  const { elements } = useMemo(() => buildGraphElements(rawGraph, candidateNames), [rawGraph, candidateNames]);
  const searchResults = useMemo(() => {
    const term = searchTerm.trim().toLowerCase();
    if (!term) return [];
    return elements
      .filter((el) => el.data.kind && el.data.label.toLowerCase().includes(term))
      .slice(0, 8)
      .map((el) => ({ id: el.data.id, label: el.data.label, kind: el.data.kind }));
  }, [elements, searchTerm]);

  function handleToggleCategory(category) {
    setHiddenCategories((prev) => ({ ...prev, [category]: !prev[category] }));
  }

  function handleSelectSearchResult(nodeId) {
    graphRef.current?.focusNode(nodeId);
    setSearchTerm("");
  }

  const hasGraph = !loading && !error && rawGraph && rawGraph.edges.length > 0;

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
          <div className={styles.headerActions}>
            {hasGraph && (
              <div className={styles.viewSwitch} role="group" aria-label="Graph view">
                <button
                  type="button"
                  className={`${styles.viewSwitchButton} ${viewMode === "2d" ? styles.viewSwitchActive : ""}`}
                  aria-pressed={viewMode === "2d"}
                  onClick={() => setViewMode("2d")}
                >
                  2D
                </button>
                <button
                  type="button"
                  className={`${styles.viewSwitchButton} ${viewMode === "3d" ? styles.viewSwitchActive : ""}`}
                  aria-pressed={viewMode === "3d"}
                  onClick={() => setViewMode("3d")}
                >
                  3D
                </button>
              </div>
            )}
            <button
              type="button"
              className={styles.resetButton}
              onClick={() => {
                graphRef.current?.resetView();
                setSearchTerm("");
              }}
              disabled={!rawGraph || rawGraph.edges.length === 0}
            >
              Reset view
            </button>
          </div>
        }
        variant="glass"
      />

      {loading && <p className={styles.status}>Loading graph…</p>}

      {!loading && error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}

      {!loading && !error && rawGraph && rawGraph.edges.length === 0 && (
        <EmptyState
          variant="glass"
          title="Nothing to graph yet"
          description="Submit a job description and match candidates to see connections here."
        />
      )}

      {hasGraph && (
        <>
          <GraphControls
            hiddenCategories={hiddenCategories}
            onToggleCategory={handleToggleCategory}
            minScore={minScore}
            onMinScoreChange={setMinScore}
            searchTerm={searchTerm}
            onSearchTermChange={setSearchTerm}
            searchResults={searchResults}
            onSelectSearchResult={handleSelectSearchResult}
          />

          {viewMode === "2d" ? (
            <MatchGraph
              key="2d"
              ref={graphRef}
              rawGraph={rawGraph}
              candidateNames={candidateNames}
              layout={FULL_GRAPH_LAYOUT}
              hiddenCategories={hiddenCategories}
              minScore={minScore}
              size="full"
            />
          ) : (
            <Suspense fallback={<p className={styles.status}>Loading 3D scene…</p>}>
              <MatchGraph3D
                key="3d"
                ref={graphRef}
                rawGraph={rawGraph}
                candidateNames={candidateNames}
                hiddenCategories={hiddenCategories}
                minScore={minScore}
                onExit={() => setViewMode("2d")}
              />
            </Suspense>
          )}
        </>
      )}
    </div>
  );
}
