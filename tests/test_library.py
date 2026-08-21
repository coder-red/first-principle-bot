"""The library is decks that were generated once, validated, reviewed and
committed. The rules that matter: a malformed file on disk never takes the
app down, unverified decks are never published, and the endpoints stay open
because they spend no credits."""

import json

import pytest

from fpb.library import Library, save_deck, slug_for
from fpb.sample import SAMPLE_DECK


def write_deck(root, slug, sector="physics", extra=None):
    deck = dict(SAMPLE_DECK)
    deck["meta"] = {"slug": slug, "sector": sector, "model": "test-model",
                    "generated_at": "2026-08-21T00:00:00Z", "reviewed": True}
    if extra:
        deck.update(extra)
    path = root / (slug + ".json")
    path.write_text(json.dumps(deck), encoding="utf-8")
    return path


# ── slugs ────────────────────────────────────────────────────────────────

def test_slug_is_lowercase_hyphenated():
    assert slug_for("Why is the sky blue?") == "why-is-the-sky-blue"


def test_slug_collapses_runs_and_trims():
    assert slug_for("  What   IS money, really?! ") == "what-is-money-really"


def test_slug_is_capped_at_60_chars():
    assert len(slug_for("w" * 200)) <= 60


# ── loading ──────────────────────────────────────────────────────────────

def test_load_indexes_decks_from_disk(tmp_path):
    write_deck(tmp_path, "why-is-the-sky-blue")
    lib = Library.load(str(tmp_path))
    assert lib.count() == 1
    entry = lib.index()[0]
    assert entry["slug"] == "why-is-the-sky-blue"
    assert entry["sector"] == "physics"
    assert entry["topic"] == SAMPLE_DECK["topic"]
    assert entry["question"] == SAMPLE_DECK["question"]


def test_deck_lookup_returns_normalized_deck_with_meta(tmp_path):
    write_deck(tmp_path, "why-is-the-sky-blue")
    deck = Library.load(str(tmp_path)).deck("why-is-the-sky-blue")
    assert deck["verified"] is True
    assert deck["meta"]["reviewed"] is True
    assert deck["cards"][0]["phase"] == "question"


def test_deck_lookup_returns_none_for_unknown_slug(tmp_path):
    assert Library.load(str(tmp_path)).deck("nope") is None


def test_malformed_file_is_skipped_not_fatal(tmp_path):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    write_deck(tmp_path, "why-is-the-sky-blue")
    lib = Library.load(str(tmp_path))
    assert lib.count() == 1


def test_missing_directory_loads_empty(tmp_path):
    lib = Library.load(str(tmp_path / "does-not-exist"))
    assert lib.count() == 0
    assert lib.index() == []


def test_index_is_sorted_by_sector_then_topic(tmp_path):
    write_deck(tmp_path, "b-deck", sector="physics")
    write_deck(tmp_path, "a-deck", sector="money")
    sectors = [e["sector"] for e in Library.load(str(tmp_path)).index()]
    assert sectors == ["money", "physics"]


# ── publishing ───────────────────────────────────────────────────────────

def test_save_deck_writes_a_verified_deck(tmp_path):
    slug = save_deck(str(tmp_path), SAMPLE_DECK["question"], "physics",
                     dict(SAMPLE_DECK, verified=True), "test-model",
                     "2026-08-21T00:00:00Z")
    assert slug == slug_for(SAMPLE_DECK["question"])
    saved = json.loads((tmp_path / (slug + ".json")).read_text(encoding="utf-8"))
    assert saved["meta"]["reviewed"] is True
    assert saved["meta"]["sector"] == "physics"


def test_save_deck_refuses_an_unverified_deck(tmp_path):
    slug = save_deck(str(tmp_path), "Why?", "physics",
                     dict(SAMPLE_DECK, verified=False), "test-model", "t")
    assert slug is None
    assert list(tmp_path.iterdir()) == []


def test_save_deck_never_overwrites(tmp_path):
    args = (str(tmp_path), SAMPLE_DECK["question"], "physics",
            dict(SAMPLE_DECK, verified=True), "test-model", "t")
    assert save_deck(*args) is not None
    assert save_deck(*args) is None
