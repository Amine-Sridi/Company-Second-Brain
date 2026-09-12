import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { uploadResume } from "../api/resumes.js";
import { ApiError } from "../api/client.js";
import PageHeader from "../components/PageHeader.jsx";
import styles from "./ResumeUploadPage.module.css";

const ACCEPTED_EXTENSIONS = ".pdf,.docx,.jpg,.jpeg,.png";
const ACCEPTED_LABEL = "PDF, DOCX, JPG, JPEG, or PNG";

// Phase 29 (see FRONTEND_HANDOFF.md): a real-browser audit found this drop
// zone read as a bland, text-only box compared to the rest of the app's
// visual polish -- same hand-authored inline-SVG style Sidebar.jsx's own nav
// icons already use, not a new icon font/library dependency.
function IconUploadGlyph(props) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...props}>
      <path d="M12 15V4" strokeLinecap="round" />
      <path d="M7 8.5 12 3.5 17 8.5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 15.5v3A2.5 2.5 0 0 0 6.5 21h11a2.5 2.5 0 0 0 2.5-2.5v-3" strokeLinecap="round" />
    </svg>
  );
}

export default function ResumeUploadPage() {
  const [file, setFile] = useState(null);
  const [status, setStatus] = useState("idle"); // idle | uploading | success | error
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef(null);

  const busy = status === "uploading";

  function chooseFile(chosenFile) {
    if (!chosenFile || busy) return;
    setFile(chosenFile);
    setError(null);
    setStatus("idle");
  }

  function handleInputChange(event) {
    chooseFile(event.target.files?.[0] ?? null);
    // Allow re-selecting the same file after a reset.
    event.target.value = "";
  }

  function handleDrop(event) {
    event.preventDefault();
    setDragActive(false);
    if (busy) return;
    chooseFile(event.dataTransfer.files?.[0] ?? null);
  }

  function handleDragOver(event) {
    event.preventDefault();
    if (busy) return;
    setDragActive(true);
  }

  function handleDragLeave() {
    setDragActive(false);
  }

  function openFilePicker() {
    if (busy) return;
    fileInputRef.current?.click();
  }

  function clearSelectedFile(event) {
    event.stopPropagation();
    setFile(null);
    setError(null);
  }

  async function handleUpload() {
    if (!file || busy) return;
    setStatus("uploading");
    setError(null);
    try {
      // Real POST /v1/resumes call -- browser talks only to RWS; RWS forwards to
      // Resume Intelligence server-side using its own service key.
      const response = await uploadResume(file);
      setResult(response);
      setStatus("success");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed. Please try again.");
      setStatus("error");
    }
  }

  function handleReset() {
    setFile(null);
    setResult(null);
    setError(null);
    setStatus("idle");
  }

  if (status === "success" && result) {
    return (
      <div className={styles.page}>
        <PageHeader
          title="Upload Resume"
          subtitle="Add a candidate's resume to the talent pool."
          variant="glass"
        />

        <div className={styles.success} role="status">
          <p className={styles.successTitle}>
            Resume uploaded — the candidate is now in the pool.
          </p>
          <div className={styles.successField}>
            <span className={styles.successLabel}>Candidate ID</span>
            <span className={styles.successValue}>{result.candidate_id}</span>
          </div>
          <div className={styles.successField}>
            <span className={styles.successLabel}>Status</span>
            <span>
              {result.is_new
                ? "Newly added to the candidate pool"
                : "Matched an existing candidate (already in the pool)"}
            </span>
          </div>
        </div>

        <div className={styles.actions}>
          {/* The created/matched candidate is never a dead-end UUID -- this is the
              same real candidate_id CandidateDetailPage already renders. */}
          <Link to={`/candidates/${result.candidate_id}`} className={styles.uploadButton}>
            View Candidate
          </Link>
          <button type="button" className={styles.resetButton} onClick={handleReset}>
            Upload another resume
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <PageHeader
        title="Upload Resume"
        subtitle="Add a candidate's resume to the talent pool."
        variant="glass"
      />

      <div
        className={[
          styles.dropZone,
          dragActive ? styles.dropZoneActive : "",
          busy ? styles.dropZoneDisabled : "",
        ]
          .filter(Boolean)
          .join(" ")}
        onClick={openFilePicker}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        role="button"
        tabIndex={0}
        aria-disabled={busy}
      >
        <IconUploadGlyph className={styles.dropZoneIcon} />
        <p>Click to choose a file, or drag one here</p>
        <p className={styles.dropZoneHint}>Supported formats: {ACCEPTED_LABEL}</p>
        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPTED_EXTENSIONS}
          className={styles.fileInput}
          onChange={handleInputChange}
          disabled={busy}
        />
      </div>

      {file && (
        <div className={styles.selectedFile}>
          <span className={styles.selectedFileName}>{file.name}</span>
          {!busy && (
            <button type="button" className={styles.clearButton} onClick={clearSelectedFile}>
              Remove
            </button>
          )}
        </div>
      )}

      {error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}

      <div className={styles.actions}>
        <button
          type="button"
          className={styles.uploadButton}
          onClick={handleUpload}
          disabled={!file || busy}
        >
          {busy ? "Uploading…" : "Upload Resume"}
        </button>
      </div>
    </div>
  );
}
