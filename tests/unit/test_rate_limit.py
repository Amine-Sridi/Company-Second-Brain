"""
Unit tests for shared.rate_limit.

The properties pinned here are the ones that separate a working rate limiter from one
that is itself a liability: that a blocked caller can eventually recover rather than
being permanently locked out, that the table cannot grow without bound under an IP-rotating
attack, and that the window genuinely slides.
"""
import time
import unittest

from shared.rate_limit import MAX_TRACKED_KEYS, RateLimiter, client_ip


class _Req:
    def __init__(self, host=None, forwarded=None):
        self.client = type("C", (), {"host": host})() if host else None
        self.headers = {"x-forwarded-for": forwarded} if forwarded else {}


class TestRateLimiter(unittest.TestCase):
    def test_allows_up_to_the_limit_then_blocks(self):
        rl = RateLimiter(limit=3, window_seconds=60)
        for i in range(3):
            allowed, _ = rl.check("k")
            self.assertTrue(allowed, f"attempt {i + 1} should be allowed")
        allowed, retry_after = rl.check("k")
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0)

    def test_keys_are_independent(self):
        rl = RateLimiter(limit=1, window_seconds=60)
        self.assertTrue(rl.check("a")[0])
        self.assertTrue(rl.check("b")[0], "one key's limit must not affect another")
        self.assertFalse(rl.check("a")[0])

    def test_a_blocked_attempt_is_not_recorded(self):
        """Otherwise a client that keeps retrying pushes its own window forward forever
        and can never recover -- a rate limit that degrades into a permanent ban."""
        rl = RateLimiter(limit=1, window_seconds=0.3)
        self.assertTrue(rl.check("k")[0])
        for _ in range(5):
            self.assertFalse(rl.check("k")[0])
        time.sleep(0.35)
        self.assertTrue(rl.check("k")[0], "must recover once the original window expires")

    def test_window_slides(self):
        rl = RateLimiter(limit=2, window_seconds=0.3)
        self.assertTrue(rl.check("k")[0])
        time.sleep(0.2)
        self.assertTrue(rl.check("k")[0])
        self.assertFalse(rl.check("k")[0], "two within the window means blocked")
        time.sleep(0.15)          # first hit has now aged out, second has not
        self.assertTrue(rl.check("k")[0])

    def test_retry_after_shrinks_as_the_window_ages(self):
        rl = RateLimiter(limit=1, window_seconds=1.0)
        rl.check("k")
        _, first = rl.check("k")
        time.sleep(0.3)
        _, later = rl.check("k")
        self.assertLess(later, first)

    def test_reset_clears_one_key_only(self):
        rl = RateLimiter(limit=1, window_seconds=60)
        rl.check("a")
        rl.check("b")
        rl.reset("a")
        self.assertTrue(rl.check("a")[0])
        self.assertFalse(rl.check("b")[0])

    def test_memory_is_bounded_against_key_rotation(self):
        """A limiter keyed by client IP is itself a DoS target: an attacker rotating
        source addresses would otherwise grow the table without bound."""
        rl = RateLimiter(limit=5, window_seconds=600)
        for i in range(MAX_TRACKED_KEYS + 500):
            rl.check(f"ip-{i}")
        self.assertLessEqual(len(rl._hits), MAX_TRACKED_KEYS)

    def test_expired_keys_are_pruned(self):
        rl = RateLimiter(limit=5, window_seconds=0.2)
        for i in range(10):
            rl.check(f"k{i}")
        self.assertEqual(len(rl._hits), 10)
        time.sleep(0.25)
        rl.check("trigger-prune")
        self.assertEqual(len(rl._hits), 1, "fully-expired windows should be dropped")

    def test_limit_must_be_positive(self):
        with self.assertRaises(ValueError):
            RateLimiter(limit=0, window_seconds=60)


class TestClientIp(unittest.TestCase):
    def test_uses_socket_peer_by_default(self):
        self.assertEqual(client_ip(_Req(host="203.0.113.7")), "203.0.113.7")

    def test_prefers_first_forwarded_entry(self):
        req = _Req(host="10.0.0.1", forwarded="198.51.100.4, 10.0.0.1")
        self.assertEqual(client_ip(req), "198.51.100.4")

    def test_falls_back_when_nothing_is_available(self):
        self.assertEqual(client_ip(_Req()), "unknown")

    def test_blank_forwarded_header_falls_back_to_peer(self):
        self.assertEqual(client_ip(_Req(host="10.0.0.1", forwarded="   ")), "10.0.0.1")


if __name__ == "__main__":
    unittest.main()
