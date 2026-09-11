import { useEffect, useRef, useState } from "react";
import { getCandidatesJobsGraph } from "../api/graph.js";
import { getCandidates } from "../api/candidates.js";
import { ApiError } from "../api/client.js";
import PageHeader from "../components/PageHeader.jsx";
import EmptyState from "../components/EmptyState.jsx";
import MatchGraph, { FULL_GRAPH_LAYOUT } from "../components/MatchGraph.jsx";
import styles from "./GraphPage.module.css";

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
 * The actual Cytoscape rendering/interaction/detail-panel logic lives in the shared
 * <MatchGraph> component (src/components/MatchGraph.jsx) -- this page owns only
 * data-fetching and the page chrome (header, reset button, loading/error/empty
 * states). CandidateDetailPage's ego graph is the same component's second call site.
 */
export default function GraphPage() {
  const [rawGraph, setRawGraph] = useState(null); // { jobs, edges } from the backend
  const [candidateNames, setCandidateNames] = useState(null); // Map<candidate_id, name>
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const graphRef = useRef(null);

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
            onClick={() => graphRef.current?.resetView()}
            disabled={!rawGraph || rawGraph.edges.length === 0}
          >
            Reset view
          </button>
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

      {!loading && !error && rawGraph && rawGraph.edges.length > 0 && (
        <MatchGraph
          ref={graphRef}
          rawGraph={rawGraph}
          candidateNames={candidateNames}
          layout={FULL_GRAPH_LAYOUT}
          showLegend
          showScoreFilter
          showSearch
          size="full"
        />
      )}
    </div>
  );
}
