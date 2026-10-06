import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { uploadResume } from "../api/resumes.js";
import { ApiError } from "../api/client.js";
import PageHeader from "../components/PageHeader.jsx";
import styles from "./ResumeUploadPage.module.css";

const ACCEPTED_EXTENSIONS = ".pdf,.docx,.jpg,.jpeg,.png";
const ACCEPTED_LABEL = "PDF, DOCX, JPG or PNG";

function IconUploadGlyph(props) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...props}>
      <path d="M12 15V4" strokeLinecap="round" />
      <path d="M7 8.5 12 3.5 17 8.5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 15.5v3A2.5 2.5 0 0 0 6.5 21h11a2.5 2.5 0 0 0 2.5-2.5v-3" strokeLinecap="round" />
    </svg>
  );
}

const STATUS_TEXT = {
  waiting: "Waiting",
  uploading: "Reading and structuring",
  created: "Profile created",
  existing: "Already in the pool",
  failed: "Failed",
};

let nextId = 1;

/**
 * Adds resumes to the pool. Several files can be chosen at once; they are sent one
 * after another through the same POST /v1/resumes call (RWS forwards each to Resume
 * Intelligence with its own service key), and each row shows its own outcome. A
 * failed file never creates a half-empty candidate, so retrying it is safe.
 *
 * The browser waits for each file to finish (the endpoint answers once the profile
 * exists), so the list only progresses while this page stays open.
 */
export default function ResumeUploadPage() {
  const [items, setItemsState] = useState([]); // { id, file, status, candidateId?, error? }
  const [dragActive, setDragActive] = useState(false);
  const [running, setRunning] = useState(false);
  const fileInputRef = useRef(null);
  // The upload loop runs across several awaits, so it reads the latest list from this
  // ref instead of a stale render's `items`.
  const itemsRef = useRef([]);

  function setItems(change) {
    setItemsState((current) => {
      const next = change(current);
      itemsRef.current = next;
      return next;
    });
  }

  function addFiles(fileList) {
    const files = Array.from(fileList ?? []);
    if (files.length === 0) return;
    setItems((current) => [...current, ...files.map((file) => ({ id: nextId++, file, status: "waiting" }))]);
  }

  function update(id, patch) {
    setItems((current) => current.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }

  async function uploadOne(item) {
    update(item.id, { status: "uploading", error: null });
    try {
      const response = await uploadResume(item.file);
      update(item.id, { status: response.is_new ? "created" : "existing", candidateId: response.candidate_id });
    } catch (err) {
      update(item.id, {
        status: "failed",
        error: err instanceof ApiError ? err.message : "Upload failed. Please try again.",
      });
    }
  }

  async function processWaiting() {
    if (running) return;
    setRunning(true);
    // One file at a time; files added while running are picked up too.
    let next = itemsRef.current.find((item) => item.status === "waiting");
    while (next) {
      await uploadOne(next);
      next = itemsRef.current.find((item) => item.status === "waiting");
    }
    setRunning(false);
  }

  function handleInputChange(event) {
    addFiles(event.target.files);
    event.target.value = ""; // allow choosing the same file again later
  }

  function handleDrop(event) {
    event.preventDefault();
    setDragActive(false);
    addFiles(event.dataTransfer.files);
  }

  function openFilePicker() {
    fileInputRef.current?.click();
  }

  function retry(item) {
    update(item.id, { status: "waiting", error: null });
  }

  function removeItem(item) {
    setItems((current) => current.filter((entry) => entry.id !== item.id));
  }

  const waitingCount = items.filter((item) => item.status === "waiting").length;
  const doneCount = items.filter((item) => item.status === "created" || item.status === "existing").length;

  return (
    <div className={styles.page}>
      <PageHeader
        title="Add candidates to the pool"
        subtitle="Each resume is read, structured and stored as a candidate profile. Files are processed one after another."
      />

      <div
        className={`${styles.dropZone} ${dragActive ? styles.dropZoneActive : ""}`}
        onClick={openFilePicker}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openFilePicker();
          }
        }}
        onDrop={handleDrop}
        onDragOver={(event) => {
          event.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        role="button"
        tabIndex={0}
        aria-label="Choose resume files"
      >
        <IconUploadGlyph className={styles.dropZoneIcon} />
        <span className={styles.dropTitle}>Drop resumes here, or choose files</span>
        <span className={styles.dropZoneHint}>{ACCEPTED_LABEL} · several files at once</span>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={ACCEPTED_EXTENSIONS}
          className={styles.fileInput}
          onChange={handleInputChange}
          tabIndex={-1}
        />
      </div>

      {items.length > 0 && (
        <section className={styles.queue} aria-label="Processing queue">
          <div className={styles.queueHead}>
            <h2 className={styles.queueTitle}>Processing queue</h2>
            <span className={styles.queueSummary}>
              {doneCount} of {items.length} processed
            </span>
            <button type="button" className={styles.uploadButton} onClick={processWaiting} disabled={running || waitingCount === 0}>
              {running ? "Processing…" : waitingCount > 0 ? `Upload ${waitingCount} file${waitingCount === 1 ? "" : "s"}` : "All files sent"}
            </button>
          </div>
          <ul className={styles.queueList}>
            {items.map((item) => (
              <li key={item.id} className={styles.queueRow}>
                <span className={styles.fileCell}>
                  <span className={styles.fileName}>{item.file.name}</span>
                  <span className={styles.progressTrack}>
                    <span className={`${styles.progressFill} ${styles[`progress_${item.status}`]}`} />
                  </span>
                  {item.error && <span className={styles.rowError}>{item.error}</span>}
                </span>
                <span className={`${styles.badge} ${styles[`badge_${item.status}`]}`}>{STATUS_TEXT[item.status]}</span>
                <span className={styles.rowAction}>
                  {(item.status === "created" || item.status === "existing") && (
                    <Link to={`/candidates/${item.candidateId}`}>Open candidate</Link>
                  )}
                  {item.status === "failed" && (
                    <button type="button" className={styles.rowButton} onClick={() => retry(item)} disabled={running}>
                      Retry
                    </button>
                  )}
                  {item.status === "waiting" && !running && (
                    <button type="button" className={styles.rowButton} onClick={() => removeItem(item)}>
                      Remove
                    </button>
                  )}
                </span>
              </li>
            ))}
          </ul>
          <p className={styles.queueNote}>A failed file never creates a half-empty candidate, so retrying it is safe.</p>
        </section>
      )}
    </div>
  );
}
