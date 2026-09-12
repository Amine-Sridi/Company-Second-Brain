import StatusBadge from "./StatusBadge.jsx";
import styles from "./MatchGraph.module.css";

/**
 * The legend/score-filter/search bar previously lived inside MatchGraph.jsx
 * itself (only GraphPage ever passed showLegend/showScoreFilter/showSearch --
 * CandidateDetailPage's ego graph never did). Extracted here, owned by
 * GraphPage, so the same filter/search *state* can drive whichever renderer
 * (2D Cytoscape or 3D force-graph) is currently mounted -- switching views no
 * longer resets "qualified only" or the score floor, since that state now
 * lives above both renderers instead of inside the one being replaced.
 *
 * Purely controlled: every value and every change handler comes from the
 * parent (GraphPage). This component makes no API call and reads no
 * Cytoscape/Three.js state itself.
 */
export default function GraphControls({
  hiddenCategories,
  onToggleCategory,
  minScore,
  onMinScoreChange,
  searchTerm,
  onSearchTermChange,
  searchResults,
  onSelectSearchResult,
}) {
  return (
    <div className={styles.card}>
      <div className={styles.legend}>
        <span className={styles.legendItem}>
          <span className={`${styles.legendSwatch} ${styles.legendJob}`} /> Job
        </span>
        <span className={styles.legendItem}>
          <span className={`${styles.legendSwatch} ${styles.legendCandidate}`} /> Candidate
        </span>
        <LegendToggle
          active={!hiddenCategories.qualified}
          onClick={() => onToggleCategory("qualified")}
          lineClassName={styles.legendQualified}
          label="Qualified match"
        />
        <LegendToggle
          active={!hiddenCategories.disqualified}
          onClick={() => onToggleCategory("disqualified")}
          lineClassName={styles.legendDisqualified}
          label="Disqualified match"
        />
      </div>
      <div className={styles.filterRow}>
        <label className={styles.scoreFilter}>
          <span className={styles.scoreFilterLabel}>Min score: {minScore}%+</span>
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={minScore}
            onChange={(event) => onMinScoreChange(Number(event.target.value))}
            className={styles.scoreFilterInput}
          />
        </label>
        <div className={styles.searchBox}>
          <input
            type="text"
            className={styles.searchInput}
            placeholder="Search candidates or jobs…"
            value={searchTerm}
            onChange={(event) => onSearchTermChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") onSearchTermChange("");
            }}
          />
          {searchTerm.trim() && (
            <ul className={styles.searchResults}>
              {searchResults.length > 0 ? (
                searchResults.map((result) => (
                  <li key={result.id}>
                    <button
                      type="button"
                      className={styles.searchResultButton}
                      onClick={() => onSelectSearchResult(result.id)}
                    >
                      <StatusBadge variant="glass" tone={result.kind === "job" ? "primary" : "accent"}>
                        {result.kind === "job" ? "Job" : "Candidate"}
                      </StatusBadge>
                      <span className={styles.searchResultLabel}>{result.label}</span>
                    </button>
                  </li>
                ))
              ) : (
                <li className={styles.searchNoResults}>No matches</li>
              )}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

function LegendToggle({ active, onClick, lineClassName, label }) {
  return (
    <button
      type="button"
      className={`${styles.legendItem} ${styles.legendToggle} ${active ? "" : styles.legendToggleOff}`}
      onClick={onClick}
      aria-pressed={active}
      title={active ? `Click to hide ${label.toLowerCase()}es` : `Click to show ${label.toLowerCase()}es`}
    >
      <span className={`${styles.legendLine} ${lineClassName}`} /> {label}
    </button>
  );
}
