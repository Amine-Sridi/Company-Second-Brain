import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { getJobs, createJob } from "../api/jobs.js";
import { getCandidatesJobsGraph } from "../api/graph.js";
import { summarizeJobs } from "../components/graph/graphModel.js";
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
  const [filter, setFilter] = useState("all"); // all | review | language
  const [query, setQuery] = useState("");
  // Qualified counts are not on GET /v1/jobs; they are read best-effort from the graph
  // endpoint (persisted MatchResults). If that read fails the column shows a dash.
  const [qualifiedByJob, setQualifiedByJob] = useState(null);
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
      getCandidatesJobsGraph()
        .then((graph) => setQualifiedByJob(new Map(summarizeJobs(graph).map((row) => [row.job_id, row.qualified]))))
        .catch(() => setQualifiedByJob(null));
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

  const visibleJobs = useMemo(() => {
    const q = query.trim().toLowerCase();
    return jobs.filter((job) => {
      if (filter === "review" && job.extraction_status !== "flagged") return false;
      if (filter === "language" && !job.required_language) return false;
      return !q || job.title.toLowerCase().includes(q);
    });
  }, [jobs, filter, query]);

  const filterCounts = {
    all: jobs.length,
    review: jobs.filter((job) => job.extraction_status === "flagged").length,
    language: jobs.filter((job) => job.required_language).length,
  };

  return (
    <div className={styles.page}>
      <PageHeader
        title="Open positions"
        subtitle="Paste a job description. It is structured, validated and matched against the whole pool."
        actions={
          <button type="button" className={styles.newJobButton} onClick={() => setShowForm((visible) => !visible)} aria-expanded={showForm}>
            {showForm ? "Hide the form" : "New job"}
          </button>
        }
      />

      <div className={styles.layout}>
        <section className={styles.listCard} aria-label="Jobs">
          <div className={styles.toolbar}>
            {[
              ["all", "All jobs"],
              ["review", "Needs review"],
              ["language", "Language required"],
            ].map(([id, label]) => (
              <button
                key={id}
                type="button"
                className={filter === id ? styles.filterOn : styles.filter}
                aria-pressed={filter === id}
                onClick={() => setFilter(id)}
              >
                {label} · {filterCounts[id]}
              </button>
            ))}
            <span className={styles.spacer} />
            <label className={styles.search}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <circle cx="11" cy="11" r="7" />
                <path d="M20 20l-3.5-3.5" />
              </svg>
              <input type="search" placeholder="Search jobs" aria-label="Search jobs" value={query} onChange={(event) => setQuery(event.target.value)} />
            </label>
          </div>

          {loading && <p className={styles.status}>Loading jobs…</p>}

          {!loading && error && (
            <div className={styles.error} role="alert">
              {error}
            </div>
          )}

          {!loading && !error && jobs.length === 0 && <EmptyState title="No jobs yet" description="Create one to get started." />}

          {!loading && !error && jobs.length > 0 && (
            <div className={styles.tableScroll}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Job</th>
                    <th className={styles.num}>Matched</th>
                    <th className={styles.num}>Qualified</th>
                    <th>Language</th>
                    <th>Extraction</th>
                    <th>Posted</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleJobs.map((job) => {
                    const qualified = qualifiedByJob?.get(job.job_id);
                    const language = formatLanguageRequirement(job.required_language, job.min_language_cefr);
                    return (
                      <tr key={job.job_id}>
                        <td>
                          <Link to={`/jobs/${job.job_id}`} className={styles.jobLink}>
                            {job.title}
                          </Link>
                        </td>
                        <td className={styles.num}>{job.match_count}</td>
                        <td className={`${styles.num} ${qualified === 0 ? styles.zero : styles.qualified}`}>
                          {qualified === undefined ? "—" : qualified}
                        </td>
                        <td className={styles.muted}>{language ?? "None"}</td>
                        <td>
                          {job.extraction_status === "flagged" ? (
                            <StatusBadge
                              tone="warning"
                              title="Either the extraction (e.g. empty, phrase-like skills) or the match results (e.g. no candidate passed the required-skills gate) look suspicious -- review it on the job page."
                            >
                              Needs review
                            </StatusBadge>
                          ) : job.extraction_status === "repaired" ? (
                            <StatusBadge
                              tone="neutral"
                              title="The first extraction looked suspicious; an automatic second attempt fixed it and passed validation. No action needed."
                            >
                              Repaired automatically
                            </StatusBadge>
                          ) : (
                            <StatusBadge tone="success">Validated</StatusBadge>
                          )}
                        </td>
                        <td className={styles.muted}>
                          {new Date(job.created_at).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {visibleJobs.length === 0 && <p className={styles.status}>No job matches this filter.</p>}
            </div>
          )}
        </section>

        {showForm && (
          <form className={styles.form} onSubmit={handleCreate} aria-label="New job">
            <h2 className={styles.formTitle}>New job</h2>
            <label className={styles.field}>
              <span>Job title</span>
              <input type="text" required placeholder="e.g. Data Engineer" value={title} onChange={(event) => setTitle(event.target.value)} disabled={creating} />
            </label>

            <label className={styles.field}>
              <span>Job description</span>
              <textarea
                rows={8}
                required
                placeholder="Paste the raw job description here"
                value={rawDescription}
                onChange={(event) => setRawDescription(event.target.value)}
                disabled={creating}
              />
            </label>

            <label className={styles.languageRequirementToggle}>
              <input type="checkbox" checked={requireLanguage} onChange={(event) => setRequireLanguage(event.target.checked)} disabled={creating} />
              Require a VoiceHR language level
            </label>

            {requireLanguage && (
              <div className={styles.field}>
                <span>Language and minimum CEFR level</span>
                <div className={styles.languageRequirementRow}>
                  <select value={requiredLanguage} onChange={(event) => setRequiredLanguage(event.target.value)} disabled={creating} aria-label="Required language">
                    {Object.entries(LANGUAGE_LABELS).map(([code, label]) => (
                      <option key={code} value={code}>
                        {label}
                      </option>
                    ))}
                  </select>
                  <select value={minLanguageCefr} onChange={(event) => setMinLanguageCefr(event.target.value)} disabled={creating} aria-label="Minimum CEFR level">
                    {CEFR_LEVELS.map((level) => (
                      <option key={level} value={level}>
                        {level}
                      </option>
                    ))}
                  </select>
                </div>
                <p className={styles.languageRequirementHint}>
                  A candidate can only be accepted once their VoiceHR interview meets this level. Matching and scores are never affected.
                </p>
              </div>
            )}

            <button type="button" className={styles.advancedToggle} onClick={() => setShowAdvanced((visible) => !visible)} aria-expanded={showAdvanced}>
              {showAdvanced ? "Hide advanced matching settings" : "Advanced matching settings"}
            </button>

            {showAdvanced && (
              <div className={styles.advancedFields}>
                <label className={styles.field}>
                  <span>Qualification threshold (0–1)</span>
                  <input type="number" min="0" max="1" step="0.05" value={threshold} onChange={(event) => setThreshold(event.target.value)} disabled={creating} />
                </label>
                <label className={styles.field}>
                  <span>Candidates retrieved</span>
                  <input type="number" min="1" max="100" step="1" value={topK} onChange={(event) => setTopK(event.target.value)} disabled={creating} />
                </label>
              </div>
            )}

            {createError && (
              <div className={styles.error} role="alert">
                {createError}
              </div>
            )}

            <button type="submit" className={styles.submitButton} disabled={creating}>
              {creating ? "Structuring and matching…" : "Submit and match"}
            </button>
            <p className={styles.formNote}>The language model only structures the text. Scores and qualification are computed by the application.</p>
          </form>
        )}
      </div>
    </div>
  );
}
