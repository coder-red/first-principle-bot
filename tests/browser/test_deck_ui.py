import json, sys, urllib.request
import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")
os.makedirs(SHOTS, exist_ok=True)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright
from harness import stub_web_fonts


def ndjson_done(deck):
    """One done event — the shape the streaming client expects."""
    return json.dumps({"type": "done", "deck": deck}) + chr(10)

BASE = os.environ.get("FP_BASE_URL", "http://127.0.0.1:8000")
DECK = json.loads(urllib.request.urlopen(BASE + "/api/sample-deck").read())
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "_shots")

fails = []
def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not cond: fails.append(name)

def new_page(browser, theme, width, height=860):
    page = browser.new_page(viewport={"width": width, "height": height})
    stub_web_fonts(page)
    errors, bodies = [], []
    def note(t):
        # Kept as a second line of defence. It only ever matched errors that
        # name the host in their message; a failed subresource does not — the
        # URL is in the console message's location, not its text — which is
        # why stub_web_fonts above removes the request instead of the noise.
        if "fonts.g" not in t and "ERR_CONNECTION" not in t: errors.append(t)
    page.on("pageerror", lambda e: note(str(e)))
    page.on("console", lambda m: note(m.text) if m.type == "error" else None)
    def handler(route):
        try: bodies.append(json.loads(route.request.post_data or "{}"))
        except Exception: bodies.append({})
        route.fulfill(status=200, content_type="application/x-ndjson", body=ndjson_done(DECK))
    page.route("**/api/chat/stream", handler)
    page.add_init_script(f"localStorage.setItem('fp_theme','{theme}')")
    page.goto(BASE); page.wait_for_selector("#message-input")
    return page, errors, bodies

