import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getInterviewReport } from "../api/interviews.js";
import { getAllApplications } from "../api/applications.js";
import { ApiError, requestBlob } from "../api/client.js";
import PageHeader from "../components/PageHeader.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import {
  EVALUATION_STATUS_LABELS,
  INSUFFICIENT_EVIDENCE_EXPLANATION,
  LANGUAGE_GATE_LABELS,
  LANGUAGE_GATE_TONE,
  LANGUAGE_LABELS,
  describeDimensionScore,
} from "../constants/language.js";
import styles from "./ReportDetailPage.module.css";

/**
 * "Required: B2 English / Assessed: C1 English / Result: Meets requirement" -- RWS's
 * own verdict (language_gate.py), never recomputed here. Only rendered when this
 * application's job actually has a requirement (see the not_required guard below),
 * so a job with no requirement shows nothing extra on an otherwise-unrelated report.
 * Deliberately separate from the interview's own assessment quality below it (see
 * AssessmentQualitySection) -- this section states a JOB REQUIREMENT and a SYSTEM
 * DECISION, both owned by RWS; the assessment section states what VoiceHR itself
 * observed. Keeping the two visually distinct is this task's own explicit ask.
 *
 * assessed_cefr and assessed_language are set independently (a webhook missing one
 * but not the other is exactly what "needs_review" exists to catch -- see
 * language_gate.py) -- rendering must handle that combination without ever
 * interpolating a bare `null`/`undefined` into the text a recruiter reads.
 */
function LanguageScreeningSection({ gate }) {
  const requiredLabel = LANGUAGE_LABELS[gate.required_language] ?? gate.required_language;
  const assessedText = gate.assessed_cefr
    ? [gate.assessed_cefr, gate.assessed_language ? (LANGUAGE_LABELS[gate.assessed_language] ?? gate.assessed_language) : null]
        .filter(Boolean)
        .join(" ")
    : "no result yet";
  const blocked = gate.status === "failed" || gate.status === "needs_review";

  return (
    <section className={`${styles.card} ${blocked ? styles.cardWarn : ""}`} aria-label="Language screening">
      <span className={styles.cardEyebrow}>Language screening for this job</span>
      <StatusBadge tone={LANGUAGE_GATE_TONE[gate.status] ?? "neutral"}>{LANGUAGE_GATE_LABELS[gate.status] ?? gate.status}</StatusBadge>
      <span className={styles.cardText}>
        Assessed {assessedText}, the job requires {gate.required_cefr} {requiredLabel}.
        {gate.status === "failed" ? " Acceptance is blocked for this job; rejecting stays possible." : ""}
      </span>
    </section>
  );
}

const CEFR_SCALE = ["A1", "A2", "B1", "B2", "C1", "C2"];
const TRANSCRIPT_PREVIEW = 8;

/**
 * The assessed level, shown as a position on the A1..C2 scale. When VoiceHR issued no
 * level (insufficient evidence, failed), the card says so plainly and shows no score:
 * overall_score/cefr_level are genuinely null for those statuses on VoiceHR's side.
 */
function LevelCard({ report }) {
  const status = report.evaluation_status;
  const hasLevel = status === "completed" && report.cefr_level;
  return (
    <section className={styles.levelCard} aria-label="Assessed level">
      <span className={styles.levelEyebrow}>{hasLevel ? "Assessed level" : "No level issued"}</span>
      {hasLevel ? (
        <>
          <span className={styles.levelLine}>
            <span className={styles.levelBig}>{report.cefr_level}</span>
            {report.overall_score != null && <span className={styles.levelScore}>{report.overall_score}/100</span>}
          </span>
          <div className={styles.scale} aria-hidden="true">
            {CEFR_SCALE.map((level) => (
              <span key={level} className={level === report.cefr_level ? styles.scaleOn : styles.scaleOff}>
                <span className={styles.scaleBar} />
                <span className={styles.scaleLabel}>{level}</span>
              </span>
            ))}
          </div>
        </>
      ) : (
        <span className={styles.levelExplain}>
          {status === "insufficient_evidence"
            ? INSUFFICIENT_EVIDENCE_EXPLANATION
            : status === "failed"
              ? "Evaluation failed, so no score is available for this interview."
              : "The evaluation did not produce a reliable level."}{" "}
          The application goes to human review.
        </span>
      )}
      <span className={styles.levelStatus}>{EVALUATION_STATUS_LABELS[status] ?? status}</span>
    </section>
  );
}

// The five real, evidenced rubric dimensions with their weights in the overall score
// (VoiceHR evaluator.py: DIMENSION_WEIGHTS). Pronunciation is deliberately absent: the
// evaluator only ever sees text, never audio, so it is always null.
const PROFILE_DIMENSIONS = [
  { key: "comprehension_relevance", label: "Comprehension and relevance", weight: "25%" },
  { key: "fluency_coherence", label: "Fluency and coherence", weight: "25%" },
  { key: "grammar_language_control", label: "Grammar and language control", weight: "20%" },
  { key: "vocabulary_range", label: "Vocabulary range", weight: "15%" },
  { key: "interaction_spontaneity", label: "Interaction and spontaneity", weight: "15%" },
];

