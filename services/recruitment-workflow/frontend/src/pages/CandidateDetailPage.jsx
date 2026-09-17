import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getCandidateKnowledge } from "../api/candidates.js";
import { getAllApplications } from "../api/applications.js";
import { getCandidatesJobsGraph } from "../api/graph.js";
import { ApiError } from "../api/client.js";
import { useCandidateIdentity } from "../hooks/useCandidateIdentity.js";
import PageHeader from "../components/PageHeader.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import EmptyState from "../components/EmptyState.jsx";
import MatchGraph, { EGO_GRAPH_LAYOUT } from "../components/MatchGraph.jsx";
import { STATUS_LABELS, STATUS_TONE } from "../constants/applicationStatus.js";
import { ROUND_TYPE_LABELS, roundStatusLabel, roundStatusTone, describeRound } from "../constants/interviewRounds.js";
import styles from "./CandidateDetailPage.module.css";

const INTERVIEW_STATUS_LABELS = {
  completed: "Completed",
  inconclusive: "Inconclusive",
  failed: "Failed",
};

/**
 * One candidate's identity + professional knowledge + every application they have
 * across every job -- the cross-linking hub every other list page's candidate name
 * now points to (CandidatesPage, JobDetailPage, InterviewsPage, ReportsPage).
 *
 * Three independent reads: identity reuses the existing useCandidateIdentity hook
 * (its own cache + failure handling, not re-implemented here -- "avoid duplicate
 * API logic"), knowledge and applications are fetched locally with their own
 * loading/error state so a failure in one section never blanks the others. Reused
 * everywhere: StatusBadge, PageHeader, EmptyState, the shared status/tone constants.
 */
