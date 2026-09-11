import StatusBadge from "./StatusBadge.jsx";
import styles from "./AssessmentPanel.module.css";

const EVALUATION_STATUS_LABELS = {
  completed: "Completed",
  inconclusive: "Inconclusive",
  failed: "Failed",
};

const EVALUATION_STATUS_TONE = {
  completed: "success",
  inconclusive: "warning",
  failed: "danger",
};

/**
 * Renders one interview's assessment exactly as RWS returns it (InterviewAssessment,
 * shared/schemas/__init__.py -- see FRONTEND_HANDOFF.md §13). Nothing rendered here
 * is invented, and no field from that schema is left out.
 *
 * Originally private to ApplicationsPage; promoted here once InterviewsPage needed
 * the identical render logic for a second, genuinely real call site (not a
 * speculative extraction -- see FRONTEND_HANDOFF.md §20).
 *
 * V2 design system, Phase 2 (see FRONTEND_HANDOFF.md): grepped and confirmed this
 * component's only three consumers (ApplicationsPage, InterviewsPage,
 * ReportDetailPage) are all migrated together in the same pass that touches this
 * file -- so, like StatTile in Phase 1, this is a direct redesign in place, not an
 * opt-in variant; there is no remaining light-theme call site to preserve.
 */
export default function AssessmentPanel({ assessment }) {
  const {
    overall_score,
    cefr_level,
    fluency_score,
    vocabulary_score,
    grammar_score,
    pronunciation_score,
    comprehension_score,
    inconclusive,
    evaluation_status,
  } = assessment;

  return (
    <div className={styles.assessment}>
      <div className={styles.assessmentHeader}>
        <span className={styles.assessmentLabel}>Interview Assessment</span>
        <StatusBadge variant="glass" tone={EVALUATION_STATUS_TONE[evaluation_status] ?? "neutral"}>
          {EVALUATION_STATUS_LABELS[evaluation_status] ?? evaluation_status}
        </StatusBadge>
        {inconclusive && (
          <span className={styles.inconclusiveNote}>Low-confidence evaluation</span>
        )}
      </div>

      {evaluation_status === "failed" ? (
        <p className={styles.assessmentFailedText}>
          Evaluation failed — no scores are available for this interview.
        </p>
      ) : (
        <div className={styles.assessmentGrid}>
          <AssessmentScoreRow label="Overall score" value={overall_score} />
          {cefr_level && <AssessmentTextRow label="CEFR level" value={cefr_level} />}
          <AssessmentScoreRow label="Fluency" value={fluency_score} />
          <AssessmentScoreRow label="Vocabulary" value={vocabulary_score} />
          <AssessmentScoreRow label="Grammar" value={grammar_score} />
          <AssessmentScoreRow label="Pronunciation" value={pronunciation_score} />
          <AssessmentScoreRow label="Comprehension" value={comprehension_score} />
        </div>
      )}
    </div>
  );
}

function AssessmentScoreRow({ label, value }) {
  return (
    <div className={styles.assessmentRow}>
      <span>{label}</span>
      <span>{value == null ? "—" : `${value}/100`}</span>
    </div>
  );
}

function AssessmentTextRow({ label, value }) {
  return (
    <div className={styles.assessmentRow}>
      <span>{label}</span>
      <span>{value}</span>
    </div>
  );
}