// A short, concrete note tied to this dimension -- a transcript quote or the
// evaluator's brief factual observation, never private model reasoning.
function evidenceNoteFor(report, dimensionKey) {
  const item = (report.evidence || []).find((entry) => entry.dimension === dimensionKey);
  if (!item) return null;
  return item.note || item.quote || null;
}

/** Only shown once a real, confident verdict exists (evaluation_status === "completed"). */
function LanguageProfileSection({ report }) {
  if (report.evaluation_status !== "completed") return null;
  return (
    <section className={`${styles.card} ${styles.wide}`} aria-label="Language profile">
      <h2 className={styles.cardTitle}>Five dimensions</h2>
      <div className={styles.dimensions}>
        {PROFILE_DIMENSIONS.map(({ key, label, weight }) => {
          const score = report.dimensions?.[key] ?? null;
          const note = evidenceNoteFor(report, key);
          return (
            <div key={key} className={styles.dimension}>
              <div className={styles.dimensionRow}>
                <span>{label}</span>
                <span className={styles.track}>
                  <span className={styles.fill} style={{ width: `${score ?? 0}%` }} />
                </span>
                <span className={styles.mono}>{score ?? "—"}</span>
                <span className={styles.weight}>{weight}</span>
              </div>
              <span className={styles.dimensionNote}>
                {describeDimensionScore(score)}
                {note ? ` · "${note}"` : ""}
              </span>
            </div>
          );
        })}
      </div>
      <p className={styles.cardNote}>
        The model scores each dimension. VoiceHR computes the weighted total and maps it to CEFR with fixed boundaries.
      </p>
    </section>
  );
}

// VoiceHR's real evaluator output shapes strengths/concerns as timestamp-linked
// objects ({ text, timestamp }), not plain strings -- confirmed live against a
// real Gemini evaluation during an earlier task's own verification, not assumed
// from the (untyped) response. Handles a plain string too, defensively, since
// nothing on the VoiceHR side guarantees the richer shape forever.
function noteText(note) {
  if (typeof note === "string") return note;
  return note?.text ?? String(note);
}

/**
 * One interview's full report -- the Language Screening (RWS's own deterministic
 * verdict) plus the VoiceHR-only detail (assessment quality, per-dimension profile,
 * strengths/concerns, transcript, recording) that only this endpoint has. The
 * hierarchy here is deliberate, not incidental: a recruiter should be able to
 * understand *why* a candidate received their language result without reading the
 * full transcript, which stays available below for anyone who wants to go deeper.
 * Self-sufficient from one fetch (GET /v1/interviews/{applicationId}/report) -- a
 * hard refresh on this URL reflects real backend state, same rule every other page
 * in this app follows. No job title shown here (VoiceHR has no concept of RWS
 * jobs); ReportsPage's list is where that lives.
 */
