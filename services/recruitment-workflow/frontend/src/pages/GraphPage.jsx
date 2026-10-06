import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getCandidatesJobsGraph } from "../api/graph.js";
import { getCandidates } from "../api/candidates.js";
import { getApplications, selectMatch } from "../api/applications.js";
import { ApiError } from "../api/client.js";
import PageHeader from "../components/PageHeader.jsx";
import EmptyState from "../components/EmptyState.jsx";
import FocusGraph from "../components/graph/FocusGraph.jsx";
import MatchInspector from "../components/graph/MatchInspector.jsx";
import MatchMatrix from "../components/graph/MatchMatrix.jsx";
import SceneErrorBoundary from "../components/graph/SceneErrorBoundary.jsx";
import { defaultJobId, jobFocusItems, percent, ringFor, summarizeJobs } from "../components/graph/graphModel.js";
import styles from "./GraphPage.module.css";

// three.js only enters the bundle, and WebGL only starts, when the 3D view opens.
const Landscape3D = lazy(() => import("../components/graph/Landscape3D.jsx"));

const VIEWS = [
  { id: "focus", label: "Focus on a job" },
  { id: "overview", label: "Overview" },
  { id: "3d", label: "3D landscape" },
];
const LANDSCAPE_JOBS = 8;

/**
 * Recruiter-facing knowledge graph: read-only exploration of persisted MatchResults,
 * built from GET /v1/graph/candidates-jobs and GET /v1/candidates. Nothing here
 * recomputes a score or a qualification (see components/graph/graphModel.js).
 *
 * Three views share the same data:
 * - Focus: one job at the centre, its matches on three rings, an inspector that
 *   explains the selected match (its persisted score components ride on each edge).
 * - Overview: a candidate x job grid instead of an all-edges hairball.
 * - 3D landscape: pins whose height is the score, for presentation.
 *
 * The view, job and candidate live in the URL (?view=&job=&candidate=), so other
 * pages can deep-link into a specific match and the browser back button works.
 */
