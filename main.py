"""First Principle Bot — the FastAPI app.

What lives here is the wiring: the routes, and the mutable singletons they
reach for (the limiters, the provider pool, the explore pools). Everything
that can be reasoned about without a server lives in `fpb/`.

The singletons stay module-level on purpose. The tests replace them with
`monkeypatch.setattr(main, ...)`, which only works if the handlers resolve
them through this module's namespace.
"""

import json
import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response, FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
import uvicorn
from dotenv import load_dotenv

from fpb.auth import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    is_enabled as access_control_enabled,
    require_access,
    token_matches,
)
from fpb.config import (
    DECK_MAX_TOKENS,
    DECK_TIMEOUT_SECONDS,
    KNOWN_PROVIDERS,
    ROUTER_MODELS,
    get_config,
    get_providers,
)
from fpb.deck import (
    EmptyCompletion,
    complete_cards,
    extract_json_object,
    is_verbatim_restatement,
    make_error_deck,
    model_failure_hint,
    normalize_deck,
    public_error,
    repair_instruction,
    validate_chain,
)
from fpb.explore import (
    EXPLORE_BATCH,
    EXPLORE_LOW_WATER,
    EXPLORE_PAGE,
    EXPLORE_POOLS,
    SECTORS,
    ExplorePool,
    normalize_questions,
    sector_by_slug,
)
from fpb.library import LIBRARY_ROOT, Library
from fpb.limits import RateLimiter, client_key, enforce
from fpb.prompts import QUESTIONS_SYSTEM, DECK_SYSTEM_PROMPT
from fpb.providers import (
    ProviderPool,
    cooldown_for,
    get_client,
    should_cool_down,
)
from fpb.sample import SAMPLE_DECK
from fpb.store import open_store
from fpb.schemas import ChatRequest, Focus, build_messages, focus_instruction
from fpb.telemetry import COUNTERS, LOG, configure_logging

# A few of the names above — KNOWN_PROVIDERS, client_key, Focus,
# focus_instruction, DECK_SYSTEM_PROMPT, ProviderPool — are not used by the
# handlers here. They are imported so that `from main import ...` keeps
# working: this module is the app's public surface, and the test suite reaches
# for it by name.

load_dotenv()
configure_logging()

app = FastAPI(title="First Principle Bot")

app.mount("/static", StaticFiles(directory="static"), name="static")


# ══════════════════════════════════════════════════════════════════════════
# MUTABLE STATE
#
# One process holds all of it. The explore pools and the rate limiters both
# assume that, which is why render.yaml pins numInstances to 1: behind two
# instances the limits double and the pools repeat questions.
# ══════════════════════════════════════════════════════════════════════════

# None unless STATE_DB is set, in which case all three survive a restart.
STORE = open_store()
if STORE is not None:
    LOG.info("persisting cooldowns, rate limits and explore pools to %s", STORE.path)

PROVIDER_POOL = ProviderPool(store=STORE)

# Loaded once at startup. Publishing a deck means committing it and
# deploying — the review gate is the git history, not an admin route.
LIBRARY = Library.load(LIBRARY_ROOT)
if LIBRARY.count():
    LOG.info("library: %d reviewed decks loaded", LIBRARY.count())

# A deck is the expensive call; sectors are cheaper but still cost one each.
DECK_LIMITER = RateLimiter(
    limit=int(os.environ.get("DECK_RATE_LIMIT", "8")),
    window=int(os.environ.get("DECK_RATE_WINDOW", "300")),
    name="deck",
    store=STORE,
)
EXPLORE_LIMITER = RateLimiter(
    limit=int(os.environ.get("EXPLORE_RATE_LIMIT", "20")),
    window=int(os.environ.get("EXPLORE_RATE_WINDOW", "300")),
    name="explore",
    store=STORE,
)

# Tighter, and separate. Without its own bucket the token exchange is an
# unmetered oracle: a caller with no token is refused by require_access before
# it ever reaches a limiter, so guessing would cost nothing.
ACCESS_LIMITER = RateLimiter(
    limit=int(os.environ.get("ACCESS_RATE_LIMIT", "10")),
    window=int(os.environ.get("ACCESS_RATE_WINDOW", "600")),
    name="access",
    store=STORE,
)

NO_PROVIDER_MESSAGE = (
    "No model provider is configured. Set PROVIDERS with a key and model for "
    "each — for example PROVIDERS=groq,gemini with GROQ_API_KEY and GROQ_MODEL "
    "set. See .env.example."
)


@app.get("/")
async def root():
    return FileResponse("static/index.html")


def json_response(payload: dict) -> Response:
    return Response(content=json.dumps(payload), media_type="application/json")


class AccessRequest(BaseModel):
    token: str = Field(min_length=1, max_length=500)


