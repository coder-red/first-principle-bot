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
still fails, it renders with a **Structure not verified** banner listing what
broke, rather than passing itself off as sound.

**This checks form, not truth.** A deck can obey every rule above and still be
substantively wrong — stopping at a "bedrock" that is not actually irreducible,
for instance. That is why the badge reads *structure checked* and not *verified*:
it says the decomposition follows its own rules, not that the claims are correct.
Judge those yourself. The tags are the model's opinion, and disagreeing with one
is often the most interesting thing in the deck.

### Testing yourself

A third tab tests whether you can **do the method**, not whether you remember
the deck. It shows a claim and asks which epistemic tag it carries — telling a
physical necessity from a human convention is the one transferable skill in any
deck. Questions are derived from the deck JSON, so it costs no extra model call.

There is no score and there are no streaks; answering reveals the reasoning. The
summary says how many claims you *matched the deck on*, not how many you got
"right" — the tags are the model's judgement, not settled fact, and disagreeing
can be the correct call.

The quiz refuses to run on a deck that failed validation. Testing yourself
against tags that broke their own rules would actively teach the wrong thing.

### Following the thread

Three ways to keep going, all of which post to the same endpoint:

- **Drill into a rung** — every link in the chain is clickable. "Go deeper" takes
  that claim as the new starting point and decomposes what *it* rests on.
- **Ask about this card** — your question carries the card as context instead of
  starting a fresh turn that has lost the thread.
- **Follow-ups** — each deck ends with two or three genuinely curious questions
  it opened up, generated in the same call at no extra cost.

Drilling shows a breadcrumb of the claims you descended through, so a run of
drill-downs reads as one exploration rather than a pile of unrelated decks. The
thread is kept in `localStorage` and restored on reload; **New thread** clears
it.

### Watching it arrive

A deck takes 25–35 seconds, and a spinner for that long reads as a hang. The
model emits cards in narrative order, so `/api/chat/stream` sends each one as
its JSON object closes — the descent appears while the rebuild is still being
written. One call, same cost, same final deck.

The authoritative deck is sent last and may replace what you have already seen,
because a chain cannot be validated until it is finished. That is the honest
trade for showing anything early.

## Setup

Requires Python 3.10+.

```bash
pip install -r requirements.txt
cp .env.example .env      # then add a provider key
python main.py
```

Open http://127.0.0.1:8000.

## Providers

Every free tier is capped **per account per day**, not per visitor. One free
provider cannot carry a public app: the first handful of visitors spend the
allowance and everyone after gets a 429. So the app takes a chain and works
down it.

```bash
PROVIDERS=groq,gemini,openrouter
GROQ_API_KEY=...
GROQ_MODEL=...
GEMINI_API_KEY=...
GEMINI_MODELS=gemini-3.6-flash,gemini-3.5-flash   # plural: caps are per model
```

Each entry needs a key and a model; anything not fully configured is skipped,
so you can list a provider before you have signed up for it. Base URLs are
built in for groq, gemini, cerebras, nvidia, mistral, together, openrouter and
openai — anything else works with `<NAME>_ENDPOINT`.

**The chain is tried in the order you wrote it, and does not rotate.**
Spreading requests evenly would assume the allowances are comparable, and they
are not: Groq refills 8000 tokens every minute while a Gemini model gets 20
requests for the entire day. Rotating between those spends the scarce allowance
to relieve the renewable one. Put the renewable entry first.

An entry that returns 429/413/402 is put on a cooldown and skipped until it
expires, rather than costing a doomed call on every request. A malformed deck
does **not** cool an entry — that is a bad roll from a working provider, not a
capacity problem.

Leaving `PROVIDERS` unset keeps the older single-endpoint setup
(`OPENROUTER_API_KEY` + `MODEL` + `FALLBACK_MODELS`) working unchanged.

`tools/check_providers.py` probes what you have configured before you deploy it.

### Choosing a model

**Pin a specific model. Do not use `openrouter/auto` or `openrouter/free`** —
they are routers that pick a different model per request. One run of
`openrouter/free` was routed to `nvidia/nemotron-3.5-content-safety`, a
classifier, which returned 17 characters and an empty answer.

