import { useEffect, useState } from "react";
import { getCandidateIdentity } from "../api/candidates.js";

// Module-level cache, not React state: shared read access across every component
// instance for the lifetime of this browser tab, so the same candidate isn't
// re-fetched on every row re-render or every page revisit within a session. This is
// deliberately a plain JS Map, not sessionStorage/localStorage -- it resets on reload,
// keeping the frontend from becoming a place candidate PII is retained across sessions
// (see FRONTEND_HANDOFF.md §9). A failed lookup is cached too (status: "error"), so a
// broken candidate_id is retried on the next page load, not hammered on every render.
const identityCache = new Map();

/**
 * Resolves one candidate's display name/email for a MatchResult/Application row.
 *
 * A lookup failure (404 unknown candidate, 502 Resume Intelligence unavailable,
 * network error) never throws -- it resolves to status "error" with name/email null,
 * so one bad candidate_id can never break the rest of the page. Callers render a
 * fallback (e.g. the raw candidate_id) when status is "loading" or "error".
 *
 * @param {string|null|undefined} candidateId
 * @returns {{ status: "loading"|"ready"|"error", name: string|null, email: string|null }}
 */
export function useCandidateIdentity(candidateId) {
  const [state, setState] = useState(
    () => identityCache.get(candidateId) ?? { status: "loading", name: null, email: null }
  );

  useEffect(() => {
    if (!candidateId) {
      setState({ status: "error", name: null, email: null });
      return;
    }

    const cached = identityCache.get(candidateId);
    if (cached) {
      setState(cached);
      return;
    }

    let cancelled = false;
    setState({ status: "loading", name: null, email: null });

    getCandidateIdentity(candidateId)
      .then((identity) => {
        const resolved = { status: "ready", name: identity.name, email: identity.email ?? null };
        identityCache.set(candidateId, resolved);
        if (!cancelled) setState(resolved);
      })
      .catch(() => {
        const failed = { status: "error", name: null, email: null };
        identityCache.set(candidateId, failed);
        if (!cancelled) setState(failed);
      });

    return () => {
      cancelled = true;
    };
  }, [candidateId]);

  return state;
}
