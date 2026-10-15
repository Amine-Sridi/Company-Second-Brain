"""
shared/rate_limit.py
---------------------
A small in-process sliding-window rate limiter.

Why hand-rolled rather than SlowAPI: this project has exactly two endpoints that need
limiting (an unauthenticated login and, in VoiceHR, some unauthenticated bearer-token
routes), the deployment is single-instance, and `docs/ARCHITECTURE_DECISIONS.md`'s amended constraint scopes
Redis to resume ingestion only. A dependency that brings its own middleware, storage
abstraction and config surface would be more machinery than the problem justifies. This
is ~60 lines with no new dependency and the same shape as the `_resend_cooldowns` dicts
already in `main.py`.

**Single-instance, and that is a real limitation, not an oversight.** State lives in this
process, so with two replicas each would allow the full quota -- the limit doubles. It is
listed with the rest of the in-memory state in SCALING_AND_FUTURE_IMPROVEMENTS.md §9,
which is the checklist for the day someone sets `replicas: 2`. For a brute-force gate on
a single-instance deployment it is genuinely sufficient; for a multi-instance one it must
move to Redis or Postgres.

Memory is bounded on purpose. A limiter keyed by client IP is itself a denial-of-service
target: an attacker rotating source addresses would grow an unbounded dict. Expired
windows are pruned on every check, and the table is hard-capped -- see `_prune`.
"""
from __future__ import annotations

import threading
import time
from collections import deque

# Hard ceiling on tracked keys. Past this the oldest-seen keys are dropped, which can let
# an attacker who is rotating IPs fast enough evict their own record and regain a fresh
# quota. That is an accepted trade: the alternative is unbounded memory growth, which is a
# worse failure. A real multi-instance deployment should use a shared store instead.
MAX_TRACKED_KEYS = 10_000


class RateLimiter:
    """
    Sliding-window limiter: at most `limit` events per `window_seconds` per key.

    Sliding rather than fixed-window because a fixed window lets through 2x the limit
    across a boundary (all of one window's quota at its end, all of the next at its
    start), which for a login gate is exactly the burst worth preventing.
    """

    def __init__(self, limit: int, window_seconds: float):
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self.limit = limit
        self.window = float(window_seconds)
        self._hits: dict[str, deque[float]] = {}
        # Endpoints may be hit concurrently by different workers in one process; the
        # deque mutations below are not atomic as a group.
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        """Drop keys whose window has fully expired, then enforce the hard cap."""
        stale = [k for k, hits in self._hits.items() if not hits or now - hits[-1] > self.window]
        for k in stale:
            del self._hits[k]
        # Leave room for the key `check` is about to insert, so the table never exceeds
        # MAX_TRACKED_KEYS rather than settling one above it.
        target = MAX_TRACKED_KEYS - 1
        if len(self._hits) > target:
            # Evict least-recently-seen first.
            for k in sorted(self._hits, key=lambda k: self._hits[k][-1])[
                : len(self._hits) - target
            ]:
                del self._hits[k]

    def check(self, key: str) -> tuple[bool, float]:
        """
        Records an attempt against `key`.

        Returns (allowed, retry_after_seconds). `retry_after_seconds` is 0 when allowed.
        A blocked attempt is deliberately **not** recorded: otherwise a client hammering
        the endpoint would keep pushing its own window forward and lock itself out
        indefinitely, which turns a rate limit into a permanent ban.
        """
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                retry_after = self.window - (now - hits[0])
                return False, max(0.0, retry_after)
            hits.append(now)
            return True, 0.0

    def reset(self, key: str | None = None) -> None:
        """Clears one key, or everything. Used by tests and by a successful login (see
        `main.py`), so a legitimate user who mistyped twice is not still penalised."""
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


def client_ip(request) -> str:
    """
    Best-effort client identity for an anonymous caller.

    X-Forwarded-For is honoured because this sits behind a reverse proxy in any real
    deployment -- but only the FIRST entry, and only as a *key*, never as a trust
    decision. A client can forge the header and get a different bucket; that is inherent
    to IP-based limiting and is why the login limiter below also keys on the submitted
    email, which an attacker targeting one account cannot vary.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"
