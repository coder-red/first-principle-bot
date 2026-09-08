"""Plan-expand fast path.

`USE_PLAN_EXPAND=1` switches on a single small model call that returns a compact
Plan (titles + principles + descent_depth), which the code then expands
deterministically into a full, guaranteed-valid Deck. It was added for speed but
had zero coverage and two bugs: the model was asked to decompose the trailing
"Build the deck now..." instruction instead of the reader's question, and the
rendered chain ladder was meaningless single letters. Both are fixed here and
locked in.
"""

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def plan_payload():
    return {
        "topic": "LIFT",
        "question": "Why do planes actually stay up?",
        "descent_depth": 3,
        "steps": [
            {"phase": "question", "title": "Planes stay up",
             "principle": "Planes stay up"},
            {"phase": "descent", "level": 1, "title": "Lift equals weight",
             "principle": "Lift must balance weight"},
            {"phase": "descent", "level": 2, "title": "Air pushes",
             "principle": "Air pushes on solid surfaces"},
            {"phase": "descent", "level": 3, "title": "Pressure",
             "principle": "Pressure differences come from flow"},
            {"phase": "bedrock", "title": "Momentum",
             "principle": "Air momentum is exchanged with the wing"},
            {"phase": "rebuild", "title": "Rebuild",
             "principle": "The wing redirects air downward"},
            {"phase": "insight", "title": "Insight",
             "principle": "Lift is ordinary Newtonian exchange"},
        ],
        "followups": ["What causes a stall?", "Why does angle of attack matter?"],
    }


class PlanClient:
    """Stands in for AsyncOpenAI: returns the canned Plan, records the call."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

        async def create(**kwargs):
            self.calls.append(kwargs)
            body = json.dumps(self.payload)
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content=body), finish_reason="stop")],
                model=kwargs.get("model"))

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def provider():
    return {"name": "test", "model": "m", "endpoint": "http://x",
            "api_key": "test-key", "max_tokens": 4000}


def setup(monkeypatch, client_stub):
    monkeypatch.setenv("USE_PLAN_EXPAND", "1")
    monkeypatch.setattr(main, "get_providers", lambda: [provider()])
    monkeypatch.setattr(main, "get_client", lambda *a, **k: client_stub)


# ── the fast path routes through /api/chat ────────────────────────────────

def test_plan_expand_serves_a_verified_deck(monkeypatch):
    stub = PlanClient(plan_payload())
    setup(monkeypatch, stub)
    body = client.post("/api/chat",
                       json={"message": "Why do planes actually stay up?"}).json()
    assert body["verified"] is True
    assert body["topic"] == "LIFT"
    assert len(body["cards"]) == 7


def test_the_model_is_asked_the_readers_question_not_the_instruction(monkeypatch):
    """The plan prompt must receive the reader's question. It used to receive
    build_messages' trailing 'Build the deck now...' instruction, which made the
    whole fast path decompose the wrong thing."""
    stub = PlanClient(plan_payload())
    setup(monkeypatch, stub)
    client.post("/api/chat", json={"message": "Why do planes actually stay up?"})

    user_prompt = stub.calls[0]["messages"][-1]["content"]
    assert "Why do planes actually stay up?" in user_prompt
    assert "Build the deck now" not in user_prompt


def test_the_plan_call_is_a_single_small_non_streaming_call(monkeypatch):
    """The fast path must use exactly one small model call — no streaming, no
    repair, no fallback round-trip."""
    stub = PlanClient(plan_payload())
    setup(monkeypatch, stub)
    client.post("/api/chat", json={"message": "Why do planes actually stay up?"})

    assert len(stub.calls) == 1
    call = stub.calls[0]
    assert call.get("stream") is None or call.get("stream") is False
    # Plan is small — budget far below a full deck.
    assert call["max_tokens"] < 4000


def test_plan_expand_also_works_through_the_stream_endpoint(monkeypatch):
    stub = PlanClient(plan_payload())
    setup(monkeypatch, stub)
    with client.stream("POST", "/api/chat/stream",
                       json={"message": "Why do planes actually stay up?"}) as r:
        events = [json.loads(l) for l in r.iter_lines() if l.strip()]
    assert events[-1]["type"] == "done"
    assert events[-1]["deck"]["verified"] is True
    # The fast path is non-streaming, so no card events — just the done deck.
    assert all(e["type"] == "done" for e in events)


# ── the expanded deck is valid and the ladder reads ├──────────────────────

def test_the_chain_ladder_is_meaningful_text_not_letters(monkeypatch):
    """The rendered chain must read as a ladder of claims, not 'a -> b -> c'."""
    stub = PlanClient(plan_payload())
    setup(monkeypatch, stub)
    body = client.post("/api/chat", json={"message": "Why do planes stay up?"}).json()

    descent = [c for c in body["cards"] if c["phase"] == "descent"]
    assert descent[0]["chain"] == ["Lift must balance weight"]
    assert descent[1]["chain"] == [
        "Lift must balance weight", "Air pushes on solid surfaces"]
    # No single-letter placeholder rungs.
    for card in body["cards"]:
        for rung in card["chain"]:
            assert len(rung) > 1, rung


# ── fallback when the plan is unusable ────────────────────────────────────

def test_a_bad_plan_falls_back_to_the_old_path(monkeypatch):
    """The fast path is optional: if the plan call raises, the app must fall
    through to the regular path instead of returning a 500."""

    class Boom:
        async def create(self, **kwargs):
            raise RuntimeError("provider down")

    boom = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=Boom().create)))
    setup(monkeypatch, boom)

    body = client.post("/api/chat", json={"message": "Q?"}).json()
    # All providers failed → an error deck, but never a 500.
    assert body["verified"] is False
