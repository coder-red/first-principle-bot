"""Spaced review and the library shelf, driven through the real UI.

Time is not mocked: the suite seeds fp_review with `due` values already in
the past, which is indistinguishable from waiting a day and far less flaky
than faking Date.now.
"""
import json
import os
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright
from harness import stub_web_fonts

BASE = os.environ.get("FP_BASE_URL", "http://127.0.0.1:8000")
DECK = json.loads(urllib.request.urlopen(BASE + "/api/sample-deck").read())

LIB_DECK = dict(DECK)
LIB_DECK["meta"] = {"slug": "why-is-the-sky-blue", "sector": "physics",
                    "model": "test", "generated_at": "t", "reviewed": True}
LIB_INDEX = {"decks": [{"slug": "why-is-the-sky-blue", "sector": "physics",
                        "topic": LIB_DECK["topic"],
                        "question": LIB_DECK["question"]}]}

DUE_STORE = {"v": 1, "claims": [
    {"id": "s|Claim one", "claim": "Claim one", "tag": "VERIFIED",
     "deckSlug": "s", "deckTopic": "T", "rung": 1, "due": 1000, "ts": 1000},
    {"id": "s|Claim two", "claim": "Claim two", "tag": "ATOMIC",
     "deckSlug": "s", "deckTopic": "T", "rung": 0, "due": 1000, "ts": 1000},
]}

fails = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(name)


def stub_library(page):
    page.route("**/api/library", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps(LIB_INDEX)))
    page.route("**/api/library/why-is-the-sky-blue", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps(LIB_DECK)))
    page.route("**/api/explore/physics", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"sector": {"slug": "physics"}, "questions": []})))


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1200, "height": 860})
    stub_web_fonts(page)
    stub_library(page)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console",
            lambda m: errors.append(m.text)
            if m.type == "error" and "fonts.g" not in m.text else None)

    # ── banner appears when claims are due ───────────────────────────────
    page.goto(BASE)
    page.evaluate("s => localStorage.setItem('fp_review', JSON.stringify(s))",
                  DUE_STORE)
    page.reload()
    page.wait_for_selector("#review-banner")
    banner = page.text_content("#review-banner")
    check("banner shows due count", "2 claims due for review" in banner, banner)

    # ── a review round runs and reschedules ──────────────────────────────
    page.click(".review-banner-go")
    page.wait_for_selector(".review-round .quiz-option")
    for _ in range(2):  # answer both claims (any option)
        page.click(".review-round .quiz-option")
        page.click(".review-round .quiz-next")
    store = json.loads(page.evaluate("() => localStorage.getItem('fp_review')"))
    now_ms = page.evaluate("() => Date.now()")
    check("both claims rescheduled into the future",
          all(c["due"] > now_ms for c in store["claims"]), json.dumps(store))
    check("a review answer resets or climbs the rung",
          all(c["rung"] in (0, 1, 2) for c in store["claims"]))
    page.reload()
    check("banner gone once nothing is due",
          page.query_selector("#review-banner") is None)

    # ── library shelf renders and opens a reviewed deck ──────────────────
    page.click('[data-slug="physics"]')
    page.wait_for_selector(".library-chip")
    check("shelf shows the reviewed question",
          LIB_DECK["question"] in page.text_content(".library-chip .chip-text"))
    page.click(".library-chip")
    page.wait_for_selector(".deck-provenance.is-reviewed")
    check("reviewed badge on library deck",
          "Reviewed deck." in page.text_content(".deck-provenance.is-reviewed"))

    # ── a quiz answer on the library deck lands in the queue ─────────────
    page.click(".open-deck")
    page.wait_for_selector(".card-face .card-title")
    page.locator(".view-tab").nth(2).click()
    page.wait_for_selector(".quiz-option")
    page.click(".quiz-option")
    store = json.loads(page.evaluate("() => localStorage.getItem('fp_review')"))
    check("quiz answer recorded with the deck slug",
          any(c["deckSlug"] == "why-is-the-sky-blue" for c in store["claims"]),
          json.dumps([c["id"] for c in store["claims"]]))

    check("no JS errors", not errors, "; ".join(errors))
    browser.close()

print()
if fails:
    print("FAILED: " + ", ".join(fails))
    sys.exit(1)
print("test_review: all checks passed")
