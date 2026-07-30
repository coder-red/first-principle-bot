# First Principle Bot

Ask it anything. It decomposes the question to what is irreducibly true, then
rebuilds the answer from there — Aristotle's "first basis from which a thing is
known", run as an explicit protocol rather than a personality prompt.

Analogies are banned. Every claim carries an epistemic tag, and the model has to
show the chain that got it there.

## How an answer works

One model call returns a **deck**: a rendering of a single decomposition chain.
You descend level by level until you hit bedrock, then climb back up rebuilding
the explanation from that bedrock alone.

```
question  ->  descent L1  ->  descent L2  ->  descent L3  ->  BEDROCK  ->  rebuild  ->  insight
```

The progress rail sits each card at its own depth, so the row of dots traces the
descent and the climb back out. Bedrock is a diamond.

Each card is tagged with how well its claim is known:

| Tag | Glyph | Meaning |
| --- | --- | --- |
| `ATOMIC` | ◆ | Irreducible — physical law, logical axiom, definitional truth |
| `VERIFIED` | ✓ | Empirically confirmed, but could in principle be otherwise |
| `CONVENTION` | ≈ | Widely accepted, not proven — discarded during rebuild |
| `ASSUMPTION` | ○ | Taken for granted — discarded during rebuild |
| `UNKNOWN` | ? | Not known; reasoning stops rather than fabricating |

### Two views, one call

**Cards** is the default — swipe or arrow through the chain.
**Full reasoning** renders the *same JSON* as a continuous argument.

There is no second prompt and no Markdown: the prose view is built from the deck
structure, so it gets real typography instead of a wall of grey text.

### The chain is checked, not just rendered

A decomposition that breaks its own rules is worse than none at all, because the
form signals rigour the content does not have. Every deck is validated against
the contract before it renders:

- exactly one bedrock, sitting **below** the deepest descent step
- descent levels strictly increasing from 1
- bedrock tagged `ATOMIC`, or `UNKNOWN` rather than fabricating a floor
- **no descent card tagged `ATOMIC`** — if a step were irreducible, that step is
  the bedrock. This is the most common model failure.
- rebuild steps standing on `ATOMIC`/`VERIFIED` material
- each card's chain extending the previous one

A violation triggers one repair call quoting the exact rule broken. If the deck
still fails, it renders with a **Chain not verified** banner listing what broke,
rather than passing itself off as sound.

### Following the thread

Three ways to keep going, all of which post to the same endpoint:

- **Drill into a rung** — every link in the chain is clickable. "Go deeper" takes
  that claim as the new starting point and decomposes what *it* rests on.
- **Ask about this card** — your question carries the card as context instead of
  starting a fresh turn that has lost the thread.
- **Follow-ups** — each deck ends with two or three genuinely curious questions
  it opened up, generated in the same call at no extra cost.

## Setup

Requires Python 3.10+.

```bash
pip install -r requirements.txt
cp .env.example .env      # then add your OpenRouter key
python main.py
```

Open http://127.0.0.1:8000. Get a key at
[openrouter.ai/keys](https://openrouter.ai/keys). `OPENAI_API_KEY` also works if
you point `API_ENDPOINT` at OpenAI.

## Choosing a model

**Pin a specific model. Do not use `openrouter/auto` or `openrouter/free`** —
they are routers that pick a different model per request. One run of
`openrouter/free` was routed to `nvidia/nemotron-3.5-content-safety`, a
classifier, which returned 17 characters and an empty answer.

Benchmarked against this app's real workload:

| Model | Deck | Notes |
| --- | --- | --- |
| `inclusionai/ling-2.6-flash` | depth 4, bedrock ✓ | Default. Longest reasoning, $0.03/Mtok |
| `meta-llama/llama-3.3-70b-instruct` | depth 4, bedrock ✓ | First fallback |
| `mistralai/mistral-small-24b-instruct-2501` | depth 3, bedrock ✓ | Second fallback |

The model must support `response_format: json_object`. `/api/health` reports the
active model and flags routers.

## Configuration

All optional except the key. See `.env.example`.

| Variable | Default | Notes |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | — | Required. `OPENAI_API_KEY` also accepted. |
| `API_ENDPOINT` | `https://openrouter.ai/api/v1` | Any OpenAI-compatible endpoint. |
| `MODEL` | `inclusionai/ling-2.6-flash` | Must support structured output. |
| `FALLBACK_MODELS` | llama-3.3-70b, mistral-small-24b | Comma-separated, tried in order. |
| `DECK_MAX_TOKENS` | `4000` | Raise if decks truncate. |
| `HOST` | `127.0.0.1` | Local-only by default — see Security. |
| `PORT` | `8000` | |
| `RELOAD` | `1` | Set `0` for non-development runs. |

## Tests

```bash
python -m pytest tests/ -q
```

Covers JSON extraction from messy model output, deck normalization, request
validation, drill-down focus injection, and the model-failure hints.

## Security

`/api/chat` has **no authentication and no rate limiting**. Anyone who can reach
the port can spend your model credits. `HOST` defaults to `127.0.0.1` for that
reason — widen it only behind a proxy that adds auth.

The API key stays server-side and is never sent to the browser. The entire UI is
built by creating DOM nodes and setting `textContent` — model output never passes
through `innerHTML`, so there is no markup-injection surface.

## Layout

```
main.py              FastAPI app, the deck prompt, validation, repair, drill-down
static/index.html    Shell only
static/script.js     Transcript, deck modal, prose view, follow-ups
static/style.css     Design tokens, light/dark themes, card and prose styles
tests/test_deck.py   Deck contract and request-validation tests
skill/               The method, packaged as a portable Claude skill
```

No build step. No npm. Edit and reload.

## Using the method without the app

`skill/first-principles/` packages the protocol, the tag vocabulary and the
self-check rules as a Claude skill, plus a self-contained HTML template that
renders a deck as an artifact. It makes no network calls and needs no server.
See [skill/README.md](skill/README.md).
