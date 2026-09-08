import os

import pytest

import main

# Anything that configures a provider. Kept in one place because three separate
# copies of this list drifted apart and each time the gap let the developer's
# real .env into a test — including one that then made live API calls.
PROVIDER_ENV_SUFFIXES = ("_API_KEY", "_MODEL", "_MODELS", "_ENDPOINT", "_MAX_TOKENS")
PROVIDER_ENV_NAMES = ("PROVIDERS", "MODEL", "MODELS", "FALLBACK_MODELS",
                      "API_ENDPOINT", "QUESTIONS_MODEL", "DECK_MAX_TOKENS")


@pytest.fixture(autouse=True)
def isolated_provider_env(monkeypatch):
    """No test sees the real .env. A test that wants a provider sets one up.

    Without this the suite's behaviour depends on whoever ran it last editing
    their .env, and a test can silently spend real quota.
    """
    for var in list(os.environ):
        if var.endswith(PROVIDER_ENV_SUFFIXES) or var in PROVIDER_ENV_NAMES:
            monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def no_optional_features(monkeypatch):
    """Access control, persistence and the speed paths are off unless a test
    turns them on.

    Same reasoning as the provider env above: a developer with APP_ACCESS_TOKEN
    set in their .env would otherwise see every endpoint test 401, and a set
    STATE_DB would have the suite writing to their real state file. Likewise the
    instant lookup and plan-expand paths default ON in production — they are
    pinned off here so the classic provider/stream tests still exercise the
    classic path, and the tests that own them re-enable the flags explicitly.
    """
    monkeypatch.delenv("APP_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("STATE_DB", raising=False)
    monkeypatch.setenv("INSTANT_LOOKUP", "0")
    monkeypatch.setenv("USE_PLAN_EXPAND", "0")


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    """The limiters are module-level and would otherwise carry counts from one
    test into the next — a suite that hits an endpoint nine times would start
    failing on the ninth for reasons that have nothing to do with the test."""
    main.DECK_LIMITER._hits.clear()
    main.EXPLORE_LIMITER._hits.clear()
    main.ACCESS_LIMITER._hits.clear()
    yield
    main.DECK_LIMITER._hits.clear()
    main.EXPLORE_LIMITER._hits.clear()
    main.ACCESS_LIMITER._hits.clear()


@pytest.fixture(autouse=True)
def fresh_provider_pool(monkeypatch):
    """Cooldowns and the rotation cursor must not leak between tests."""
    monkeypatch.setattr(main, "PROVIDER_POOL", main.ProviderPool())
