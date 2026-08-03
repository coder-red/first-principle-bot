"""The streaming path renders cards as they arrive, so the parser has to pull
complete card objects out of JSON that is still being written. Everything here
is about that partial state — the finished-deck cases are covered by
tests/test_deck.py."""

import json

from main import complete_cards


def wrap(cards_json: str, closed: bool = False) -> str:
    """A deck prefix as it looks mid-stream: the keys the model emits before
    `cards`, then however much of the array has arrived."""
    head = '{"topic": "why the sky is blue", "question": "Why blue?", "cards": ['
    return head + cards_json + ("]}" if closed else "")


CARD_A = '{"phase": "question", "level": 0, "title": "The Sky Is Blue"}'
CARD_B = '{"phase": "descent", "level": 1, "title": "Scattering"}'


def test_returns_nothing_before_the_cards_array_opens():
    assert complete_cards('{"topic": "why the sky is bl') == []


def test_returns_nothing_while_the_first_card_is_incomplete():
    assert complete_cards(wrap('{"phase": "question", "lev')) == []


def test_returns_a_card_as_soon_as_it_closes():
    cards = complete_cards(wrap(CARD_A))
    assert [c["title"] for c in cards] == ["The Sky Is Blue"]


def test_ignores_a_trailing_partial_card_after_a_complete_one():
    cards = complete_cards(wrap(CARD_A + ', {"phase": "desc'))
    assert [c["title"] for c in cards] == ["The Sky Is Blue"]


def test_returns_every_card_once_the_array_closes():
    cards = complete_cards(wrap(CARD_A + ", " + CARD_B, closed=True))
    assert [c["phase"] for c in cards] == ["question", "descent"]


def test_a_brace_inside_a_string_does_not_end_the_card_early():
    card = '{"phase": "descent", "explanation": "the set {x} is closed", "level": 1}'
    cards = complete_cards(wrap(card))
    assert cards[0]["explanation"] == "the set {x} is closed"


def test_an_escaped_quote_does_not_end_the_string_early():
    card = r'{"phase": "descent", "title": "the \"sky\" is not a thing", "level": 1}'
    cards = complete_cards(wrap(card))
    assert cards[0]["title"] == 'the "sky" is not a thing'


def test_a_nested_object_inside_a_card_is_kept_whole():
    card = '{"phase": "descent", "meta": {"depth": {"n": 2}}, "level": 1}'
    cards = complete_cards(wrap(card))
    assert cards[0]["meta"] == {"depth": {"n": 2}}


def test_a_nested_array_inside_a_card_is_kept_whole():
    card = '{"phase": "descent", "chain": ["a", "b"], "level": 1}'
    cards = complete_cards(wrap(card))
    assert cards[0]["chain"] == ["a", "b"]


def test_the_word_cards_inside_an_earlier_string_is_not_mistaken_for_the_key():
    text = '{"topic": "how do playing cards work", "cards": [' + CARD_A
    assert [c["title"] for c in complete_cards(text)] == ["The Sky Is Blue"]


def test_stops_at_the_close_of_the_cards_array():
    """followups follow the array; their strings must not be read as cards."""
    text = wrap(CARD_A, closed=True)[:-1] + ', "followups": ["why not violet?"]}'
    assert [c["title"] for c in complete_cards(text)] == ["The Sky Is Blue"]


def test_a_card_missing_its_closing_brace_is_withheld():
    """The card before it is still delivered — one unfinished object must not
    hold back the cards that already landed."""
    assert complete_cards(wrap(CARD_A + ", " + CARD_B[:-1])) == [json.loads(CARD_A)]


# ── the streaming endpoint ───────────────────────────────────────────────
#
# Cards must reach the client as they close, not in one lump at the end —
# that is the entire point of the endpoint. The authoritative deck still
# arrives last, because a chain cannot be validated until it is finished.

import main
from types import SimpleNamespace
from fastapi.testclient import TestClient

client = TestClient(main.app)


def chunked(text: str, size: int = 40):
    return [text[i:i + size] for i in range(0, len(text), size)]


def fake_client_streaming(chunks):
    """Stands in for AsyncOpenAI, emitting the given chunks as deltas."""

    async def create(**kwargs):
        if not kwargs.get("stream"):
            raise AssertionError("the streaming path must request stream=True")

        class Stream:
            def __aiter__(self):
                async def gen():
                    for piece in chunks:
                        yield SimpleNamespace(choices=[SimpleNamespace(
                            delta=SimpleNamespace(content=piece))])
                return gen()

        return Stream()

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def events_for(monkeypatch, chunks):
    monkeypatch.setattr(main, "get_providers", lambda: [
        {"name": "test", "model": "m", "endpoint": "http://x",
         "api_key": "test-key", "max_tokens": 4000},
    ])
    monkeypatch.setattr(main, "get_client", lambda *a, **k: fake_client_streaming(chunks))

    with client.stream("POST", "/api/chat/stream",
                       json={"message": "Why is the sky blue?"}) as response:
        assert response.status_code == 200
        return [json.loads(line) for line in response.iter_lines() if line.strip()]


def sample_deck_json():
    return json.dumps(client.get("/api/sample-deck").json())


def test_each_card_arrives_as_its_own_event(monkeypatch):
    events = events_for(monkeypatch, chunked(sample_deck_json()))
    cards = [e for e in events if e["type"] == "card"]
    assert len(cards) == len(client.get("/api/sample-deck").json()["cards"])


def test_cards_arrive_before_the_deck_is_finished(monkeypatch):
    events = events_for(monkeypatch, chunked(sample_deck_json()))
    kinds = [e["type"] for e in events]
    assert kinds.index("card") < kinds.index("done")


