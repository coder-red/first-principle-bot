"""Runtime configuration, read from the environment.

Nothing here is cached at import time except the two token/timeout ceilings —
the rest is read per call so editing .env under `uvicorn --reload` takes effect
without a code change.
"""

import os
from typing import List

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
