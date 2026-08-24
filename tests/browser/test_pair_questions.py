"""Pair questions: two claims side by side, pick which survives the rebuild.

The quiz mixes these into the tag-matching questions. They exist because
matching a claim to its label can be memorised; choosing between two claims
against a stated rule has to be reasoned through.
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

fails = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(name)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1200, "height": 860})
    stub_web_fonts(page)
    page.route("**/api/library", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps(LIB_INDEX)))
    page.route("**/api/library/why-is-the-sky-blue", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps(LIB_DECK)))
    page.route("**/api/explore/physics", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"sector": {"slug": "physics"}, "questions": []})))
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(BASE)
    page.click('[data-slug="physics"]')
    page.wait_for_selector(".library-chip")
    page.click(".library-chip")
    page.wait_for_selector(".deck-provenance.is-reviewed")
    page.click(".open-deck")
    page.wait_for_selector(".card-face .card-title")
    page.locator(".view-tab").nth(2).click()
    page.wait_for_selector(".quiz-option")

    seen_pair_ask = False
    walked = 0
    for _ in range(20):
        ask = page.text_content(".quiz-ask")
        if "rebuild stand on" in ask or "set aside" in ask:
            seen_pair_ask = True
            # two options only, both claim text, no glyphs to leak the answer
            opts = page.locator(".quiz-option")
            check("pair question has exactly 2 options", opts.count() == 2, str(opts.count()))
            break
        page.locator(".quiz-option[data-correct]").click()
        walked += 1
        page.wait_for_selector(".quiz-next")
        if page.text_content(".quiz-next").strip() == "See how you did":
            break
        page.click(".quiz-next")

    check("a pair question appeared", seen_pair_ask,
          f"walked {walked} tag questions first")

    if seen_pair_ask:
        # answer correctly via data-correct, inspect the reveal
        page.locator(".quiz-option[data-correct]").click()
        page.wait_for_selector(".quiz-reveal")
        verdict = page.text_content(".quiz-verdict")
        check("pair verdict names the rule", "rules sort it" in verdict, verdict)
        meanings = page.locator(".quiz-reveal .quiz-meaning")
        check("reveal explains both claims", meanings.count() == 2, str(meanings.count()))

    check("no JS errors", not errors, "; ".join(errors))
    browser.close()

print()
if fails:
    print("FAILED: " + ", ".join(fails))
    sys.exit(1)
print("pair-question check: all passed")
