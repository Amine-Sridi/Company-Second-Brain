import { Link } from "react-router-dom";
import StatusBadge from "../StatusBadge.jsx";
import { RING_LABELS, coverageOf, initialsOf, percent, ringFor } from "./graphModel.js";
import styles from "./MatchInspector.module.css";

const RING_TONE = { qualified: "success", gate: "warning", below: "neutral" };

/**
 * Side panel for the match selected in the focus graph: why this candidate sits on
 * this ring. Every number is read from the persisted MatchResult; `detail` carries
 * the score components (the graph edge itself) and the panel simply shows less
 * when they are absent.
 *
 * @param {{
 *   edge: object, name: string, detail?: object|null,
 *   otherMatches: Array<{jobId, title, score, qualified}>,
 *   selectState: "idle"|"saving"|"selected"|"error", selectError?: string|null,
 *   onSelectForJob: () => void, jobId: string,
 * }} props
 */
export default function MatchInspector({ edge, name, detail, otherMatches, selectState, selectError, onSelectForJob, jobId }) {
  const ring = ringFor(edge);
  const coverage = coverageOf(edge);
  const gateFailed = !edge.hard_gate_passed;

  return (
    <aside className={styles.panel} aria-label="Selected candidate">
      <div className={styles.head}>
        <span className={`${styles.avatar} ${styles[`avatar_${ring}`]}`} aria-hidden="true">
          {initialsOf(name)}
        </span>
        <div className={styles.headText}>
          <span className={styles.name}>{name}</span>
          <StatusBadge tone={RING_TONE[ring]}>{edge.qualified ? "Qualified" : RING_LABELS[ring]}</StatusBadge>
        </div>
      </div>

      <section className={styles.block}>
        <div className={styles.scoreRow}>
          <span className={styles.blockTitle}>Final score</span>
          <span className={styles.bigScore}>{percent(edge.final_score)}%</span>
        </div>
        {detail ? (
          <>
            <div className={styles.bars}>
              <Bar label="Semantic" value={detail.semantic_score} tone="light" />
              <Bar label="Deterministic" value={detail.deterministic_score} />
              <Bar label="· Skills" value={detail.skill_score} small />
              <Bar label="· Experience" value={detail.experience_score} small />
              <Bar label="· Title" value={detail.title_score} small />
            </div>
            <p className={styles.note}>
              Computed by the application from these components, never by the language model.
            </p>
          </>
        ) : null}
      </section>

      <section className={styles.block}>
        <div className={styles.scoreRow}>
          <span className={styles.blockTitle}>Required skills</span>
          {coverage.ratio !== null && (
            <span className={`${styles.coverage} ${gateFailed ? styles.coverageLow : ""}`}>
              {coverage.matched} of {coverage.total} · {coverage.ratio.toFixed(2)}
            </span>
          )}
        </div>
        <div className={styles.chips}>
          {edge.matched_requirements.map((skill) => (
            <span key={`m-${skill}`} className={styles.chipMatched}>
              <CheckIcon /> {skill}
            </span>
          ))}
          {edge.missing_requirements.map((skill) => (
            <span key={`x-${skill}`} className={styles.chipMissing}>
              <DashIcon /> {skill}
            </span>
          ))}
          {coverage.total === 0 && <span className={styles.note}>This job lists no required skill.</span>}
        </div>
        <p className={styles.note}>
          {gateFailed
            ? "The required-skill gate failed, so this candidate cannot qualify whatever the score."
            : edge.qualified
              ? "The gate passed and the score reaches the job's threshold."
              : "The gate passed, but the score is below the job's threshold."}
        </p>
      </section>

      {otherMatches.length > 0 && (
        <section className={styles.block}>
          <span className={styles.blockTitle}>Also matched to</span>
          <ul className={styles.others}>
            {otherMatches.map((m) => (
              <li key={m.jobId}>
                <Link to={`/graph?job=${m.jobId}&candidate=${edge.candidate_id}`}>{m.title}</Link>
                <span className={styles.otherScore}>
                  {percent(m.score)}% · {m.qualified ? "qualified" : "not qualified"}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className={styles.actions}>
        {edge.qualified && (
          <button
            type="button"
            className={selectState === "selected" ? styles.selectedButton : styles.primaryButton}
            onClick={onSelectForJob}
            disabled={selectState === "saving" || selectState === "selected"}
          >
            {selectState === "selected" ? "Selected" : selectState === "saving" ? "Selecting…" : "Select for this job"}
          </button>
        )}
        <Link className={styles.secondaryButton} to={`/candidates/${edge.candidate_id}`}>
          Open profile
        </Link>
        <Link className={styles.secondaryButton} to={`/jobs/${jobId}`}>
          Open job
        </Link>
      </div>
      {selectState === "selected" && (
        <p className={styles.note}>
          Follow it in <Link to={`/jobs/${jobId}/applications`}>the job's applications</Link>.
        </p>
      )}
      {selectState === "error" && selectError && (
        <p className={styles.error} role="alert">
          {selectError}
        </p>
      )}
      {!edge.qualified && (
        <p className={styles.blocked}>Only qualified matches can be selected.</p>
      )}
    </aside>
  );
}

function Bar({ label, value, tone, small }) {
  const pct = Math.round((value ?? 0) * 100);
  return (
    <div className={`${styles.bar} ${small ? styles.barSmall : ""}`}>
      <span>{label}</span>
      <span className={styles.track}>
        <span className={tone === "light" ? styles.fillLight : styles.fill} style={{ width: `${pct}%` }} />
      </span>
      <span className={styles.barValue}>{pct}</span>
    </div>
  );
}

function CheckIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </svg>
  );
}

function DashIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" aria-hidden="true">
      <path d="M6 12h12" />
    </svg>
  );
}