@app.post("/api/access")
async def exchange_access_token(body: AccessRequest, request: Request):
    """Trade the shared token for a cookie, so the UI can carry it.

    404 rather than 200 when access control is off: an instance that is open
    should not advertise a lock it does not have.
    """
    if not access_control_enabled():
        raise HTTPException(status_code=404, detail="This instance is open.")

    enforce(ACCESS_LIMITER, request)

    if not token_matches(body.token.strip()):
        COUNTERS.bump("access.rejected")
        LOG.warning("rejected access token from %s", client_key(request))
        raise HTTPException(status_code=401, detail="That token is not valid.")

    COUNTERS.bump("access.granted")
    response = json_response({"ok": True})
    response.set_cookie(
        COOKIE_NAME, body.token.strip(),
        max_age=COOKIE_MAX_AGE,
        httponly=True,       # the token is never readable from JavaScript
        samesite="lax",
        # Only over TLS in production. Left off locally because 127.0.0.1 is
        # plain HTTP and the cookie would simply never be stored.
        secure=os.environ.get("PUBLIC_URL", "").startswith("https://"),
    )
    return response


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


async def attempt_repair(client: AsyncOpenAI, model: str, messages: List[dict],
                         raw: str, deck: dict, max_tokens: Optional[int]) -> dict:
    """One repair pass, quoting the exact rules broken.

    Worth a single extra call: an unsound chain is the one thing this app
    cannot afford to render as though it passed. Returns whichever deck is
    less broken — never raises, because a failed repair still leaves a deck
    that is correctly flagged unverified.
    """
    hard, _ = validate_chain(deck["cards"])
    COUNTERS.bump("deck.repair.attempted")
    try:
        repaired, _ = await request_deck(client, model, messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": repair_instruction(hard)},
        ], max_tokens)
    except Exception as exc:
        COUNTERS.bump("deck.repair.errored")
        LOG.info("repair call failed on %s: %s", model, exc)
        return deck  # keep the original; it is already flagged unverified

    if repaired["verified"]:
        COUNTERS.bump("deck.repair.succeeded")
        LOG.info("repair fixed the chain on %s", model)
        return repaired

    if len(repaired["issues"]) < len(deck["issues"]):
        COUNTERS.bump("deck.repair.improved")
        return repaired

    COUNTERS.bump("deck.repair.failed")
    return deck


def _serve(deck: dict, req: ChatRequest, provider: dict) -> dict:
    """Record what is about to go out, then hand it over."""
    state = "verified" if deck["verified"] else "unverified"
    COUNTERS.bump(f"deck.served.{state}")
    COUNTERS.bump(f"provider.{provider['name']}.served")
    LOG.info("deck served %s by %s/%s", state, provider["name"], provider["model"])
    return finalize(deck, req)


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    require_access(request)
    enforce(DECK_LIMITER, request)
    providers = get_providers()

    if not providers:
        return json_response(make_error_deck(req.message, NO_PROVIDER_MESSAGE))

    messages = build_messages(req)

    last_error = ""
    best = None            # the least-broken deck seen, if none verify
    best_provider = None

    for provider in PROVIDER_POOL.order(providers):
        client = get_client(provider["api_key"], provider["endpoint"])
        model = provider["model"]
        try:
            deck, raw = await request_deck(client, model, messages,
                                           provider["max_tokens"])
            if deck["verified"]:
                return json_response(_serve(deck, req, provider))

            deck = await attempt_repair(client, model, messages, raw, deck,
                                        provider["max_tokens"])
            if deck["verified"]:
                return json_response(_serve(deck, req, provider))

            # Still unsound: keep it as a floor and let the next provider try.
            if best is None or len(deck["issues"]) < len(best["issues"]):
                best, best_provider = deck, provider
            last_error = f"{provider['name']}/{model}: chain not verified"
            COUNTERS.bump(f"provider.{provider['name']}.unverified")
            LOG.warning("%s — trying the next provider", last_error)
            continue
        except EmptyCompletion as exc:
            last_error = str(exc)
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = f"{provider['name']}/{model} returned unusable JSON: {exc}"
        except Exception as exc:
            last_error = f"{provider['name']}/{model}: {exc}"
            if should_cool_down(str(exc)):
                PROVIDER_POOL.penalise(provider, cooldown_for(str(exc)))

        COUNTERS.bump(f"provider.{provider['name']}.error")
        LOG.warning("%s", last_error)

    if best is not None:
        return json_response(_serve(best, req, best_provider))

    return json_response(make_error_deck(
        req.message, public_error(f"Deck generation failed. {last_error}")))


