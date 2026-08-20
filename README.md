<!-- Banner: drop an image at assets/banner.png and uncomment.
<p align="center">
  <img src="assets/banner.png" alt="Project Banner" width="100%">
</p>
-->

# First Principle Bot

![Python version](https://img.shields.io/badge/Python%20version-3.10%2B-lightgrey)
![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=flat&logo=fastapi)
![OpenAI SDK](https://img.shields.io/badge/OpenAI%20SDK-412991?style=flat&logo=openai&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-2EAD33?style=flat&logo=playwright&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=flat&logo=sqlite&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-blue)

Ask it anything. It decomposes the question to what is irreducibly true, then rebuilds the answer from there — first-principles reasoning run as an explicit, machine-checked protocol rather than a personality prompt. Analogies are banned, every claim carries an epistemic tag, and the model has to show the chain that got it there.

## Live Demo

<!-- Fill in once deployed. render.yaml targets https://first-principle-bot.onrender.com -->
- App: _not yet public — see [Deployment](#deployment)_

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
- [Limitations & Trade-offs](#limitations--trade-offs)
- [Repository Structure](#repository-structure)

---

## Architecture

One model call returns a **deck**: a single decomposition chain rendered as cards. The model descends level by level until it hits bedrock, then climbs back up rebuilding the explanation from that bedrock alone. The server validates the chain against a formal contract before it renders, streams cards as they close, and works down a chain of free-tier LLM providers so one exhausted allowance does not take the app down.

```
question → prompt → provider chain → JSON deck → normalize → validate ──┬─ pass → render (structure checked)
                                                                        └─ fail → repair call → render, or flag as unverified
```

Every card in the deck carries how well its claim is known:

| Tag | Glyph | Meaning |
|---|---|---|
| `ATOMIC` | ◆ | Irreducible — physical law, logical axiom, definitional truth |
| `VERIFIED` | ✓ | Empirically confirmed, but could in principle be otherwise |
| `CONVENTION` | ≈ | Widely accepted, not proven — discarded during rebuild |
| `ASSUMPTION` | ○ | Taken for granted — discarded during rebuild |
| `UNKNOWN` | ? | Not known; reasoning stops rather than fabricating |

### Design Decisions

| Decision | Rationale |
|---|---|
| **One call, one JSON deck** | Cards, the prose "Full reasoning" view, the self-test quiz and the follow-up questions are all derived from the same JSON. No second prompt, no Markdown, no extra cost. |
| **Chain validation is a hard contract** | A decomposition that breaks its own rules signals rigour it does not have. Exactly one bedrock below the deepest step, levels strictly increasing, no descent card tagged `ATOMIC`, rebuild steps standing only on `ATOMIC`/`VERIFIED` material. Violations trigger one repair call quoting the exact rule broken; a second failure renders with a **Structure not verified** banner rather than passing as sound. |
| **Structure checked ≠ verified** | Validation checks form, not truth. The badge wording is deliberate: it says the deck follows its own rules, not that the claims are correct. |
| **Ordered provider chain, no rotation** | Free tiers are capped per account per day, not per visitor. Rotating spends the scarce allowance (Gemini: 20 req/day) to relieve the renewable one (Groq: 8k tokens/min). The chain is tried in the order written; put the renewable entry first. |
| **429/413/402 → cooldown, malformed deck → no cooldown** | A quota error is a capacity problem and the entry is skipped until the cooldown expires. A bad deck is a bad roll from a working provider and is retried down the chain. |
| **Streaming by card boundary** | A deck takes 25–35s and a spinner that long reads as a hang. Cards are emitted as each JSON object closes; the authoritative deck is sent last because a chain cannot be validated until it is finished. |
| **`response_format: json_object` is the only hard model requirement** | Routers like `openrouter/auto` are rejected — one run of `openrouter/free` was routed to a content-safety classifier that returned 17 characters. `/api/health` flags routers. |
| **Shared token instead of user accounts** | The exposure on a public URL is "can a stranger spend my credits", not identity. `APP_ACCESS_TOKEN` is exchanged once for an `HttpOnly` cookie; the token is never held in JavaScript. |
| **SQLite via stdlib for state** | Cooldowns, rate limits and explore pools live in memory by default. `STATE_DB` persists them so a restart on a sleeping host does not cost a round of doomed provider calls. No server, a few kilobytes. |
| **No build step, no `innerHTML`** | Vanilla JS builds the UI with `textContent`, so model output has no markup-injection surface. Edit and reload. |

### Request Lifecycle

1. `POST /api/chat/stream` receives `{ message, context?, focus? }` — a fresh question, a drill-down on a card, or a question about a card
2. `require_access()` checks header / bearer / cookie when `APP_ACCESS_TOKEN` is set
3. Per-IP sliding-window rate limiter (`fpb/limits.py`) admits or refuses
4. Prompt assembled (`fpb/prompts.py`, `fpb/schemas.py`); drill-downs inject the focus claim as the new starting point
5. Provider chain walked in order, skipping entries on cooldown (`fpb/providers.py`)
6. Streaming JSON reader emits each card as its object closes (`fpb/deck.py: complete_cards`)
7. `normalize_deck()` guarantees shape; `validate_chain()` checks the contract
8. On violation → one repair call quoting the broken rule; on second failure → deck flagged unverified
9. Authoritative deck sent last; client replaces any early cards
10. Client renders cards / prose / quiz / follow-ups from the same JSON; thread persisted to `localStorage`

## Tech Stack

| Layer | Technology |
|---|---|
| **Language** | Python 3.10+ (tested on 3.10 and 3.13) |
| **API Framework** | FastAPI + Uvicorn (async, SSE-style streaming) |
| **LLM Client** | OpenAI SDK against any OpenAI-compatible endpoint |
| **Providers** | Groq, Gemini, Cerebras, NVIDIA, Mistral, Together, OpenRouter, OpenAI — built-in base URLs; anything else via `<NAME>_ENDPOINT` |
| **Persistence** | stdlib `sqlite3` (optional, `STATE_DB`) |
| **Frontend** | Vanilla JS + CSS design tokens, light/dark themes — no framework, no bundler |
| **Testing** | pytest (unit/integration) + Playwright (seven browser suites against the real UI) |
| **CI** | GitHub Actions — pytest matrix + headless Chromium suites on every push |
| **Portable form** | The method packaged as a Claude skill (`skill/first-principles/`) with a self-contained HTML deck renderer |

## Deployment

| Target | Platform | Config | Notes |
|---|---|---|---|
| **Recommended** | Oracle Cloud Always Free (Ampere A1) | [`deploy/oci/`](deploy/oci/README.md) — systemd unit, nginx, update script | Never sleeps, real disk, so `STATE_DB` is worth setting |
| Alternative | Render (Docker-less blueprint) | [`render.yaml`](render.yaml) | Simplest to stand up; free plan sleeps and filesystem is ephemeral, so cooldowns and limits reset on wake |

**Whatever you deploy to:** set `APP_ACCESS_TOKEN`, `HOST=0.0.0.0`, `RELOAD=0`, and `PUBLIC_URL=https://…` (the access cookie is only marked `Secure` when it is). Run a single instance — limits and pools are process-local. Behind a reverse proxy, make it **overwrite** `X-Forwarded-For` with the real peer rather than append; per-IP limits key on its first entry.

`tools/check_providers.py` probes the configured chain before you ship it.

## Security & Observability

- **Provider keys stay server-side.** Never sent to the browser.
- **Access control:** `APP_ACCESS_TOKEN` gates `/api/chat`, `/api/chat/stream` and `/api/explore/<sector>`. `/api/health` deliberately stays open so platform health checks do not 401 the service down. `/api/access` has its own, tighter rate limit so it cannot be used as a guessing oracle.
- **Rate limiting:** three independent per-IP sliding windows (decks, explore top-ups, token guesses). A limiter bounds *how fast* a stranger spends your money; the token is the actual control.
- **Constant-time token compare** (`hmac.compare_digest`).
- **No markup injection:** the UI never uses `innerHTML`.
- **Health endpoint:** `/api/health` reports the configured chain (no keys), entries currently cooling, access-control and persistence modes, and process-local counters — decks verified vs unverified, repair-pass fires and outcomes, per-provider errors and cooldowns, rate-limit refusals.
- **Logging:** structured via `fpb/telemetry.py`; `DEBUG_ERRORS=1` forwards raw provider errors to the browser (never on a public deploy).
- **Threat model & disclosure:** [SECURITY.md](SECURITY.md).

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

All optional except a provider key. `.env.example` carries the reasoning behind each default.

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

Drill-downs ("Go deeper"), "Ask about this card" and follow-ups all post to the same chat endpoint with different `context`/`focus`.

## Testing

- **Python suite** (`tests/`): JSON extraction from messy model output, deck normalization, chain validation and the repair pass, request validation, drill-down focus injection, question reframing, model-failure hints, the provider chain and cooldowns, rate limiting, streaming, access control, persistence. Never makes a model call.
- **Browser suites** (`tests/browser/`): seven Playwright suites driving the real UI. They exist because several bugs were invisible to the Python tests — a deck that locked up under frame throttling, chip colours leaking onto the depth rail, regenerate corrupting the stored thread only when combined with persistence. Endpoints that would spend credits are stubbed; no API key needed. See [tests/browser/README.md](tests/browser/README.md).
- **CI:** [`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs both on every push (pytest on 3.10 and 3.13, Chromium suites on 3.13).

## Limitations & Trade-offs

- **Form is checked, truth is not.** A deck can obey every structural rule and still stop at a "bedrock" that is not irreducible. The tags are the model's judgement; disagreeing with one is often the most interesting thing in the deck.
- **Single-instance by design.** Cooldowns, rate limits and pools are process-local (or one SQLite file). Horizontal scaling would need shared state.
- **Shared secret, not accounts.** `APP_ACCESS_TOKEN` answers "may this person spend my credits", nothing finer. No per-user quotas or RBAC.
- **Free-tier providers are the bottleneck.** Latency is 25–35s per deck and daily caps are per account. The chain mitigates; it does not remove the ceiling.
- **`X-Forwarded-For` is client-controlled.** Per-IP limits are only as trustworthy as the proxy in front. Without one that overwrites the header, every request can land in a fresh bucket.
- **Early cards may be replaced.** Streaming shows the descent before the rebuild is written; the final validated deck wins.

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
│   ├── auth.py             # Optional shared-token access control
│   ├── store.py            # Optional SQLite persistence
│   └── telemetry.py        # Logging and counters
├── static/
│   ├── index.html          # Shell only
│   ├── script.js           # Transcript, deck modal, prose view, quiz, access gate
│   └── style.css           # Design tokens, light/dark themes
├── deploy/oci/             # OCI runbook, systemd unit, nginx config, update script
├── skill/first-principles/ # The method as a portable Claude skill + HTML renderer
├── tools/check_providers.py# Probe the configured chain
├── tests/                  # pytest suites, one per concern
│   └── browser/            # Playwright end-to-end suites
├── CONTRIBUTING.md · SECURITY.md · CODE_OF_CONDUCT.md · CHANGELOG.md
└── LICENSE                 # MIT
```

</details>

## Contributing

Bug reports and pull requests are welcome; no API key is needed for most work. See [CONTRIBUTING.md](CONTRIBUTING.md). Report vulnerabilities privately per [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
