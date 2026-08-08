"""Per-caller rate limiting.

Every deck and every sector costs a model call from one shared budget. On a
public URL the limiter is the last thing between a visitor and someone else's
bill — optional token auth (fpb.auth) sits in front of it, but is off unless
APP_ACCESS_TOKEN is set.

A fixed window per caller, in memory. That is enough for one process, which is
what this app is: the explore pools already assume a single instance. Behind
more than one instance the limits become per-instance.
"""

import time
from typing import Dict, List, Optional

from fastapi import HTTPException

from fpb.telemetry import COUNTERS, LOG


def client_key(request) -> str:
    """Who to charge a request to.

    Behind a proxy every request arrives from the proxy's address, so the
    socket peer is useless — without the forwarded header every visitor would
    share one bucket and the first few would lock out everyone else. The first
    entry is the original client; the rest are hops.
    """
    forwarded = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


class RateLimiter:
    def __init__(self, limit: int, window: int, name: str = "requests", store=None):
        self.limit = limit
        self.window = window
        # Labels the counter and the log line, and names the bucket in the
        # store. An unnamed limiter built in a test still behaves identically.
        self.name = name
        self._hits: Dict[str, List[float]] = {}
        # Optional fpb.store.StateStore. With one, restarting the process is
        # no longer a way to clear your own rate limit.
        self._store = store

    def _recent(self, key: str, now: float) -> List[float]:
        cutoff = now - self.window
        if self._store is not None:
            return self._store.hits(self.name, key, cutoff)
        return [t for t in self._hits.get(key, []) if t > cutoff]

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        self._evict(now)

        recent = self._recent(key, now)
        if len(recent) >= self.limit:
            # Deliberately not recording the refused attempt: counting it would
            # let a retry loop hold its own window open indefinitely.
            if self._store is None:
                self._hits[key] = recent
            return False

        if self._store is not None:
            self._store.record_hit(self.name, key, now)
        else:
            recent.append(now)
            self._hits[key] = recent
        return True

    def retry_after(self, key: str, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        recent = self._recent(key, now)
        if not recent:
            return 0
        return max(0, int(recent[0] + self.window - now))

    def _evict(self, now: float) -> None:
        """Drop callers with nothing left in the window. Without this the dict
        grows forever, keyed by anything that can reach the port."""
        cutoff = now - self.window
        if self._store is not None:
            self._store.evict_hits(self.name, cutoff)
            return
        for key in [k for k, v in self._hits.items() if not any(t > cutoff for t in v)]:
            del self._hits[key]


def enforce(limiter: RateLimiter, request) -> None:
    key = client_key(request)
    if not limiter.allow(key):
        wait = limiter.retry_after(key)
        COUNTERS.bump(f"ratelimit.{limiter.name}.refused")
        # The caller key is an IP. Logged because a limiter that fires
        # constantly is either misconfigured or being abused, and you cannot
        # tell which without knowing whether it is one caller or many.
        LOG.info("rate limited %s on %s; retry in %ds", key, limiter.name, wait)
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Try again in {wait} seconds.",
            headers={"Retry-After": str(wait)},
        )
