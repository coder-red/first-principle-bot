"""Free tiers are capped per account per day, so one provider cannot carry a
public app. The fallback chain therefore has to cross providers -- each hop
needs its own base URL and key, not just a different model name."""

import asyncio
import json
import os
from types import SimpleNamespace

import pytest

from main import KNOWN_PROVIDERS, get_providers

SOUND = {
    "topic": "T", "question": "Q?",
    "cards": [{"phase": "descent", "level": 1, "title": "One",
               "tag": "VERIFIED", "principle": "p", "chain": ["a"]}],
}


# ── explicit multi-provider config ───────────────────────────────────────

def test_builds_one_entry_per_named_provider(monkeypatch):
    monkeypatch.setenv("PROVIDERS", "groq,gemini")
    monkeypatch.setenv("GROQ_API_KEY", "g-key")
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    monkeypatch.setenv("GEMINI_API_KEY", "m-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.0-flash")

    assert [p["name"] for p in get_providers()] == ["groq", "gemini"]


def test_order_is_the_order_given(monkeypatch):
    """The first provider is the one that carries normal traffic; the rest
    only run once it has hit its cap."""
    monkeypatch.setenv("PROVIDERS", "gemini,groq")
    for name in ("GROQ", "GEMINI"):
        monkeypatch.setenv(f"{name}_API_KEY", "k")
        monkeypatch.setenv(f"{name}_MODEL", "m")

    assert [p["name"] for p in get_providers()] == ["gemini", "groq"]


def test_a_known_provider_supplies_its_own_base_url(monkeypatch):
    monkeypatch.setenv("PROVIDERS", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GROQ_MODEL", "m")

    assert get_providers()[0]["endpoint"] == KNOWN_PROVIDERS["groq"]


def test_an_explicit_endpoint_overrides_the_known_one(monkeypatch):
    monkeypatch.setenv("PROVIDERS", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GROQ_MODEL", "m")
    monkeypatch.setenv("GROQ_ENDPOINT", "http://localhost:9999/v1")

    assert get_providers()[0]["endpoint"] == "http://localhost:9999/v1"


def test_an_unknown_provider_works_when_given_an_endpoint(monkeypatch):
    monkeypatch.setenv("PROVIDERS", "myhost")
    monkeypatch.setenv("MYHOST_API_KEY", "k")
    monkeypatch.setenv("MYHOST_MODEL", "m")
    monkeypatch.setenv("MYHOST_ENDPOINT", "https://example.test/v1")

    assert get_providers()[0]["endpoint"] == "https://example.test/v1"


# ── skipping what cannot work ────────────────────────────────────────────

def test_a_provider_with_no_key_is_skipped(monkeypatch):
    """Naming a provider you have not got a key for must not break the chain --
    the point of listing several is that some will be unconfigured."""
    monkeypatch.setenv("PROVIDERS", "groq,gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_MODEL", "m")

    assert [p["name"] for p in get_providers()] == ["gemini"]


def test_a_provider_with_no_model_is_skipped(monkeypatch):
    monkeypatch.setenv("PROVIDERS", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "k")

    assert get_providers() == []


def test_an_unknown_provider_with_no_endpoint_is_skipped(monkeypatch):
    monkeypatch.setenv("PROVIDERS", "mystery")
    monkeypatch.setenv("MYSTERY_API_KEY", "k")
    monkeypatch.setenv("MYSTERY_MODEL", "m")

    assert get_providers() == []


def test_whitespace_and_case_in_the_list_are_tolerated(monkeypatch):
    monkeypatch.setenv("PROVIDERS", "  GROQ , gemini  ")
    for name in ("GROQ", "GEMINI"):
        monkeypatch.setenv(f"{name}_API_KEY", "k")
        monkeypatch.setenv(f"{name}_MODEL", "m")

    assert [p["name"] for p in get_providers()] == ["groq", "gemini"]


# ── the single-provider config still works ───────────────────────────────

def test_falls_back_to_the_original_openrouter_settings(monkeypatch):
    """PROVIDERS unset must keep behaving exactly as before, so an existing
    .env is not silently broken by the upgrade."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("MODEL", "primary-model")
    monkeypatch.setenv("FALLBACK_MODELS", "second-model,third-model")

    got = get_providers()
    assert [p["model"] for p in got] == ["primary-model", "second-model", "third-model"]
    assert {p["api_key"] for p in got} == {"or-key"}
    assert {p["endpoint"] for p in got} == {KNOWN_PROVIDERS["openrouter"]}


def test_no_key_at_all_yields_no_providers(monkeypatch):
    assert get_providers() == []


# ── per-provider token budget ────────────────────────────────────────────
#
# One global DECK_MAX_TOKENS cannot serve both kinds of provider. Gemini bills
# hidden thinking against the budget, so it needs ~12k or the deck truncates.
# Groq's free tier caps tokens-per-minute at 8000 and counts max_tokens toward
# it, so the same 12k is rejected outright with 413. Measured, not guessed.

import main


def test_a_provider_defaults_to_the_global_budget(monkeypatch):
    monkeypatch.setenv("PROVIDERS", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GROQ_MODEL", "m")

    assert get_providers()[0]["max_tokens"] == main.DECK_MAX_TOKENS


def test_a_provider_can_cap_its_own_budget(monkeypatch):
    monkeypatch.setenv("PROVIDERS", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GROQ_MODEL", "m")
    monkeypatch.setenv("GROQ_MAX_TOKENS", "6000")

    assert get_providers()[0]["max_tokens"] == 6000


def test_the_legacy_config_also_carries_a_budget(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert all(p["max_tokens"] == main.DECK_MAX_TOKENS for p in get_providers())


def test_request_deck_honours_the_budget_it_is_given():
    """The budget has to reach the wire, not just the config dict."""
    seen = {}

    async def create(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(SOUND)),
                finish_reason="stop")],
            model="m")

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    asyncio.run(main.request_deck(client, "m", [], max_tokens=6000))
    assert seen["max_tokens"] == 6000


def test_request_deck_falls_back_to_the_global_budget():
    seen = {}

    async def create(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(SOUND)),
                finish_reason="stop")],
            model="m")

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    asyncio.run(main.request_deck(client, "m", []))
    assert seen["max_tokens"] == main.DECK_MAX_TOKENS
