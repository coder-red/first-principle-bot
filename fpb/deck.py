"""Turning model output into a deck, and checking that the deck is sound.

Two separate jobs live here and the distinction matters:

`normalize_deck` guarantees the SHAPE — every field the client renders exists
and has the right type. `validate_chain` checks the CONTRACT — that the
decomposition obeys its own rules. A deck can pass the first and fail the
second, and that is exactly the case this app cannot afford to render as
though it passed.
"""

import json
import os
from typing import List, Optional

from fpb.config import PHASES, ROUTER_MODELS, TAGS
from fpb.telemetry import COUNTERS, LOG


class EmptyCompletion(Exception):
    """The provider answered, but with no usable content."""


def extract_json_object(content: str) -> dict:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise


def _cards_array_start(partial: str) -> int:
    """Index just past the `[` that opens the top-level `cards` array, or -1.

    Scans string tokens properly rather than searching for the substring, so a
    topic like "how do playing cards work" is not mistaken for the key.
    """
    i, n = 0, len(partial)
    while i < n:
        if partial[i] != '"':
            i += 1
            continue

        # Read to the end of this string token, honouring escapes.
        j, escaped = i + 1, False
        while j < n:
            if escaped:
                escaped = False
            elif partial[j] == "\\":
                escaped = True
            elif partial[j] == '"':
                break
            j += 1
        if j >= n:
            return -1  # string never closed; nothing further is trustworthy

        token = partial[i + 1:j]
        k = j + 1
        while k < n and partial[k].isspace():
            k += 1
        if token == "cards" and k < n and partial[k] == ":":
            k += 1
            while k < n and partial[k].isspace():
                k += 1
            return k + 1 if k < n and partial[k] == "[" else -1
        i = j + 1
    return -1


