# Contributing

Thanks for looking. This is a small project with a strong opinion about one
thing — that a decomposition which breaks its own rules is worse than none at
all — and most of the code exists to hold that line. Changes that make the
chain easier to check, or the failure modes more honest, are the most welcome
kind.

## Getting set up

```bash
pip install -r requirements-dev.txt
cp .env.example .env      # then add a provider key
python main.py
```

No build step and no npm. `static/` is served as-is; edit and reload.

You do **not** need an API key to work on most of this. The Python suite never
makes a model call, and the browser suites stub the endpoints that would.

## Running the tests

```bash
python -m pytest tests/ -q
```

```bash
python -m playwright install chromium

python main.py                     # one shell
python tests/browser/run_all.py    # another
```

Both run in CI on every push. A PR that fails either will not be merged, and
"it passes locally" is usually a sign the test depends on timing — say so in
the PR rather than retrying it.

The browser suites exist because several real bugs here were invisible to the
Python tests *and* to reading the code. If you change anything the reader
actually touches — the deck modal, the rail, the prose view, the quiz, the
access gate — add or extend a suite in `tests/browser/`.

## What a good change looks like

**Bug fixes want a failing test first.** Write the test that reproduces it,
watch it fail, then fix it. That the test fails before the fix is the only
evidence the test is testing anything.

**Comments explain why, not what.** The existing comments in this repo are
mostly load-bearing history — why `DECK_MAX_TOKENS` defaults so high, why the
pool does not rotate, why the limiter refuses to record a refused attempt.
Deleting one of those loses the reason someone will otherwise rediscover the
hard way. If you change the behaviour a comment describes, change the comment.

**Prefer stdlib.** `store.py` is stdlib `sqlite3` on purpose: adding redis for
a few kilobytes of state would mean a dependency and a server. New dependencies
need a reason in the PR description.

**Do not weaken the validator to make a model pass it.** If a provider keeps
producing decks that fail `validate_chain`, the answer is a better prompt, a
repair pass, or a different provider — not a looser contract. The badge says
*structure checked*, and it has to keep meaning that.

## Things that need care

- **`fpb/deck.py`** — chain validation and the streaming JSON reader. The
  reader parses partial JSON by hand; it is the trickiest code here and the
  best covered. Read `tests/test_deck.py` and `tests/test_stream.py` before
  changing it.
- **`fpb/limits.py` and `fpb/auth.py`** — the only things between a public
  deploy and someone else's model bill. Changes here should say in the PR what
  the new failure mode is.
- **Single-instance assumptions.** The explore pools, the limiters and the
  provider cooldowns all assume one process, which is why `render.yaml` pins
  `numInstances: 1`. Anything that makes the app multi-instance has to address
  all three together.
- **`skill/`** is a self-contained Claude skill with no network calls and no
  server. Keep it that way.

## Adding a provider

`KNOWN_PROVIDERS` in `fpb/config.py` is just a name-to-base-URL map, so a new
entry is one line — but the provider must support
`response_format: {"type": "json_object"}`. One without it returns prose and
every deck fails. Check with `python tools/check_providers.py` before opening
the PR, and mention in the description which model you verified against.

## Pull requests

- Branch off `main`.
- One concern per PR. A refactor bundled with a fix is hard to review and
  harder to revert.
- Commit messages in the imperative, prefixed the way the existing history
  does it — `fix:`, `feat:`, `refactor:`, `docs:`, `test:`, `ci:`, `chore:`.
- Add a line to `CHANGELOG.md` under Unreleased for anything a user would
  notice.
- Say what you tested and how. If you tested against a specific model, name it.

## Security

Please do not open a public issue for a vulnerability. See
[SECURITY.md](SECURITY.md).
