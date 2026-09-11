import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { getCandidates } from "../api/candidates.js";
import { ApiError } from "../api/client.js";
import PageHeader from "../components/PageHeader.jsx";
import EmptyState from "../components/EmptyState.jsx";
import styles from "./CandidatesPage.module.css";

/**
 * Every candidate ever ingested, newest first -- read-only, no search/filter/
 * pagination yet (matches this project's existing "no infra ahead of demonstrated
 * need" rule, same as JobsListPage/JobDetailPage). Always fetched fresh on mount,
 * same pattern as every other list page in this app, so a hard browser refresh on
 * this URL reflects real backend state, not a stale local copy.
 *
 * Rows navigate to /candidates/:candidateId (same click/keyboard pattern as
 * ReportsPage's rows) -- added once that destination page actually existed.
 */
export default function CandidatesPage() {
  const navigate = useNavigate();
  const [candidates, setCandidates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    loadCandidates();
  }, []);

  async function loadCandidates() {
    setLoading(true);
    setError(null);
    try {
      const response = await getCandidates();
      setCandidates(response);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load candidates.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={styles.page}>
      <PageHeader
        title="Candidates"
        subtitle="Every candidate in the talent pool, newest first."
        actions={
          <Link to="/upload" className={styles.uploadLink}>
            Upload Resume
          </Link>
        }
        variant="glass"
      />

      {loading && <p className={styles.status}>Loading candidates…</p>}

      {!loading && error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}

      {!loading && !error && candidates.length === 0 && (
        <EmptyState
          variant="glass"
          title="No candidates yet"
          description="Upload a resume to add the first candidate to the pool."
        />
      )}

      {!loading && !error && candidates.length > 0 && (
        <div className={styles.tableWrapper}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Added</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((candidate) => (
                <tr
                  key={candidate.candidate_id}
                  className={styles.row}
                  onClick={() => navigate(`/candidates/${candidate.candidate_id}`)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      if (event.key === " ") event.preventDefault();
                      navigate(`/candidates/${candidate.candidate_id}`);
                    }
                  }}
                  role="button"
                  tabIndex={0}
                >
                  <td className={styles.candidateName}>{candidate.name}</td>
                  <td>
                    {candidate.email ? (
                      candidate.email
                    ) : (
                      <span className={styles.noEmail}>No email on file</span>
                    )}
                  </td>
                  <td className={styles.dateCell}>
                    {new Date(candidate.created_at).toLocaleDateString(undefined, {
                      year: "numeric",
                      month: "short",
                      day: "numeric",
                    })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
