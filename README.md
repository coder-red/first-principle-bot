# First Principle Bot

![Python version](https://img.shields.io/badge/Python%20version-3.10%2B-lightgrey)
![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=flat&logo=fastapi)
![OpenAI SDK](https://img.shields.io/badge/OpenAI%20SDK-412991?style=flat&logo=openai&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-2EAD33?style=flat&logo=playwright&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=flat&logo=sqlite&logoColor=white)

You ask a question. The bot breaks it down step by step until it reaches something solidly true, then rebuilds the answer from there. It runs as an explicit protocol, not a personality prompt: analogies are banned, every claim carries a tag saying how well it is known, and the model has to show the chain that got it to the answer. A validator checks every deck against the rules before you see any of it.

## Live Demo

- App: _not public yet — see [Deployment](#deployment)_

## Author

- [@coder-red](https://www.github.com/coder-red)

## Table of Contents

- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Deployment](#deployment)
- [Security & Observability](#security--observability)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Testing](#testing)
- [Limitations & What Can Be Improved](#limitations--what-can-be-improved)
- [Repository Structure](#repository-structure)

---

## Architecture

One call to the model returns a **deck**: cards that walk down the question one level at a time until they reach bedrock, then climb back up and rebuild the explanation from that bedrock alone. Before anything renders, the server checks the chain against a fixed contract. Cards stream in as each one closes, so you read while it works. If the first provider is out of credits, the app moves down a chain of free-tier providers instead of dying.

```
question → prompt → provider chain → JSON deck → normalize → validate ──┬─ pass → render (structure checked)
                                                                        └─ fail → repair call → render, or flag as unverified
```

Every claim carries a tag:

| Tag | Glyph | Meaning |
|---|---|---|
| `ATOMIC` | ◆ | Irreducible — physical law, logical axiom, definitional truth |
| `VERIFIED` | ✓ | Confirmed, but could in principle be otherwise |
| `CONVENTION` | ≈ | Widely accepted, not proven — discarded during rebuild |
| `ASSUMPTION` | ○ | Taken for granted — discarded during rebuild |
| `UNKNOWN` | ? | Not known. Reasoning stops here rather than inventing something |

### Design Decisions

| Decision | Why |
|---|---|
| **One call, one JSON deck** | The cards, the prose view, the quiz and the follow-up questions all come from the same JSON. No second prompt, no extra cost. |
| **Chain validation is a hard contract** | A deck that breaks its own rules looks rigorous when it is not. So: exactly one bedrock below the deepest step, levels strictly increasing, no `ATOMIC` inside the descent, and rebuild steps standing on `ATOMIC`/`VERIFIED` only. Break a rule and the model gets one repair call quoting the exact rule. Fail again and the deck renders under a **Structure not verified** banner instead of passing as sound. |
| **Checked form is not checked truth** | The badge says the deck follows its own rules. It does not say the claims are correct. |
| **Ordered provider chain, no rotation** | Free tiers cap per account per day, not per visitor. Rotating spends Gemini's 20 requests a day to relieve Groq's tokens-per-minute, which refills every minute anyway. So the chain runs in the order written, renewable entry first. |
| **Out of quota cools a provider, a bad deck does not** | 429/413/402 is a capacity problem, so the entry is skipped until the cooldown expires. Malformed JSON is a bad roll from a working provider, so it retries down the chain. |
| **Streaming by card boundary** | A deck takes 25–35 seconds and a spinner that long reads as dead. Cards arrive as each JSON object closes. The full validated deck comes last and replaces any early cards. |
| **`json_object` mode or nothing** | Routers like `openrouter/free` once sent my question to a content safety classifier, which answered in 17 characters. Routers are rejected and flagged in `/api/health`. |
| **A shared token, not user accounts** | On a public URL the risk is a stranger spending your credits. `APP_ACCESS_TOKEN` is exchanged once for an `HttpOnly` cookie, so JavaScript never holds the token. |
| **A curated library, not just live calls** | Decks in `library/decks/` were generated once with a strong model, passed validation, and I read them before committing. They serve instantly and cost nothing per request, marked `Reviewed deck.` Live decks say `Generated live — structure checked, content unreviewed.` `tools/build_library.py` refuses to write a deck that fails validation. |
| **Spaced review, no accounts** | Quiz answers feed a review schedule in your browser (`1/3/7/16/35` days; miss a claim and it drops to the bottom). Progress stays per-browser on purpose. Nothing leaves your machine. |
| **SQLite for state** | Cooldowns, rate limits and pools live in memory by default. `STATE_DB` persists them across restarts, which matters on hosts that sleep. Stdlib sqlite3, a few kilobytes, no server. |
| **No build step, no `innerHTML`** | Vanilla JS builds the UI with `textContent`, so model output has no way to inject markup. Edit and reload. |

### Request Lifecycle

1. `POST /api/chat/stream` receives `{ message, context?, focus? }` — a fresh question, a drill-down into a card, or a question about a card
2. `require_access()` checks header / bearer / cookie when `APP_ACCESS_TOKEN` is set
3. Per-IP sliding-window rate limiter (`fpb/limits.py`) admits or refuses
4. Prompt assembled (`fpb/prompts.py`, `fpb/schemas.py`); drill-downs start from the focused claim
5. Provider chain walked in order, skipping entries on cooldown (`fpb/providers.py`)
6. Streaming JSON reader emits each card as its object closes (`fpb/deck.py: complete_cards`)
7. `normalize_deck()` fixes shape; `validate_chain()` applies the contract
8. Rule broken → one repair call quoting it; broken twice → flagged unverified
9. The authoritative deck goes last; the client replaces any early cards
10. The client renders cards / prose / quiz / follow-ups from the same JSON and saves the thread to `localStorage`

## Tech Stack

| Layer | Technology |
|---|---|
| **Language** | Python 3.10+ (tested on 3.10 and 3.13) |
| **API Framework** | FastAPI + Uvicorn (async, streaming) |
| **LLM Client** | OpenAI SDK against any OpenAI-compatible endpoint |
| **Providers** | Groq, Gemini, Cerebras, NVIDIA, Mistral, Together, OpenRouter, OpenAI — base URLs built in; anything else via `<NAME>_ENDPOINT` |
| **Persistence** | stdlib `sqlite3` (optional, `STATE_DB`) |
| **Frontend** | Vanilla JS + CSS, light/dark themes — no framework, no bundler |
| **Testing** | pytest (unit/integration) + Playwright (eight browser suites against the real UI) |
| **CI** | GitHub Actions — pytest matrix + headless Chromium suites on every push |
| **Portable form** | The method packaged as a Claude skill (`skill/first-principles/`) with a self-contained HTML deck renderer |

## Deployment

| Target | Platform | Config | Notes |
|---|---|---|---|
| **Recommended** | Oracle Cloud Always Free (Ampere A1) | [`deploy/oci/`](deploy/oci/) — systemd unit, nginx, update script | Never sleeps, real disk, so `STATE_DB` persists cooldowns across restarts |
| Alternative | Render (Docker-less blueprint) | [`render.yaml`](render.yaml) | Minimal setup; free plan sleeps and the filesystem is ephemeral, so cooldowns and limits reset on wake |

Production settings are `APP_ACCESS_TOKEN`, `HOST=0.0.0.0`, `RELOAD=0`, and `PUBLIC_URL=https://…` (the access cookie is only marked `Secure` on https). One instance only — limits and pools live in process memory. The bundled nginx config overwrites `X-Forwarded-For` with the real peer instead of appending, and that first entry is what the per-IP limits key on.

`tools/check_providers.py` probes the configured chain before a deploy.

## Security & Observability

- **Provider keys stay server-side.** Never sent to the browser.
- **Access control:** `APP_ACCESS_TOKEN` gates `/api/chat`, `/api/chat/stream` and `/api/explore/<sector>`. `/api/health` stays open so platform health checks don't take the service down. `/api/access` has its own tighter rate limit so it can't be used as a guessing oracle.
- **Rate limiting:** three independent per-IP sliding windows (decks, explore top-ups, token guesses). A limiter bounds how fast a stranger spends your money; the token is the actual control.
- **Constant-time token compare** (`hmac.compare_digest`).
- **No markup injection:** the UI never touches `innerHTML`.
- **Health endpoint:** `/api/health` reports the configured chain (no keys), entries cooling down, access-control and persistence modes, and counters — decks verified vs unverified, repair-pass outcomes, per-provider errors and cooldowns, rate-limit refusals.
- **Logging:** structured via `fpb/telemetry.py`; `DEBUG_ERRORS=1` forwards raw provider errors to the browser. Never on a public deploy.

## Quick Start

**Prerequisites:** Python 3.10+, at least one provider API key.

```bash
git clone https://github.com/coder-red/first-principle-bot.git
cd first-principle-bot
pip install -r requirements.txt
cp .env.example .env   # add a provider key
```

**Run the app:**
```bash
python main.py
```
Open http://127.0.0.1:8000.

**Run tests:**
```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

**Run browser suites:**
```bash
python -m playwright install chromium
python main.py                     # one shell
python tests/browser/run_all.py    # another
```

## Configuration

All optional except a provider key. `.env.example` explains the reasoning behind each default.

```bash
PROVIDERS=groq,gemini,openrouter
GROQ_API_KEY=...
GROQ_MODEL=...
GEMINI_API_KEY=...
GEMINI_MODELS=gemini-3.6-flash,gemini-3.5-flash   # plural: caps are per model
```

| Variable | Default | Notes |
|---|---|---|
| `PROVIDERS` | — | Comma-separated chain, tried in order. Each needs `<NAME>_API_KEY` and `<NAME>_MODEL`; incomplete entries are skipped. |
| `<NAME>_MODELS` | — | Plural. One chain entry per model, sharing the key. |
| `<NAME>_MAX_TOKENS` | `DECK_MAX_TOKENS` | Per-provider ceiling. |
| `OPENROUTER_API_KEY` | — | Single-endpoint fallback when `PROVIDERS` is unset. `OPENAI_API_KEY` also accepted. |
| `MODEL` / `FALLBACK_MODELS` | `inclusionai/ling-2.6-flash` / llama-3.3-70b, mistral-small-24b | Used only when `PROVIDERS` is unset. |
| `DECK_MAX_TOKENS` | `12000` | Reasoning models bill hidden thinking against this. |
| `DECK_TIMEOUT_SECONDS` | `90` | Ceiling on one model call. |
| `APP_ACCESS_TOKEN` | — | Gate the credit-spending endpoints. |
| `STATE_DB` | — | SQLite path; persists cooldowns, limits and pools. |
| `DECK_RATE_LIMIT` / `_WINDOW` | `8` / `300` | Per-IP decks. |
| `EXPLORE_RATE_LIMIT` / `_WINDOW` | `20` / `300` | Per-IP sector top-ups. |
| `ACCESS_RATE_LIMIT` / `_WINDOW` | `10` / `600` | Per-IP token guesses. |
| `PUBLIC_URL` | — | `https://…` marks the access cookie `Secure`. |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Local-only by default. |
| `RELOAD` | `1` | Set `0` outside development. |
| `LOG_LEVEL` / `DEBUG_ERRORS` | `INFO` / — | |

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Serves the UI |
| `/api/health` | GET | Chain, cooldowns, modes, counters — always open |
| `/api/access` | POST | Exchange `APP_ACCESS_TOKEN` for an `HttpOnly` cookie |
| `/api/chat` | POST | One question → one validated deck (JSON) |
| `/api/chat/stream` | POST | Same, streamed card-by-card; authoritative deck last |
| `/api/explore/sectors` | GET | List the explore sectors |
| `/api/explore/{slug}` | GET | Top up a sector's question pool |
| `/api/sample-deck` | GET | A static deck for the UI without a model call |
| `/api/library` | GET | Index of reviewed library decks — always open |
| `/api/library/{slug}` | GET | One reviewed deck, served from disk — always open |

Drill-downs ("Go deeper"), "Ask about this card" and follow-ups all post to the same chat endpoint with different `context`/`focus`.

## Testing

- **Python suite** (`tests/`): JSON extraction from messy model output, deck normalization, chain validation and the repair pass, request validation, drill-down focus injection, question reframing, model-failure hints, the provider chain and cooldowns, rate limiting, streaming, access control, persistence, and the deck library (loading, the publish gate, the open endpoints). Never makes a model call, so it runs free.
- **Browser suites** (`tests/browser/`): eight Playwright suites driving the real UI. Several bugs were invisible to the Python tests — a deck that locked up under frame throttling, chip colours leaking onto the depth rail, regenerate corrupting the stored thread only when combined with persistence. Endpoints that would spend credits are stubbed, so no API key is needed. See [tests/browser/README.md](tests/browser/README.md).
- **CI:** [`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs both on every push (pytest on 3.10 and 3.13, Chromium suites on 3.13).

## Limitations & What Can Be Improved

- **Form is checked, truth is not.** A deck can obey every structural rule and still stop at a "bedrock" that isn't really irreducible. The tags are the model's judgement, and disagreeing with one is often the most interesting part of a deck.
- **Single instance by design.** Cooldowns, rate limits and pools live in process memory (or one SQLite file). Scaling out needs shared state first.
- **Shared secret, not accounts.** `APP_ACCESS_TOKEN` answers "may this person spend my credits" and nothing finer. No per-user quotas.
- **Free tiers are the bottleneck.** 25–35 seconds per deck, and daily caps are per account. The chain softens this; it doesn't remove the ceiling.
- **`X-Forwarded-For` is client-controlled.** Per-IP limits are only as trustworthy as the proxy in front. Without one that overwrites the header, a caller varying the header lands in a fresh bucket every request.
- **Early cards may be replaced.** Streaming shows the descent before the rebuild is written. The final validated deck wins.

## Repository Structure

<details>
  <summary><strong>Click to expand</strong></summary>

```text
.
├── main.py                 # FastAPI app: routes and the singletons they reach for
├── render.yaml             # Render blueprint
├── requirements.txt        # Runtime deps
├── requirements-dev.txt    # + pytest, playwright
├── fpb/
│   ├── config.py           # Environment config and the provider chain
│   ├── providers.py        # Cooldowns, pool ordering, pooled HTTP clients
│   ├── deck.py             # Normalization, chain validation, streaming JSON reader
│   ├── prompts.py          # Deck and question system prompts
│   ├── schemas.py          # Request bodies and message building
│   ├── explore.py          # Sectors and the question pool
│   ├── limits.py           # Per-IP rate limiting
│   ├── library.py          # Reviewed deck library: load, index, publish gate
│   ├── auth.py             # Optional shared-token access control
│   ├── store.py            # Optional SQLite persistence
│   └── telemetry.py        # Logging and counters
├── static/
│   ├── index.html          # Shell only
│   ├── script.js           # Transcript, deck modal, prose view, quiz, access gate
│   └── style.css           # Design tokens, light/dark themes
├── deploy/oci/             # OCI runbook, systemd unit, nginx config, update script
├── library/
│   ├── topics.txt          # sector: question — the library's build list
│   └── decks/              # Reviewed decks, one JSON each; committing = publishing
├── skill/first-principles/ # The method as a portable Claude skill + HTML renderer
├── tools/check_providers.py# Probe the configured chain
├── tools/build_library.py  # Generate library decks; refuses the unsound
├── tests/                  # pytest suites, one per concern
│   └── browser/            # Playwright end-to-end suites
└── CHANGELOG.md
```

</details>
