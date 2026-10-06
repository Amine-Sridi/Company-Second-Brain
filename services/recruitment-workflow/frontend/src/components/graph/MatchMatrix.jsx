import { useMemo, useState } from "react";
import { buildMatrix, gateBlockedLeaders, percent, summarizeJobs } from "./graphModel.js";
import styles from "./MatchMatrix.module.css";

/**
 * Overview: every candidate against every job, one cell per persisted MatchResult.
 * Replaces the old force-directed "everything at once" graph, which drew ~300 lines
 * on top of each other. A grid shows the same information without a single crossing.
 *
 * @param {{ graph: object, names: Map<string,string>, onOpen: (jobId, candidateId) => void }} props
 */
export default function MatchMatrix({ graph, names, onOpen }) {
  const [sortBy, setSortBy] = useState("best");
  const [onlyUsefulJobs, setOnlyUsefulJobs] = useState(true);
  const [fadeOthers, setFadeOthers] = useState(false);

  const jobRows = useMemo(() => summarizeJobs(graph), [graph]);
  const jobIds = useMemo(
    () => jobRows.filter((job) => !onlyUsefulJobs || job.qualified > 0).map((job) => job.job_id),
    [jobRows, onlyUsefulJobs],
  );
  const { columns, rows } = useMemo(() => buildMatrix(graph, names, { jobIds, sortBy }), [graph, names, jobIds, sortBy]);
  const visibleRows = useMemo(() => rows.filter((row) => row.cells.some(Boolean)), [rows]);
  const titles = useMemo(() => new Map(graph.jobs.map((job) => [job.job_id, job.title])), [graph]);
  const blocked = useMemo(() => gateBlockedLeaders(graph, 6), [graph]);

  return (
    <div className={styles.layout}>
      <section className={styles.card} aria-label="Match matrix">
        <div className={styles.toolbar}>
          <span className={styles.toolbarLabel}>Sort candidates</span>
          <Toggle pressed={sortBy === "best"} onClick={() => setSortBy("best")}>
            Best match first
          </Toggle>
          <Toggle pressed={sortBy === "name"} onClick={() => setSortBy("name")}>
            Name
          </Toggle>
          <span className={styles.spacer} />
          <Check checked={onlyUsefulJobs} onChange={setOnlyUsefulJobs}>
            Only jobs with a qualified candidate
          </Check>
          <Check checked={fadeOthers} onChange={setFadeOthers}>
            Fade non-qualified cells
          </Check>
        </div>

        {columns.length === 0 ? (
          <p className={styles.empty}>No job has a qualified candidate yet. Untick the filter to see every job.</p>
        ) : (
          <div className={styles.scroller}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th scope="col" className={styles.corner}>
                    Candidate
                  </th>
                  {columns.map((job) => (
                    <th key={job.job_id} scope="col" className={styles.colHead} title={job.title}>
                      <span>{job.title}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row) => (
                  <tr key={row.candidateId}>
                    <th scope="row" className={styles.rowHead}>
                      <span className={styles.rowName}>{row.name}</span>
                      {row.qualifiedCount > 0 && <span className={styles.rowCount}>{row.qualifiedCount} qualified</span>}
                    </th>
                    {row.cells.map((edge, j) => (
                      <td key={columns[j].job_id} className={styles.cellWrap}>
                        {edge ? (
                          <button
                            type="button"
                            className={cellClass(edge, fadeOthers)}
                            style={{ background: shade(edge.final_score), color: edge.final_score >= 0.6 ? "#ffffff" : "#15171c" }}
                            onClick={() => onOpen(edge.job_id, edge.candidate_id)}
                            aria-label={`${row.name}, ${columns[j].title}, ${percent(edge.final_score)} percent${
                              edge.qualified ? ", qualified" : edge.hard_gate_passed ? "" : ", required-skill gate failed"
                            }`}
                          >
                            {percent(edge.final_score)}
                            {edge.qualified && <span className={styles.dot} aria-hidden="true" />}
                          </button>
                        ) : (
                          <span className={styles.none} title="Not retrieved for this job" />
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <aside className={styles.side}>
        <div className={styles.card}>
          <h2 className={styles.sideTitle}>How to read it</h2>
          <div className={styles.legendRow}>
            <span className={styles.ramp} aria-hidden="true" />
            <span className={styles.mono}>40 → 75%</span>
          </div>
          <div className={styles.legendRow}>
            <span className={`${styles.swatch} ${styles.swatchQualified}`} aria-hidden="true">
              <span className={styles.dot} />
            </span>
            Qualified
          </div>
          <div className={styles.legendRow}>
            <span className={`${styles.swatch} ${styles.swatchGate}`} aria-hidden="true" />
            Required-skill gate failed
          </div>
          <div className={styles.legendRow}>
            <span className={`${styles.swatch} ${styles.swatchNone}`} aria-hidden="true" />
            Not retrieved for this job
          </div>
          <p className={styles.hint}>Select a cell to open that job in the focus view with the candidate selected.</p>
        </div>

        {blocked.length > 0 && (
          <div className={styles.card}>
            <h2 className={styles.sideTitle}>Worth a look</h2>
            <p className={styles.hint}>High scores blocked by the required-skill gate.</p>
            <ul className={styles.blockedList}>
              {blocked.map((edge) => (
                <li key={edge.match_id}>
                  <button type="button" onClick={() => onOpen(edge.job_id, edge.candidate_id)}>
                    <span>
                      {names.get(edge.candidate_id) ?? "Unknown candidate"}
                      <span className={styles.blockedJob}> · {titles.get(edge.job_id)}</span>
                    </span>
                    <span className={styles.blockedScore}>{percent(edge.final_score)}%</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </aside>
    </div>
  );
}

function cellClass(edge, fadeOthers) {
  const parts = [styles.cell];
  if (edge.qualified) parts.push(styles.cellQualified);
  else if (!edge.hard_gate_passed) parts.push(styles.cellGate);
  if (fadeOthers && !edge.qualified) parts.push(styles.cellFaded);
  return parts.join(" ");
}

// 0.40 -> near white, 0.75 -> full accent blue. Below/above clamp.
function shade(score) {
  const t = Math.max(0, Math.min(1, (score - 0.4) / 0.35));
  const a = [238, 241, 247];
  const b = [36, 72, 199];
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * t));
  return `rgb(${c.join(",")})`;
}

function Toggle({ pressed, onClick, children }) {
  return (
    <button type="button" aria-pressed={pressed} className={pressed ? styles.toggleOn : styles.toggle} onClick={onClick}>
      {children}
    </button>
  );
}

function Check({ checked, onChange, children }) {
  return (
    <label className={styles.check}>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      {children}
    </label>
  );
}
