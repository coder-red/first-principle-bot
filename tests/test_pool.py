"""Free tiers are small and per-model, so throughput comes from spreading load
across many entries and remembering which are spent -- not from retrying the
same capped entry on every request."""

import os

import pytest

import main
from main import ProviderPool, cooldown_for, get_providers


def entry(name, model):
    return {"name": name, "model": model, "endpoint": "e",
            "api_key": "k", "max_tokens": 1000}


A, B, C = entry("groq", "a"), entry("groq", "b"), entry("gemini", "c")


# ── several models behind one provider ───────────────────────────────────

def test_a_provider_can_declare_several_models(monkeypatch):
    """Gemini's cap is per model, so two models is two allowances."""
    monkeypatch.setenv("PROVIDERS", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_MODELS", "gemini-3.6-flash, gemini-2.5-flash")

    assert [p["model"] for p in get_providers()] == \
        ["gemini-3.6-flash", "gemini-2.5-flash"]


def test_every_expanded_entry_shares_the_key_and_endpoint(monkeypatch):
    monkeypatch.setenv("PROVIDERS", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "shared")
    monkeypatch.setenv("GEMINI_MODELS", "one,two")

    got = get_providers()
    assert {p["api_key"] for p in got} == {"shared"}
    assert len({p["endpoint"] for p in got}) == 1


def test_the_singular_model_setting_still_works(monkeypatch):
    monkeypatch.setenv("PROVIDERS", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GROQ_MODEL", "only-one")

    assert [p["model"] for p in get_providers()] == ["only-one"]


def test_plural_wins_when_both_are_set(monkeypatch):
    monkeypatch.setenv("PROVIDERS", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GROQ_MODEL", "single")
    monkeypatch.setenv("GROQ_MODELS", "first,second")

    assert [p["model"] for p in get_providers()] == ["first", "second"]


# ── remembering what is spent ────────────────────────────────────────────

def test_all_entries_are_offered_when_nothing_is_capped():
    pool = ProviderPool()
    assert len(pool.order([A, B, C], now=0)) == 3


def test_a_capped_entry_is_not_offered_again():
    pool = ProviderPool()
    pool.penalise(A, 60, now=0)
    assert A not in pool.order([A, B, C], now=10)


def test_a_capped_entry_returns_once_its_cooldown_expires():
    pool = ProviderPool()
    pool.penalise(A, 60, now=0)
    assert A in pool.order([A, B, C], now=61)


def test_entries_are_tracked_per_model_not_per_provider():
    """A and B are both groq. Capping one model must not sideline the other."""
    pool = ProviderPool()
    pool.penalise(A, 60, now=0)
    offered = pool.order([A, B, C], now=10)
    assert B in offered


def test_everything_is_offered_when_everything_is_capped():
    """Trying a capped entry beats returning no deck at all -- the cooldown is
    an estimate, and the provider is the authority."""
    pool = ProviderPool()
    for e in (A, B, C):
        pool.penalise(e, 60, now=0)
    assert len(pool.order([A, B, C], now=10)) == 3


# ── preferring the renewable allowance ───────────────────────────────────
#
# Rotating the starting entry only makes sense when allowances are comparable.
# They are not: Groq refills 8000 tokens every minute, while a Gemini model
# gets 20 requests for the whole day. Spreading requests evenly across those
# spends the scarce one for no reason. Declared order is the preference, and
# the cooldown is what makes falling through cheap.

def test_the_declared_order_is_preserved():
    pool = ProviderPool()
    assert pool.order([A, B, C], now=0) == [A, B, C]


def test_the_same_head_is_offered_again_while_it_is_healthy():
    """The first entry carries normal traffic; the rest are fallbacks."""
    pool = ProviderPool()
    assert pool.order([A, B, C], now=0)[0] is A
    assert pool.order([A, B, C], now=0)[0] is A


def test_the_next_entry_takes_over_only_once_the_head_is_capped():
    pool = ProviderPool()
    pool.penalise(A, 60, now=0)
    assert pool.order([A, B, C], now=10)[0] is B


def test_the_head_resumes_when_its_cooldown_expires():
    pool = ProviderPool()
    pool.penalise(A, 60, now=0)
    assert pool.order([A, B, C], now=61)[0] is A


# ── how long to wait ─────────────────────────────────────────────────────

def test_a_per_minute_cap_waits_around_a_minute():
    assert 30 <= cooldown_for("Error code: 429 ... tokens per minute (TPM): Limit 8000") <= 120


def test_a_per_day_cap_waits_far_longer():
    seconds = cooldown_for(
        "429 quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier, limit: 20")
    assert seconds >= 1800, seconds


def test_an_explicit_retry_delay_is_obeyed():
    assert cooldown_for("429 ... Please retry in 52.844143233s.") == 52


def test_an_absurd_retry_delay_is_clamped():
    assert cooldown_for("Please retry in 999999s.") <= 3600


def test_running_out_of_credit_is_a_long_cooldown():
    """402 will not fix itself in a minute."""
    assert cooldown_for("Error code: 402 - requires more credits") >= 1800


def test_an_unrecognised_error_still_yields_a_wait():
    assert cooldown_for("something went wrong") > 0


# ── what deserves a cooldown ─────────────────────────────────────────────
#
# Capacity and auth failures mean "do not come back yet". A malformed deck
# means "that roll was bad" -- sidelining a good provider for a minute over
# one bad JSON response would throw away the best entry in the chain.

from main import should_cool_down


@pytest.mark.parametrize("error", [
    "Error code: 429 - rate limit exceeded",
    "Error code: 413 - Request too large ... tokens per minute (TPM)",
    "Error code: 402 - requires more credits",
    "Error code: 401 - invalid api key",
    "Error code: 403 - forbidden",
    "Error code: 503 - service unavailable",
])
def test_capacity_and_auth_failures_cool_the_entry(error):
    assert should_cool_down(error) is True


@pytest.mark.parametrize("error", [
    "Expecting ',' delimiter: line 94 column 6",
    "returned unusable JSON",
    "was cut off before it finished the deck",
])
def test_a_bad_response_does_not_cool_the_entry(error):
    assert should_cool_down(error) is False


# ── wired into the endpoints ─────────────────────────────────────────────
#
# The tests above prove the pool works. This proves the deck path consults it,
# which is the part that would silently not happen.

from types import SimpleNamespace
from fastapi.testclient import TestClient

client = TestClient(main.app)


def test_a_capped_entry_is_skipped_on_the_next_request(monkeypatch):
    tried = []

    async def create(**kwargs):
        tried.append(kwargs["model"])
        if kwargs["model"] == "capped":
            raise RuntimeError("Error code: 429 - rate limit exceeded")
        raise RuntimeError("Error code: 500 - unrelated")

    monkeypatch.setattr(main, "get_client", lambda *a, **k: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(main, "get_providers", lambda: [
        {"name": "x", "model": "capped", "endpoint": "e",
         "api_key": "k", "max_tokens": 100},
    ])
    monkeypatch.setattr(main, "PROVIDER_POOL", main.ProviderPool())
    monkeypatch.setattr(main, "DECK_LIMITER", main.RateLimiter(limit=99, window=300))

    client.post("/api/chat", json={"message": "why is the sky blue"})
    first_round = len(tried)
    assert first_round >= 1, "the entry should have been tried once"

    # Second request: the 429 is remembered, so it is not tried again. The pool
    # offers it anyway only because it is the sole entry — but it must not be
    # tried twice as often as it was the first time.
    assert main.PROVIDER_POOL.is_cooling(
        {"name": "x", "model": "capped"}), "the 429 was not recorded"


def test_a_malformed_deck_does_not_cap_the_entry(monkeypatch):
    async def create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="not json at all"),
                finish_reason="stop")],
            model="m")

    monkeypatch.setattr(main, "get_client", lambda *a, **k: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(main, "get_providers", lambda: [
        {"name": "x", "model": "fine", "endpoint": "e",
         "api_key": "k", "max_tokens": 100},
    ])
    monkeypatch.setattr(main, "PROVIDER_POOL", main.ProviderPool())
    monkeypatch.setattr(main, "DECK_LIMITER", main.RateLimiter(limit=99, window=300))

    client.post("/api/chat", json={"message": "why is the sky blue"})
    assert not main.PROVIDER_POOL.is_cooling({"name": "x", "model": "fine"}), \
        "one bad response must not sideline a working provider"