def test_the_last_event_carries_the_validated_deck(monkeypatch):
    events = events_for(monkeypatch, chunked(sample_deck_json()))
    assert events[-1]["type"] == "done"
    deck = events[-1]["deck"]
    assert deck["verified"] is True
    assert deck["followups"]


def test_cards_are_streamed_in_chain_order(monkeypatch):
    events = events_for(monkeypatch, chunked(sample_deck_json()))
    streamed = [e["card"]["phase"] for e in events if e["type"] == "card"]
    assert streamed[0] == "question"
    assert "bedrock" in streamed
    assert streamed[-1] == "insight"


def test_an_empty_completion_still_ends_with_a_done_event(monkeypatch):
    """A dead model must not leave the client waiting on a stream that never
    closes; it gets an error deck like the non-streaming path does."""
    events = events_for(monkeypatch, [""])
    assert events[-1]["type"] == "done"
    assert events[-1]["deck"]["cards"]


def test_junk_output_does_not_crash_the_stream(monkeypatch):
    events = events_for(monkeypatch, chunked("this is not json at all"))
    assert events[-1]["type"] == "done"


# ── falling through on an unsound chain ──────────────────────────────────
#
# A deck that breaks its own rules is the one thing this app cannot serve as
# though it passed. The chain exists precisely for that, but the stream used
# to return the first deck it got, verified or not, and never asked the next
# provider. gpt-oss-120b routinely returns a depth-2 descent where the
# contract needs 3, so this is the common case, not an edge one.

SHALLOW = {
    "topic": "T", "question": "Q?",
    "cards": [
        {"phase": "question", "level": 0, "title": "Surface", "tag": "ASSUMPTION",
         "principle": "p", "chain": []},
        {"phase": "descent", "level": 1, "title": "One", "tag": "VERIFIED",
         "principle": "p1", "chain": ["a"]},
        {"phase": "descent", "level": 2, "title": "Two", "tag": "VERIFIED",
         "principle": "p2", "chain": ["a", "b"]},
        {"phase": "bedrock", "level": 2, "title": "Floor", "tag": "ATOMIC",
         "principle": "irreducible", "chain": ["a", "b"]},
    ],
}

SOUND = {
    "topic": "T", "question": "Q?",
    "cards": [
        {"phase": "question", "level": 0, "title": "Surface", "tag": "ASSUMPTION",
         "principle": "p", "chain": []},
        {"phase": "descent", "level": 1, "title": "One", "tag": "VERIFIED",
         "principle": "p1", "chain": ["a"]},
        {"phase": "descent", "level": 2, "title": "Two", "tag": "VERIFIED",
         "principle": "p2", "chain": ["a", "b"]},
        {"phase": "descent", "level": 3, "title": "Three", "tag": "VERIFIED",
         "principle": "p3", "chain": ["a", "b", "c"]},
        {"phase": "bedrock", "level": 4, "title": "Floor", "tag": "ATOMIC",
         "principle": "irreducible", "chain": ["a", "b", "c", "d"]},
        {"phase": "rebuild", "level": 2, "title": "Rebuild", "tag": "VERIFIED",
         "principle": "r", "chain": ["d", "c"]},
        {"phase": "insight", "level": 0, "title": "Insight", "tag": "VERIFIED",
         "principle": "i", "chain": []},
    ],
}


def client_by_model(payload_for_model):
    """One fake standing in for every provider, answering per model name."""
    def factory(api_key, endpoint):
        async def create(**kwargs):
            body = json.dumps(payload_for_model[kwargs["model"]])
            if kwargs.get("stream"):
                class Stream:
                    def __aiter__(self):
                        async def gen():
                            yield SimpleNamespace(choices=[SimpleNamespace(
                                delta=SimpleNamespace(content=body))])
                        return gen()
                return Stream()
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content=body), finish_reason="stop")],
                model=kwargs["model"])
        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return factory


def entry(model):
    return {"name": "p", "model": model, "endpoint": "e",
            "api_key": "k", "max_tokens": 4000}


def done_deck(monkeypatch, providers, payloads):
    monkeypatch.setattr(main, "get_providers", lambda: providers)
    monkeypatch.setattr(main, "get_client", client_by_model(payloads))
    with client.stream("POST", "/api/chat/stream",
                       json={"message": "Why is the sky blue?"}) as r:
        events = [json.loads(l) for l in r.iter_lines() if l.strip()]
    return events[-1]["deck"]


def test_an_unsound_deck_falls_through_to_the_next_provider(monkeypatch):
    deck = done_deck(monkeypatch, [entry("weak"), entry("strong")],
                     {"weak": SHALLOW, "strong": SOUND})
    assert deck["verified"] is True


def test_a_sound_deck_stops_the_chain_immediately(monkeypatch):
    """A verified deck must not cost a second provider's allowance."""
    used = []

    factory = client_by_model({"strong": SOUND, "weak": SHALLOW})

    def counting(api_key, endpoint):
        used.append(endpoint)
        return factory(api_key, endpoint)

    monkeypatch.setattr(main, "get_providers", lambda: [entry("strong"), entry("weak")])
    monkeypatch.setattr(main, "get_client", counting)
    with client.stream("POST", "/api/chat/stream",
                       json={"message": "Why is the sky blue?"}) as r:
        list(r.iter_lines())
    assert len(used) == 1, used


def test_the_best_deck_is_returned_when_none_verify(monkeypatch):
    """Still a deck, still flagged — better than no answer at all."""
    deck = done_deck(monkeypatch, [entry("weak"), entry("weak2")],
                     {"weak": SHALLOW, "weak2": SHALLOW})
    assert deck["verified"] is False
    assert deck["cards"], "an unsound deck must still be shown, not dropped"
