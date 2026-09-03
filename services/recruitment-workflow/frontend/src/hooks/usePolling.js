import { useEffect, useRef } from "react";

/**
 * Generic polling hook -- calls `callback` every `intervalMs` while `enabled` is true.
 *
 * - Does not fire `callback` immediately on mount -- the owning page's own initial
 *   fetch already covers the first load; this only schedules the repeat.
 * - Pauses entirely while the tab is hidden (`document.hidden`): the timer itself is
 *   stopped, not just the fetch skipped, and resumes via the `visibilitychange` event
 *   once the tab is visible again.
 * - Cleans up its timer and listener on unmount, and whenever `enabled`/`intervalMs`
 *   changes (e.g. `enabled` flipping to `false` because nothing is pending anymore
 *   stops polling immediately, no extra call needed from the owning page).
 * - Errors thrown by `callback` are swallowed here on purpose: a transient poll
 *   failure must never crash the page or blank out already-rendered data (see
 *   FRONTEND_HANDOFF.md §12). A caller that wants a failed poll to change what's on
 *   screen has to do that itself, inside its own callback, only on success.
 *
 * @param {() => void | Promise<void>} callback
 * @param {{ intervalMs: number, enabled: boolean }} options
 */
export function usePolling(callback, { intervalMs, enabled }) {
  // Always the latest callback, so a stale closure (e.g. capturing an old jobId or
  // old setState) never runs -- read fresh on every tick.
  const callbackRef = useRef(callback);
  callbackRef.current = callback;

  useEffect(() => {
    if (!enabled) return undefined;

    let intervalId = null;

    async function tick() {
      try {
        await callbackRef.current();
      } catch {
        // Swallowed deliberately -- see the doc comment above.
      }
    }

    function start() {
      if (intervalId !== null) return;
      intervalId = setInterval(tick, intervalMs);
    }

    function stop() {
      if (intervalId === null) return;
      clearInterval(intervalId);
      intervalId = null;
    }

    function handleVisibilityChange() {
      if (document.hidden) {
        stop();
      } else {
        start();
      }
    }

    if (!document.hidden) start();
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      stop();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [enabled, intervalMs]);
}