with sync_playwright() as p:
    browser = p.chromium.launch()

    for theme, width in [("dark", 1280), ("light", 1280), ("dark", 360)]:
        label = f"{theme}@{width}"
        print(f"\n=== {label} ===")
        page, errors, bodies = new_page(browser, theme, width)

        check("no mode toggle left", page.locator(".mode-toggle").count() == 0)
        check("4 suggestions render", page.locator(".suggestion-chip").count() == 4)
        first = page.inner_text(".suggestion-chip")
        page.click("#shuffle-suggestions"); page.wait_for_timeout(200)
        check("shuffle changes suggestions", page.inner_text(".suggestion-chip") != first)
        if width > 700: page.screenshot(path=os.path.join(OUT, f"v2-welcome-{label}.png"))

        page.fill("#message-input", "why is the sky blue")
        page.click("#send-btn")
        page.wait_for_selector(".open-deck", timeout=8000)
        check("answer card renders", page.is_visible(".open-deck"), page.inner_text(".answer-meta"))
        check("followup chips render", page.locator(".followup-chip").count() == 3)
        check("live deck carries the unreviewed line",
              "content unreviewed" in (page.text_content(".deck-provenance.is-live") or ""))
        check("answer states the bedrock inline",
              "accelerating electric charge" in page.inner_text(".answer-bedrock-text"))
        check("rail shows one tick per card",
              page.locator(".rail-tick").count() == len(DECK["cards"]))
        check("rail encodes depth (ticks at different heights)",
              len(set(page.eval_on_selector_all(".rail-tick",
                  "e=>e.map(t=>Math.round(t.getBoundingClientRect().top))"))) > 2)
        check("no emoji icons left", page.locator(".chip-icon").count() == 0)
        widths = page.eval_on_selector_all(".suggestion-chip", "e=>e.map(c=>Math.round(c.getBoundingClientRect().width))") if page.locator(".suggestion-chip").count() else []
        check("no chat bubbles left", page.locator(".message").count() == 0)
        check("no body h-scroll", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"),
              f'{page.evaluate("document.documentElement.scrollWidth")} vs {width}')
        if width > 700: page.screenshot(path=os.path.join(OUT, f"v2-transcript-{label}.png"))

        page.click(".open-deck"); page.wait_for_selector(".card-face .card-title")
        check("cards view default", page.is_visible(".view-cards"))
        check("depth dots", page.locator(".depth-dot").count() == len(DECK["cards"]))

        # walk deck
        titles = []
        for i in range(len(DECK["cards"])):
            page.wait_for_timeout(300)
            titles.append(page.inner_text(".card-title"))
            if i == 4 and width > 700: page.screenshot(path=os.path.join(OUT, f"v2-bedrock-{label}.png"))
            page.keyboard.press("ArrowRight")
        check("keyboard walks every card", titles == [c["title"] for c in DECK["cards"]])

        # rail tick deep-links into the deck
        page.keyboard.press("Escape"); page.wait_for_timeout(200)
        page.locator(".rail-tick").nth(4).click()
        page.wait_for_selector(".card-face"); page.wait_for_timeout(350)
        check("rail tick opens that exact card",
              page.get_attribute(".card-face", "data-phase") == "bedrock",
              page.inner_text(".card-title"))

        # prose view
        page.click("text=Full reasoning"); page.wait_for_timeout(250)
        check("prose view shows", page.is_visible(".view-prose"))
        check("prose has one step per card",
              page.locator(".prose-step").count() == len(DECK["cards"]),
              f'{page.locator(".prose-step").count()} steps')
        check("prose renders tag chips", page.locator(".prose-step .tag-chip").count() >= 5)
        check("prose shows the chain ladder", page.locator(".view-prose .chain-step").count() >= 3)
        check("prose has no raw phase banners", "══" not in page.inner_text(".view-prose"))
        check("prose line length capped",
              page.evaluate("Math.round(document.querySelector('.prose-explanation').getBoundingClientRect().width)") < 700)
        if width > 700: page.screenshot(path=os.path.join(OUT, f"v2-prose-{label}.png"))

        # ask-about-this-card sends focus
        page.fill(".ask-input", "does this apply underwater?")
        page.click(".ask-send")
        page.wait_for_timeout(600)
        check("ask bar sent focus", bool(bodies) and bodies[-1].get("focus") is not None,
              json.dumps(bodies[-1].get("focus", {}))[:70] if bodies else "")
        check("ask bar sent the question", bodies[-1].get("message") == "does this apply underwater?")
        check("modal closed after asking", page.locator(".card-dialog").count() == 0)

        # drill into a chain rung
        page.wait_for_selector(".open-deck")
        page.locator(".open-deck").last.click()
        page.wait_for_selector(".card-face")
        page.keyboard.press("Home"); page.wait_for_timeout(300)
        for _ in range(3): page.keyboard.press("ArrowRight"); page.wait_for_timeout(300)
        check("chain rungs are drillable", page.locator(".chain-drill").count() == 4)
        page.locator(".chain-drill").last.click()
        page.wait_for_timeout(600)
        f = bodies[-1].get("focus") or {}
        check("rung drill sent its chain", len(f.get("chain", [])) == 4, json.dumps(f.get("chain", []))[:80])
        check("rung drill message names the claim",
              "Decompose this further" in bodies[-1].get("message", ""))

        # go-deeper button
        page.wait_for_selector(".open-deck")
        page.locator(".open-deck").last.click()
        page.wait_for_selector(".card-face")
        page.keyboard.press("Home"); page.wait_for_timeout(320)
        check("go-deeper offered on non-atomic card", page.locator(".go-deeper").count() == 1)
        page.keyboard.press("End"); page.wait_for_timeout(320)
        page.locator(".depth-dot").nth(4).click(); page.wait_for_timeout(340)
        check("no go-deeper on the ATOMIC bedrock card", page.locator(".go-deeper").count() == 0,
              page.inner_text(".card-title"))

        # focus trap + escape
        for _ in range(16): page.keyboard.press("Tab")
        check("focus stays in dialog",
              page.evaluate("document.querySelector('.card-dialog').contains(document.activeElement)"))
        page.keyboard.press("Escape"); page.wait_for_timeout(150)
        check("escape closes", page.locator(".card-dialog").count() == 0)

        # typing in ask bar must not steal arrow keys
        page.locator(".open-deck").last.click(); page.wait_for_selector(".card-face")
        t0 = page.inner_text(".card-title")
        page.click(".ask-input")
        page.keyboard.press("ArrowRight"); page.wait_for_timeout(300)
        check("arrows do not flip cards while typing", page.inner_text(".card-title") == t0)
        page.keyboard.press("Escape")

        # followup chip drives a new deck
        page.wait_for_timeout(200)
        before = page.locator(".thread-q").count()
        page.locator(".followup-chip").last.click()
        page.wait_for_timeout(700)
        check("followup chip asks a new question", page.locator(".thread-q").count() == before + 1)
        check("followup sent no focus", bodies[-1].get("focus") is None)

        check("no JS errors", not errors, "; ".join(errors[:2]))
        page.close()

    browser.close()

print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILED: {sorted(set(fails))}"))
sys.exit(1 if fails else 0)
