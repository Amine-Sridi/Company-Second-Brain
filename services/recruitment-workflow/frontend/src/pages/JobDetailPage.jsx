import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getJobs, getJobMatches, setJobLanguageRequirement } from "../api/jobs.js";
import { selectMatch } from "../api/applications.js";
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
        variant="glass"
      />

      {job && <LanguageRequirementSection job={job} onUpdated={setJob} />}

      {job && job.extraction_status === "flagged" && (
        <div className={styles.extractionWarning} role="alert">
          <strong>This job description's extraction looks suspicious.</strong> Matching still
          ran normally, but you may want to review the requirements below.
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
          <Link
            to={`/candidates/${match.candidate_id}`}
            className={styles.candidateName}
            onClick={(event) => event.stopPropagation()}
          >
            {displayName}
          </Link>
          {identity.status === "error" && (
            <span className={styles.identityWarning}> (name unavailable)</span>
          )}
        </td>
        <td>
          <ScoreBar value={match.final_score} />
        </td>
        <td>
          <StatusBadge variant="glass" tone={match.qualified ? "success" : "neutral"}>
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
