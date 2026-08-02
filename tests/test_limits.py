"""Every model call is spent from one shared budget, and the endpoints that
trigger them are unauthenticated. The limiter is the only thing standing
between a public URL and someone else's bill."""

import pytest
from types import SimpleNamespace

from main import RateLimiter, client_key


def request_with(headers=None, host="1.2.3.4"):
    return SimpleNamespace(
        headers=headers or {},
        client=SimpleNamespace(host=host) if host else None,
    )


# ── identifying the caller ───────────────────────────────────────────────

def test_falls_back_to_the_socket_address():
    assert client_key(request_with(host="203.0.113.7")) == "203.0.113.7"


def test_prefers_the_forwarded_client_behind_a_proxy():
    """On a host like Render every request arrives from the proxy, so without
    this every visitor shares one bucket and the first few lock out the rest."""
    req = request_with({"x-forwarded-for": "198.51.100.9, 10.0.0.1"})
    assert client_key(req) == "198.51.100.9"


def test_takes_the_original_client_not_the_last_proxy():
    req = request_with({"x-forwarded-for": "198.51.100.9, 10.0.0.1, 10.0.0.2"})
    assert client_key(req) == "198.51.100.9"


def test_ignores_an_empty_forwarded_header():
    req = request_with({"x-forwarded-for": "   "}, host="203.0.113.7")
    assert client_key(req) == "203.0.113.7"


def test_a_request_with_no_client_still_yields_a_key():
    assert client_key(request_with(host=None)) == "unknown"


# ── the limiter ──────────────────────────────────────────────────────────

def test_allows_up_to_the_limit():
    limiter = RateLimiter(limit=3, window=60)
    assert [limiter.allow("a", now=0) for _ in range(3)] == [True, True, True]


def test_refuses_the_one_past_the_limit():
    limiter = RateLimiter(limit=2, window=60)
    limiter.allow("a", now=0)
    limiter.allow("a", now=0)
    assert limiter.allow("a", now=0) is False


def test_callers_do_not_share_a_budget():
    limiter = RateLimiter(limit=1, window=60)
    assert limiter.allow("a", now=0) is True
    assert limiter.allow("b", now=0) is True


def test_the_window_reopens_once_it_has_passed():
    limiter = RateLimiter(limit=1, window=60)
    limiter.allow("a", now=0)
    assert limiter.allow("a", now=59) is False
    assert limiter.allow("a", now=61) is True


def test_a_refused_request_does_not_extend_the_block():
    """A caller hammering the endpoint must still be let back in on schedule,
    otherwise a retry loop locks them out forever."""
    limiter = RateLimiter(limit=1, window=60)
    limiter.allow("a", now=0)
    for t in range(1, 60):
        limiter.allow("a", now=t)
    assert limiter.allow("a", now=61) is True


def test_retry_after_reports_the_wait():
    limiter = RateLimiter(limit=1, window=60)
    limiter.allow("a", now=10)
    assert limiter.retry_after("a", now=30) == 40


def test_retry_after_is_never_negative():
    limiter = RateLimiter(limit=1, window=60)
    limiter.allow("a", now=0)
    assert limiter.retry_after("a", now=999) == 0


def test_idle_callers_are_forgotten_rather_than_accumulating():
    """The limiter lives for the life of the process; without eviction it is
    an unbounded dict keyed by anything that can reach the port."""
    limiter = RateLimiter(limit=5, window=60)
    for n in range(50):
        limiter.allow(f"caller-{n}", now=0)
    limiter.allow("late", now=10_000)
    assert len(limiter._hits) == 1


# ── wired into the endpoints ─────────────────────────────────────────────
#
# The unit tests above prove the limiter counts. These prove it is actually
# reached, which is the part that would silently not happen.

import main
from fastapi.testclient import TestClient

client = TestClient(main.app)


def post_deck():
    return client.post("/api/chat/stream", json={"message": "Why is the sky blue?"})


def test_the_deck_endpoint_refuses_past_its_limit(monkeypatch):
    monkeypatch.setattr(main, "DECK_LIMITER", RateLimiter(limit=2, window=300))
    # No providers, so the endpoint returns an error deck without touching the
    # network — the limiter is what is under test, not generation.
    monkeypatch.setattr(main, "get_providers", lambda: [])
    assert post_deck().status_code == 200
    assert post_deck().status_code == 200
    assert post_deck().status_code == 429


def test_a_refusal_says_how_long_to_wait(monkeypatch):
    monkeypatch.setattr(main, "DECK_LIMITER", RateLimiter(limit=1, window=300))
    # No providers, so the endpoint returns an error deck without touching the
    # network — the limiter is what is under test, not generation.
    monkeypatch.setattr(main, "get_providers", lambda: [])
    post_deck()
    refused = post_deck()
    assert refused.status_code == 429
    assert refused.headers["retry-after"].isdigit()


def test_browsing_a_cached_sector_is_not_charged(monkeypatch):
    """Only generation costs a call. Charging for a pool hit would make the
    rail unusable after a few clicks."""
    async def fake_generate(sector, avoid, count):
        return [{"question": f"q{n}?", "hook": ""} for n in range(count)]

    monkeypatch.setattr(main, "generate_questions", fake_generate)
    monkeypatch.setattr(main, "EXPLORE_LIMITER", RateLimiter(limit=1, window=300))
    main.EXPLORE_POOLS.clear()

    assert client.get("/api/explore/money").status_code == 200   # generates
    assert client.get("/api/explore/money").status_code == 200   # from the pool