export default function ReportDetailPage() {
  const { applicationId } = useParams();

  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [recordingUrl, setRecordingUrl] = useState(null);
  const [recordingError, setRecordingError] = useState(null);
  const [showFullTranscript, setShowFullTranscript] = useState(false);

  // Best-effort only, for the "candidate name links to Candidate Detail" and "job
  // title links to Job Detail" cross-links: VoiceHR's report has no concept of
  // RWS's candidate_id or job_id, so this page independently looks both up via the
  // already-existing global applications list rather than adding a new backend
  // endpoint or a second call just for two IDs already sitting on that same row. A
  // failure or a not-found row here never blocks the report itself from rendering
  // -- the candidate name/job title just stay plain text instead of a link.
  const [candidateId, setCandidateId] = useState(null);
  const [job, setJob] = useState(null); // { jobId, jobTitle } | null
  // RWS's own gate verdict for this application (main.py: _language_gate_info) --
  // fetched from the same GlobalApplicationResponse row as candidateId/job above,
  // not recomputed here. null while unresolved, or when the lookup below fails.
  const [languageGate, setLanguageGate] = useState(null);

  useEffect(() => {
    loadReport();
    setCandidateId(null);
    setJob(null);
    setLanguageGate(null);
    getAllApplications()
      .then((applications) => {
        const match = applications.find((application) => application.application_id === applicationId);
        if (match) {
          setCandidateId(match.candidate_id);
          setJob({ jobId: match.job_id, jobTitle: match.job_title });
          setLanguageGate(match.language_gate ?? null);
        }
      })
      .catch(() => {
        // Silently left unresolved -- see the comment above.
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applicationId]);

  async function loadReport() {
    setLoading(true);
    setError(null);
    setReport(null);
    try {
      const response = await getInterviewReport(applicationId);
      setReport(response);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load report.");
    } finally {
      setLoading(false);
    }
  }

  // A recording, if any, is fetched as a blob (an <audio src> can't attach the
  // Authorization header itself -- see api/client.js: requestBlob) and turned into
  // an object URL, revoked again on cleanup/unmount so it doesn't leak memory.
  useEffect(() => {
    setRecordingUrl(null);
    setRecordingError(null);
    if (!report?.recording?.available || !report.recording.url) return undefined;

    let objectUrl = null;
    let cancelled = false;
    requestBlob(report.recording.url)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setRecordingUrl(objectUrl);
      })
      .catch((err) => {
        if (!cancelled) {
          setRecordingError(err instanceof ApiError ? err.message : "Unable to load recording.");
        }
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [report]);

  const transcript = report?.transcript ?? [];
  const shownTranscript = showFullTranscript ? transcript : transcript.slice(0, TRANSCRIPT_PREVIEW);

  return (
    <div className={styles.page}>
      <Link to="/reports" className={styles.backLink}>
        Back to reports
      </Link>
      <PageHeader
        title={
          report ? (
            candidateId ? (
              <Link to={`/candidates/${candidateId}`} className={styles.candidateLink}>
                {report.candidate_name}
              </Link>
            ) : (
              report.candidate_name
            )
          ) : (
            "Report"
          )
        }
        subtitle={
          report
            ? [
                "VoiceHR interview report",
                job?.jobTitle,
                report.language === "fr" ? "French" : "English",
                report.interview_date
                  ? new Date(report.interview_date).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
                  : null,
              ]
                .filter(Boolean)
                .join(" · ")
            : undefined
        }
        actions={
          job && (
            <Link to={`/jobs/${job.jobId}/applications`} className={styles.secondaryAction}>
              Open the job's applications
            </Link>
          )
        }
      />

      {loading && <p className={styles.status}>Loading report…</p>}

      {!loading && error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}

      {!loading && !error && report && (
        <div className={styles.content}>
          <div className={styles.topRow}>
            <LevelCard report={report} />
            {languageGate && languageGate.status !== "not_required" ? (
              <LanguageScreeningSection gate={languageGate} />
            ) : (
              <section className={styles.card} aria-label="Language screening">
                <span className={styles.cardEyebrow}>Language screening for this job</span>
                <span className={styles.cardText}>This job has no language requirement, so the level is informative only.</span>
              </section>
            )}
          </div>

          <div className={styles.row}>
            <LanguageProfileSection report={report} />

            {(report.strengths?.length > 0 || report.concerns?.length > 0) && (
              <section className={styles.card} aria-label="Observations">
                {report.strengths?.length > 0 && (
                  <div className={styles.notes}>
                    <h2 className={styles.strengthsTitle}>Strengths</h2>
                    <ul className={styles.notesList}>
                      {report.strengths.map((item, index) => (
                        <li key={index}>{noteText(item)}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {report.concerns?.length > 0 && (
                  <div className={styles.notes}>
                    <h2 className={styles.concernsTitle}>Concerns</h2>
                    <ul className={styles.notesList}>
                      {report.concerns.map((item, index) => (
                        <li key={index}>{noteText(item)}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </section>
            )}
          </div>

          {(report.recording?.available || transcript.length > 0) && (
            <section className={styles.card} aria-label="Recording and transcript">
              {report.recording?.available && (
                <div className={styles.recording}>
                  <h2 className={styles.cardTitle}>Recording</h2>
                  {recordingUrl && (
                    <audio controls src={recordingUrl} className={styles.audio}>
                      Your browser does not support audio playback.
                    </audio>
                  )}
                  {recordingError && (
                    <div className={styles.error} role="alert">
                      {recordingError}
                    </div>
                  )}
                  {!recordingUrl && !recordingError && <p className={styles.status}>Loading recording…</p>}
                </div>
              )}

              {transcript.length > 0 && (
                <div className={styles.transcriptBlock}>
                  <div className={styles.transcriptHead}>
                    <h2 className={styles.cardTitle}>Transcript</h2>
                    {transcript.length > TRANSCRIPT_PREVIEW && (
                      <button
                        type="button"
                        className={styles.toggleButton}
                        onClick={() => setShowFullTranscript((value) => !value)}
                        aria-expanded={showFullTranscript}
                      >
                        {showFullTranscript ? "Show less" : `Show all ${transcript.length} turns`}
                      </button>
                    )}
                  </div>
                  <div className={styles.transcript}>
                    {shownTranscript.map((turn, index) => (
                      <div key={index} className={styles.turn}>
                        <span className={turn.role === "user" ? styles.roleCandidate : styles.roleInterviewer}>
                          {turn.role === "user" ? "Candidate" : "Interviewer"}
                        </span>
                        <span className={styles.turnText}>{turn.text}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </section>
          )}
        </div>
      )}
    </div>
  );
}
