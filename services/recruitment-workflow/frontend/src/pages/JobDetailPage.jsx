import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getJobs, getJobMatches } from "../api/jobs.js";
import { selectMatch } from "../api/applications.js";
import { ApiError } from "../api/client.js";
import { useCandidateIdentity } from "../hooks/useCandidateIdentity.js";
import PageHeader from "../components/PageHeader.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import EmptyState from "../components/EmptyState.jsx";
import styles from "./JobDetailPage.module.css";

/**
 * The ranked-candidates view -- the core recruiter screen. Every score/qualification
 * field is rendered exactly as RWS returns it (MatchResultResponse); nothing here
 * recomputes, adjusts, or re-ranks (see FRONTEND_HANDOFF.md §11).
 */
export default function JobDetailPage() {
  const { jobId } = useParams();

  const [job, setJob] = useState(null); // best-effort header info from GET /v1/jobs
  const [matches, setMatches] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    loadJobDetail();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  async function loadJobDetail() {
    setLoading(true);
    setError(null);
    setJob(null);

    // Job header (title/created_at/match_count) has no dedicated GET /v1/jobs/{id}
    // endpoint on RWS -- only the list endpoint carries it. This is best-effort: if it
    // fails, the page still renders the matches table below with a generic heading,
    // rather than failing the whole page over a decoration.
    getJobs()
      .then((jobs) => setJob(jobs.find((j) => j.job_id === jobId) ?? null))
      .catch(() => setJob(null));

    // The matches call is the page's real data and the real source of "does this job
    // exist" -- GET /v1/jobs/{job_id}/matches 404s if it doesn't. Always fetched fresh
    // here (never from router/navigation state), so a hard browser refresh on this URL
    // still works correctly from backend state alone.
    try {
      const response = await getJobMatches(jobId);
      setMatches(response);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load candidates.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={styles.page}>
      <PageHeader
        title={job ? job.title : "Job Detail"}
        subtitle={
          job &&
          `${job.match_count} candidate${job.match_count === 1 ? "" : "s"} matched · posted ${new Date(
            job.created_at
          ).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })}`
        }
        actions={
          <Link to={`/jobs/${jobId}/applications`} className={styles.applicationsLink}>
            View Applications
          </Link>
        }
      />

      {loading && <p className={styles.status}>Loading candidates…</p>}

      {!loading && error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}

      {!loading && !error && matches.length === 0 && (
        <EmptyState
          title="No candidates matched"
          description="This job has no matched candidates yet."
        />
      )}

      {!loading && !error && matches.length > 0 && (
        <div className={styles.tableWrapper}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Candidate</th>
                <th>Score</th>
                <th>Qualified</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {matches.map((match) => (
                <CandidateRow key={match.match_id} match={match} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function CandidateRow({ match }) {
  const identity = useCandidateIdentity(match.candidate_id);
  const [expanded, setExpanded] = useState(false);
  const [selectStatus, setSelectStatus] = useState("idle"); // idle | selecting | selected | error
  const [selectError, setSelectError] = useState(null);

  const displayName =
    identity.status === "ready"
      ? identity.name
      : identity.status === "loading"
        ? "Loading…"
        : `Candidate ${match.candidate_id.slice(0, 8)}…`;

  async function handleSelect(event) {
    event.stopPropagation();
    if (selectStatus === "selecting" || selectStatus === "selected") return;
    setSelectStatus("selecting");
    setSelectError(null);
    try {
      // Real POST /v1/matches/{match_id}/select. RWS itself enforces the
      // qualification rule (422) and duplicate-selection rule (409) -- this button
      // being disabled for an unqualified match is a UX hint only, never a substitute
      // for the backend's own check.
      await selectMatch(match.match_id);
      setSelectStatus("selected");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Already selected (e.g. a previous session, or a concurrent selection) --
        // treat this as the same end state as a fresh success, since it reflects
        // real backend state, not a failure to show the recruiter.
        setSelectStatus("selected");
      } else {
        setSelectError(err instanceof ApiError ? err.message : "Failed to select candidate.");
        setSelectStatus("error");
      }
    }
  }

  return (
    <>
      <tr
        className={styles.row}
        onClick={() => setExpanded((value) => !value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            if (event.key === " ") event.preventDefault();
            setExpanded((value) => !value);
          }
        }}
        role="button"
        tabIndex={0}
      >
        <td>
          <span className={styles.candidateName}>{displayName}</span>
          {identity.status === "error" && (
            <span className={styles.identityWarning}> (name unavailable)</span>
          )}
        </td>
        <td>
          <ScoreBar value={match.final_score} />
        </td>
        <td>
          <StatusBadge tone={match.qualified ? "success" : "neutral"}>
            {match.qualified ? "Qualified" : "Not qualified"}
          </StatusBadge>
        </td>
        <td onClick={(event) => event.stopPropagation()}>
          {selectStatus === "selected" ? (
            <span className={styles.selectedLabel}>Selected ✓</span>
          ) : (
            <button
              type="button"
              className={styles.selectButton}
              onClick={handleSelect}
              disabled={!match.qualified || selectStatus === "selecting"}
              title={!match.qualified ? "Only qualified candidates can be selected" : undefined}
            >
              {selectStatus === "selecting" ? "Selecting…" : "Select"}
            </button>
          )}
        </td>
      </tr>

      {selectError && (
        <tr>
          <td colSpan={4}>
            <div className={styles.error} role="alert">
              {selectError}
            </div>
          </td>
        </tr>
      )}

      {expanded && (
        <tr>
          <td colSpan={4}>
            <div className={styles.detail}>
              <div className={styles.scoreBreakdown}>
                <ScoreDetail label="Deterministic score" value={match.deterministic_score} />
                <ScoreDetail label="Semantic score" value={match.semantic_score} />
                <ScoreDetail label="Skill score" value={match.skill_score} />
                <ScoreDetail label="Experience score" value={match.experience_score} />
                <ScoreDetail label="Title score" value={match.title_score} />
                <div className={styles.scoreDetailRow}>
                  <span>Hard gate passed</span>
                  <span>{match.hard_gate_passed ? "Yes" : "No"}</span>
                </div>
              </div>

              <RequirementChips label="Matched requirements" items={match.matched_requirements} tone="matched" />
              <RequirementChips label="Missing requirements" items={match.missing_requirements} tone="missing" />

              {match.explanation && (
                <div className={styles.explanation}>
                  <span className={styles.explanationLabel}>AI explanation</span>
                  <p>{match.explanation}</p>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function ScoreBar({ value }) {
  const percent = Math.round(value * 100);
  return (
    <div className={styles.scoreBarWrapper} aria-label={`Score: ${percent}%`}>
      <div className={styles.scoreBarTrack}>
        <div className={styles.scoreBarFill} style={{ width: `${percent}%` }} />
      </div>
      <span className={styles.scoreBarLabel}>{percent}%</span>
    </div>
  );
}

function ScoreDetail({ label, value }) {
  return (
    <div className={styles.scoreDetailRow}>
      <span>{label}</span>
      <span>{Math.round(value * 100)}%</span>
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
          <span
            key={item}
            className={tone === "matched" ? styles.chipMatched : styles.chipMissing}
          >
            {item}
          </span>
        ))}
      </div>
    </div>
  );
}
