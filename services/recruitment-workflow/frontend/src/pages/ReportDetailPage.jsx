import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getInterviewReport } from "../api/interviews.js";
import { getAllApplications } from "../api/applications.js";
import { ApiError, requestBlob } from "../api/client.js";
import PageHeader from "../components/PageHeader.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import {
  EVALUATION_STATUS_LABELS,
  EVALUATION_STATUS_TONE,
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
    : "No result yet";

  return (
    <div className={styles.languageGate}>
      <h2 className={styles.notesTitle}>Language screening</h2>
      <div className={styles.languageGateRow}>
        <span>Required</span>
        <span>
          {gate.required_cefr} {requiredLabel}
        </span>
      </div>
      <div className={styles.languageGateRow}>
        <span>Assessed</span>
        <span>{assessedText}</span>
      </div>
      <div className={styles.languageGateRow}>
        <span>Result</span>
        <StatusBadge variant="glass" tone={LANGUAGE_GATE_TONE[gate.status] ?? "neutral"}>
          {LANGUAGE_GATE_LABELS[gate.status] ?? gate.status}
        </StatusBadge>
      </div>
    </div>
  );
}

/**
 * First-class treatment for whether VoiceHR's own evaluation produced enough to
 * trust at all -- distinct from, and shown above, the Language Screening verdict
 * (which can only ever be as good as this). "insufficient_evidence" is never styled
 * like "failed": inconclusive is not the same as failed (a warning tone throughout,
 * never danger), and no fake CEFR/0-score is ever shown in its place -- overall_score/
 * cefr_level are genuinely null for this status, by construction on VoiceHR's side.
 */
function AssessmentQualitySection({ report }) {
  const status = report.evaluation_status;
  return (
    <div className={styles.section}>
      <h2 className={styles.notesTitle}>Assessment quality</h2>
      <div className={styles.qualityRow}>
        <StatusBadge variant="glass" tone={EVALUATION_STATUS_TONE[status] ?? "neutral"}>
          {EVALUATION_STATUS_LABELS[status] ?? status}
        </StatusBadge>
        {status === "completed" && report.cefr_level && (
          <span className={styles.qualityDetail}>
            {report.cefr_level}
            {report.overall_score != null ? ` · ${report.overall_score}/100` : ""}
          </span>
        )}
      </div>
      {status === "insufficient_evidence" && (
        <p className={styles.qualityExplanation}>{INSUFFICIENT_EVIDENCE_EXPLANATION}</p>
      )}
      {status === "failed" && (
        <p className={styles.qualityExplanation}>
          Evaluation failed — no scores are available for this interview.
        </p>
      )}
    </div>
  );
}

// The five real, evidenced rubric dimensions (backend/services/evaluator.py:
// DIMENSION_WEIGHTS) -- pronunciation is deliberately absent everywhere in this
// section: it is always null (the evaluator only ever sees text, never audio), and
// listing it here would only invite "why is this always blank?" instead of
// answering that question.
const PROFILE_DIMENSIONS = [
  { key: "fluency_coherence", label: "Fluency & coherence" },
  { key: "vocabulary_range", label: "Vocabulary" },
  { key: "grammar_language_control", label: "Grammar" },
  { key: "comprehension_relevance", label: "Comprehension" },
  { key: "interaction_spontaneity", label: "Interaction" },
];

// A short, concrete, observable note tied to this dimension -- transcript quotes or
// the evaluator's own brief factual observation, never private model reasoning
// (evaluator.py's own prompt never asks for -- and normalize_result never copies --
// anything resembling chain-of-thought).
function evidenceNoteFor(report, dimensionKey) {
  const item = (report.evidence || []).find((entry) => entry.dimension === dimensionKey);
  if (!item) return null;
  return item.note || item.quote || null;
}

/**
 * Only shown once a real, confident verdict exists (evaluation_status === "completed")
 * -- for "insufficient_evidence"/"failed", AssessmentQualitySection above is already
 * the complete, honest picture; a per-dimension breakdown here would risk implying
 * these numbers are as trustworthy as a normal result, which is exactly what this
 * task's own "insufficient evidence needs first-class treatment" instruction warns
 * against.
 */
function LanguageProfileSection({ report }) {
  if (report.evaluation_status !== "completed") return null;
  return (
    <div className={styles.section}>
      <h2 className={styles.notesTitle}>Language profile</h2>
      <div className={styles.profileGrid}>
        {PROFILE_DIMENSIONS.map(({ key, label }) => {
          const score = report.dimensions?.[key] ?? null;
          const note = evidenceNoteFor(report, key);
          return (
            <div key={key} className={styles.profileRow}>
              <div className={styles.profileHeader}>
                <span className={styles.profileLabel}>{label}</span>
                <span className={styles.profileRating}>{describeDimensionScore(score)}</span>
              </div>
              {note && <p className={styles.profileEvidence}>"{note}"</p>}
            </div>
          );
        })}
      </div>
    </div>
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

  return (
    <div className={styles.page}>
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
        subtitle={report ? (report.language === "fr" ? "French interview" : "English interview") : undefined}
        actions={
          <Link to="/reports" className={styles.backLink}>
            Back to Reports
          </Link>
        }
        variant="glass"
      />

      {loading && <p className={styles.status}>Loading report…</p>}

      {!loading && error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}

      {!loading && !error && report && (
        <div className={styles.content}>
          <div className={styles.meta}>
            {job && (
              <Link to={`/jobs/${job.jobId}`} className={styles.inlineLink}>
                {job.jobTitle}
              </Link>
            )}
            {report.candidate_email && <span>{report.candidate_email}</span>}
            {report.interview_date && (
              <span>
                {new Date(report.interview_date).toLocaleDateString(undefined, {
                  year: "numeric",
                  month: "short",
                  day: "numeric",
                })}
              </span>
            )}
            {report.status && <span>Status: {report.status}</span>}
          </div>

          {languageGate && languageGate.status !== "not_required" && (
            <LanguageScreeningSection gate={languageGate} />
          )}

          <AssessmentQualitySection report={report} />

          <LanguageProfileSection report={report} />

          {(report.strengths?.length > 0 || report.concerns?.length > 0) && (
            <div className={styles.notesGrid}>
              {report.strengths?.length > 0 && (
                <div className={styles.notesColumn}>
                  <h2 className={styles.notesTitle}>Strengths</h2>
                  <ul className={styles.notesList}>
                    {report.strengths.map((item, index) => (
                      <li key={index}>{noteText(item)}</li>
                    ))}
                  </ul>
                </div>
              )}
              {report.concerns?.length > 0 && (
                <div className={styles.notesColumn}>
                  <h2 className={styles.notesTitle}>Concerns</h2>
                  <ul className={styles.notesList}>
                    {report.concerns.map((item, index) => (
                      <li key={index}>{noteText(item)}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {report.recording?.available && (
            <div className={styles.section}>
              <h2 className={styles.notesTitle}>Recording</h2>
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
              {!recordingUrl && !recordingError && (
                <p className={styles.status}>Loading recording…</p>
              )}
            </div>
          )}

          {report.transcript?.length > 0 && (
            <div className={styles.section}>
              <h2 className={styles.notesTitle}>Transcript</h2>
              <div className={styles.transcript}>
                {report.transcript.map((turn, index) => (
                  <p key={index} className={styles.transcriptLine}>
                    <span className={styles.transcriptRole}>
                      {turn.role === "user" ? "Candidate" : "Interviewer"}:
                    </span>{" "}
                    {turn.text}
                  </p>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