The one hard requirement is `response_format: json_object`. A model without it
returns prose and every deck fails. `/api/health` reports the active chain and
flags routers.

## Configuration

All optional except a provider key. See `.env.example` for the full set with
the reasoning behind each default.

| Variable | Default | Notes |
| --- | --- | --- |
| `PROVIDERS` | — | Comma-separated chain. Each needs `<NAME>_API_KEY` and `<NAME>_MODEL`. |
| `<NAME>_MODELS` | — | Plural. One chain entry per model, sharing the key. |
| `<NAME>_MAX_TOKENS` | `DECK_MAX_TOKENS` | Per-provider; they disagree wildly. |
| `OPENROUTER_API_KEY` | — | The single-endpoint fallback. `OPENAI_API_KEY` also accepted. |
| `MODEL` | `inclusionai/ling-2.6-flash` | Used only when `PROVIDERS` is unset. |
| `FALLBACK_MODELS` | llama-3.3-70b, mistral-small-24b | Same. |
| `DECK_MAX_TOKENS` | `12000` | Reasoning models bill hidden thinking against this. |
| `DECK_TIMEOUT_SECONDS` | `90` | Ceiling on one model call. Decks normally take 25–35s. |
| `APP_ACCESS_TOKEN` | — | Set it and the credit-spending endpoints require it. See Security. |
| `STATE_DB` | — | Set it and cooldowns, limits and pools survive a restart. |
| `DECK_RATE_LIMIT` / `_WINDOW` | `8` / `300` | Per-IP ceiling on decks. |
| `EXPLORE_RATE_LIMIT` / `_WINDOW` | `20` / `300` | Per-IP ceiling on sector top-ups. |
| `ACCESS_RATE_LIMIT` / `_WINDOW` | `10` / `600` | Per-IP ceiling on token guesses. |
| `LOG_LEVEL` | `INFO` | |
| `DEBUG_ERRORS` | — | Send raw provider errors to the browser. Not for public. |
| `HOST` | `127.0.0.1` | Local-only by default — see Security. |
| `PORT` | `8000` | |
| `RELOAD` | `1` | Set `0` for non-development runs. |

## Deploying

Whatever you deploy to, set `APP_ACCESS_TOKEN` — otherwise anyone who finds the
URL spends your provider credits, and the rate limits only bound how fast. Set
`HOST=0.0.0.0` and `RELOAD=0` on any host that is not a bare VM behind a proxy.

**Oracle Cloud Always Free (recommended)** — an Ampere A1 instance never sleeps
and has a real disk, which is what `STATE_DB` needs to be worth setting: warm
provider cooldowns are the difference between a restart being free and a
restart costing a round of calls to providers whose daily allowance is already
spent. Full runbook, systemd unit and nginx config in
[deploy/oci/](deploy/oci/README.md).

**Render** — [`render.yaml`](render.yaml) is a working blueprint. Simpler to
stand up, but the free plan sleeps and its filesystem is ephemeral, so
cooldowns and rate limits reset on every wake-up.

Behind any reverse proxy, make it **overwrite** `X-Forwarded-For` with the real
peer rather than appending to it — see Security below.

## Security

The API key stays server-side and is never sent to the browser. The entire UI is
built by creating DOM nodes and setting `textContent` — model output never passes
through `innerHTML`, so there is no markup-injection surface.

**By default the endpoints that spend credits are open.** `/api/chat`,
`/api/chat/stream` and `/api/explore/<sector>` are rate limited per IP, but a
rate limit only bounds how *fast* a stranger can spend your money. `HOST`
defaults to `127.0.0.1` for that reason.

Set `APP_ACCESS_TOKEN` and those three require it. It is a single shared
secret, not user accounts — it answers "should this person be able to spend my
credits at all", which is the actual exposure on a public URL. The browser
posts it once to `/api/access` and receives an `HttpOnly` cookie, so the token
is never held in JavaScript or in `localStorage`. `/api/health` deliberately
stays open, because a platform health check calls it and a 401 there would take
the service down.

