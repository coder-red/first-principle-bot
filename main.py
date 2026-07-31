import os
import json
from functools import lru_cache
from typing import List, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
import uvicorn
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="First Principle Bot")

app.mount("/static", StaticFiles(directory="static"), name="static")

# Benchmarked against this app's actual workload: ling-2.6-flash produced the
# deepest decks (depth 4, proper bedrock) and the longest reasoning, at
# $0.03/Mtok. Routers like openrouter/auto and openrouter/free are deliberately
# NOT defaults — they pick a different model per request and can land on one
# that cannot hold a chat at all.
DEFAULT_MODEL = "inclusionai/ling-2.6-flash"
DEFAULT_FALLBACKS = "meta-llama/llama-3.3-70b-instruct,mistralai/mistral-small-24b-instruct-2501"
DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1"

DECK_MAX_TOKENS = int(os.environ.get("DECK_MAX_TOKENS", 4000))

ROUTER_MODELS = ("openrouter/auto", "openrouter/free")

PHASES = ("question", "descent", "bedrock", "rebuild", "insight")
TAGS = ("ATOMIC", "VERIFIED", "CONVENTION", "ASSUMPTION", "UNKNOWN")


@app.get("/")
async def root():
    return FileResponse("static/index.html")


def get_config() -> dict:
    """Read runtime config from the environment on each request.

    Kept dynamic so editing .env under `uvicorn --reload` takes effect without
    a code change, while the HTTP client itself is still pooled by get_client.
    """
    return {
        "api_key": os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY"),
        "endpoint": os.environ.get("API_ENDPOINT", DEFAULT_ENDPOINT),
        "model": os.environ.get("MODEL", DEFAULT_MODEL),
        "fallbacks": [
            m.strip()
            for m in os.environ.get("FALLBACK_MODELS", DEFAULT_FALLBACKS).split(",")
            if m.strip()
        ],
    }


@lru_cache(maxsize=8)
def get_client(api_key: str, endpoint: str) -> AsyncOpenAI:
    """One pooled client per (key, endpoint) instead of one per request."""
    return AsyncOpenAI(
        api_key=api_key,
        base_url=endpoint,
        default_headers={
            "HTTP-Referer": "http://127.0.0.1:8000",
            "X-Title": "First Principle Bot",
        },
    )


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