export default function CandidateDetailPage() {
  const { candidateId } = useParams();
  const identity = useCandidateIdentity(candidateId);

  const [knowledge, setKnowledge] = useState(null);
  const [knowledgeLoading, setKnowledgeLoading] = useState(true);
  const [knowledgeError, setKnowledgeError] = useState(null);

  const [applications, setApplications] = useState([]);
  const [applicationsLoading, setApplicationsLoading] = useState(true);
  const [applicationsError, setApplicationsError] = useState(null);

  // { jobs, edges } from GET /v1/graph/candidates-jobs, filtered client-side to this
  // one candidate -- see loadEgoGraph() below.
  const [egoGraph, setEgoGraph] = useState(null);
  const [egoGraphLoading, setEgoGraphLoading] = useState(true);
  const [egoGraphError, setEgoGraphError] = useState(null);

  useEffect(() => {
    loadKnowledge();
    loadApplications();
    loadEgoGraph();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidateId]);

  async function loadKnowledge() {
    setKnowledgeLoading(true);
    setKnowledgeError(null);
    try {
      const response = await getCandidateKnowledge(candidateId);
      setKnowledge(response);
    } catch (err) {
      setKnowledgeError(err instanceof ApiError ? err.message : "Unable to load skills.");
    } finally {
      setKnowledgeLoading(false);
    }
  }

  async function loadApplications() {
    setApplicationsLoading(true);
    setApplicationsError(null);
    try {
      // GET /v1/applications is already global/unscoped -- filtered here to this
      // one candidate rather than adding a new backend endpoint for it (no
      // candidate_id query param exists, and this project's own rule is to name a
      // concrete scale problem before adding one, not build ahead of it).
      const response = await getAllApplications();
      setApplications(response.filter((application) => application.candidate_id === candidateId));
    } catch (err) {
      setApplicationsError(err instanceof ApiError ? err.message : "Unable to load applications.");
    } finally {
      setApplicationsLoading(false);
    }
  }

  async function loadEgoGraph() {
    setEgoGraphLoading(true);
    setEgoGraphError(null);
    try {
      // Same GET /v1/graph/candidates-jobs GraphPage (/graph) already calls -- no
      // new backend endpoint for this candidate's own "ego graph". Filtered
      // client-side to just this candidate's edges, and to only the jobs those
      // edges reference, same "filter an existing unscoped read" pattern this
      // project already uses for GET /v1/applications above.
      const graph = await getCandidatesJobsGraph();
      const edges = graph.edges.filter((edge) => edge.candidate_id === candidateId);
      const jobIds = new Set(edges.map((edge) => edge.job_id));
      const jobs = graph.jobs.filter((job) => jobIds.has(job.job_id));
      setEgoGraph({ jobs, edges });
    } catch (err) {
      setEgoGraphError(err instanceof ApiError ? err.message : "Unable to load the knowledge graph.");
    } finally {
      setEgoGraphLoading(false);
    }
  }

  const displayName =
    identity.status === "ready"
      ? identity.name
      : identity.status === "loading"
        ? "Loading…"
        : `Candidate ${candidateId.slice(0, 8)}…`;

  // MatchGraph resolves a candidate node's label from this map -- reuses the name
  // already fetched by useCandidateIdentity above instead of a second GET
  // /v1/candidates call (GraphPage's own name source) for a graph of just one
  // candidate.
  const candidateNames = useMemo(() => new Map([[candidateId, displayName]]), [candidateId, displayName]);

  return (
    <div className={styles.page}>
      <PageHeader
        title={displayName}
        subtitle={identity.status === "ready" ? identity.email ?? undefined : undefined}
        variant="glass"
      />

      {identity.status === "error" && (
        <div className={styles.error} role="alert">
          Unable to load this candidate's identity. They may no longer exist in
          Resume Intelligence.
        </div>
      )}

      <div className={styles.content}>
        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>Professional knowledge</h2>
          {knowledgeLoading && <p className={styles.status}>Loading…</p>}
          {!knowledgeLoading && knowledgeError && (
            <div className={styles.error} role="alert">
              {knowledgeError}
            </div>
          )}
          {!knowledgeLoading && !knowledgeError && knowledge && (
            <div className={styles.knowledgeGrid}>
              <div>
                <span className={styles.fieldLabel}>Years of experience</span>
                <p className={styles.fieldValueAccent}>
                  {knowledge.years_of_experience != null ? knowledge.years_of_experience : "—"}
                </p>
              </div>
              <div>
                <span className={styles.fieldLabel}>Job titles</span>
                {knowledge.job_titles.length > 0 ? (
                  <div className={styles.chips}>
                    {knowledge.job_titles.map((title) => (
                      <span key={title} className={styles.chipAccent}>
                        {title}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className={styles.fieldValue}>—</p>
                )}
              </div>
              <div className={styles.skillsField}>
                <span className={styles.fieldLabel}>Skills</span>
                {knowledge.skills.length > 0 ? (
                  <div className={styles.chips}>
                    {knowledge.skills.map((skill) => (
                      <span key={skill} className={styles.chip}>
                        {skill}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className={styles.fieldValue}>—</p>
                )}
              </div>
            </div>
          )}
        </section>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>Applications</h2>
          {applicationsLoading && <p className={styles.status}>Loading…</p>}
          {!applicationsLoading && applicationsError && (
            <div className={styles.error} role="alert">
              {applicationsError}
            </div>
          )}
          {!applicationsLoading && !applicationsError && applications.length === 0 && (
            <EmptyState
              variant="glass"
              title="No applications yet"
              description="This candidate hasn't been selected for any job yet."
            />
          )}
          {!applicationsLoading && !applicationsError && applications.length > 0 && (
            <div className={styles.tableWrapper}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Job</th>
                    <th>Status</th>
                    <th>Interview</th>
                    <th>Interview rounds</th>
                    <th aria-label="Report" />
                  </tr>
                </thead>
                <tbody>
                  {applications.map((application) => (
                    <tr key={application.application_id}>
                      <td>
                        <Link to={`/jobs/${application.job_id}`} className={styles.inlineLink}>
                          {application.job_title}
                        </Link>
                      </td>
                      <td>
                        <StatusBadge variant="glass" tone={STATUS_TONE[application.status] ?? "neutral"}>
                          {STATUS_LABELS[application.status] ?? application.status}
                        </StatusBadge>
                      </td>
                      <td>
                        {application.interview_status ? (
                          INTERVIEW_STATUS_LABELS[application.interview_status] ?? application.interview_status
                        ) : (
                          <span className={styles.interviewStatusEmpty}>—</span>
                        )}
                      </td>
                      <td className={styles.roundsCell}>
                        {(application.interview_rounds ?? []).length === 0 ? (
                          <span className={styles.interviewStatusEmpty}>—</span>
                        ) : (
                          <div className={styles.roundsList}>
                            {application.interview_rounds.map((round) => (
                              <StatusBadge
                                key={round.id}
                                variant="glass"
                                tone={roundStatusTone(round)}
                                title={describeRound(round)}
                              >
                                {ROUND_TYPE_LABELS[round.round_type] ?? round.round_type}: {roundStatusLabel(round)}
                              </StatusBadge>
                            ))}
                          </div>
                        )}
                      </td>
                      <td>
                        {application.interview_assessment != null && (
                          <Link
                            to={`/reports/${application.application_id}`}
                            className={styles.inlineLink}
                          >
                            View Report
                          </Link>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>Knowledge Graph</h2>
          {egoGraphLoading && <p className={styles.status}>Loading…</p>}
          {!egoGraphLoading && egoGraphError && (
            <div className={styles.error} role="alert">
              {egoGraphError}
            </div>
          )}
          {!egoGraphLoading && !egoGraphError && egoGraph && egoGraph.edges.length === 0 && (
            <EmptyState
              variant="glass"
              title="No job matches yet"
              description="This candidate has no persisted matches against any job yet."
            />
          )}
          {!egoGraphLoading && !egoGraphError && egoGraph && egoGraph.edges.length > 0 && (
            // MatchGraph.jsx's own buildStylesheet() now reads --v2-* tokens
            // directly (V2 design system, final slice -- see
            // FRONTEND_HANDOFF.md) since both of its real call sites (here and
            // GraphPage) are on V2 as of this same pass -- no theme prop needed
            // here, this call is unchanged from before that migration.
            <MatchGraph
              rawGraph={egoGraph}
              candidateNames={candidateNames}
              layout={EGO_GRAPH_LAYOUT}
              showResetButton
              size="ego"
            />
          )}
        </section>
      </div>
    </div>
  );
}
