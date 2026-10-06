import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getCandidateKnowledge } from "../api/candidates.js";
import { getAllApplications } from "../api/applications.js";
import { getCandidatesJobsGraph } from "../api/graph.js";
import { ApiError } from "../api/client.js";
import { useCandidateIdentity } from "../hooks/useCandidateIdentity.js";
import StatusBadge from "../components/StatusBadge.jsx";
import { coverageOf, ringFor } from "../components/graph/graphModel.js";
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
 * everywhere: StatusBadge and the shared status/tone constants.
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

  // This candidate's matches, best first, each with the reason for its result -- read
  // off the persisted flags and requirement lists, never recomputed.
  const matches = useMemo(() => {
    if (!egoGraph) return [];
    const titles = new Map(egoGraph.jobs.map((job) => [job.job_id, job.title]));
    return [...egoGraph.edges]
      .sort((a, b) => b.final_score - a.final_score)
      .map((edge) => {
        const ring = ringFor(edge);
        const { matched, total, ratio } = coverageOf(edge);
        const missing = edge.missing_requirements;
        const reason =
          ring === "qualified"
            ? total
              ? `${matched} of ${total} required skills covered`
              : "No required skill listed"
            : ring === "gate"
              ? `Missing ${missing.slice(0, 3).join(", ")}${missing.length > 3 ? "…" : ""} · coverage ${ratio?.toFixed(2) ?? "—"}`
              : "Below the score threshold";
        return { edge, ring, reason, title: titles.get(edge.job_id) ?? "Job" };
      });
  }, [egoGraph]);
  const bestMatch = matches[0]?.edge ?? null;
  const initials = displayName
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
  const RESULT = {
    qualified: { label: "Qualified", tone: "success" },
    gate: { label: "Missing required skills", tone: "warning" },
    below: { label: "Below threshold", tone: "neutral" },
  };

  return (
    <div className={styles.page}>
      <Link to="/candidates" className={styles.backLink}>
        Back to candidates
      </Link>
      <header className={styles.header}>
        <div className={styles.who}>
          <span className={styles.avatar} aria-hidden="true">
            {initials || "?"}
          </span>
          <div className={styles.whoText}>
            <h1 className={styles.title}>{displayName}</h1>
            <span className={styles.meta}>
              {[
                knowledge?.job_titles?.[0],
                knowledge?.years_of_experience != null ? `${knowledge.years_of_experience} years of experience` : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </div>
        </div>
        {bestMatch && (
          <Link to={`/graph?job=${bestMatch.job_id}&candidate=${candidateId}`} className={styles.secondaryAction}>
            Show in knowledge graph
          </Link>
        )}
      </header>

      {identity.status === "error" && (
        <div className={styles.error} role="alert">
          Unable to load this candidate's identity. They may no longer exist in Resume Intelligence.
        </div>
      )}

      <div className={styles.layout}>
        <div className={styles.mainCol}>
          <section className={styles.card} aria-label="Job matches">
            <h2 className={styles.cardTitle}>Job matches</h2>
            {egoGraphLoading && <p className={styles.status}>Loading…</p>}
            {!egoGraphLoading && egoGraphError && (
              <div className={styles.error} role="alert">
                {egoGraphError}
              </div>
            )}
            {!egoGraphLoading && !egoGraphError && matches.length === 0 && (
              <p className={styles.status}>This candidate has no stored match against any job yet.</p>
            )}
            {matches.length > 0 && (
              <ul className={styles.matchList}>
                {matches.map(({ edge, ring, reason, title }) => (
                  <li key={edge.match_id}>
                    <Link to={`/graph?job=${edge.job_id}&candidate=${candidateId}`} className={styles.matchRow}>
                      <span className={styles.matchText}>
                        <span className={styles.matchTitle}>{title}</span>
                        <span className={ring === "gate" ? styles.reasonWarn : styles.reason}>{reason}</span>
                      </span>
                      <StatusBadge tone={RESULT[ring].tone}>{RESULT[ring].label}</StatusBadge>
                      <span className={styles.matchScore}>{Math.round(edge.final_score * 100)}%</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className={styles.card} aria-label="Applications">
            <h2 className={styles.cardTitle}>Applications</h2>
            {applicationsLoading && <p className={styles.status}>Loading…</p>}
            {!applicationsLoading && applicationsError && (
              <div className={styles.error} role="alert">
                {applicationsError}
              </div>
            )}
            {!applicationsLoading && !applicationsError && applications.length === 0 && (
              <p className={styles.status}>Not selected for any job yet.</p>
            )}
            {applications.map((application) => (
              <div key={application.application_id} className={styles.appRow}>
                <Link to={`/jobs/${application.job_id}/applications`} className={styles.appJob}>
                  {application.job_title}
                </Link>
                <StatusBadge tone={STATUS_TONE[application.status] ?? "neutral"}>
                  {STATUS_LABELS[application.status] ?? application.status}
                </StatusBadge>
                <span className={styles.appMeta}>
                  {application.interview_status
                    ? `Interview ${(INTERVIEW_STATUS_LABELS[application.interview_status] ?? application.interview_status).toLowerCase()}`
                    : "No interview yet"}
                  {(application.interview_rounds ?? []).length > 0 &&
                    ` · ${application.interview_rounds.length} human round${application.interview_rounds.length === 1 ? "" : "s"}`}
                </span>
                <span className={styles.appRounds}>
                  {(application.interview_rounds ?? []).map((round) => (
                    <StatusBadge key={round.id} tone={roundStatusTone(round)} title={describeRound(round)}>
                      {ROUND_TYPE_LABELS[round.round_type] ?? round.round_type}: {roundStatusLabel(round)}
                    </StatusBadge>
                  ))}
                </span>
                {application.interview_assessment != null && (
                  <Link to={`/reports/${application.application_id}`} className={styles.inlineLink}>
                    Open report
                  </Link>
                )}
              </div>
            ))}
          </section>
        </div>

        <aside className={styles.sideCol}>
          <section className={styles.card} aria-label="Professional knowledge">
            <h2 className={styles.cardTitle}>Professional knowledge</h2>
            {knowledgeLoading && <p className={styles.status}>Loading…</p>}
            {!knowledgeLoading && knowledgeError && (
              <div className={styles.error} role="alert">
                {knowledgeError}
              </div>
            )}
            {!knowledgeLoading && !knowledgeError && knowledge && (
              <>
                <div className={styles.field}>
                  <span className={styles.fieldLabel}>Job titles</span>
                  <span className={styles.fieldValue}>{knowledge.job_titles.length > 0 ? knowledge.job_titles.join(" · ") : "—"}</span>
                </div>
                <div className={styles.field}>
                  <span className={styles.fieldLabel}>Years of experience</span>
                  <span className={styles.fieldValueMono}>{knowledge.years_of_experience ?? "—"}</span>
                </div>
                <div className={styles.field}>
                  <span className={styles.fieldLabel}>Skills · {knowledge.skills.length}</span>
                  <div className={styles.chips}>
                    {knowledge.skills.map((skill) => (
                      <span key={skill} className={styles.chip}>
                        {skill}
                      </span>
                    ))}
                    {knowledge.skills.length === 0 && <span className={styles.fieldValue}>—</span>}
                  </div>
                </div>
              </>
            )}
          </section>

          <section className={styles.card} aria-label="Contact">
            <h2 className={styles.cardTitle}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <rect x="5" y="10" width="14" height="10" rx="2" />
                <path d="M8 10V7a4 4 0 0 1 8 0v3" />
              </svg>
              Contact
            </h2>
            <span className={styles.fieldValue}>{identity.status === "ready" ? identity.email ?? "No email on file" : "—"}</span>
            <span className={styles.note}>
              Read live from Resume Intelligence each time. This service stores only the opaque candidate id.
            </span>
          </section>
        </aside>
      </div>
    </div>
  );
}