DECK_SYSTEM_PROMPT = """You are First Principle Bot.

You explain things using genuine first-principles reasoning — the method Aristotle called "the first basis from which a thing is known" and Descartes practiced as systematic doubt. You do NOT fill out a template. You reason, then you render the reasoning as a deck.

A deck is NOT a list of facts about the topic. A deck is a RENDERING OF ONE DECOMPOSITION CHAIN: the reader descends through the layers of the topic until they hit bedrock, then climbs back up rebuilding the explanation from that bedrock alone.

══ THINK FIRST (silently, do not output) ══
1. IDENTIFY THE QUESTION — the single precise question the deck answers.
2. INVENTORY — every common belief about the topic.
3. CARTESIAN DOUBT — for each: can I prove it? what would falsify it? is it physical necessity, logical necessity, or human convention?
   Label each: ATOMIC (irreducible — physical law, logical axiom, definitional truth), VERIFIED (empirically confirmed but could be otherwise), CONVENTION (widely accepted, unproven), ASSUMPTION (taken for granted), UNKNOWN.
4. RECURSIVE DECOMPOSITION — take the surviving claim and ask "what is this made of?", "what is this based on?", "what must be true for this to exist?" Repeat until you reach ATOMIC. Go at least 3 levels deep.
5. DISCARD — drop every CONVENTION, ASSUMPTION and UNKNOWN.
6. RECONSTRUCTION — rebuild using ONLY ATOMIC and VERIFIED items.
7. INSIGHT — what does discarding the conventions reveal?

══ THEN OUTPUT ══
Return ONLY valid JSON. No Markdown, no code fences, no prose outside the object.

{
  "topic": "short topic title",
  "question": "the single precise question this deck answers",
  "reframed": false,
  "cards": [
    {
      "phase": "question | descent | bedrock | rebuild | insight",
      "level": 0,
      "title": "short card title",
      "question": "the one question this card answers",
      "tag": "ATOMIC | VERIFIED | CONVENTION | ASSUMPTION | UNKNOWN",
      "principle": "the single claim this card establishes, in one sentence",
      "chain": ["level 1 claim", "level 2 what it is made of", "level 3 what THAT is made of"],
      "discarded": ["a convention or assumption dropped at this step"],
      "explanation": "2-4 short sentences justifying this step",
      "takeaway": "one concise memory hook"
    }
  ],
  "followups": ["a genuinely intriguing next question", "another", "a third"]
}

══ DECK STRUCTURE — follow exactly ══
Produce 6 or 7 cards in this order:

1. ONE card with phase "question", level 0. It states the precise question and the surface belief most people start from. Tag it CONVENTION or ASSUMPTION — the starting point is almost never a first principle. "discarded" lists the beliefs you are about to strip away.

2. THREE OR MORE cards with phase "descent", level 1, 2, 3 (increasing, one per card). Each answers "what is THIS made of / based on?" about the previous card. Each goes strictly deeper. Tag each honestly.

   "chain" holds the path from the surface down to this card. Build it cumulatively: a descent card's chain must repeat the previous card's chain ENTRY FOR ENTRY, in the same order, and then append exactly one new rung. Never drop, reword or reorder an earlier rung. The bedrock card's chain repeats the deepest descent card's chain and appends its own rung. So the chains grow 2, 3, 4, 5 entries as the deck descends.

3. ONE card with phase "bedrock", level = the deepest level reached. This is where decomposition stops. Tag MUST be ATOMIC, or UNKNOWN if you genuinely cannot reduce further and will not fabricate.

4. ONE OR TWO cards with phase "rebuild", level counting back DOWN toward 1. Reconstruct the original topic using ONLY ATOMIC and VERIFIED material. If a step needs a CONVENTION, say so explicitly in "explanation" and tag that card CONVENTION.

5. ONE card with phase "insight", level 0. What becomes visible now that the conventions are gone and that analogical thinking would have missed.

══ REFRAMED ══
Set "reframed" to true ONLY if "question" asks something materially different from what the user actually typed — you narrowed a vague prompt, corrected a false premise, or replaced the surface question with the one that has to be answered first. Set it to false when "question" is the user's own question reworded, expanded or made more formal, however different the wording looks. Rewording is not reframing. When in doubt, false.

══ FOLLOWUPS ══
End with 2-3 "followups": short, genuinely curious questions this deck opens up. They must be answerable by the same decomposition method, and they must be interesting to a curious person — not homework restatements of the topic. Never repeat the deck's own question.

══ STRICT RULES ══
1. ZERO analogies. Never write "it's like", "similar to", "think of it as". Forbidden.
2. Descent levels must strictly increase; each descent card must decompose the one before it, not restate it.
3. The bedrock card must be genuinely irreducible. Do not stop at a convention and call it ATOMIC.
4. Rebuild cards may use ONLY ATOMIC and VERIFIED material.
5. If you don't know something, say "I don't know" in that card and tag it UNKNOWN. Never fabricate.
6. "chain" strings are short — under 60 characters each. They render as a ladder, not as prose. Once a rung is written, later cards must repeat it verbatim.
7. A curious 16-year-old must be able to follow every card."""


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20000)


class Focus(BaseModel):
    """A node the reader wants decomposed further.

    Drilling into a chain rung, drilling into a card, and asking a free-text
    question about a card all reduce to this: build a deck that starts here.
    """
    title: str = Field(default="", max_length=300)
    principle: str = Field(default="", max_length=2000)
    chain: List[str] = Field(default_factory=list, max_length=6)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    history: List[HistoryMessage] = []
    focus: Optional[Focus] = None


def focus_instruction(focus: Focus) -> str:
    lines = [
        "This is a DRILL-DOWN. Do not restart from the surface.",
        f'Take this established claim as the deck\'s starting point: "{focus.principle or focus.title}"',
    ]
    if focus.chain:
        lines.append("It was reached through this chain: " + " -> ".join(focus.chain))
    lines.append(
        "The question card must state that claim as the starting belief, and every descent "
        "card must go BELOW it — decompose what that claim itself rests on. Do not re-derive "
        "the chain above it."
    )
    return "\n".join(lines)


def build_messages(req: ChatRequest) -> List[dict]:
    messages = [{"role": "system", "content": DECK_SYSTEM_PROMPT}]
    for msg in req.history[-20:]:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": req.message})
    if req.focus:
        messages.append({"role": "user", "content": focus_instruction(req.focus)})
    messages.append({
        "role": "user",
        "content": "Build the deck now. Return only the JSON object with topic, question, cards and followups.",
    })
    return messages


