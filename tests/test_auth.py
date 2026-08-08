"""Optional shared-token access control.

The behaviour that matters most is the OFF case: this app is meant to stay a
zero-config local run, and the browser suites drive it with no token at all.
So every "on" test here sets APP_ACCESS_TOKEN explicitly, and the first block
proves nothing changes when it is unset.
"""

import pytest
from fastapi.testclient import TestClient

import main
from fpb.auth import configured_token, is_enabled, token_matches

client = TestClient(main.app)

TOKEN = "s3cret-shared-token"


@pytest.fixture
def gated(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_TOKEN", TOKEN)
    # No providers: these tests are about who gets through the door, not about
    # what the model returns once they do.
    monkeypatch.setattr(main, "get_providers", lambda: [])


def post_deck(**kwargs):
    return client.post("/api/chat/stream",
                       json={"message": "Why is the sky blue?"}, **kwargs)


# ── off by default ───────────────────────────────────────────────────────

def test_the_deck_endpoint_is_open_when_no_token_is_set(monkeypatch):
    monkeypatch.setattr(main, "get_providers", lambda: [])
    assert post_deck().status_code == 200


def test_explore_is_open_when_no_token_is_set(monkeypatch):
    monkeypatch.setattr(main, "get_providers", lambda: [])
    assert client.get("/api/explore/money").status_code == 200


def test_an_open_instance_does_not_advertise_a_lock():
    """404, not 401. A token exchange that answers on an open instance tells a
    stranger there is something to guess."""
    assert client.post("/api/access", json={"token": "anything"}).status_code == 404


def test_health_reports_open():
    assert client.get("/api/health").json()["access_control"] == "open"


# ── on ───────────────────────────────────────────────────────────────────

def test_the_deck_endpoint_refuses_without_a_token(gated):
    assert post_deck().status_code == 401


def test_the_streaming_and_plain_endpoints_are_both_gated(gated):
    assert client.post("/api/chat", json={"message": "Why?"}).status_code == 401
    assert post_deck().status_code == 401


def test_explore_is_gated_too(gated):
    """A sector top-up is a model call, so leaving it open would leave the
    cheapest way to spend someone else's credits wide open."""
    assert client.get("/api/explore/money").status_code == 401


def test_a_header_token_gets_through(gated):
    assert post_deck(headers={"X-Access-Token": TOKEN}).status_code == 200


def test_a_bearer_token_gets_through(gated):
    assert post_deck(headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_a_wrong_token_does_not(gated):
    assert post_deck(headers={"X-Access-Token": "wrong"}).status_code == 401


def test_health_stays_open_to_report_the_lock(gated):
    """Health must not be gated: it is what a platform health check calls, and
    a 401 there would take the service down."""
    body = client.get("/api/health")
    assert body.status_code == 200
    assert body.json()["access_control"] == "token"


def test_health_never_leaks_the_token(gated):
    assert TOKEN not in client.get("/api/health").text


# ── the cookie exchange ──────────────────────────────────────────────────

def test_exchanging_the_token_sets_a_cookie_that_works(gated):
    granted = client.post("/api/access", json={"token": TOKEN})
    assert granted.status_code == 200
    assert granted.json() == {"ok": True}

    # The TestClient keeps the cookie, which is the whole point: the browser
    # carries it on every later request without JavaScript touching it.
    assert post_deck().status_code == 200
    client.cookies.clear()


def test_the_cookie_is_not_readable_from_javascript(gated):
    granted = client.post("/api/access", json={"token": TOKEN})
    assert "httponly" in granted.headers["set-cookie"].lower()
    client.cookies.clear()


def test_a_wrong_token_is_refused_at_the_exchange(gated):
    assert client.post("/api/access", json={"token": "wrong"}).status_code == 401


def test_an_empty_token_is_rejected_by_validation(gated):
    assert client.post("/api/access", json={"token": ""}).status_code == 422


def test_the_exchange_is_rate_limited(gated, monkeypatch):
    """Otherwise it is an unmetered guessing oracle: a caller with no token is
    refused before it reaches any other limiter, so guessing would be free."""
    monkeypatch.setattr(main, "ACCESS_LIMITER",
                        main.RateLimiter(limit=2, window=600, name="access"))
    assert client.post("/api/access", json={"token": "a"}).status_code == 401
    assert client.post("/api/access", json={"token": "b"}).status_code == 401
    assert client.post("/api/access", json={"token": "c"}).status_code == 429


# ── the comparison itself ────────────────────────────────────────────────

def test_no_candidate_ever_matches_an_unset_token():
    """The dangerous failure would be an empty configured token matching an
    empty presented one and quietly opening a gated instance."""
    assert not is_enabled()
    assert not token_matches("")
    assert not token_matches(None)
    assert not token_matches("anything")


def test_a_configured_token_matches_only_itself(gated):
    assert configured_token() == TOKEN
    assert token_matches(TOKEN)
    assert not token_matches(TOKEN + "x")
    assert not token_matches(TOKEN[:-1])
    assert not token_matches(None)


def test_surrounding_whitespace_in_the_env_is_ignored(monkeypatch):
    """A token pasted into a dashboard field routinely arrives with a newline."""
    monkeypatch.setenv("APP_ACCESS_TOKEN", f"  {TOKEN}\n")
    assert configured_token() == TOKEN
    assert token_matches(TOKEN)
