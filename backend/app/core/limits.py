"""Per-session rate limits for the expensive endpoints.

Why this exists: the demo is a public URL with no accounts, and the two costly
operations - uploading a document (OCR can take seconds per page) and running an
audit - had no ceiling at all. One script could occupy the single worker and take
the demo down mid-judging.

Design notes, deliberately simple:

* **In memory, per process.** This is a single-node demo on one worker. A shared
  store would be a distributed-systems answer to a problem this deployment does
  not have, and it would make the limits look stronger than they are.
* **A sliding window, not a token bucket.** The question being asked is narrow -
  "has this session done too much in the last minute?" - and a window answers it
  in a dozen lines that a judge can read.
* **Keyed by session, not IP.** Sessions already identify the caller, and an IP
  key would punish a cyber-café or a shared phone where several people legitimately
  use the app from one address.

The limits are chosen to be far above any real use: a tenant uploads three
documents and runs a handful of audits. They stop a runaway script, not a person.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import HTTPException


class SlidingWindow:
    """Allow at most `limit` events per `window_seconds` per key."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, now: float | None = None) -> bool:
        """Record an event. Returns False when the key is over its limit."""
        now = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            cutoff = now - self.window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            return True

    def retry_after(self, key: str, now: float | None = None) -> int:
        """Seconds until the oldest event leaves the window."""
        now = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._hits.get(key)
            if not bucket:
                return 0
            return max(0, int(bucket[0] + self.window - now) + 1)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


# Uploads: a document can take seconds to OCR. Ten a minute is generous for a
# tenant with a lease, an amendment and a bill, and stops a loop dead.
UPLOAD_LIMIT = SlidingWindow(limit=10, window_seconds=60)
# Audits are cheaper but hit several queries each; twenty a minute still allows a
# tenant to correct a term and re-check a dozen times without friction.
AUDIT_LIMIT = SlidingWindow(limit=20, window_seconds=60)

# Exposed so the tests can put them back after a test that exhausts a window.
ALL_LIMITS = (UPLOAD_LIMIT, AUDIT_LIMIT)


def enforce(limiter: SlidingWindow, user_id: str, what: str) -> None:
    """Raise 429 with a plain-language message and a Retry-After value."""
    if limiter.hit(user_id):
        return
    wait = limiter.retry_after(user_id)
    raise HTTPException(
        status_code=429,
        detail={
            "error": {
                "code": "rate_limited",
                "message": (
                    f"Too many {what} in a short time. Please wait about {wait} "
                    f"second{'s' if wait != 1 else ''} and try again."
                ),
            }
        },
        headers={"Retry-After": str(wait)},
    )
