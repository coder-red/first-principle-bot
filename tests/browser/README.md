# Browser suites

These drive the real UI in a real browser. They exist because several bugs in
this project were invisible to unit tests and to reading the code, and only
showed up when something was rendered or clicked:

- The deck locked up permanently when a `requestAnimationFrame` was throttled,
  swallowing every keypress and swipe from then on.
- Chip background colours leaked onto the depth rail through an unscoped class,
  turning it into a barcode.
- The peek cards behind the deck were invisible, because a uniform `scale`
  shrank their height *inside* the card face instead of below it.
- Regenerate looked correct in isolation and corrupted the stored thread when
  combined with persistence.
- The ask bar kept its `hidden` class but stayed visible, because `.hidden` is
  not global in this stylesheet.

Every one of those passed the Python tests.

## Running

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium

python main.py                     # one shell
python tests/browser/run_all.py    # another
```

Point them elsewhere with `FP_BASE_URL`. Screenshots land in `_shots/`
(gitignored) — several bugs above were found by looking at those rather than by
an assertion, so they are worth a glance when something changes visually.

No API key is needed: every suite stubs `/api/chat/stream` and uses
`/api/sample-deck` as its fixture. The only unstubbed call a suite makes is
`/api/explore/sectors`, which is a static list and costs nothing.

## What each covers

| Suite | Covers |
| --- | --- |
| `test_deck_ui.py` | Deck walk, prose view, drill-down payloads, focus trap, dark/light/360px |
| `test_quiz_and_thread.py` | Quiz behaviour and refusal, drill trail, persistence across reload |
| `test_verification_states.py` | The verified badge vs the unverified banner |
| `test_regenerate.py` | Regenerate replacing a turn rather than accumulating one |
| `test_reframe.py` | Showing the deck's question only when it genuinely reframes |
| `test_skill_template.py` | `skill/first-principles/assets/deck.html` standalone, no network |
| `test_access_gate.py` | The token gate: unlock, replay, decline, Escape |

## A note on assertions

Prefer asserting the **outcome** over the mechanism. Checking that an element
carries a `hidden` class passed while the element was plainly visible on screen;
`not page.is_visible(...)` would have caught it. Where a check reads a rendered
string, remember CSS `text-transform` — several assertions here compare
lowercased text for that reason.
