import os
import json
import re
import time
from functools import lru_cache
from typing import Dict, List, Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response, FileResponse, StreamingResponse
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

# 4000 was tuned against non-reasoning models. Reasoning models bill their
# hidden thinking against this budget but report only the visible tokens, so
# gemini-3.6-flash was finishing with finish_reason="length" at 336 reported
# tokens and a deck cut off mid-string. Raising it costs nothing on models that
# do not think — they stop at ~1800 tokens either way, and you are billed for
# what is generated, not for the ceiling.
DECK_MAX_TOKENS = int(os.environ.get("DECK_MAX_TOKENS", 12000))

# Without this a stalled provider holds the request open forever, and the
# reader watches a shimmer with no way to tell slow from dead. Decks routinely
# take 25-35s, so the ceiling has to sit well above that.
DECK_TIMEOUT_SECONDS = float(os.environ.get("DECK_TIMEOUT_SECONDS", 90))

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
        # Writing a question is a far smaller job than decomposing one, so it
        # defaults to the same cheap model and can be pointed somewhere cheaper.
        "questions_model": os.environ.get("QUESTIONS_MODEL", "")
        or os.environ.get("MODEL", DEFAULT_MODEL),
    }


# ══════════════════════════════════════════════════════════════════════════
# PROVIDERS
#
# Every free tier is capped per account per day, so no single one can carry a
# public app: the first handful of visitors spend the allowance and everyone
# after gets a 429. Chaining several is what makes "free" actually hold up.
#
# A fallback therefore has to change the base URL and the key, not just the
# model name — which is what FALLBACK_MODELS could never express. All of these
# speak the OpenAI wire format, so one client class covers them.
# ══════════════════════════════════════════════════════════════════════════

KNOWN_PROVIDERS = {
    "groq":       "https://api.groq.com/openai/v1",
    "gemini":     "https://generativelanguage.googleapis.com/v1beta/openai/",
    "cerebras":   "https://api.cerebras.ai/v1",
    "nvidia":     "https://integrate.api.nvidia.com/v1",
    "mistral":    "https://api.mistral.ai/v1",
    "together":   "https://api.together.xyz/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "openai":     "https://api.openai.com/v1",
}


