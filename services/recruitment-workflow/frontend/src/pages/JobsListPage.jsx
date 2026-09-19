import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getJobs, createJob } from "../api/jobs.js";
import { ApiError } from "../api/client.js";
import PageHeader from "../components/PageHeader.jsx";
import EmptyState from "../components/EmptyState.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import { CEFR_LEVELS, LANGUAGE_LABELS, formatLanguageRequirement } from "../constants/language.js";
import styles from "./JobsListPage.module.css";

// Matches the backend's own defaults (schemas.py: JobSubmissionRequest) -- pre-filled,
// not invented by the frontend.
const DEFAULT_THRESHOLD = 0.6;
const DEFAULT_TOP_K = 20;

export default function JobsListPage() {
  const navigate = useNavigate();

  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState("");
  const [rawDescription, setRawDescription] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [threshold, setThreshold] = useState(String(DEFAULT_THRESHOLD));
  const [topK, setTopK] = useState(String(DEFAULT_TOP_K));
  const [requireLanguage, setRequireLanguage] = useState(false);
  const [requiredLanguage, setRequiredLanguage] = useState("en");
  const [minLanguageCefr, setMinLanguageCefr] = useState("B2");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState(null);

  useEffect(() => {
    loadJobs();
  }, []);

  async function loadJobs() {
    setLoading(true);
    setError(null);
    try {
      // Always re-fetched from RWS on mount -- this page never assumes stale local
      // state is still correct, so a manual browser reload or a return visit from
      // another page always reflects the backend's current state.
      const response = await getJobs();
      setJobs(response);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load jobs.");
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate(event) {
    event.preventDefault();
    if (!title.trim() || !rawDescription.trim() || creating) return;

    const parsedThreshold = Number(threshold);
    const parsedTopK = Number(topK);
    if (Number.isNaN(parsedThreshold) || Number.isNaN(parsedTopK)) {
      setCreateError("Threshold and max candidates must be numbers.");
      return;
    }

    setCreating(true);
    setCreateError(null);
    try {
      // Real POST /v1/jobs -- runs the full JD-structuring + retrieval + scoring
      // pipeline server-side. The frontend does not compute or preview any of that; it
      // only submits the raw text and renders whatever RWS returns.
      const job = await createJob({
        title: title.trim(),
        raw_description: rawDescription.trim(),
        threshold: parsedThreshold,
        top_k: parsedTopK,
        required_language: requireLanguage ? requiredLanguage : undefined,
        min_language_cefr: requireLanguage ? minLanguageCefr : undefined,
      });
      navigate(`/jobs/${job.job_id}`);
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : "Failed to create job.");
      setCreating(false);
    }
  }

  return (
    <div className={styles.page}>
      <PageHeader
        title="Jobs"
        subtitle="Post a job description and review the system's ranked candidates."
        actions={
          <button
            type="button"
            className={styles.newJobButton}
            onClick={() => setShowForm((visible) => !visible)}
          >
            {showForm ? "Cancel" : "New Job"}
          </button>
        }
        variant="glass"
      />

      {showForm && (
        <form className={styles.form} onSubmit={handleCreate}>
          <label className={styles.field}>
            <span>Job title</span>
            <input
              type="text"
              required
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              disabled={creating}
            />
          </label>

          <label className={styles.field}>
            <span>Job description</span>
            <textarea
              rows={8}
              required
              placeholder="Paste the raw job description here…"
              value={rawDescription}
              onChange={(event) => setRawDescription(event.target.value)}
              disabled={creating}
            />
          </label>

          <button
            type="button"
            className={styles.advancedToggle}
            onClick={() => setShowAdvanced((visible) => !visible)}
          >
            {showAdvanced ? "Hide advanced options" : "Advanced options"}
          </button>

          {showAdvanced && (
            <div className={styles.advancedFields}>
              <label className={styles.field}>
                <span>Qualification threshold (0–1)</span>
                <input
                  type="number"
                  min="0"
                  max="1"
                  step="0.05"
                  value={threshold}
                  onChange={(event) => setThreshold(event.target.value)}
                  disabled={creating}
                />
              </label>
              <label className={styles.field}>
                <span>Max candidates retrieved</span>
                <input
                  type="number"
                  min="1"
                  max="100"
                  step="1"
                  value={topK}
                  onChange={(event) => setTopK(event.target.value)}
                  disabled={creating}
                />
              </label>

              <label className={styles.languageRequirementToggle}>
                <input
                  type="checkbox"
                  checked={requireLanguage}
                  onChange={(event) => setRequireLanguage(event.target.checked)}
                  disabled={creating}
                />
                Require a VoiceHR language interview to pass a minimum level
              </label>

              {requireLanguage && (
                <div className={styles.field}>
                  <span>Required language and minimum CEFR level</span>
                  <div className={styles.languageRequirementRow}>
                    <select
                      value={requiredLanguage}
                      onChange={(event) => setRequiredLanguage(event.target.value)}
                      disabled={creating}
                    >
                      {Object.entries(LANGUAGE_LABELS).map(([code, label]) => (
                        <option key={code} value={code}>
                          {label}
                        </option>
                      ))}
                    </select>
                    <select
                      value={minLanguageCefr}
                      onChange={(event) => setMinLanguageCefr(event.target.value)}
                      disabled={creating}
                    >
                      {CEFR_LEVELS.map((level) => (
                        <option key={level} value={level}>
                          {level}
                        </option>
                      ))}
                    </select>
                  </div>
                  <p className={styles.languageRequirementHint}>
                    A candidate can only be accepted once their VoiceHR interview meets this
                    level in this language. Resume matching and scores are never affected.
                  </p>
                </div>
              )}
            </div>
          )}

          {createError && (
            <div className={styles.error} role="alert">
              {createError}
            </div>
          )}

          <div className={styles.formActions}>
            <button type="submit" className={styles.submitButton} disabled={creating}>
              {creating ? "Creating job… (this runs matching, may take a moment)" : "Create Job"}
            </button>
          </div>
        </form>
      )}

      {loading && <p className={styles.status}>Loading jobs…</p>}

      {!loading && error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}

      {!loading && !error && jobs.length === 0 && (
        <EmptyState variant="glass" title="No jobs yet" description="Create one to get started." />
      )}

      {!loading && !error && jobs.length > 0 && (
        <ul className={styles.jobList}>
          {jobs.map((job) => (
            <li key={job.job_id}>
              <button
                type="button"
                className={styles.jobCard}
                onClick={() => navigate(`/jobs/${job.job_id}`)}
              >
                <span className={styles.jobTitle}>
                  {job.title}
                  {job.extraction_status === "flagged" && (
                    <StatusBadge
                      variant="glass"
                      tone="warning"
                      title="Gemini's extraction of this job description looked suspicious (e.g. empty, phrase-like skills) -- review it on the job page."
                    >
                      Needs review
                    </StatusBadge>
                  )}
                </span>
                <span className={styles.jobMeta}>
                  {job.match_count} candidate{job.match_count === 1 ? "" : "s"} matched
                  {" · "}
                  {new Date(job.created_at).toLocaleDateString(undefined, {
                    year: "numeric",
                    month: "short",
                    day: "numeric",
                  })}
                  {formatLanguageRequirement(job.required_language, job.min_language_cefr) && (
                    <> · Requires {formatLanguageRequirement(job.required_language, job.min_language_cefr)}</>
                  )}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