def json_response(payload: dict) -> Response:
    return Response(content=json.dumps(payload), media_type="application/json")


class EmptyCompletion(Exception):
    """The provider answered, but with no usable content."""


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


async def request_deck(client: AsyncOpenAI, model: str, messages: List[dict]) -> tuple:
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        stream=False,
        temperature=0.4,
        max_tokens=DECK_MAX_TOKENS,
        response_format={"type": "json_object"},
    )
    choice = response.choices[0] if response.choices else None
    content = choice.message.content if choice else ""
    finish = choice.finish_reason if choice else None

    if not content or not content.strip():
        raise EmptyCompletion(
            (f"{model} returned an empty response. "
             + model_failure_hint(model, response.model, finish)).strip()
        )

    return normalize_deck(extract_json_object(content)), content


def finalize(deck: dict, req: ChatRequest) -> dict:
    if deck.get("reframed") and is_verbatim_restatement(req.message, deck["question"]):
        deck["reframed"] = False
    return deck


@app.post("/api/chat")
async def chat(req: ChatRequest):
    config = get_config()

    if not config["api_key"]:
        return json_response(make_error_deck(
            req.message,
            "OPENROUTER_API_KEY is not set for this server process. Copy .env.example "
            "to .env, add your key, then restart the server.",
        ))

    client = get_client(config["api_key"], config["endpoint"])
    messages = build_messages(req)

    last_error = ""
    for model in [config["model"], *config["fallbacks"]]:
        try:
            deck, raw = await request_deck(client, model, messages)
            if deck["verified"]:
                return json_response(finalize(deck, req))

            # One repair pass, quoting the exact rules broken. Worth a single
            # extra call: an unsound chain is the one thing this app cannot
            # afford to render as though it passed.
            hard, _ = validate_chain(deck["cards"])
            try:
                repaired, _ = await request_deck(client, model, messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": repair_instruction(hard)},
                ])
                if repaired["verified"]:
                    return json_response(finalize(repaired, req))
                if len(repaired["issues"]) < len(deck["issues"]):
                    deck = repaired
            except Exception:
                pass  # keep the original deck; it is already flagged unverified

            # Still unsound. Return it labelled rather than pretending it passed.
            return json_response(finalize(deck, req))
        except EmptyCompletion as exc:
            last_error = str(exc)
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = f"{model} returned unusable JSON: {exc}"
        except Exception as exc:
            last_error = f"{model}: {exc}"

    return json_response(make_error_deck(req.message, f"Deck generation failed. {last_error}"))


@app.get("/api/health")
async def health():
    config = get_config()
    return {
        "status": "ok",
        "api_key_configured": bool(config["api_key"]),
        "api_endpoint": config["endpoint"],
        "model": config["model"],
        "fallback_models": config["fallbacks"],
        "model_is_router": config["model"] in ROUTER_MODELS,
    }


