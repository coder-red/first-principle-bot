# First Principle Bot

![Python version](https://img.shields.io/badge/Python%20version-3.10%2B-lightgrey)
![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=flat&logo=fastapi)
![OpenAI SDK](https://img.shields.io/badge/OpenAI%20SDK-412991?style=flat&logo=openai&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-2EAD33?style=flat&logo=playwright&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=flat&logo=sqlite&logoColor=white)

A chatbot that reasons from first principles. Ask a question and it breaks the question down level by level until it reaches basic truths, then rebuilds the answer from those alone. Every claim carries a tag saying how well it is known, analogies are banned, and a validator checks the chain against fixed rules before anything renders.

## Live Demo

- App: _not public yet, see [Deployment](#deployment)_

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

One model call returns a **deck**: cards that step down the question until they hit bedrock, then climb back up and rebuild the explanation from that bedrock. Cards stream in as they finish, and the server checks the chain against a fixed contract before rendering. Providers form an ordered chain of free tiers, so one running out of credits just hands the work to the next.

```
question → prompt → provider chain → JSON deck → validate ──┬─ pass → render
                                                            └─ fail → repair call → render, or flag unverified
```

Every claim carries a tag:

| Tag | Glyph | Meaning |
|---|---|---|
| `ATOMIC` | ◆ | Basic truth: physical law, logical axiom, definition |
| `VERIFIED` | ✓ | Confirmed, but could be otherwise |
| `CONVENTION` | ≈ | Widely accepted, not proven |
| `ASSUMPTION` | ○ | Taken for granted |
| `UNKNOWN` | ? | Not known, so reasoning stops here |

### Design Decisions

| Decision | Rationale |
|---|---|
| **One call, one JSON deck** | The cards, prose view, quiz and follow-ups all come from the same JSON. No second prompt. |
| **Chain validation** | A deck must have exactly one bedrock at the bottom, strictly increasing levels, and rebuild steps standing on `ATOMIC`/`VERIFIED` claims only. A broken rule buys one repair call; fail again and the deck renders under an unverified banner. |
| **Checked form is not truth** | The badge says the deck follows its own rules, nothing more. |
| **Ordered provider chain** | Free tiers cap per account per day, so rotating spends the scarce allowance first. The chain tries providers in written order instead. |
| **Cooldown vs retry** | Quota errors (429/413/402) cool a provider down. Malformed JSON is retried on the next provider. |
| **Streaming by card** | A deck takes 25–35 seconds, so cards appear as each JSON object closes rather than after a long spinner. |
| **Shared token, not accounts** | `APP_ACCESS_TOKEN` gates the credit-spending endpoints through an `HttpOnly` cookie. |
| **Reviewed deck library** | Decks in `library/decks/` were generated once, validated, and read by me before committing. They serve instantly and cost nothing. Live decks are labeled unreviewed. |
| **Spaced review** | Quiz answers feed a review schedule in localStorage (`1/3/7/16/35` days). Nothing leaves the browser. |
| **No `innerHTML`** | The UI builds everything with `textContent`, so model output cannot inject markup. |

### Request Lifecycle

1. `POST /api/chat/stream` receives `{ message, context?, focus? }`: a new question, a drill-down, or a question about a card
2. Access check (`APP_ACCESS_TOKEN`) if set, then the per-IP rate limiter
3. Prompt assembled; provider chain walked in order, skipping cooled-down entries
4. Cards stream out as each JSON object closes
5. `normalize_deck()` fixes shape, `validate_chain()` applies the contract, one repair call on violation
6. Full validated deck arrives last, rendered as cards / prose / quiz, thread saved to `localStorage`

## Tech Stack

| Layer | Technology |
|---|---|
| **Language** | Python 3.10+ (tested on 3.10 and 3.13) |
| **API Framework** | FastAPI + Uvicorn |
| **LLM Client** | OpenAI SDK against any OpenAI-compatible endpoint |
| **Providers** | Groq, Gemini, Cerebras, NVIDIA, Mistral, Together, OpenRouter, OpenAI |
| **Persistence** | stdlib `sqlite3` (optional, `STATE_DB`) |
| **Frontend** | Vanilla JS + CSS, light/dark themes, no bundler |
| **Testing** | pytest + Playwright (eight browser suites) |
| **CI** | GitHub Actions, both suites on every push |
| **Portable form** | The method packaged as a Claude skill (`skill/first-principles/`) |

## Deployment

| Service | Platform | Config | Notes |
|---|---|---|---|
| App | Oracle Cloud Always Free (Ampere A1) | [`deploy/oci/`](deploy/oci/) — systemd unit, nginx, update script | Never sleeps, real disk, `STATE_DB` persists across restarts |

A Render blueprint ([`render.yaml`](render.yaml)) is included too. Production runs a single instance with `APP_ACCESS_TOKEN`, `HOST=0.0.0.0`, `RELOAD=0`, since limits and pools live in process memory. `tools/check_providers.py` probes the chain before a deploy.

## Security & Observability

- Provider keys stay server-side, never sent to the browser.
- `APP_ACCESS_TOKEN` gates `/api/chat` and `/api/explore`; `/api/health` stays open for platform health checks.
- Three per-IP rate limiters (decks, explore top-ups, token guesses) and constant-time token compare (`hmac.compare_digest`).
- No `innerHTML`, so no markup injection surface.
- `/api/health` reports the chain (no keys), cooldowns and counters. `DEBUG_ERRORS=1` forwards provider errors, off on public deploys.

## Quick Start

**Prerequisites:** Python 3.10+, at least one provider API key.

```bash
git clone https://github.com/coder-red/first-principle-bot.git
cd first-principle-bot
pip install -r requirements.txt
cp .env.example .env   # add a provider key
python main.py
```

Open http://127.0.0.1:8000.

**Tests:**
```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

**Browser suites:**
```bash
python -m playwright install chromium
python main.py                     # one shell
python tests/browser/run_all.py    # another
```

## Configuration

All optional except a provider key. `.env.example` explains each default.

| Variable | Default | Notes |
|---|---|---|
| `PROVIDERS` | — | Provider chain, tried in order. Each entry needs `<NAME>_API_KEY` and `<NAME>_MODEL`. |
| `<NAME>_MODELS` | — | One chain entry per model, sharing the key. |
| `<NAME>_MAX_TOKENS` | `DECK_MAX_TOKENS` | Per-provider ceiling. |
| `OPENROUTER_API_KEY` | — | Fallback when `PROVIDERS` is unset. `OPENAI_API_KEY` accepted too. |
| `MODEL` / `FALLBACK_MODELS` | `inclusionai/ling-2.6-flash` | Used only when `PROVIDERS` is unset. |
| `DECK_MAX_TOKENS` | `12000` | Reasoning models bill hidden thinking against this. |
| `DECK_TIMEOUT_SECONDS` | `90` | Ceiling on one model call. |
| `APP_ACCESS_TOKEN` | — | Gates the credit-spending endpoints. |
| `STATE_DB` | — | SQLite path, persists cooldowns, limits and pools. |
| `DECK_RATE_LIMIT` / `_WINDOW` | `8` / `300` | Per-IP decks. |
| `EXPLORE_RATE_LIMIT` / `_WINDOW` | `20` / `300` | Per-IP sector top-ups. |
| `ACCESS_RATE_LIMIT` / `_WINDOW` | `10` / `600` | Per-IP token guesses. |
| `PUBLIC_URL` | — | Marks the access cookie `Secure` on https. |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Local-only by default. |
| `RELOAD` | `1` | `0` outside development. |
| `LOG_LEVEL` / `DEBUG_ERRORS` | `INFO` / — | |

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Serves the UI |
| `/api/health` | GET | Chain, cooldowns, counters |
| `/api/access` | POST | Exchange `APP_ACCESS_TOKEN` for a cookie |
| `/api/chat` | POST | Question → validated deck (JSON) |
| `/api/chat/stream` | POST | Same, streamed card-by-card |
| `/api/explore/sectors` | GET | List explore sectors |
| `/api/explore/{slug}` | GET | Top up a sector's question pool |
| `/api/sample-deck` | GET | Static deck, no model call |
| `/api/library` | GET | Index of reviewed decks |
| `/api/library/{slug}` | GET | One reviewed deck from disk |

Drill-downs, "ask about this card" and follow-ups all post to the same chat endpoint with different `context`/`focus`.

## Testing

- **Python suite** (`tests/`): parsing, normalization, chain validation, the repair pass, rate limiting, access control, persistence and the deck library. Makes no model calls.
- **Browser suites** (`tests/browser/`): eight Playwright runs against the real UI, credits stubbed. These caught bugs the Python suite could not see.
- **CI:** both suites on every push ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

## Limitations & What Can Be Improved

- Validation checks form, not truth. A deck can follow every rule and still stop at a weak "bedrock". Disagreeing with a tag is often the useful part.
- Single instance by design. Scaling out needs shared state for limits and pools.
- `APP_ACCESS_TOKEN` only answers who may spend credits. No per-user quotas.
- Free tiers mean 25–35 seconds per deck and daily caps per account.
- Without a proxy that overwrites `X-Forwarded-For`, per-IP limits can be bypassed by varying the header.
- Streamed early cards get replaced once the final validated deck arrives.

## Repository Structure

<details>
  <summary><strong>Click to expand</strong></summary>

```text
.
├── main.py                 # FastAPI app: routes
├── render.yaml             # Render blueprint
├── requirements.txt        # Runtime deps
├── requirements-dev.txt    # + pytest, playwright
├── fpb/
│   ├── config.py           # Env config, provider chain
│   ├── providers.py        # Cooldowns, pool ordering, HTTP clients
│   ├── deck.py             # Normalization, validation, streaming reader
│   ├── prompts.py          # Deck and question prompts
│   ├── schemas.py          # Request bodies
│   ├── explore.py          # Sectors, question pool
│   ├── limits.py           # Per-IP rate limiting
│   ├── library.py          # Reviewed decks: load, index, publish gate
│   ├── auth.py             # Shared-token access control
│   ├── store.py            # SQLite persistence
│   └── telemetry.py        # Logging, counters
├── static/                 # index.html, script.js, style.css
├── deploy/oci/             # OCI runbook, systemd unit, nginx config
├── library/
│   ├── topics.txt          # Build list for the library
│   └── decks/              # Reviewed decks, committing = publishing
├── skill/first-principles/ # The method as a portable skill + HTML renderer
├── tools/                  # check_providers.py, build_library.py
├── tests/                  # pytest suites + browser/ Playwright suites
└── CHANGELOG.md
```

</details>