# ══════════════════════════════════════════════════════════════════════════
# EXPLORE
# ══════════════════════════════════════════════════════════════════════════


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
                COUNTERS.bump("explore.generated", len(questions))
                return questions
        except Exception as exc:
            COUNTERS.bump(f"provider.{provider['name']}.error")
            LOG.warning("explore: %s: %s", provider["name"], exc)
            if should_cool_down(str(exc)):
                PROVIDER_POOL.penalise(provider, cooldown_for(str(exc)))

    return []


@app.get("/api/explore/sectors")
async def explore_sectors():
    return json_response({"sectors": SECTORS})


# Open on purpose: a library deck spends no credits, so gating it would only
# blank the shelf for readers who have not entered the token yet.
@app.get("/api/library")
async def library_index():
    return json_response({"decks": LIBRARY.index()})


@app.get("/api/library/{slug}")
async def library_deck(slug: str):
    deck = LIBRARY.deck(slug)
    if deck is None:
        raise HTTPException(status_code=404, detail=f"No library deck: {slug}")
    return json_response(deck)


@app.get("/api/explore/{slug}")
async def explore_sector(slug: str, request: Request):
    # Gated too: a top-up is a model call, so an open Explore would leave the
    # cheapest way to spend someone else's credits wide open.
    require_access(request)

    sector = sector_by_slug(slug)
    if sector is None:
        raise HTTPException(status_code=404, detail=f"Unknown sector: {slug}")

    pool = EXPLORE_POOLS.get(slug)
    if pool is None:
        pool = EXPLORE_POOLS[slug] = ExplorePool(slug=slug, store=STORE)
    if pool.needs_refill(EXPLORE_LOW_WATER):
        enforce(EXPLORE_LIMITER, request)
        COUNTERS.bump("explore.topup")
        try:
            pool.add(await generate_questions(sector, pool.avoid_list(), EXPLORE_BATCH))
        except Exception as exc:
            # A sector that cannot generate shows nothing and stays browsable.
            # The rest of the page is unaffected.
            COUNTERS.bump("explore.topup_failed")
            LOG.warning("explore top-up failed for %s: %s", slug, exc)

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
    require_access(request)
    enforce(DECK_LIMITER, request)
    providers = get_providers()

    async def emit():
        if not providers:
            yield _ndjson({"type": "done",
                           "deck": make_error_deck(req.message, NO_PROVIDER_MESSAGE)})
            return

        messages = build_messages(req)
        last_error = ""
        best = None            # the least-broken deck seen, if none verify
        best_provider = None

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
                    deck = await attempt_repair(client, model, messages, raw, deck,
                                                provider["max_tokens"])

                # Still unsound after its own repair: this model cannot hold the
                # contract for this question. Keep it only as a floor and ask the
                # next provider, which is what the chain is for. Returning here
                # was why a weak model's shallow deck reached the reader while
                # better providers sat unused.
                if not deck["verified"]:
                    if best is None or len(deck["issues"]) < len(best["issues"]):
                        best, best_provider = deck, provider
                    last_error = f"{provider['name']}/{model}: chain not verified"
                    COUNTERS.bump(f"provider.{provider['name']}.unverified")
                    LOG.warning("%s — trying the next provider", last_error)
                    continue

                yield _ndjson({"type": "done", "deck": _serve(deck, req, provider)})
                return

            except EmptyCompletion as exc:
                last_error = str(exc)
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = f"{provider['name']}/{model} returned unusable JSON: {exc}"
            except Exception as exc:
                last_error = f"{provider['name']}/{model}: {exc}"
                if should_cool_down(str(exc)):
                    PROVIDER_POOL.penalise(provider, cooldown_for(str(exc)))

            COUNTERS.bump(f"provider.{provider['name']}.error")
            LOG.warning("%s", last_error)

        # Every provider tried. A flagged deck still beats no answer.
        if best is not None:
            yield _ndjson({"type": "done", "deck": _serve(best, req, best_provider)})
            return

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
        # Whether a stranger can spend your credits. Never the token itself.
        "access_control": "token" if access_control_enabled() else "open",
        # Whether the counters and cooldowns below survive a restart.
        "persistence": "sqlite" if STORE is not None else "memory",
        # The chain, without the keys. Which providers are configured and in
        # what order is the thing you actually need when a deploy misbehaves.
        "providers": [{"name": p["name"], "model": p["model"]} for p in providers],
        # Which of them are currently sidelined, and what the process has done
        # since it started. Both reset on restart — they are for reading off a
        # running deploy, not for scraping over time.
        "cooling": PROVIDER_POOL.cooling_now(providers),
        "counters": COUNTERS.snapshot(),
    }


@app.get("/api/sample-deck")
async def sample_deck():
    return normalize_deck(SAMPLE_DECK)


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 8000))
    reload = os.environ.get("RELOAD", "1") == "1"
    uvicorn.run("main:app", host=host, port=port, reload=reload)
