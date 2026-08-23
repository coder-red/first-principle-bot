# Changelog

Notable changes, newest first. Follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Dates are the day the work landed on `main`. This project has not cut a
numbered release yet, so everything below the top section is grouped by the day
it shipped rather than by version.

## [Unreleased]

### Added

- A curated deck library. `library/decks/` holds decks generated once with a
  strong model, validated against the chain contract, and read by a human
  before being committed — the repo is the CMS. Two new open endpoints
  (`/api/library`, `/api/library/{slug}`) serve them instantly from memory,
  and the explore panel shows each sector's reviewed decks above the
  generated question suggestions. `tools/build_library.py` builds the
  library from `library/topics.txt`, refuses to write any deck that fails
  validation, and never overwrites a slug a human already reviewed.
- Provenance on every deck: library decks say `Reviewed deck.`, live ones say
  `Generated live — structure checked, content unreviewed.` The validator
  only ever checked form, so whether a human has read the content is now
  stated out loud instead of implied.
- Spaced review. Quiz answers feed a localStorage ladder (1/3/7/16/35 days;
  a miss falls back to the bottom rung), and returning with claims due shows
  a "N claims due for review" banner that runs a review round across past
  decks. No accounts and nothing leaves the browser — progress is per-machine
  by design.

- Oracle Cloud Always Free deploy set in `deploy/oci/`: a hardened systemd unit,
  an nginx reverse-proxy config, a systemd-format env template, a redeploy
  script that rolls back on a failed health check, and a runbook. An Ampere A1
  instance is the first host where `STATE_DB` earns its keep — it never sleeps
  and has a real disk, so provider cooldowns stay warm instead of costing a
  round of exhausted-provider calls after every wake-up.
- The nginx config carries the security headers the app does not set, including
  a CSP tight enough to name every third party: the frontend has no inline
  script and no inline style, so only Google Fonts and one `data:` SVG need
  allowing.
- Open-source scaffolding: `CONTRIBUTING.md`, `SECURITY.md`,
  `CODE_OF_CONDUCT.md`, issue and pull-request templates, and a Dependabot
  config covering pip and the GitHub Actions the CI workflow pins.

### Changed

- `.gitignore` now ignores the whole `.env.*` family — re-admitting only
  `.env.example` — plus files that are credentials by shape (`*.pem`, `*.key`,
  `service-account*.json`). Only `.env` itself was covered before, so a
  `.env.production` would have been committed.

### Fixed

- The README overstated how much the rate limiter protects. Per-IP keying reads
  `X-Forwarded-For`, which the client sets: without a proxy that overwrites it,
  one caller varying the header per request lands in a fresh bucket every time
  and all three limiters — including the one in front of the access-token
  exchange — are bypassed. Documented rather than changed, because the header
  has to be trusted for the limiter to work behind a proxy at all; the shipped
  nginx config overwrites it with the real peer, which is what makes the per-IP
  limits mean anything.
- `render.yaml` never mentioned `APP_ACCESS_TOKEN`, so the blueprint deployed an
  anonymous instance that spent the configured provider keys for anyone who
  found the URL. Now present as `sync: false`, with the exposure spelled out.

### Removed

- The open-source scaffolding: `CONTRIBUTING.md`, `SECURITY.md`,
  `CODE_OF_CONDUCT.md` and the MIT `LICENSE`, along with the README sections
  that linked to them. The project is single-author again; the changelog
  stays as the record of what shipped.

### Added (earlier in this cycle)

- Optional shared-token access control. Set `APP_ACCESS_TOKEN` and `/api/chat`,
  `/api/chat/stream` and `/api/explore/<sector>` require it; leave it unset and
  nothing changes. The browser exchanges the token once at `/api/access` for an
  `HttpOnly` cookie, so it never sits in JavaScript or `localStorage`. The
  exchange has its own tighter rate limit, because a caller with no token never
  reaches any other limiter. `/api/health` stays open — a platform health check
  calls it, and a 401 there would take the service down.
- Optional SQLite persistence behind `STATE_DB`. Provider cooldowns, rate limits
  and the explore pools survive a restart. The cooldowns are the ones that cost
  money: on a host that sleeps, the first request after every wake-up was paying
  for a call to a provider whose daily allowance was already spent.
- Structured logging and counters, replacing four `print()` calls. `/api/health`
  now reports which providers are cooling, how often the repair pass fired and
  whether it worked, per-provider errors, and rate-limit refusals.
- `LICENSE` (MIT). The skill in `skill/` is meant to be lifted and reused, and
  without a licence nobody legally could.
- GitHub Actions CI running both suites on every push, on Python 3.10 and 3.13.
- `requirements-dev.txt`, pinning `playwright`. It had only ever been mentioned
  in prose, so the browser suites were not reproducible from the repo.
- A browser suite for the access gate, covering unlock, question replay,
  decline and Escape.

### Changed

- `main.py` split from 1398 lines into an `fpb/` package. The routes and the
  mutable singletons stay in `main.py`, because that is what the handlers and
  the tests resolve them through; everything reasonable without a server moved
  out. No behaviour change — all 194 existing tests passed untouched across the
  move.

### Fixed

- `README.md` claimed `/api/chat` had "no authentication and no rate limiting".
  Rate limiting shipped on 2026-08-02. The configuration table also still
  described the single-provider setup, months after the provider chain replaced
  it, and documented `DECK_MAX_TOKENS` as `4000` when the default is `12000`.
- `.env.example` said the pool "rotates which entry starts each request". It
  stopped rotating on 2026-08-03, deliberately — rotating spends a scarce daily
  allowance to relieve a renewable per-minute one.

## 2026-08-03

- Hand an unsound chain to the next provider instead of serving it. A weak
  model's shallow deck was reaching the reader while better providers sat
  unused.
- Prefer the renewable allowance instead of rotating. Groq refills 8000 tokens
  a minute; a Gemini model gets 20 requests for the whole day. Spreading
  requests evenly between those drains the wrong one.
- Pool providers so free tiers actually add up, with cooldowns for entries that
  return 429/413/402.
- Per-provider token budgets, and catch truncation properly. A reasoning model
  bills hidden thinking against `max_tokens` without reporting it, so decks were
  dying in the JSON parser and being reported as "unusable JSON".
- A provider probe (`tools/check_providers.py`), and NVIDIA's base URL.

## 2026-08-02

- Chain free providers so a free deploy actually holds up. A fallback has to
  change the base URL and the key, not just the model name.
- Rate limiting, bounded request bodies, and a Render blueprint.
- Stream the deck, explore by sector, and go pitch black.

## 2026-07-31

- Stop the browser suites committing screenshots.
- Keep the browser suites in the repo.
- Make a stalled request escapable, and stop the badge overclaiming.
- Only show the deck's question when it actually reframes the ask.

## 2026-07-30

- Regenerate now replaces a turn instead of accumulating one.
- Quiz the method, persist the thread, show the drill trail.
- Validate the chain, and package the method as a skill.
- Cards-only decks, chain-derived UI, and a pinned model.

## 2026-07-28

- Initial commit: a first-principles reasoning chatbot with text and flashcard
  modes, then several rounds on the flashcard UI, JSON reliability via
  `response_format`, and model fallbacks.