export default function GraphPage() {
  const [params, setParams] = useSearchParams();
  const [graph, setGraph] = useState(null);
  const [names, setNames] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showBelow, setShowBelow] = useState(true);
  const [loadedJobs, setLoadedJobs] = useState(new Set()); // jobs whose applications were read
  const [selected, setSelected] = useState(new Set()); // match ids already selected
  const [selectState, setSelectState] = useState({ matchId: null, state: "idle", error: null });
  const [focus3d, setFocus3d] = useState(null);
  const landscapeRef = useRef(null);

  const view = VIEWS.some((v) => v.id === params.get("view")) ? params.get("view") : "focus";

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const [g, candidates] = await Promise.all([getCandidatesJobsGraph(), getCandidates()]);
        if (cancelled) return;
        setGraph(g);
        setNames(new Map(candidates.map((c) => [c.candidate_id, c.name])));
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Unable to load the graph.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const jobRows = useMemo(() => (graph ? summarizeJobs(graph) : []), [graph]);
  const jobId = graph ? (jobRows.some((j) => j.job_id === params.get("job")) ? params.get("job") : defaultJobId(graph)) : null;
  const job = jobRows.find((j) => j.job_id === jobId) ?? null;

  const items = useMemo(() => (graph && jobId ? jobFocusItems(graph, jobId, names) : []), [graph, jobId, names]);
  const firstQualified = items.filter((i) => i.ring === "qualified").sort((a, b) => b.score - a.score)[0];
  const candidateParam = params.get("candidate");
  const selectedId = items.some((i) => i.id === candidateParam) ? candidateParam : (firstQualified ?? items[0])?.id ?? null;
  const selectedItem = items.find((i) => i.id === selectedId) ?? null;
  const ringCounts = useMemo(() => {
    const c = { qualified: 0, gate: 0, below: 0 };
    for (const item of items) c[item.ring] += 1;
    return c;
  }, [items]);

  // Which matches of the focused job are already selected, read once per job, so the
  // inspector shows "Selected" instead of offering a selection that would 409.
  useEffect(() => {
    if (!jobId || loadedJobs.has(jobId)) return;
    let cancelled = false;
    getApplications(jobId)
      .catch(() => [])
      .then((applications) => {
        if (cancelled) return;
        setLoadedJobs((prev) => new Set(prev).add(jobId));
        setSelected((prev) => {
          const next = new Set(prev);
          for (const app of applications) next.add(app.match_result_id);
          return next;
        });
      });
    return () => {
      cancelled = true;
    };
  }, [jobId, loadedJobs]);

  function update(next) {
    const merged = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value === null || value === undefined) merged.delete(key);
      else merged.set(key, value);
    }
    setParams(merged, { replace: false });
  }

  async function handleSelectForJob() {
    const edge = selectedItem?.edge;
    if (!edge) return;
    setSelectState({ matchId: edge.match_id, state: "saving", error: null });
    try {
      await selectMatch(edge.match_id);
      setSelected((prev) => new Set(prev).add(edge.match_id));
      setSelectState({ matchId: edge.match_id, state: "selected", error: null });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setSelected((prev) => new Set(prev).add(edge.match_id));
        setSelectState({ matchId: edge.match_id, state: "selected", error: null });
      } else {
        setSelectState({
          matchId: edge.match_id,
          state: "error",
          error: err instanceof ApiError ? err.message : "Failed to select the candidate.",
        });
      }
    }
  }

  const landscapeJobs = useMemo(
    () =>
      [...jobRows]
        .sort((a, b) => b.qualified - a.qualified || b.best - a.best)
        .slice(0, LANDSCAPE_JOBS)
        .map((j) => j.job_id),
    [jobRows],
  );

  const otherMatches = useMemo(() => {
    if (!graph || !selectedItem) return [];
    const titles = new Map(graph.jobs.map((j) => [j.job_id, j.title]));
    return graph.edges
      .filter((e) => e.candidate_id === selectedItem.id && e.job_id !== jobId)
      .sort((a, b) => b.final_score - a.final_score)
      .slice(0, 5)
      .map((e) => ({ jobId: e.job_id, title: titles.get(e.job_id), score: e.final_score, qualified: e.qualified }));
  }, [graph, selectedItem, jobId]);

  const counts = graph && {
    jobs: graph.jobs.length,
    candidates: new Set(graph.edges.map((e) => e.candidate_id)).size,
    matches: graph.edges.length,
  };

  return (
    <div className={styles.page}>
      <PageHeader
        title={view === "focus" && job ? `Who fits ${job.title}, and why` : view === "overview" ? "Every candidate against every job" : "The talent landscape"}
        subtitle={
          counts &&
          (view === "focus" && job
            ? `${job.matches} matched · ${job.qualified} qualified · from ${counts.matches} stored matches across ${counts.jobs} jobs`
            : view === "overview"
              ? "One cell per stored MatchResult. An empty cell means retrieval did not return that candidate for the job."
              : "Each pin is one match. It stands next to its job, and its height is the final score.")
        }
        actions={
          <div className={styles.tabs} role="tablist" aria-label="Graph view">
            {VIEWS.map((v) => (
              <button
                key={v.id}
                type="button"
                role="tab"
                aria-selected={view === v.id}
                className={view === v.id ? styles.tabActive : styles.tab}
                onClick={() => update({ view: v.id === "focus" ? null : v.id })}
              >
                {v.label}
              </button>
            ))}
          </div>
        }
      />

      {loading && <p className={styles.status}>Loading graph…</p>}
      {!loading && error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}
      {!loading && !error && graph && graph.edges.length === 0 && (
        <EmptyState title="Nothing to graph yet" description="Submit a job description to see its matches here." />
      )}

      {!loading && !error && graph && graph.edges.length > 0 && view === "focus" && (
        <>
          <div className={styles.toolbar}>
            <label className={styles.jobPicker}>
              <span>Job</span>
              <select value={jobId ?? ""} onChange={(event) => update({ job: event.target.value, candidate: null })}>
                {jobRows.map((j) => (
                  <option key={j.job_id} value={j.job_id}>
                    {j.title} · {j.qualified} qualified / {j.matches}
                  </option>
                ))}
              </select>
            </label>
            <span className={styles.spacer} />
            <label className={styles.check}>
              <input type="checkbox" checked={showBelow} onChange={(event) => setShowBelow(event.target.checked)} />
              Show matches below the threshold
            </label>
            <Link className={styles.linkButton} to={`/jobs/${jobId}`}>
              Open job
            </Link>
          </div>

          <div className={styles.focusLayout}>
            <section className={styles.graphCard} aria-label="Focus graph">
              <FocusGraph
                items={items}
                centerTitle={job?.title ?? ""}
                centerSubtitle={job ? `${job.matches} matches · ${job.qualified} qualified` : undefined}
                selectedId={selectedId}
                onSelect={(id) => update({ job: jobId, candidate: id })}
                showBelow={showBelow}
              />
              <div className={styles.legend}>
                <span className={styles.legendItem}>
                  <span className={styles.keyQualified} aria-hidden="true" />
                  Qualified · {ringCounts.qualified}
                </span>
                <span className={styles.legendItem}>
                  <span className={styles.keyGate} aria-hidden="true" />
                  Missing required skills · {ringCounts.gate}
                </span>
                <span className={styles.legendItem}>
                  <span className={styles.keyBelow} aria-hidden="true" />
                  Below the score threshold · {ringCounts.below}
                </span>
                <span className={styles.legendNote}>Closer to the centre is a better fit. Node size follows the final score.</span>
              </div>
            </section>

            {selectedItem && (
              <MatchInspector
                edge={selectedItem.edge}
                name={selectedItem.label}
                detail={typeof selectedItem.edge.semantic_score === "number" ? selectedItem.edge : null}
                otherMatches={otherMatches}
                jobId={jobId}
                selectState={
                  selected.has(selectedItem.edge.match_id)
                    ? "selected"
                    : selectState.matchId === selectedItem.edge.match_id
                      ? selectState.state
                      : "idle"
                }
                selectError={selectState.matchId === selectedItem.edge.match_id ? selectState.error : null}
                onSelectForJob={handleSelectForJob}
              />
            )}
          </div>
        </>
      )}

      {!loading && !error && graph && graph.edges.length > 0 && view === "overview" && (
        <MatchMatrix graph={graph} names={names} onOpen={(job, candidate) => update({ view: null, job, candidate })} />
      )}

      {!loading && !error && graph && graph.edges.length > 0 && view === "3d" && (
        <div className={styles.focusLayout}>
          <section className={styles.graphCard} aria-label="3D landscape">
            <div className={styles.chips}>
              <label className={styles.jobPicker}>
                <span>Highlight job</span>
                <select value={focus3d ?? ""} onChange={(event) => setFocus3d(event.target.value || null)}>
                  <option value="">All jobs</option>
                  {landscapeJobs.map((id) => (
                    <option key={id} value={id}>
                      {jobRows.find((j) => j.job_id === id)?.title}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className={styles.sceneWrap}>
              <SceneErrorBoundary>
                <Suspense fallback={<p className={styles.status}>Loading 3D scene…</p>}>
                  <Landscape3D
                    ref={landscapeRef}
                    graph={graph}
                    names={names}
                    jobIds={landscapeJobs}
                    focusJobId={focus3d}
                    selectedCandidateId={candidateParam}
                    onSelectCandidate={(id) => update({ candidate: id })}
                  />
                </Suspense>
              </SceneErrorBoundary>
              <div className={styles.sceneButtons}>
                <button type="button" aria-label="Rotate left" onClick={() => landscapeRef.current?.rotate(-30)}>
                  <RotateIcon />
                </button>
                <button type="button" aria-label="Rotate right" onClick={() => landscapeRef.current?.rotate(30)}>
                  <RotateIcon mirrored />
                </button>
                <button type="button" onClick={() => landscapeRef.current?.reset()}>
                  Reset view
                </button>
              </div>
              <div className={styles.sceneLegend}>
                <span className={styles.legendItem}>
                  <span className={styles.keyQualified} aria-hidden="true" />
                  Qualified
                </span>
                <span className={styles.legendItem}>
                  <span className={styles.keyGate3d} aria-hidden="true" />
                  Missing required skills
                </span>
                <span className={styles.legendItem}>
                  <span className={styles.keyBelow} aria-hidden="true" />
                  Below the threshold
                </span>
                <span className={styles.legendNote}>Height = final score · drag to orbit, scroll to zoom</span>
              </div>
            </div>
            <p className={styles.sceneNote}>Showing the {landscapeJobs.length} jobs with the most qualified candidates.</p>
          </section>

          <CandidateMatches graph={graph} names={names} candidateId={candidateParam} onOpen={(j) => update({ view: null, job: j })} />
        </div>
      )}
    </div>
  );
}

function CandidateMatches({ graph, names, candidateId, onOpen }) {
  const titles = new Map(graph.jobs.map((j) => [j.job_id, j.title]));
  const matches = candidateId
    ? graph.edges.filter((e) => e.candidate_id === candidateId).sort((a, b) => b.final_score - a.final_score)
    : [];
  return (
    <aside className={styles.sidePanel} aria-label="Selected candidate">
      <span className={styles.sideEyebrow}>Selected candidate</span>
      {candidateId ? (
        <>
          <span className={styles.sideName}>{names.get(candidateId) ?? "Unknown candidate"}</span>
          <span className={styles.sideSummary}>
            {matches.length} matches, {matches.filter((m) => m.qualified).length} qualified.
          </span>
          <ul className={styles.sideList}>
            {matches.map((m) => {
              const ring = ringFor(m);
              return (
                <li key={m.match_id}>
                  <button type="button" onClick={() => onOpen(m.job_id)}>
                    <span className={styles.sideJob}>
                      {titles.get(m.job_id)}
                      <span className={styles[`tag_${ring}`]}>
                        {ring === "qualified" ? "Qualified" : ring === "gate" ? "Missing required skills" : "Below the threshold"}
                      </span>
                    </span>
                    <span className={styles.sideScore}>{percent(m.final_score)}%</span>
                  </button>
                </li>
              );
            })}
          </ul>
          <Link className={styles.primaryLink} to={`/candidates/${candidateId}`}>
            Open profile
          </Link>
        </>
      ) : (
        <p className={styles.sideSummary}>Select a pin to see that person's matches. The same person standing tall next to several jobs is a strong, versatile profile.</p>
      )}
    </aside>
  );
}

function RotateIcon({ mirrored }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={mirrored ? { transform: "scaleX(-1)" } : undefined}>
      <path d="M4 12a8 8 0 1 0 2.3-5.7" />
      <path d="M4 4v4h4" />
    </svg>
  );
}
