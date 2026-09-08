"""Instant lookup: a question that matches a reviewed library deck is served
before any model call. Matching is conservative — a near miss must fall through
to generation rather than risk serving the wrong deck."""

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

import main
from fpb.deck import normalize_deck
from fpb.library import Library

client = TestClient(main.app)


def build_deck(slug="why-do-empires-fall", question="Why do large, centralized "
                "empires inevitably collapse?", topic="EMPIRES"):
    deck = normalize_deck({
        "topic": topic,
        "question": question,
        "cards": [
            {"phase": "question", "level": 0, "title": "Empires fall",
             "tag": "ASSUMPTION", "principle": "Large empires tend to collapse",
             "chain": []},
            {"phase": "descent", "level": 1, "title": "Costs rise",
             "tag": "VERIFIED", "principle": "Empires expand spending",
             "chain": ["Empires expand spending"]},
            {"phase": "descent", "level": 2, "title": "Center strains",
             "tag": "VERIFIED", "principle": "Centralized control strains",
             "chain": ["Empires expand spending", "Centralized control strains"]},
            {"phase": "descent", "level": 3, "title": "Periphery stops paying",
             "tag": "VERIFIED", "principle": "Peripheries stop subsidizing the center",
             "chain": ["Empires expand spending", "Centralized control strains",
                       "Peripheries stop subsidizing the center"]},
            {"phase": "bedrock", "level": 4, "title": "Energy limits",
             "tag": "ATOMIC", "principle": "Systems need energy to stay together",
             "chain": ["Empires expand spending", "Centralized control strains",
                       "Peripheries stop subsidizing the center"]},
            {"phase": "rebuild", "level": 2, "title": "Fragments",
             "tag": "VERIFIED", "principle": "Fragments form smaller units",
             "chain": ["Peripheries stop subsidizing the center",
                       "Systems need energy to stay together"]},
            {"phase": "insight", "level": 0, "title": "Reorganization",
             "tag": "VERIFIED", "principle": "Collapse is reorganization",
             "chain": []},
        ],
        "followups": ["Why does the center lose control?"],
    })
    assert deck["verified"], deck["issues"]
    deck["meta"] = {"slug": slug, "reviewed": True, "sector": "history"}
    return deck


def library_with(deck):
    return Library({deck["meta"]["slug"]: deck})


def setup(monkeypatch, deck=None, providers=True):
    monkeypatch.setenv("INSTANT_LOOKUP", "1")
    monkeypatch.setenv("USE_PLAN_EXPAND", "0")
    monkeypatch.setattr(main, "LIBRARY", library_with(deck or build_deck()))
    if providers:
        monkeypatch.setattr(main, "get_providers", lambda: [
            {"name": "test", "model": "m", "endpoint": "http://x",
             "api_key": "test-key", "max_tokens": 4000},
        ])


# ── the matcher ───────────────────────────────────────────────────────────

def test_rephrased_question_matches():
    from fpb.retrieve import find_library_match, match_score
    deck = build_deck()
    # Slug/topic vocabulary bridges "fall" (slug) vs "collapse" (question).
    assert match_score("Why did empires fall?", deck) == 1.0
    assert find_library_match("Why did empires fall?", library_with(deck)) is not None


def test_calorie_style_single_word_questions_match():
    from fpb.retrieve import find_library_match
    deck = build_deck(slug="what-is-a-calorie-really",
                      question="What is a calorie, really?",
                      topic="CALORIES")
    assert find_library_match("What is a calorie?", library_with(deck)) is not None


def test_no_match_for_an_unrelated_question():
    from fpb.retrieve import find_library_match
    lib = library_with(build_deck())
    assert find_library_match("Why does toast fall butter-side down?", lib) is None


def test_a_single_shared_keyword_cannot_hijack_a_deck():
    from fpb.retrieve import find_library_match
    lib = library_with(build_deck())
    assert find_library_match("Why is nuclear fall equally dangerous?", lib) is None


def test_a_qualifier_question_is_a_near_miss_not_a_hit():
    """'What color is a blue whale?' shares words with the sky deck but asks a
    different question — sharing a word must not serve a deck that misserves."""
    from fpb.retrieve import find_library_match
    lib = library_with(build_deck())
    assert find_library_match("What color is a blue whale?", lib) is None


# ── instant serving through /api/chat ─────────────────────────────────────

def test_a_matching_question_is_served_without_any_provider(monkeypatch):
    """Instant lookup must not need a model at all — that is its whole point."""
    setup(monkeypatch, providers=False)
    body = client.post("/api/chat",
                       json={"message": "Why did empires fall?"}).json()
    assert body["verified"] is True
    assert body["meta"]["reviewed"] is True
    assert body["meta"]["slug"] == "why-do-empires-fall"


def test_instant_hit_never_calls_a_provider(monkeypatch):
    calls = []

    def counting(*a, **k):
        calls.append(a)
        raise AssertionError("library hit must not build a client")

    setup(monkeypatch, providers=True)
    monkeypatch.setattr(main, "get_client", counting)
    client.post("/api/chat", json={"message": "Why did empires fall?"})
    assert calls == []


def test_an_unmatched_question_falls_through_to_generation(monkeypatch):
    """A near miss must not be returned as though it were the deck the reader
    asked for; it goes to the providers (here, all absent → error deck)."""
    setup(monkeypatch, providers=False)
    body = client.post("/api/chat",
                       json={"message": "Why do the leaves turn red in autumn?"}).json()
    assert body["verified"] is False
    assert "meta" not in body or body.get("meta", {}).get("reviewed") is not True


def test_instant_serves_a_done_deck_immediately_on_the_stream(monkeypatch):
    setup(monkeypatch, providers=True)
    with client.stream("POST", "/api/chat/stream",
                       json={"message": "Why did empires fall?"}) as r:
        events = [json.loads(l) for l in r.iter_lines() if l.strip()]
    assert len(events) == 1
    assert events[0]["type"] == "done"
    assert events[0]["deck"]["meta"]["reviewed"] is True


def test_serving_never_rewrites_the_cached_deck(monkeypatch):
    """The match is the shared cached object and `finalize` flips `reframed`
    on what it is given — serving must go through a copy, or one request
    rewrites the deck for every request after it."""
    deck = build_deck()
    deck["reframed"] = True
    lib = library_with(deck)
    setup(monkeypatch, deck)
    body = client.post("/api/chat", json={"message": deck["question"]}).json()
    assert body["reframed"] is False  # finalize did its job on the response
    assert lib.deck(deck["meta"]["slug"])["reframed"] is True  # cache untouched


def test_instant_lookup_is_off_when_flag_is_0(monkeypatch):
    setup(monkeypatch, providers=False)
    monkeypatch.setenv("INSTANT_LOOKUP", "0")
    body = client.post("/api/chat",
                       json={"message": "Why did empires fall?"}).json()
    assert body["verified"] is False  # fell through to "no providers"


# ── speed paths default ON in production ──────────────────────────────────

def test_instant_lookup_defaults_on(monkeypatch):
    monkeypatch.delenv("INSTANT_LOOKUP", raising=False)
    assert main.instant_lookup_enabled() is True


def test_plan_expand_defaults_on(monkeypatch):
    monkeypatch.delenv("USE_PLAN_EXPAND", raising=False)
    assert main.plan_expand_enabled() is True