def get_providers() -> List[dict]:
    """The chain to try, in order, as {name, endpoint, api_key, model}.

    Set PROVIDERS to a comma-separated list and give each one a _API_KEY and
    _MODEL. Anything unconfigured is skipped rather than failing the chain —
    listing a provider you have not signed up for yet is not an error.
    """
    names = [n.strip().lower()
             for n in os.environ.get("PROVIDERS", "").split(",") if n.strip()]

    if names:
        chain = []
        for name in names:
            prefix = name.upper()
            key = os.environ.get(f"{prefix}_API_KEY", "").strip()
            endpoint = (os.environ.get(f"{prefix}_ENDPOINT", "").strip()
                        or KNOWN_PROVIDERS.get(name, ""))

            # Caps are usually per model, so several models behind one key are
            # several allowances. _MODELS wins over _MODEL when both are set.
            plural = os.environ.get(f"{prefix}_MODELS", "").strip()
            models = ([m.strip() for m in plural.split(",") if m.strip()] if plural
                      else [os.environ.get(f"{prefix}_MODEL", "").strip()])

            for model in models:
                if key and model and endpoint:
                    chain.append({
                        "name": name, "endpoint": endpoint,
                        "api_key": key, "model": model,
                        # Providers disagree wildly on this, so it is per-entry.
                        "max_tokens": int(os.environ.get(
                            f"{prefix}_MAX_TOKENS", DECK_MAX_TOKENS)),
                    })
        return chain

    # No PROVIDERS set: keep the original single-endpoint config working so an
    # existing .env is not silently broken.
    key = (os.environ.get("OPENROUTER_API_KEY") or
           os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        return []

    endpoint = os.environ.get("API_ENDPOINT", DEFAULT_ENDPOINT)
    models = [os.environ.get("MODEL", DEFAULT_MODEL)]
    models += [m.strip() for m in
               os.environ.get("FALLBACK_MODELS", DEFAULT_FALLBACKS).split(",")
               if m.strip()]

    return [{"name": "openrouter", "endpoint": endpoint, "api_key": key,
             "model": model, "max_tokens": DECK_MAX_TOKENS} for model in models]


# Ceiling on any cooldown. A wrong guess should cost minutes, not a day.
MAX_COOLDOWN = 3600
DEFAULT_COOLDOWN = 60


def cooldown_for(error: str) -> int:
    """How long to stop using an entry after it refused a request.

    The provider is the authority when it says so — Gemini returns "Please
    retry in 52.8s" — otherwise the wording distinguishes a per-minute cap
    (clears itself shortly) from a per-day one or an empty wallet (will not).
    """
    said = re.search(r"retry in ([0-9]+(?:\.[0-9]+)?)s", error, re.I)
    if said:
        return max(1, min(MAX_COOLDOWN, int(float(said.group(1)))))

    low = error.lower()
    if "perday" in low.replace("-", "").replace("_", "") or "per day" in low \
            or "per-day" in low or "402" in low or "credit" in low or "billing" in low:
        return MAX_COOLDOWN
    return DEFAULT_COOLDOWN


# HTTP statuses that mean "this entry cannot serve you right now". Anything
# else — a malformed deck, a truncated one — is a bad roll from a working
# provider, and sidelining it for a minute would throw away the best entry in
# the chain over one unlucky response.
CAPACITY_CODES = ("400", "401", "402", "403", "413", "429", "500", "502", "503", "529")


def should_cool_down(error: str) -> bool:
    low = error.lower()
    if any(f"error code: {code}" in low for code in CAPACITY_CODES):
        return True
    return any(word in low for word in
               ("rate limit", "quota", "too many requests", "overloaded",
                "insufficient", "billing", "payment required"))


class ProviderPool:
    """Which entries are worth trying right now, and in what order.

    Declared order is the preference — the first entry carries normal traffic
    and the rest are fallbacks. What makes that work is remembering what is
    spent, so a capped entry is skipped instead of costing a doomed call on
    every request.

    Deliberately NOT round-robin. Spreading requests evenly assumes the
    allowances are comparable, and they are not: Groq refills 8000 tokens every
    minute while a Gemini model gets 20 requests for the entire day. Rotating
    between those spends the scarce one to relieve the renewable one.
    """

    def __init__(self):
        self._until: Dict[str, float] = {}

    @staticmethod
    def _key(provider: dict) -> str:
        return f"{provider['name']}:{provider['model']}"

    def penalise(self, provider: dict, seconds: int,
                 now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        self._until[self._key(provider)] = now + seconds

    def is_cooling(self, provider: dict, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        return self._until.get(self._key(provider), 0) > now

    def order(self, providers: List[dict], now: Optional[float] = None) -> List[dict]:
        now = time.time() if now is None else now
        healthy = [p for p in providers if not self.is_cooling(p, now)]

        # Everything is cooling: try anyway rather than refuse outright. The
        # cooldown is an estimate; the provider decides.
        return healthy or list(providers)


PROVIDER_POOL = ProviderPool()


@lru_cache(maxsize=8)
def get_client(api_key: str, endpoint: str) -> AsyncOpenAI:
    """One pooled client per (key, endpoint) instead of one per request."""
    return AsyncOpenAI(
        api_key=api_key,
        base_url=endpoint,
        default_headers={
            "HTTP-Referer": os.environ.get("PUBLIC_URL", "http://127.0.0.1:8000"),
            "X-Title": "First Principle Bot",
        },
    )


# ══════════════════════════════════════════════════════════════════════════
# RATE LIMITING
#
# Every deck and every sector costs a model call from one shared budget, and
# nothing here is authenticated. On a public URL the limiter is the only thing
# between a visitor and someone else's bill.
#
# A fixed window per caller, in memory. That is enough for one process, which
# is what this app is: EXPLORE_POOLS already assumes a single instance. Behind
# more than one instance the limits become per-instance and this needs Redis.
# ══════════════════════════════════════════════════════════════════════════


def client_key(request) -> str:
    """Who to charge a request to.

    Behind a proxy every request arrives from the proxy's address, so the
    socket peer is useless — without the forwarded header every visitor would
    share one bucket and the first few would lock out everyone else. The first
    entry is the original client; the rest are hops.
    """
    forwarded = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


class RateLimiter:
    def __init__(self, limit: int, window: int):
        self.limit = limit
        self.window = window
        self._hits: Dict[str, List[float]] = {}

    def _recent(self, key: str, now: float) -> List[float]:
        cutoff = now - self.window
        return [t for t in self._hits.get(key, []) if t > cutoff]

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        self._evict(now)

        recent = self._recent(key, now)
        if len(recent) >= self.limit:
            # Deliberately not recording the refused attempt: counting it would
            # let a retry loop hold its own window open indefinitely.
            self._hits[key] = recent
            return False

        recent.append(now)
        self._hits[key] = recent
        return True

    def retry_after(self, key: str, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        recent = self._recent(key, now)
        if not recent:
            return 0
        return max(0, int(recent[0] + self.window - now))

    def _evict(self, now: float) -> None:
        """Drop callers with nothing left in the window. Without this the dict
        grows forever, keyed by anything that can reach the port."""
        cutoff = now - self.window
        for key in [k for k, v in self._hits.items() if not any(t > cutoff for t in v)]:
            del self._hits[key]


# A deck is the expensive call; sectors are cheaper but still cost one each.
DECK_LIMITER = RateLimiter(
    limit=int(os.environ.get("DECK_RATE_LIMIT", "8")),
    window=int(os.environ.get("DECK_RATE_WINDOW", "300")),
)
EXPLORE_LIMITER = RateLimiter(
    limit=int(os.environ.get("EXPLORE_RATE_LIMIT", "20")),
    window=int(os.environ.get("EXPLORE_RATE_WINDOW", "300")),
)


def enforce(limiter: RateLimiter, request) -> None:
    key = client_key(request)
    if not limiter.allow(key):
        wait = limiter.retry_after(key)
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Try again in {wait} seconds.",
            headers={"Retry-After": str(wait)},
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
    print(f"[deck] {detail}", flush=True)
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
    # build_messages only uses the last 20; the cap is well above that so a
    # long session never 422s, while the body stays bounded.
    history: List[HistoryMessage] = Field(default_factory=list, max_length=60)
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


async def request_deck(client: AsyncOpenAI, model: str, messages: List[dict],
                       max_tokens: Optional[int] = None) -> tuple:
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        stream=False,
        temperature=0.4,
        max_tokens=max_tokens or DECK_MAX_TOKENS,
        response_format={"type": "json_object"},
        timeout=DECK_TIMEOUT_SECONDS,
    )
    choice = response.choices[0] if response.choices else None
    content = choice.message.content if choice else ""
    finish = choice.finish_reason if choice else None

    if not content or not content.strip():
        raise EmptyCompletion(
            (f"{model} returned an empty response. "
             + model_failure_hint(model, response.model, finish)).strip()
        )

    # Truncated output is non-empty, so it used to skip the check above and die
    # in the JSON parser instead — reported as "unusable JSON", which points at
    # the wrong problem entirely. A reasoning model bills hidden thinking
    # against max_tokens without reporting it, so this fires well below the
    # apparent budget.
    if finish == "length":
        raise EmptyCompletion(
            (f"{model} was cut off before it finished the deck. "
             + model_failure_hint(model, response.model, finish)).strip()
        )

    return normalize_deck(extract_json_object(content)), content


def finalize(deck: dict, req: ChatRequest) -> dict:
    if deck.get("reframed") and is_verbatim_restatement(req.message, deck["question"]):
        deck["reframed"] = False
    return deck


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    enforce(DECK_LIMITER, request)
    providers = get_providers()

    if not providers:
        return json_response(make_error_deck(req.message, "No model provider is configured. Set PROVIDERS with a key and model for each — for example PROVIDERS=groq,gemini with GROQ_API_KEY and GROQ_MODEL set. See .env.example."))

    messages = build_messages(req)

    last_error = ""
    for provider in PROVIDER_POOL.order(providers):
        client = get_client(provider["api_key"], provider["endpoint"])
        model = provider["model"]
        try:
            deck, raw = await request_deck(client, model, messages,
                                           provider["max_tokens"])
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
                ], provider["max_tokens"])
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
            last_error = f"{provider['name']}/{model} returned unusable JSON: {exc}"
        except Exception as exc:
            last_error = f"{provider['name']}/{model}: {exc}"
            if should_cool_down(str(exc)):
                PROVIDER_POOL.penalise(provider, cooldown_for(str(exc)))

    return json_response(make_error_deck(
        req.message, public_error(f"Deck generation failed. {last_error}")))


# ══════════════════════════════════════════════════════════════════════════
# EXPLORE — a question worth decomposing, for people who cannot think of one.
#
# The blank box is the real bottleneck in this app: the method is sharp, but
# composing a question whose conventional answer is itself a convention is a
# skill. So the model writes them, per sector, and no question is served twice.
# ══════════════════════════════════════════════════════════════════════════

SECTORS = [
    {"slug": "career",     "label": "Career",     "blurb": "work, money, status"},
    {"slug": "health",     "label": "Health",     "blurb": "the body, sleep, food"},
    {"slug": "football",   "label": "Football",   "blurb": "the game, the physics"},
    {"slug": "history",    "label": "History",    "blurb": "how we got here"},
    {"slug": "money",      "label": "Money",      "blurb": "value, price, debt"},
    {"slug": "mind",       "label": "Mind",       "blurb": "memory, belief, self"},
    {"slug": "physics",    "label": "Physics",    "blurb": "matter, light, time"},
    {"slug": "technology", "label": "Technology", "blurb": "machines that think"},
    {"slug": "everyday",   "label": "Everyday",   "blurb": "the ordinary, examined"},
]

# How many to show at once, and the mark below which the pool is topped up.
EXPLORE_PAGE = 6
EXPLORE_LOW_WATER = 6
EXPLORE_BATCH = 12


def sector_by_slug(slug: str) -> Optional[dict]:
    return next((s for s in SECTORS if s["slug"] == slug), None)


def _looks_like_a_question(text: str) -> bool:
    """A topic heading is not a question, and the deck prompt needs a question."""
    return text.endswith("?") and len(text) >= 12


def normalize_questions(raw) -> List[dict]:
    """Keep the entries that clear the bar; drop the rest silently.

    The generator is a cheap model and will return headings, fragments and the
    occasional bare string. None of that is worth showing.
    """
    if not isinstance(raw, list):
        return []

    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        question = _text(item.get("question"))
        if not _looks_like_a_question(question):
            continue
        out.append({"question": question, "hook": _text(item.get("hook"))})
    return out


def _key(question: str) -> str:
    return " ".join(question.lower().split())


class ExplorePool:
    """Questions waiting to be shown for one sector, and every one already
    shown. The generator is asked to avoid repeats and will repeat anyway, so
    the pool is the backstop rather than the prompt."""

    def __init__(self):
        self._waiting: List[dict] = []
        self._served: List[str] = []
        self._seen: set = set()

    def add(self, questions: List[dict]) -> None:
        for item in questions:
            key = _key(item["question"])
            if key in self._seen:
                continue
            self._seen.add(key)
            self._waiting.append(item)

    def take(self, count: int) -> List[dict]:
        taken, self._waiting = self._waiting[:count], self._waiting[count:]
        self._served.extend(item["question"] for item in taken)
        return taken

    def needs_refill(self, low_water: int) -> bool:
        return len(self._waiting) < low_water

    def avoid_list(self, limit: int = 40) -> List[str]:
        """The most recently served questions, to steer the next batch away."""
        return self._served[-limit:]


EXPLORE_POOLS: Dict[str, ExplorePool] = {}


QUESTIONS_SYSTEM = """You write questions for a tool that decomposes things to first principles.

A good question here is one where the conventional answer is ITSELF a convention — where the thing most people would say is a habit of speech rather than a fact. Decomposing it has to actually pay off.

Good: "Why do planes actually stay up?" (the equal-transit story is wrong)
Good: "What is money, really?" (a shared belief, not a substance)
Good: "Why does a mirror flip left to right but not up and down?" (it does neither)
Bad: "How does inflation work?" (a topic, not a question with a convention inside it)
Bad: "What are the benefits of sleep?" (homework, and the answer is a list)

Each question must be answerable by decomposing to physical law, logical necessity or definitional truth. A curious 16-year-old must understand the question without a glossary.

Return ONLY a JSON object:
{"questions": [{"question": "...", "hook": "..."}]}

"hook" is at most five words naming the belief the question is about to take apart — "the equal-transit myth", "money as a substance". Lowercase, no final period. It is a label, not a sentence."""


async def generate_questions(sector: dict, avoid: List[str], count: int) -> List[dict]:
    providers = get_providers()
    if not providers:
        return []

    ask = (
        f"Sector: {sector['label']} — {sector['blurb']}.\n"
        f"Write {count} questions in this sector."
    )
    if avoid:
        listed = "\n".join(f"- {q}" for q in avoid)
        ask += f"\n\nDo not write any of these, or a rewording of them:\n{listed}"

    # Same chain as a deck: if the first provider has spent its daily free
    # allowance, Explore falls through rather than going blank.
    override = os.environ.get("QUESTIONS_MODEL", "").strip()
    for provider in PROVIDER_POOL.order(providers):
        client = get_client(provider["api_key"], provider["endpoint"])
        try:
            response = await client.chat.completions.create(
                model=override or provider["model"],
                messages=[
                    {"role": "system", "content": QUESTIONS_SYSTEM},
                    {"role": "user", "content": ask},
                ],
                stream=False,
                temperature=1.0,  # variety beats precision for a question list
                max_tokens=1200,
                response_format={"type": "json_object"},
                timeout=45,
            )
            choice = response.choices[0] if response.choices else None
            content = choice.message.content if choice else ""
            if not content or not content.strip():
                continue

            questions = normalize_questions(extract_json_object(content).get("questions"))
            if questions:
                return questions
        except Exception as exc:
            print(f"[explore] {provider['name']}: {exc}", flush=True)
            if should_cool_down(str(exc)):
                PROVIDER_POOL.penalise(provider, cooldown_for(str(exc)))

    return []


@app.get("/api/explore/sectors")
async def explore_sectors():
    return json_response({"sectors": SECTORS})


@app.get("/api/explore/{slug}")
async def explore_sector(slug: str, request: Request):
    sector = sector_by_slug(slug)
    if sector is None:
        raise HTTPException(status_code=404, detail=f"Unknown sector: {slug}")

    pool = EXPLORE_POOLS.setdefault(slug, ExplorePool())
    if pool.needs_refill(EXPLORE_LOW_WATER):
        enforce(EXPLORE_LIMITER, request)
        try:
            pool.add(await generate_questions(sector, pool.avoid_list(), EXPLORE_BATCH))
        except Exception:
            # A sector that cannot generate shows nothing and stays browsable.
            # The rest of the page is unaffected.
            pass

    return json_response({"sector": sector, "questions": pool.take(EXPLORE_PAGE)})


# ══════════════════════════════════════════════════════════════════════════
# STREAMING — the same deck, delivered as it is written.
#
# A deck takes 25-35s, and a spinner for that long reads as a hang. The model
# emits cards in narrative order, so they can be shown as they close: the
# descent appears while the rebuild is still being written. One call, same
# cost, same final deck. The authoritative deck is sent last because a chain
# cannot be validated until it is finished.
# ══════════════════════════════════════════════════════════════════════════


def _ndjson(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


async def stream_cards(client: AsyncOpenAI, model: str, messages: List[dict],
                       max_tokens: Optional[int] = None):
    """Yield ("card", card) as each one closes, then ("raw", full_text)."""
    stream = await client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        temperature=0.4,
        max_tokens=max_tokens or DECK_MAX_TOKENS,
        response_format={"type": "json_object"},
        timeout=DECK_TIMEOUT_SECONDS,
    )

    accumulated, sent = "", 0
    async for chunk in stream:
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        piece = getattr(delta, "content", None) if delta is not None else None
        if not piece:
            continue

        accumulated += piece
        cards = complete_cards(accumulated)
        while sent < len(cards):
            yield "card", cards[sent]
            sent += 1

    yield "raw", accumulated


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    enforce(DECK_LIMITER, request)
    providers = get_providers()

    async def emit():
        if not providers:
            yield _ndjson({"type": "done", "deck": make_error_deck(
                req.message, "No model provider is configured. Set PROVIDERS with a key and model for each — for example PROVIDERS=groq,gemini with GROQ_API_KEY and GROQ_MODEL set. See .env.example.")})
            return

        messages = build_messages(req)
        last_error = ""

        for provider in PROVIDER_POOL.order(providers):
            client = get_client(provider["api_key"], provider["endpoint"])
            model = provider["model"]
            raw = ""
            try:
                async for kind, value in stream_cards(client, model, messages,
                                                      provider["max_tokens"]):
                    if kind == "card":
                        yield _ndjson({"type": "card", "card": value})
                    else:
                        raw = value

                if not raw.strip():
                    raise EmptyCompletion(f"{model} returned an empty response.")

                deck = normalize_deck(extract_json_object(raw))

                # Same one-shot repair as the non-streaming path. The cards
                # already sent may be replaced by the done deck; that is the
                # honest trade for showing them early.
                if not deck["verified"]:
                    hard, _ = validate_chain(deck["cards"])
                    try:
                        repaired, _ = await request_deck(client, model, messages + [
                            {"role": "assistant", "content": raw},
                            {"role": "user", "content": repair_instruction(hard)},
                        ], provider["max_tokens"])
                        if repaired["verified"] or len(repaired["issues"]) < len(deck["issues"]):
                            deck = repaired
                    except Exception:
                        pass  # keep the original; it is already flagged unverified

                yield _ndjson({"type": "done", "deck": finalize(deck, req)})
                return

            except EmptyCompletion as exc:
                last_error = str(exc)
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = f"{provider['name']}/{model} returned unusable JSON: {exc}"
            except Exception as exc:
                last_error = f"{provider['name']}/{model}: {exc}"
                if should_cool_down(str(exc)):
                    PROVIDER_POOL.penalise(provider, cooldown_for(str(exc)))

        yield _ndjson({"type": "done", "deck": make_error_deck(
            req.message, public_error(f"Deck generation failed. {last_error}"))})

    return StreamingResponse(
        emit(),
        media_type="application/x-ndjson",
        # Without these a proxy will sit on the response and hand it over in
        # one piece, which defeats the whole endpoint.
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.get("/api/health")
async def health():
    config = get_config()
    providers = get_providers()
    return {
        "status": "ok",
        "api_key_configured": bool(config["api_key"]) or bool(providers),
        "api_endpoint": config["endpoint"],
        "model": config["model"],
        "fallback_models": config["fallbacks"],
        "model_is_router": config["model"] in ROUTER_MODELS,
        # The chain, without the keys. Which providers are configured and in
        # what order is the thing you actually need when a deploy misbehaves.
        "providers": [{"name": p["name"], "model": p["model"]} for p in providers],
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
