"""Optional SQLite persistence.

The point of every test here is the same: state that used to die with the
process now outlives it. So each one builds a component, throws it away, and
builds a *fresh* one against the same file — because a test that reused the
object would pass even if nothing were being written.
"""

import pytest

from fpb.explore import ExplorePool
from fpb.limits import RateLimiter
from fpb.providers import ProviderPool
from fpb.store import StateStore, open_store


@pytest.fixture
def store(tmp_path):
    s = StateStore(str(tmp_path / "state.db"))
    yield s
    s.close()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "state.db")


# ── the switch ───────────────────────────────────────────────────────────

def test_no_store_without_the_env_var():
    """The zero-config run must not grow a database file."""
    assert open_store() is None


def test_a_blank_env_var_is_still_no_store(monkeypatch):
    monkeypatch.setenv("STATE_DB", "   ")
    assert open_store() is None


def test_the_env_var_opens_a_store(monkeypatch, db_path):
    monkeypatch.setenv("STATE_DB", db_path)
    opened = open_store()
    assert opened is not None
    assert opened.path == db_path
    opened.close()


def test_a_missing_parent_directory_is_created(tmp_path):
    nested = tmp_path / "does" / "not" / "exist" / "state.db"
    StateStore(str(nested)).close()
    assert nested.exists()


# ── cooldowns ────────────────────────────────────────────────────────────

GEMINI = {"name": "gemini", "model": "flash"}
GROQ = {"name": "groq", "model": "llama"}


def test_a_cooldown_survives_a_restart(store):
    ProviderPool(store=store).penalise(GEMINI, 3600, now=1000.0)

    # A brand new pool, as though the process had restarted.
    revived = ProviderPool(store=store)
    assert revived.is_cooling(GEMINI, now=1100.0)
    assert not revived.is_cooling(GROQ, now=1100.0)


def test_a_cooldown_still_expires(store):
    ProviderPool(store=store).penalise(GEMINI, 60, now=1000.0)
    assert not ProviderPool(store=store).is_cooling(GEMINI, now=1100.0)


def test_a_restarted_pool_skips_the_exhausted_provider(store):
    """The reason this exists: without it, the first request after every
    restart pays for a call to a provider whose allowance is already spent."""
    ProviderPool(store=store).penalise(GEMINI, 3600, now=1000.0)
    order = ProviderPool(store=store).order([GEMINI, GROQ], now=1100.0)
    assert order == [GROQ]


def test_everything_cooling_still_falls_back_to_the_full_chain(store):
    pool = ProviderPool(store=store)
    pool.penalise(GEMINI, 3600, now=1000.0)
    pool.penalise(GROQ, 3600, now=1000.0)
    assert ProviderPool(store=store).order([GEMINI, GROQ], now=1100.0) == [GEMINI, GROQ]


def test_expired_cooldowns_are_swept(store):
    ProviderPool(store=store).penalise(GEMINI, 60, now=1000.0)
    store.cooldowns(now=2000.0)                      # the read does the sweep
    assert store.cooldowns(now=0.0) == {}


def test_health_reads_cooling_from_the_store(store):
    ProviderPool(store=store).penalise(GEMINI, 3600, now=1000.0)
    revived = ProviderPool(store=store)
    assert revived.cooling_now([GEMINI, GROQ], now=1100.0) == ["gemini:flash"]


# ── rate limits ──────────────────────────────────────────────────────────

def limiter(store):
    return RateLimiter(limit=2, window=300, name="deck", store=store)


def test_rate_limits_survive_a_restart(store):
    """Otherwise restarting the process is a way to clear your own limit."""
    first = limiter(store)
    assert first.allow("1.2.3.4", now=1000.0)
    assert first.allow("1.2.3.4", now=1001.0)

    assert not limiter(store).allow("1.2.3.4", now=1002.0)


def test_the_window_still_slides(store):
    limiter(store).allow("1.2.3.4", now=1000.0)
    limiter(store).allow("1.2.3.4", now=1001.0)
    # Far enough past the 300s window that both hits have aged out.
    assert limiter(store).allow("1.2.3.4", now=2000.0)


