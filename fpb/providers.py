"""Which provider entry to try, and when to stop trying one."""

import os
import re
import time
from functools import lru_cache
from typing import Dict, List, Optional

from openai import AsyncOpenAI

from fpb.telemetry import COUNTERS, LOG

# Ceiling on any cooldown. A wrong guess should cost minutes, not a day.
MAX_COOLDOWN = 3600
DEFAULT_COOLDOWN = 60


def cooldown_for(error: str) -> int:
    """How long to stop using an entry after it refused a request.

    The provider is the authority when it says so — Gemini returns "Please
    retry in 52.8s" — otherwise the wording distinguishes a per-minute cap
    (clears itself shortly) from a per-day one or an empty wallet (will not).
    """
    said = re.search(r"retry in ([0-9]+(?:\.[0-9]+)?)s", error, re.I)
    if said:
        return max(1, min(MAX_COOLDOWN, int(float(said.group(1)))))

    low = error.lower()
    if "perday" in low.replace("-", "").replace("_", "") or "per day" in low \
            or "per-day" in low or "402" in low or "credit" in low or "billing" in low:
        return MAX_COOLDOWN
    return DEFAULT_COOLDOWN


# HTTP statuses that mean "this entry cannot serve you right now". Anything
# else — a malformed deck, a truncated one — is a bad roll from a working
# provider, and sidelining it for a minute would throw away the best entry in
# the chain over one unlucky response.
CAPACITY_CODES = ("400", "401", "402", "403", "413", "429", "500", "502", "503", "529")


def should_cool_down(error: str) -> bool:
    low = error.lower()
    if any(f"error code: {code}" in low for code in CAPACITY_CODES):
        return True
    return any(word in low for word in
               ("rate limit", "quota", "too many requests", "overloaded",
                "insufficient", "billing", "payment required"))


class ProviderPool:
    """Which entries are worth trying right now, and in what order.

    Declared order is the preference — the first entry carries normal traffic
    and the rest are fallbacks. What makes that work is remembering what is
    spent, so a capped entry is skipped instead of costing a doomed call on
    every request.

    Deliberately NOT round-robin. Spreading requests evenly assumes the
    allowances are comparable, and they are not: Groq refills 8000 tokens every
    minute while a Gemini model gets 20 requests for the entire day. Rotating
    between those spends the scarce one to relieve the renewable one.
    """

    def __init__(self, store=None):
        self._until: Dict[str, float] = {}
        # Optional fpb.store.StateStore. With one, a spent daily allowance is
        # still remembered after a restart — which is the difference between
        # skipping an exhausted provider and paying for a doomed call on the
        # first request after every wake-up.
        self._store = store

    @staticmethod
    def _key(provider: dict) -> str:
        return f"{provider['name']}:{provider['model']}"

    def _live(self, now: float) -> Dict[str, float]:
        return self._until if self._store is None else self._store.cooldowns(now)

    def penalise(self, provider: dict, seconds: int,
                 now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        key = self._key(provider)
        self._until[key] = now + seconds
        if self._store is not None:
            self._store.set_cooldown(key, now + seconds)
        COUNTERS.bump(f"provider.{provider['name']}.cooldown")
        LOG.warning("cooling %s for %ds", key, seconds)

    def is_cooling(self, provider: dict, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        return self._live(now).get(self._key(provider), 0) > now

    def order(self, providers: List[dict], now: Optional[float] = None) -> List[dict]:
        now = time.time() if now is None else now
        # Read the cooldowns once rather than once per entry: with a store
        # behind it, is_cooling is a query.
        live = self._live(now)
        healthy = [p for p in providers if live.get(self._key(p), 0) <= now]

        # Everything is cooling: try anyway rather than refuse outright. The
        # cooldown is an estimate; the provider decides.
        if not healthy:
            LOG.warning("every provider is cooling; trying the full chain anyway")
        return healthy or list(providers)

    def cooling_now(self, providers: List[dict],
                    now: Optional[float] = None) -> List[str]:
        """Which entries are sidelined, for /api/health. Keys only, no secrets."""
        now = time.time() if now is None else now
        live = self._live(now)
        return [self._key(p) for p in providers if live.get(self._key(p), 0) > now]


@lru_cache(maxsize=8)
def get_client(api_key: str, endpoint: str) -> AsyncOpenAI:
    """One pooled client per (key, endpoint) instead of one per request."""
    return AsyncOpenAI(
        api_key=api_key,
        base_url=endpoint,
        default_headers={
            "HTTP-Referer": os.environ.get("PUBLIC_URL", "http://127.0.0.1:8000"),
            "X-Title": "First Principle Bot",
        },
    )
