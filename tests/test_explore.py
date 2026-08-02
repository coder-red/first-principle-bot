"""Explore hands people a question worth decomposing when they cannot think of
one themselves. The rules that matter: a question is never served twice, and
model output that does not clear the bar is dropped rather than shown."""

import pytest
from fastapi.testclient import TestClient

import main
from main import (
    SECTORS,
    ExplorePool,
    normalize_questions,
    sector_by_slug,
)


# ── sectors ──────────────────────────────────────────────────────────────

def test_every_sector_has_a_unique_slug():
    slugs = [s["slug"] for s in SECTORS]
    assert len(slugs) == len(set(slugs))


def test_sector_lookup_finds_a_known_slug():
    assert sector_by_slug("health")["label"] == "Health"


def test_sector_lookup_returns_none_for_an_unknown_slug():
    assert sector_by_slug("astrology") is None


# ── parsing what the model returned ──────────────────────────────────────

def test_keeps_a_well_formed_question_and_its_hook():
    got = normalize_questions([
        {"question": "Why do planes actually stay up?", "hook": "the equal-transit myth"},
    ])
    assert got == [{"question": "Why do planes actually stay up?",
                    "hook": "the equal-transit myth"}]


def test_drops_an_entry_with_no_question():
    assert normalize_questions([{"hook": "something"}]) == []


def test_keeps_a_question_that_arrived_without_a_hook():
    got = normalize_questions([{"question": "What is money, really?"}])
    assert got == [{"question": "What is money, really?", "hook": ""}]


def test_drops_a_question_too_short_to_be_real():
    assert normalize_questions([{"question": "Why?"}]) == []


def test_drops_a_statement_that_is_not_a_question():
    """The prompt asks for questions; a topic heading is not one."""
    assert normalize_questions([{"question": "The history of money"}]) == []


def test_ignores_junk_that_is_not_an_object():
    assert normalize_questions(["Why is the sky blue?", None, 7]) == []


def test_trims_surrounding_whitespace():
    got = normalize_questions([{"question": "  What is fire?  ", "hook": "  heat  "}])
    assert got == [{"question": "What is fire?", "hook": "heat"}]


# ── never serving the same question twice ────────────────────────────────

def q(text):
    return {"question": text, "hook": ""}


def test_take_returns_the_requested_number():
    pool = ExplorePool()
    pool.add([q("a?"), q("b?"), q("c?")])
    assert len(pool.take(2)) == 2


def test_take_never_returns_the_same_question_twice():
    pool = ExplorePool()
    pool.add([q("a?"), q("b?"), q("c?"), q("d?")])
    first = {item["question"] for item in pool.take(2)}
    second = {item["question"] for item in pool.take(2)}
    assert first.isdisjoint(second)


def test_take_returns_what_is_left_when_the_pool_runs_short():
    pool = ExplorePool()
    pool.add([q("a?")])
    assert len(pool.take(4)) == 1


def test_a_question_already_served_is_refused_on_refill():
    """The generator is told what to avoid but will repeat itself anyway; the
    pool is the backstop."""
    pool = ExplorePool()
    pool.add([q("a?")])
    pool.take(1)
    pool.add([q("a?"), q("b?")])
    assert [item["question"] for item in pool.take(2)] == ["b?"]


def test_a_duplicate_inside_one_batch_is_added_once():
    pool = ExplorePool()
    pool.add([q("a?"), q("a?")])
    assert len(pool.take(2)) == 1


def test_matching_ignores_case_and_surrounding_space():
    pool = ExplorePool()
    pool.add([q("What Is Fire?")])
    pool.take(1)
    pool.add([q("  what is fire?  ")])
    assert pool.take(1) == []


def test_avoid_list_reports_what_has_been_served():
    pool = ExplorePool()
    pool.add([q("a?"), q("b?")])
    pool.take(1)
    assert pool.avoid_list() == ["a?"]


def test_needs_refill_when_the_pool_is_below_the_mark():
    pool = ExplorePool()
    assert pool.needs_refill(4) is True
    pool.add([q(f"{n}?") for n in range(6)])
    assert pool.needs_refill(4) is False


# ── the endpoint ─────────────────────────────────────────────────────────

client = TestClient(main.app)


def test_sectors_endpoint_lists_every_sector():
    body = client.get("/api/explore/sectors").json()
    assert [s["slug"] for s in body["sectors"]] == [s["slug"] for s in SECTORS]


def test_unknown_sector_is_a_404():
    assert client.get("/api/explore/astrology").status_code == 404


def test_a_sector_serves_questions_without_calling_the_model_twice(monkeypatch):
    calls = []

    async def fake_generate(sector, avoid, count):
        calls.append(sector["slug"])
        return [q(f"{sector['slug']} question {n}?") for n in range(count)]

    monkeypatch.setattr(main, "generate_questions", fake_generate)
    main.EXPLORE_POOLS.clear()

    first = client.get("/api/explore/football").json()
    second = client.get("/api/explore/football").json()

    assert len(calls) == 1, "the second request should be served from the pool"
    asked = [item["question"] for item in first["questions"]]
    again = [item["question"] for item in second["questions"]]
    assert set(asked).isdisjoint(again)


def test_a_sector_that_the_model_fails_for_returns_an_empty_list_not_a_500(monkeypatch):
    async def boom(sector, avoid, count):
        raise RuntimeError("model is down")

    monkeypatch.setattr(main, "generate_questions", boom)
    main.EXPLORE_POOLS.clear()

    response = client.get("/api/explore/history")
    assert response.status_code == 200
    assert response.json()["questions"] == []