Per-IP limits are keyed on the first entry of `X-Forwarded-For` when present,
so they work behind a proxy. That header is set by the client, so the keying is
only as trustworthy as whatever sits in front of the app: **reachable without a
proxy, or behind one that appends rather than overwrites the header, a caller
can vary it per request and every request lands in a fresh bucket.** That
applies to all three limiters, including the one guarding the access-token
exchange. Deploy behind a proxy that overwrites `X-Forwarded-For` with the real
peer — Render, Fly, Cloudflare and nginx with `real_ip_header` all do — and
treat `APP_ACCESS_TOKEN`, not the limiter, as the actual control.

The full threat model, and how to report a vulnerability, is in
[SECURITY.md](SECURITY.md).

## Persistence

Provider cooldowns, rate limits and the explore pools all live in process
memory. That is why `render.yaml` pins `numInstances: 1` — behind two instances
the limits double and the pools repeat questions.

Restarting also clears all three, and on a host that sleeps that costs money:
the first request after every wake-up pays for a call to a provider whose daily
allowance is already spent.

Set `STATE_DB` to a writable path and all three survive a restart, via stdlib
`sqlite3` — no server, a few kilobytes. It needs a real disk; on an ephemeral
filesystem the file goes with the container, which is no worse than leaving it
unset. `/api/health` reports which mode is active.

## Observability

`/api/health` reports the configured chain (without keys), which entries are
currently cooling, the access-control and persistence modes, and a set of
counters: decks served verified vs unverified, how often the repair pass fired
and whether it worked, per-provider errors and cooldowns, and rate-limit
refusals. The counters are process-local and reset on restart — they are for
reading off a running deploy, not for scraping.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

Covers JSON extraction from messy model output, deck normalization, chain
validation and the repair pass, request validation, drill-down focus injection,
question reframing, the model-failure hints, the provider chain and its
cooldowns, rate limiting, streaming, access control, and persistence.

```bash
python -m playwright install chromium

python main.py                     # one shell
python tests/browser/run_all.py    # another
```

Seven suites that drive the real UI in a real browser. They exist because
several bugs here were invisible to the Python tests and to reading the code — a
deck that locked up when a frame was throttled, chip colours leaking onto the
depth rail, peek cards rendering inside the card instead of below it, regenerate
corrupting the stored thread only when combined with persistence. See
[tests/browser/README.md](tests/browser/README.md). No API key needed; they stub
the endpoints that would make model calls.

CI runs both on every push — see [.github/workflows/ci.yml](.github/workflows/ci.yml).

## Layout

```
main.py              FastAPI app: routes, and the singletons they reach for
fpb/config.py        Environment config and the provider chain
fpb/providers.py     Cooldowns, pool ordering, pooled HTTP clients
fpb/deck.py          Normalization, chain validation, the streaming JSON reader
fpb/prompts.py       The deck and question system prompts
fpb/schemas.py       Request bodies and message building
fpb/explore.py       Sectors and the question pool
fpb/limits.py        Per-IP rate limiting
fpb/auth.py          Optional shared-token access control
fpb/store.py         Optional SQLite persistence
fpb/telemetry.py     Logging and counters
static/index.html    Shell only
static/script.js     Transcript, deck modal, prose view, follow-ups, access gate
static/style.css     Design tokens, light/dark themes, card and prose styles
tests/               Python suites, one per concern
tests/browser/       End-to-end suites driving the real UI
skill/               The method, packaged as a portable Claude skill
```

No build step. No npm. Edit and reload.

## Using the method without the app

`skill/first-principles/` packages the protocol, the tag vocabulary and the
self-check rules as a Claude skill, plus a self-contained HTML template that
renders a deck as an artifact. It makes no network calls and needs no server.
See [skill/README.md](skill/README.md).

## Contributing

Bug reports and pull requests are welcome. No API key is needed to work on most
of this — the Python suite never makes a model call, and the browser suites
stub the endpoints that would.

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to set up, what a good change
looks like, and which parts want care. Please report vulnerabilities privately
— see [SECURITY.md](SECURITY.md) — and note the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Licence

MIT — see [LICENSE](LICENSE).
