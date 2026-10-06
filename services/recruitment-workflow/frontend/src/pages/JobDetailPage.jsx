import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getJobs, getJobMatches, setJobLanguageRequirement } from "../api/jobs.js";
import { getApplications, selectMatch } from "../api/applications.js";
import { ApiError } from "../api/client.js";
import { useCandidateIdentity } from "../hooks/useCandidateIdentity.js";
import PageHeader from "../components/PageHeader.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import EmptyState from "../components/EmptyState.jsx";
import { CEFR_LEVELS, LANGUAGE_LABELS, formatLanguageRequirement } from "../constants/language.js";
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
  const [selectedMatchIds, setSelectedMatchIds] = useState(new Set()); // already selected, from GET applications
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

    // Also best-effort: which matches already have an application, so their row says
    // "Selected" from the start instead of offering a selection RWS would refuse (409).
    getApplications(jobId)
      .then((applications) => setSelectedMatchIds(new Set(applications.map((app) => app.match_result_id))))
      .catch(() => setSelectedMatchIds(new Set()));

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

  const qualifiedCount = matches.filter((match) => match.qualified).length;

  return (
    <div className={styles.page}>
      <Link to="/jobs" className={styles.backLink}>
        Back to jobs
      </Link>
      <PageHeader
        title={job ? job.title : "Job detail"}
        subtitle={
          job &&
          `${job.match_count} matched · ${qualifiedCount} qualified · posted ${new Date(job.created_at).toLocaleDateString(
            undefined,
            { year: "numeric", month: "short", day: "numeric" },
          )}`
        }
        actions={
          <>
            <Link to={`/graph?job=${jobId}`} className={styles.secondaryAction}>
              See in graph
            </Link>
            <Link to={`/jobs/${jobId}/applications`} className={styles.primaryAction}>
              Applications
            </Link>
          </>
        }
      />

      {job && <LanguageRequirementSection job={job} onUpdated={setJob} />}

      {job && job.extraction_status === "flagged" && (
        <div className={styles.extractionWarning} role="alert">
          {/* Deliberately generic: "flagged" can mean the extraction itself looked
              suspicious (jd_validation.py) or that matching produced a suspicious
              outcome despite a clean extraction (match_anomaly.py) -- the specific
              reason is always in the warning list below, so the intro must not
              claim a cause ("extraction looks suspicious") that isn't always true. */}
          <strong>This job needs a second look.</strong> Matching still ran normally, but
          you may want to review the details below.
          <ul>
            {job.extraction_warnings.map((warning, index) => (
              <li key={index}>{warning.message}</li>
            ))}
          </ul>
        </div>
      )}

      {job && job.extraction_status === "repaired" && (
        <div className={styles.extractionRepairedNote}>
          Gemini's first extraction of this job description looked suspicious; an automatic
          second attempt fixed it and passed validation. No action needed.
        </div>
      )}

      {loading && <p className={styles.status}>Loading candidates…</p>}

      {!loading && error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}

      {!loading && !error && matches.length === 0 && (
        <EmptyState
          variant="glass"
          title="No candidates matched"
          description="This job has no matched candidates yet."
        />
      )}

      {!loading && !error && matches.length > 0 && (
        <div className={styles.tableWrapper}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.rankCol}>#</th>
                <th>Candidate</th>
                <th>Final score</th>
                <th>Required skills</th>
                <th>Result</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {matches.map((match, index) => (
                <CandidateRow
                  key={match.match_id}
                  match={match}
                  rank={index + 1}
                  alreadySelected={selectedMatchIds.has(match.match_id)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// The three outcomes, read off the persisted flags only (same rule as the graph).
function resultOf(match) {
  if (match.qualified) return { label: "Qualified", tone: "success" };
  if (!match.hard_gate_passed) return { label: "Missing required skills", tone: "warning" };
  return { label: "Below threshold", tone: "neutral" };
}

function CandidateRow({ match, rank, alreadySelected }) {
  const identity = useCandidateIdentity(match.candidate_id);
  const [expanded, setExpanded] = useState(false);
  const [selectState, setSelectStatus] = useState("idle"); // idle | selecting | selected | error
  const selectStatus = alreadySelected ? "selected" : selectState;
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

  const result = resultOf(match);
  const matchedCount = match.matched_requirements?.length ?? 0;
  const requiredCount = matchedCount + (match.missing_requirements?.length ?? 0);
  const coverage = requiredCount ? matchedCount / requiredCount : null;
  const detailId = `match-detail-${match.match_id}`;

  return (
    <>
      <tr className={`${styles.row} ${expanded ? styles.rowOpen : ""}`}>
        <td className={`${styles.rankCol} ${styles.mono}`}>{rank}</td>
        <td>
          <div className={styles.nameCell}>
            <button
              type="button"
              className={styles.expandButton}
              onClick={() => setExpanded((value) => !value)}
              aria-expanded={expanded}
              aria-controls={detailId}
              aria-label={`${expanded ? "Hide" : "Show"} the score details of ${displayName}`}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M9 6l6 6-6 6" />
              </svg>
            </button>
            <Link to={`/candidates/${match.candidate_id}`} className={styles.candidateName}>
              {displayName}
            </Link>
            {identity.status === "error" && <span className={styles.identityWarning}>(name unavailable)</span>}
          </div>
        </td>
        <td>
          <ScoreBar value={match.final_score} qualified={match.qualified} />
        </td>
        <td className={`${styles.mono} ${match.hard_gate_passed ? styles.coverageOk : styles.coverageLow}`}>
          {coverage === null ? "—" : `${matchedCount}/${requiredCount} · ${coverage.toFixed(2)}`}
        </td>
        <td>
          <StatusBadge tone={result.tone}>{result.label}</StatusBadge>
        </td>
        <td className={styles.actionCell}>
          {match.qualified &&
            (selectStatus === "selected" ? (
              <span className={styles.selectedLabel}>Selected</span>
            ) : (
              <button type="button" className={styles.selectButton} onClick={handleSelect} disabled={selectStatus === "selecting"}>
                {selectStatus === "selecting" ? "Selecting…" : "Select"}
              </button>
            ))}
        </td>
      </tr>

      {selectError && (
        <tr>
          <td colSpan={6}>
            <div className={styles.error} role="alert">
              {selectError}
            </div>
          </td>
        </tr>
      )}

      {expanded && (
        <tr className={styles.detailRow} id={detailId}>
          <td colSpan={6}>
            <div className={styles.detail}>
              <div className={styles.detailBlock}>
                <span className={styles.detailTitle}>Score components</span>
                <ScoreDetail label="Semantic" value={match.semantic_score} light />
                <ScoreDetail label="Deterministic" value={match.deterministic_score} />
                <ScoreDetail label="· Skills" value={match.skill_score} small />
                <ScoreDetail label="· Experience" value={match.experience_score} small />
                <ScoreDetail label="· Title" value={match.title_score} small />
              </div>

              <div className={styles.detailBlock}>
                <span className={styles.detailTitle}>Required skills</span>
                <div className={styles.chips}>
                  {match.matched_requirements.map((item) => (
                    <span key={`m-${item}`} className={styles.chipMatched}>
                      {item}
                    </span>
                  ))}
                  {match.missing_requirements.map((item) => (
                    <span key={`x-${item}`} className={styles.chipMissing}>
                      {item}
                    </span>
                  ))}
                  {requiredCount === 0 && <span className={styles.muted}>This job lists no required skill.</span>}
                </div>
                <span className={styles.muted}>
                  {match.hard_gate_passed ? "The required-skill gate passed." : "The required-skill gate failed, so this match cannot qualify."}
                </span>
              </div>

              <div className={`${styles.detailBlock} ${styles.detailWide}`}>
                <span className={styles.detailTitle}>
                  AI explanation <span className={styles.advisory}>advisory, written after the score</span>
                </span>
                <p className={styles.explanationText}>
                  {match.explanation ?? "Not generated yet. Only a few are written per visit, so it appears on a later visit to this page."}
                </p>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

/**
 * Displays (and lets a recruiter configure) this job's optional VoiceHR language
 * eligibility requirement -- see RWS's language_gate.py. Editing here only ever
 * calls PATCH /v1/jobs/{id}/language-requirement; it never touches any Application
 * already selected for this job, each of which keeps its own selection-time
 * snapshot regardless of what this section is later changed to.
 */
function LanguageRequirementSection({ job, onUpdated }) {
  const [editing, setEditing] = useState(false);
  const [language, setLanguage] = useState(job.required_language ?? "en");
  const [cefr, setCefr] = useState(job.min_language_cefr ?? "B2");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);

  const requirementLabel = formatLanguageRequirement(job.required_language, job.min_language_cefr);

  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await setJobLanguageRequirement(job.job_id, {
        required_language: language,
        min_language_cefr: cefr,
      });
      onUpdated((current) => (current ? { ...current, ...updated } : updated));
      setEditing(false);
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Failed to update requirement.");
    } finally {
      setSaving(false);
    }
  }

  async function handleClear() {
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await setJobLanguageRequirement(job.job_id, {
        required_language: null,
        min_language_cefr: null,
      });
      onUpdated((current) => (current ? { ...current, ...updated } : updated));
      setEditing(false);
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Failed to clear requirement.");
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    return (
      <div className={styles.languageRequirement}>
        <span>
          {requirementLabel ? `Required language: ${requirementLabel}` : "No language requirement configured"}
        </span>
        <button type="button" className={styles.languageRequirementEditButton} onClick={() => setEditing(true)}>
          {requirementLabel ? "Edit" : "Set requirement"}
        </button>
      </div>
    );
  }

  return (
    <div className={styles.languageRequirement}>
      <select value={language} onChange={(event) => setLanguage(event.target.value)} disabled={saving}>
        {Object.entries(LANGUAGE_LABELS).map(([code, label]) => (
          <option key={code} value={code}>
            {label}
          </option>
        ))}
      </select>
      <select value={cefr} onChange={(event) => setCefr(event.target.value)} disabled={saving}>
        {CEFR_LEVELS.map((level) => (
          <option key={level} value={level}>
            {level}
          </option>
        ))}
      </select>
      <button type="button" className={styles.languageRequirementEditButton} onClick={handleSave} disabled={saving}>
        {saving ? "Saving…" : "Save"}
      </button>
      {requirementLabel && (
        <button type="button" className={styles.languageRequirementEditButton} onClick={handleClear} disabled={saving}>
          Clear
        </button>
      )}
      <button
        type="button"
        className={styles.languageRequirementEditButton}
        onClick={() => setEditing(false)}
        disabled={saving}
      >
        Cancel
      </button>
      {saveError && (
        <div className={styles.error} role="alert">
          {saveError}
        </div>
      )}
    </div>
  );
}

function ScoreBar({ value, qualified }) {
  const percent = Math.round(value * 100);
  return (
    <div className={styles.scoreBarWrapper} aria-label={`Score: ${percent}%`}>
      <div className={styles.scoreBarTrack}>
        <div className={qualified ? styles.scoreBarFill : styles.scoreBarFillMuted} style={{ width: `${percent}%` }} />
      </div>
      <span className={styles.scoreBarLabel}>{percent}%</span>
    </div>
  );
}

function ScoreDetail({ label, value, light, small }) {
  const percent = Math.round(value * 100);
  return (
    <div className={`${styles.scoreDetailRow} ${small ? styles.scoreDetailSmall : ""}`}>
      <span>{label}</span>
      <span className={styles.miniTrack}>
        <span className={light ? styles.miniFillLight : styles.miniFill} style={{ width: `${percent}%` }} />
      </span>
      <span className={styles.mono}>{percent}</span>
    </div>
  );
}