def complete_cards(partial: str) -> List[dict]:
    """Every fully-closed card object in a deck whose JSON is still arriving.

    The model emits cards in narrative order, so this is what lets the client
    render the descent while the rebuild is still being written.
    """
    start = _cards_array_start(partial)
    if start < 0:
        return []

    cards: List[dict] = []
    depth, obj_start = 0, -1
    in_string, escaped = False, False

    for i in range(start, len(partial)):
        ch = partial[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and obj_start >= 0:
                try:
                    cards.append(json.loads(partial[obj_start:i + 1]))
                except json.JSONDecodeError:
                    pass  # not yet parseable; a later chunk may complete it
                obj_start = -1
        elif ch == "]" and depth == 0:
            break  # the array closed; followups start here

    return cards


def _text(value, fallback: str = "") -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _text_list(value, limit: int = 8) -> List[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()][:limit]


def _tag(value) -> str:
    """Normalize '[atomic]', 'ATOMIC', ' Verified ' -> a known tag, else ''."""
    candidate = _text(value).strip("[] ").upper()
    return candidate if candidate in TAGS else ""


MIN_DESCENT_CARDS = 3

# Sound material for a reconstruction: a rebuild step may lean on a convention
# only if it says so, but never on a bare assumption or an unknown.
UNSOUND_FOR_REBUILD = ("ASSUMPTION", "UNKNOWN")


def validate_chain(cards: List[dict]) -> tuple:
    """Check the decomposition contract, not just the JSON shape.

    normalize_deck guarantees every field has the right type. That is not the
    same as the reasoning being sound: a deck can be perfectly well-formed and
    still tag a descent card ATOMIC, or stop at a bedrock no deeper than the
    step that reached it. This app's whole claim is rigour, so a chain that
    breaks its own rules must not render as though it passed.

    Returns (hard, soft). Hard violations are contradictions worth spending a
    repair call on; soft ones are worth showing but not worth re-asking for.
    """
    by_phase = {phase: [c for c in cards if c["phase"] == phase] for phase in PHASES}
    descent = by_phase["descent"]
    bedrock = by_phase["bedrock"]

    hard, soft = [], []

    if len(bedrock) != 1:
        hard.append(
            "There must be exactly one bedrock card; this deck has %d." % len(bedrock)
        )

    if len(descent) < MIN_DESCENT_CARDS:
        hard.append(
            "The descent must be at least %d cards deep; this deck has %d."
            % (MIN_DESCENT_CARDS, len(descent))
        )

    levels = [c["level"] for c in descent]
    if levels and levels[0] != 1:
        hard.append("The descent must start at level 1; this one starts at %d." % levels[0])
    if any(b <= a for a, b in zip(levels, levels[1:])):
        hard.append(
            "Each descent card must go strictly deeper than the one before it; "
            "the levels run %s." % (levels,)
        )

    for card in descent:
        if card["tag"] == "ATOMIC":
            hard.append(
                'Descent card "%s" is tagged ATOMIC. If a claim is irreducible the '
                "decomposition stops there and that card is the bedrock." % card["title"]
            )

    if len(bedrock) == 1:
        bed = bedrock[0]
        deepest = max(levels) if levels else 0
        if bed["level"] <= deepest:
            hard.append(
                'The bedrock "%s" sits at level %d, no deeper than the descent that '
                "reached it (level %d). Bedrock must lie below the last descent step."
                % (bed["title"], bed["level"], deepest)
            )
        if bed["tag"] not in ("ATOMIC", "UNKNOWN"):
            hard.append(
                'The bedrock "%s" is tagged %s. Bedrock must be ATOMIC, or UNKNOWN if '
                "it genuinely cannot be reduced without fabricating."
                % (bed["title"], bed["tag"] or "nothing")
            )
        if not bed["chain"]:
            soft.append("The bedrock card shows no chain, so the path to it is not visible.")

    for card in by_phase["rebuild"]:
        if card["tag"] in UNSOUND_FOR_REBUILD:
            soft.append(
                'Rebuild card "%s" is tagged %s. A reconstruction should stand on '
                "ATOMIC or VERIFIED material." % (card["title"], card["tag"])
            )

    for card in by_phase["question"]:
        if card["tag"] in ("ATOMIC", "VERIFIED"):
            soft.append(
                'The starting point "%s" is tagged %s. The belief a reader starts from '
                "is rarely already a first principle." % (card["title"], card["tag"])
            )

    if not by_phase["insight"]:
        soft.append("The deck never states what the decomposition revealed.")

    # Each descent card should carry the path that produced it, so the chain
    # deepens rather than restarting.
    for previous, current in zip(descent, descent[1:]):
        if previous["chain"] and current["chain"]:
            if current["chain"][:len(previous["chain"])] != previous["chain"]:
                soft.append(
                    'The chain on "%s" does not extend the chain on "%s".'
                    % (current["title"], previous["title"])
                )

    return hard, soft


def normalize_deck(data: dict) -> dict:
    """Coerce raw model output into the exact shape the client renders.

    The client trusts this result, so every field is forced to the right type
    here rather than defended against in JavaScript.
    """
    if not isinstance(data, dict):
        raise ValueError("Deck must be a JSON object.")

    raw_cards = data.get("cards")
    if not isinstance(raw_cards, list) or not raw_cards:
        raise ValueError("Deck must include a non-empty 'cards' array.")

    cards = []
    for index, raw in enumerate(raw_cards[:9]):
        if not isinstance(raw, dict):
            continue

        phase = _text(raw.get("phase")).lower()
        if phase not in PHASES:
            phase = "descent"

        try:
            level = int(raw.get("level", 0))
        except (TypeError, ValueError):
            level = 0

        cards.append({
            "phase": phase,
            "level": max(0, min(level, 9)),
            "title": _text(raw.get("title"), f"Step {index + 1}"),
            "question": _text(raw.get("question"), "What must be true here?"),
            "tag": _tag(raw.get("tag")),
            "principle": _text(raw.get("principle")),
            "chain": _text_list(raw.get("chain"), limit=6),
            "discarded": _text_list(raw.get("discarded"), limit=4),
            "explanation": _text(raw.get("explanation"), "I don't know."),
            "takeaway": _text(raw.get("takeaway")),
        })

    if not cards:
        raise ValueError("Deck contained no usable cards.")

    hard, soft = validate_chain(cards)

    return {
        "topic": _text(data.get("topic"), "Flashcards"),
        "question": _text(data.get("question"), cards[0]["question"]),
        "reframed": data.get("reframed") is True,
        "cards": cards,
        "followups": _text_list(data.get("followups"), limit=3),
        "verified": not hard,
        "issues": hard + soft,
    }


def public_error(detail: str) -> str:
    """What a visitor is allowed to see when generation fails.

    Raw provider exceptions carry model names, endpoint URLs and occasionally
    an echo of the request. That is useful on your own machine and needless
    exposure on a public URL, so it goes to the log unless DEBUG_ERRORS is set.
    """
    COUNTERS.bump("deck.failed")
    LOG.error("%s", detail)
    if os.environ.get("DEBUG_ERRORS", "").strip() in ("1", "true", "True"):
        return detail
    return ("The model could not be reached, or returned something unusable. "
            "This is a problem on the server, not with your question.")


def make_error_deck(topic: str, error: str) -> dict:
    topic = _text(topic, "your question")
    return {
        "topic": "Setup issue",
        "question": f"Why did the bot fail to answer: {topic[:80]}?",
        "cards": [
            {
                "phase": "question",
                "level": 0,
                "title": "I Could Not Build This Deck",
                "question": f"Why did the bot fail to answer: {topic[:80]}?",
                "tag": "VERIFIED",
                "principle": "The app needs a working model response before it can build a decomposition chain.",
                "chain": [],
                "discarded": [],
                "explanation": error,
                "takeaway": "Fix the model/API setup, then ask again.",
            },
        ],
        "followups": [],
        "reframed": False,
        "verified": False,
        "issues": [],
    }


def model_failure_hint(requested: str, routed: Optional[str], finish: Optional[str]) -> str:
    """Explain a bad completion instead of shrugging at the user."""
    parts = []
    if routed and routed != requested:
        parts.append(f"`{requested}` routed this request to `{routed}`.")
    if finish == "length":
        parts.append(
            "It hit the token limit before producing usable output — often a reasoning "
            "model spending the whole budget on hidden reasoning tokens. Raise "
            "DECK_MAX_TOKENS or pick a non-reasoning model."
        )
    elif requested in ROUTER_MODELS:
        parts.append(
            f"`{requested}` is a router: it picks a different model per request, and some "
            "of them cannot hold a chat at all. Pin a specific model with MODEL= in .env."
        )
    return " ".join(parts)


QUESTION_STOPWORDS = frozenset(
    "a an the is are was were do does did why what how when where which who of in on at "
    "to for from with that this it its be been being actually really than rather any "
    "other and or but so if we you i us not no there their they can could would should".split()
)


def _content_words(text: str) -> frozenset:
    cleaned = "".join(c.lower() if (c.isalnum() or c.isspace()) else " " for c in text)
    return frozenset(w for w in cleaned.split() if len(w) > 2 and w not in QUESTION_STOPWORDS)


def is_verbatim_restatement(asked: str, restated: str) -> bool:
    """Catch a deck claiming to have reframed a question it merely retyped.

    Deliberately strict. Word overlap cannot tell a rewording from a genuine
    reframe — "how does a magnet pull on something it never touches" and "how
    does a magnet exert force across empty space" share no content words yet
    mean the same thing, while "why is glass transparent" and "what must be
    true of a material for light to pass through it" also share none and do
    not. Only the model knows which it did, so this checks the one case that
    needs no judgement: the words are the same.
    """
    asked_words, restated_words = _content_words(asked), _content_words(restated)
    if not asked_words or not restated_words:
        return False
    return asked_words == restated_words


def repair_instruction(violations: List[str]) -> str:
    lines = ["That deck breaks the decomposition contract:"]
    lines += ["- " + v for v in violations]
    lines.append(
        "Rebuild the deck so that none of those hold. Keep the same topic and question. "
        "If a claim really is irreducible, make it the bedrock rather than tagging a "
        "descent card ATOMIC. Return only the JSON object."
    )
    return "\n".join(lines)
