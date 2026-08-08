"""The access gate, driven in a real browser.

Everything here is stubbed at the network layer, so it needs no gated server
and no API key: the app only ever learns it is gated by getting a 401, which
is exactly what these routes return.

The behaviours worth protecting are the ones a unit test cannot see — that the
question the reader typed survives the gate, and that dismissing it leaves no
half-rendered deck on screen.
"""

import json
import os
import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.environ.get("FP_BASE_URL", "http://127.0.0.1:8000")
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")
os.makedirs(SHOTS, exist_ok=True)

TOKEN = "letmein"

fails = []


def check(name, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not condition:
        fails.append(name)


def wire(page, deck, state):
    """401 until the token is exchanged, then the real deck."""

    def on_access(route):
        body = json.loads(route.request.post_data or "{}")
        if body.get("token") == TOKEN:
            state["unlocked"] = True
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"ok": True}))
        else:
            route.fulfill(status=401, content_type="application/json",
                          body=json.dumps({"detail": "That token is not valid."}))

    def on_deck(route):
        state["deck_calls"] += 1
        if not state["unlocked"]:
            route.fulfill(status=401, content_type="application/json",
                          body=json.dumps({"detail": "This instance requires an access token."}))
            return
        route.fulfill(status=200, content_type="application/x-ndjson",
                      body=json.dumps({"type": "done", "deck": deck}) + "\n")

    def on_sector(route):
        if not state["unlocked"]:
            route.fulfill(status=401, content_type="application/json",
                          body=json.dumps({"detail": "This instance requires an access token."}))
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "sector": {"slug": "money", "label": "Money", "blurb": "value, price, debt"},
            "questions": [{"question": "What is money, really?", "hook": "money as a substance"}],
        }))

    page.route("**/api/access", on_access)
    page.route("**/api/chat/stream", on_deck)
    page.route("**/api/explore/money", on_sector)


def fresh(browser, deck, state):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    wire(page, deck, state)
    page.goto(BASE)
    page.wait_for_selector("#message-input")
    page.evaluate("localStorage.removeItem('fp_thread_v1')")
    page.reload()
    page.wait_for_selector("#message-input")
    return page, errors


with sync_playwright() as p:
    import urllib.request
    deck = json.loads(urllib.request.urlopen(BASE + "/api/sample-deck").read())
    browser = p.chromium.launch()

    # ── unlocking, then replaying the question ───────────────────────────
    print("\n=== unlocking ===")
    state = {"unlocked": False, "deck_calls": 0}
    page, errors = fresh(browser, deck, state)

    page.fill("#message-input", "why is the sky blue")
    page.click("#send-btn")
    page.wait_for_selector(".gate-dialog", timeout=5000)
    check("the gate appears on a 401", page.locator(".gate-dialog").count() == 1)
    check("the token field is focused", page.evaluate(
        "document.activeElement && document.activeElement.className") == "gate-input")
    check("the token field is masked",
          page.get_attribute(".gate-input", "type") == "password")
    page.screenshot(path=os.path.join(SHOTS, "gate-locked.png"))

    page.fill(".gate-input", "wrong")
    page.click(".gate-submit")
    page.wait_for_selector(".gate-error:not([hidden])", timeout=5000)
    check("a wrong token is reported, not accepted",
          "not accepted" in page.inner_text(".gate-error").lower())
    check("the gate stays open after a wrong token",
          page.locator(".gate-dialog").count() == 1)

    page.fill(".gate-input", TOKEN)
    page.click(".gate-submit")
    page.wait_for_selector(".open-deck", timeout=10000)
    check("the gate closes once the token is accepted",
          page.locator(".gate-dialog").count() == 0)
    check("the deck renders after unlocking", page.locator(".open-deck").count() == 1)
    check("the question is replayed, not retyped",
          page.inner_text("#messages").lower().count("why is the sky blue") == 1,
          str(page.inner_text("#messages").lower().count("why is the sky blue")))
    check("the deck was actually re-requested", state["deck_calls"] == 2,
          f"{state['deck_calls']} calls")
    check("no JS errors", not errors, "; ".join(errors[:2]))
    page.screenshot(path=os.path.join(SHOTS, "gate-unlocked.png"))
    page.close()

    # ── declining ────────────────────────────────────────────────────────
    print("\n=== declining ===")
    state = {"unlocked": False, "deck_calls": 0}
    page, errors = fresh(browser, deck, state)

    page.fill("#message-input", "why is the sky blue")
    page.click("#send-btn")
    page.wait_for_selector(".gate-dialog", timeout=5000)
    page.click(".gate-cancel")
    page.wait_for_timeout(300)

    check("dismissing closes the gate", page.locator(".gate-dialog").count() == 0)
    # A pending deck left on screen would sit there shimmering forever.
    check("no half-rendered deck is left behind",
          page.locator(".deck-pending, .is-pending").count() == 0)
    check("the unanswered question is cleared too",
          "why is the sky blue" not in page.inner_text("#messages").lower())
    check("a toast offers the way back in",
          not page.is_hidden("#error-toast")
          and "token" in page.inner_text("#error-toast").lower())
    check("the input is usable again", page.is_enabled("#message-input"))
    check("no JS errors", not errors, "; ".join(errors[:2]))
    page.close()

    # ── escape dismisses too ─────────────────────────────────────────────
    print("\n=== escape ===")
    state = {"unlocked": False, "deck_calls": 0}
    page, errors = fresh(browser, deck, state)
    page.fill("#message-input", "why is the sky blue")
    page.click("#send-btn")
    page.wait_for_selector(".gate-dialog", timeout=5000)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check("Escape closes the gate", page.locator(".gate-dialog").count() == 0)
    check("no JS errors", not errors, "; ".join(errors[:2]))
    page.close()

    browser.close()

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {sorted(set(fails))}"))
sys.exit(1 if fails else 0)