@app.get("/api/sample-deck")
async def sample_deck():
    return normalize_deck({
        "topic": "Why the sky is blue",
        "question": "Why does the daytime sky appear blue rather than any other colour?",
        "followups": [
            "Why is a sunset red if the same air is doing the scattering?",
            "What colour is the sky on a planet with no atmosphere?",
            "Why is the sea blue — is it the same reason?",
        ],
        "cards": [
            {
                "phase": "question",
                "level": 0,
                "title": "The Sky Is Blue",
                "question": "What do we actually start out believing here?",
                "tag": "ASSUMPTION",
                "principle": "The sky is a blue thing that we look at.",
                "chain": [],
                "discarded": [
                    "The sky is a surface with a colour",
                    "The sky reflects the ocean",
                ],
                "explanation": "Ordinary speech treats the sky as an object that owns a colour. Nothing has been proven yet. Before decomposing, note that 'the sky' is not a material object at all — it is a direction you look in.",
                "takeaway": "The starting belief names an object that does not exist.",
            },
            {
                "phase": "descent",
                "level": 1,
                "title": "Colour Is Light Reaching An Eye",
                "question": "What is a perceived colour made of?",
                "tag": "VERIFIED",
                "principle": "Seeing a colour means light of certain wavelengths arrives at the retina.",
                "chain": ["The sky looks blue", "Blue light arrives from that direction"],
                "discarded": [],
                "explanation": "No colour exists in the air itself. What can be measured is light travelling from a direction into an eye. So the question becomes: why does light from empty directions reach us at all?",
                "takeaway": "Ask what arrives, not what the thing is.",
            },
            {
                "phase": "descent",
                "level": 2,
                "title": "Air Redirects Light",
                "question": "Why does light arrive from a direction with no source in it?",
                "tag": "VERIFIED",
                "principle": "Gas molecules redirect passing electromagnetic waves in all directions.",
                "chain": [
                    "The sky looks blue",
                    "Blue light arrives from that direction",
                    "Air redirects light toward us",
                ],
                "discarded": [],
                "explanation": "The Sun sits in one small part of the sky, yet light comes from everywhere. Something between the Sun and the eye must be redirecting it. Measurement shows the atmosphere's gas molecules do this.",
                "takeaway": "Empty-looking sky is full of redirected light.",
            },
            {
                "phase": "descent",
                "level": 3,
                "title": "Redirection Depends On Wavelength",
                "question": "Why is the redirected light not white?",
                "tag": "VERIFIED",
                "principle": "Scattering by particles far smaller than the wavelength grows sharply as wavelength shortens.",
                "chain": [
                    "The sky looks blue",
                    "Blue light arrives from that direction",
                    "Air redirects light toward us",
                    "Short wavelengths redirect far more",
                ],
                "discarded": ["Sunlight is 'white' and therefore colourless"],
                "explanation": "Sunlight contains many wavelengths together. Air molecules are much smaller than those wavelengths, and in that regime the redirection strength rises steeply as wavelength falls. Short waves are redirected many times more than long ones.",
                "takeaway": "Short waves scatter, long waves pass through.",
            },
            {
                "phase": "bedrock",
                "level": 4,
                "title": "Charges Radiate When Driven",
                "question": "Why should a small molecule redirect short waves more at all?",
                "tag": "ATOMIC",
                "principle": "An accelerating electric charge radiates, and radiated power rises with the square of the driving frequency.",
                "chain": [
                    "The sky looks blue",
                    "Blue light arrives from that direction",
                    "Air redirects light toward us",
                    "Short wavelengths redirect far more",
                    "Driven charges radiate, harder at high frequency",
                ],
                "discarded": [],
                "explanation": "A passing wave drives the charges in a molecule back and forth. Driven charges accelerate, and accelerating charges radiate a new wave in all directions. Higher frequency means harder acceleration, so more radiated power. This is electromagnetism itself — it does not reduce further.",
                "takeaway": "Bedrock: driven charge radiates, harder at higher frequency.",
            },
            {
                "phase": "rebuild",
                "level": 2,
                "title": "Rebuild: The Sky From Bedrock",
                "question": "Can the whole effect be rebuilt from that alone?",
                "tag": "VERIFIED",
                "principle": "Sunlight drives atmospheric charges, which re-radiate short wavelengths preferentially in every direction.",
                "chain": [
                    "Driven charge radiates, harder at high frequency",
                    "Air re-radiates short waves in all directions",
                    "Every direction glows with short-wavelength light",
                ],
                "discarded": [],
                "explanation": "Sunlight of many wavelengths enters the air. Each molecule's charges are driven and re-radiate, most strongly at the short-wave end. That re-radiated light leaves in every direction, so every line of sight glows — including ones pointing nowhere near the Sun.",
                "takeaway": "No surface needed. The air itself glows.",
            },
            {
                "phase": "insight",
                "level": 0,
                "title": "Why Blue And Not Violet",
                "question": "What does this reveal that the surface story hides?",
                "tag": "VERIFIED",
                "principle": "The perceived colour is set jointly by the physics of scattering and the response of the eye.",
                "chain": [],
                "discarded": ["The sky 'is' blue"],
                "explanation": "Violet scatters even more strongly than blue, so pure physics predicts a violet sky. Sunlight contains less violet, more is absorbed high up, and human cones respond weakly to it. The answer is not a property of the sky — it is a property of light and observer together.",
                "takeaway": "Remove the observer and the question loses its answer.",
            },
        ],
    })


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 8000))
    reload = os.environ.get("RELOAD", "1") == "1"
    uvicorn.run("main:app", host=host, port=port, reload=reload)
