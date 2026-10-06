import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";
import { ApiError } from "../api/client.js";
import styles from "./LoginPage.module.css";

export default function LoginPage() {
  const { login, isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  // Already logged in (e.g. navigated back to /login manually) -- go straight to the app.
  if (isAuthenticated) {
    return <Navigate to="/" replace />;
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      navigate("/", { replace: true });
    } catch (err) {
      // Shows the real backend detail (e.g. "Invalid email or password") verbatim --
      // the backend's own error messages are already specific and PII-safe.
      setError(err instanceof ApiError ? err.message : "Login failed. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className={styles.page}>
      <section className={styles.brandPanel} aria-label="Company Second Brain">
        <div className={styles.brand}>
          <span className={styles.brandMark} aria-hidden="true">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6">
              <circle cx="5" cy="6" r="2" />
              <circle cx="5" cy="14" r="2" />
              <circle cx="14" cy="10" r="2.6" />
              <path d="M6.8 6.9 11.7 9" />
              <path d="M6.8 13.1 11.7 11" />
            </svg>
          </span>
          Company Second Brain
        </div>
        <svg className={styles.constellation} viewBox="0 0 300 300" aria-hidden="true">
          <circle cx="150" cy="150" r="140" fill="none" stroke="#3b4150" strokeDasharray="4 6" />
          <circle cx="150" cy="150" r="85" fill="none" stroke="#3b4150" strokeDasharray="4 6" />
          <rect x="115" y="133" width="70" height="34" rx="8" fill="#ffffff" />
          <circle cx="150" cy="65" r="9" fill="#5b74cf" />
          <circle cx="228" cy="182" r="8" fill="#5b74cf" />
          <circle cx="12" cy="150" r="7" fill="none" stroke="#e08a3c" strokeWidth="3" />
          <circle cx="250" cy="40" r="6" fill="#3b4150" />
          <circle cx="66" cy="262" r="6" fill="#3b4150" />
        </svg>
        <div className={styles.pitch}>
          <span className={styles.pitchTitle}>Read every resume once. Reuse it for every job.</span>
          <span className={styles.pitchText}>AI reads the documents. The application scores, qualifies and keeps the decision with you.</span>
        </div>
      </section>

      <section className={styles.formPanel}>
        <form className={styles.card} onSubmit={handleSubmit}>
          <div className={styles.heading}>
            <h1 className={styles.title}>Sign in</h1>
            <span className={styles.subtitle}>Recruiter access</span>
          </div>

          <label className={styles.field}>
            <span>Email</span>
            <input
              type="email"
              required
              autoComplete="username"
              placeholder="name@company.com"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </label>

          <label className={styles.field}>
            <span>Password</span>
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>

          {error && (
            <div className={styles.error} role="alert">
              {error}
            </div>
          )}

          <button type="submit" className={styles.submitButton} disabled={submitting}>
            {submitting ? "Signing in…" : "Sign in"}
          </button>
          <span className={styles.hint}>After five failed attempts, sign-in pauses for up to five minutes.</span>
        </form>
      </section>
    </div>
  );
}