def test_callers_are_still_counted_separately(store):
    limiter(store).allow("1.2.3.4", now=1000.0)
    limiter(store).allow("1.2.3.4", now=1001.0)
    assert limiter(store).allow("5.6.7.8", now=1002.0)


def test_buckets_do_not_bleed_into_each_other(store):
    """The deck and explore limiters share one table and must not share counts."""
    RateLimiter(limit=1, window=300, name="deck", store=store).allow("ip", now=1000.0)
    assert RateLimiter(limit=1, window=300, name="explore",
                       store=store).allow("ip", now=1000.0)


def test_a_refusal_is_not_recorded(store):
    """Same rule as in memory: counting refusals lets a retry loop hold its own
    window open forever."""
    limiter(store).allow("ip", now=1000.0)
    limiter(store).allow("ip", now=1001.0)
    assert not limiter(store).allow("ip", now=1002.0)
    # The two originals aged out; the refusal at 1002 must not extend anything.
    assert limiter(store).allow("ip", now=1301.5)


def test_retry_after_reports_from_the_stored_hits(store):
    limiter(store).allow("ip", now=1000.0)
    limiter(store).allow("ip", now=1001.0)
    assert limiter(store).retry_after("ip", now=1002.0) == 298


# ── explore ──────────────────────────────────────────────────────────────

def q(text, hook=""):
    return {"question": text, "hook": hook}


def test_questions_survive_a_restart(store):
    ExplorePool("money", store).add([q("What is money, really?"), q("Why is debt?")])
    taken = ExplorePool("money", store).take(2)
    assert [t["question"] for t in taken] == ["What is money, really?", "Why is debt?"]


def test_a_served_question_is_not_served_again_after_a_restart(store):
    """This is the whole reason the pool exists, and it used to reset on every
    deploy."""
    ExplorePool("money", store).add([q("What is money, really?")])
    assert ExplorePool("money", store).take(1)
    assert ExplorePool("money", store).take(1) == []


def test_a_served_question_is_not_re_added(store):
    ExplorePool("money", store).add([q("What is money, really?")])
    ExplorePool("money", store).take(1)
    ExplorePool("money", store).add([q("What is money, really?")])
    assert ExplorePool("money", store).take(1) == []


def test_a_reworded_case_or_spacing_still_counts_as_the_same_question(store):
    ExplorePool("money", store).add([q("What is money, really?")])
    ExplorePool("money", store).take(1)
    ExplorePool("money", store).add([q("  what is MONEY,   really?  ")])
    assert ExplorePool("money", store).take(1) == []


def test_sectors_keep_their_own_queues(store):
    ExplorePool("money", store).add([q("What is money, really?")])
    assert ExplorePool("physics", store).take(1) == []
    assert ExplorePool("money", store).take(1)


def test_refill_is_decided_from_the_stored_count(store):
    pool = ExplorePool("money", store)
    assert pool.needs_refill(2)
    pool.add([q("one?" * 4), q("two?" * 4)])
    assert not ExplorePool("money", store).needs_refill(2)


def test_the_avoid_list_outlives_the_process(store):
    ExplorePool("money", store).add([q("What is money, really?"), q("Why is debt?")])
    ExplorePool("money", store).take(2)
    assert ExplorePool("money", store).avoid_list() == [
        "What is money, really?", "Why is debt?"]


def test_the_avoid_list_keeps_the_most_recent_within_its_limit(store):
    ExplorePool("money", store).add([q(f"question number {n}?") for n in range(5)])
    ExplorePool("money", store).take(5)
    assert ExplorePool("money", store).avoid_list(limit=2) == [
        "question number 3?", "question number 4?"]


# ── the in-memory path is untouched ──────────────────────────────────────

def test_without_a_store_nothing_persists():
    """The default must stay exactly what it was: two separate objects share
    nothing at all."""
    ExplorePool("money").add([q("What is money, really?")])
    assert ExplorePool("money").take(1) == []

    ProviderPool().penalise(GEMINI, 3600, now=1000.0)
    assert not ProviderPool().is_cooling(GEMINI, now=1100.0)

    RateLimiter(limit=1, window=300).allow("ip", now=1000.0)
    assert RateLimiter(limit=1, window=300).allow("ip", now=1001.0)
