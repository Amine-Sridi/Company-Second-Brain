import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getInterviewReport } from "../api/interviews.js";
import { ApiError, requestBlob } from "../api/client.js";
import PageHeader from "../components/PageHeader.jsx";
import AssessmentPanel from "../components/AssessmentPanel.jsx";
import styles from "./ReportDetailPage.module.css";

// AssessmentPanel expects InterviewAssessment's flat shape (fluency_score,
// vocabulary_score, ...) -- VoiceHR's own report nests the same numbers under
// dimensions.fluency/.vocabulary/... (see report.py: get_report, the "short
// names for existing report consumers" block). This adapts one to the other so
// AssessmentPanel can be reused completely unmodified, per this task's own
// "reuse AssessmentPanel where appropriate" instruction -- not a redesign of it.
// VoiceHR's real evaluator output shapes strengths/concerns as timestamp-linked
// objects ({ text, timestamp }), not plain strings -- confirmed live against a
// real Gemini evaluation during this task's own verification, not assumed from
// the (untyped) response. Handles a plain string too, defensively, since nothing
// on the VoiceHR side guarantees the richer shape forever.
function noteText(note) {
  if (typeof note === "string") return note;
  return note?.text ?? String(note);
}

function toAssessmentShape(report) {
  return {
    overall_score: report.overall_score ?? null,
    cefr_level: report.cefr_level ?? null,
    fluency_score: report.dimensions?.fluency ?? null,
    vocabulary_score: report.dimensions?.vocabulary ?? null,
    grammar_score: report.dimensions?.grammar ?? null,
    pronunciation_score: report.dimensions?.pronunciation ?? null,
    comprehension_score: report.dimensions?.comprehension ?? null,
    inconclusive: Boolean(report.inconclusive),
    evaluation_status: report.evaluation_status,
  };
}

/**
 * One interview's full report -- candidate/RWS's own AssessmentPanel scores plus
 * the VoiceHR-only detail (strengths/concerns, transcript, recording) that only
 * this endpoint has. Self-sufficient from one fetch (GET /v1/interviews/
 * {applicationId}/report) -- a hard refresh on this URL reflects real backend
 * state, same rule every other page in this app follows. No job title shown here
 * (VoiceHR has no concept of RWS jobs); ReportsPage's list is where that lives.
 */
export default function ReportDetailPage() {
  const { applicationId } = useParams();

  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [recordingUrl, setRecordingUrl] = useState(null);
  const [recordingError, setRecordingError] = useState(null);

  useEffect(() => {
    loadReport();
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
        title={report ? report.candidate_name : "Report"}
        subtitle={report ? (report.language === "fr" ? "French interview" : "English interview") : undefined}
        actions={
          <Link to="/reports" className={styles.backLink}>
            Back to Reports
          </Link>
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
          <div className={styles.meta}>
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

          <AssessmentPanel assessment={toAssessmentShape(report)} />

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
