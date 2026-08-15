import copy, json, sys, urllib.request
from playwright.sync_api import sync_playwright
from harness import stub_web_fonts


def ndjson_done(deck):
    """One done event — the shape the streaming client expects."""
    return json.dumps({"type": "done", "deck": deck}) + chr(10)
import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")
os.makedirs(SHOTS, exist_ok=True)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.environ.get("FP_BASE_URL", "http://127.0.0.1:8000")
GOOD=json.loads(urllib.request.urlopen(BASE+"/api/sample-deck").read())
BAD=copy.deepcopy(GOOD); BAD["verified"]=False
BAD["issues"]=['Descent card "X" is tagged ATOMIC.']

fails=[]
def check(n,c,d=""):
    print(("  PASS  " if c else "  FAIL  ")+n+(f"   {d}" if d else ""))
    if not c: fails.append(n)

def page_with(b, deck, theme="dark", w=1280):
    pg=b.new_page(viewport={"width":w,"height":900})
    stub_web_fonts(pg)
    errs=[]
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type=="error" else None)
    def h(route): route.fulfill(status=200, content_type="application/x-ndjson", body=ndjson_done(deck))
    pg.route("**/api/chat/stream", h)
    pg.add_init_script(f"localStorage.setItem('fp_theme','{theme}')")
    return pg, errs

with sync_playwright() as p:
    b=p.chromium.launch()

    # ---- quiz on a sound deck ----
    print("\n=== quiz (verified deck) ===")
    pg,errs=page_with(b,GOOD)
    pg.goto(BASE); pg.wait_for_selector("#message-input")
    pg.evaluate("localStorage.removeItem('fp_thread_v1')")
    pg.reload(); pg.wait_for_selector("#message-input")
    pg.fill("#message-input","why is the sky blue"); pg.click("#send-btn")
    pg.wait_for_selector(".open-deck"); pg.click(".open-deck"); pg.wait_for_selector(".card-face")
    pg.locator(".view-tab").nth(2).click(); pg.wait_for_timeout(300)
    check("quiz view opens", pg.is_visible(".view-quiz"))
    check("asks about a claim, not recall", "How well is this known" in pg.inner_text(".quiz-ask"))
    check("4 tag options", pg.locator(".quiz-option").count()==4)
    check("claim text is a principle from the deck",
          any(pg.inner_text(".quiz-claim").strip() == c["principle"] for c in GOOD["cards"]),
          pg.inner_text(".quiz-claim")[:50])
    check("ask bar actually hidden during quiz", not pg.is_visible(".ask-bar"))
    pg.locator(".view-tab").nth(0).click(); pg.wait_for_timeout(200)
    check("ask bar returns on the cards view", pg.is_visible(".ask-bar"))
    pg.locator(".view-tab").nth(2).click(); pg.wait_for_timeout(250)

    # answer wrong deliberately: pick an option that is not the correct one
    wrong = pg.locator(".quiz-option:not([data-correct])").first
    wrong.click(); pg.wait_for_timeout(250)
    check("reveals the deck's tag on a wrong answer", pg.locator(".quiz-verdict.is-wrong").count()==1)
    check("marks the correct option too", pg.locator(".quiz-option.is-right").count()==1)
    check("explains why", bool(pg.inner_text(".quiz-meaning").strip()))
    check("options locked after answering",
          pg.eval_on_selector_all(".quiz-option","e=>e.every(o=>o.disabled)"))
    check("no numeric score shown", "point" not in pg.inner_text(".view-quiz").lower())
    pg.screenshot(path=os.path.join(SHOTS, f"quiz-answered.png"))

    # walk to the summary
    for _ in range(6):
        if pg.locator(".quiz-next").count():
            pg.click(".quiz-next"); pg.wait_for_timeout(200)
            if pg.locator(".quiz-option:not([data-correct])").count():
                pg.locator(".quiz-option:not([data-correct])").first.click(); pg.wait_for_timeout(150)
    check("reaches a summary", pg.locator(".quiz-summary").count()==1,
          pg.inner_text(".quiz-summary-count") if pg.locator(".quiz-summary").count() else "")
    check("summary says matched, not scored", "matched" in pg.inner_text(".quiz-summary-count").lower())
    check("no JS errors", not errs, "; ".join(errs[:2]))
    pg.close()

    # ---- quiz refuses an unverified deck ----
    print("\n=== quiz (unverified deck) ===")
    pg,errs=page_with(b,BAD)
    pg.goto(BASE); pg.wait_for_selector("#message-input")
    pg.evaluate("localStorage.removeItem('fp_thread_v1')"); pg.reload(); pg.wait_for_selector("#message-input")
    pg.fill("#message-input","x"); pg.click("#send-btn")
    pg.wait_for_selector(".open-deck"); pg.click(".open-deck"); pg.wait_for_selector(".card-face")
    pg.locator(".view-tab").nth(2).click(); pg.wait_for_timeout(300)
    check("quiz refuses an unsound chain", pg.locator(".quiz-blocked").count()==1)
    check("no questions offered", pg.locator(".quiz-option").count()==0)
    check("states the reason", "did not pass validation" in pg.inner_text(".quiz-blocked-body"))
    check("no JS errors", not errs, "; ".join(errs[:2]))
    pg.close()

    # ---- drill trail ----
    print("\n=== drill trail ===")
    pg,errs=page_with(b,GOOD)
    pg.goto(BASE); pg.wait_for_selector("#message-input")
    pg.evaluate("localStorage.removeItem('fp_thread_v1')"); pg.reload(); pg.wait_for_selector("#message-input")
    pg.fill("#message-input","why is the sky blue"); pg.click("#send-btn")
    pg.wait_for_selector(".open-deck")
    check("no trail on a fresh question", pg.locator(".trail").count()==0)
    pg.click(".open-deck"); pg.wait_for_selector(".card-face")
    pg.keyboard.press("Home"); pg.wait_for_timeout(300)
    pg.click(".go-deeper"); pg.wait_for_timeout(700)
    check("first drill marks the question", pg.locator(".thread-q.is-drill").count()==1)
    pg.locator(".open-deck").last.click(); pg.wait_for_selector(".card-face")
    pg.keyboard.press("Home"); pg.wait_for_timeout(300)
    pg.click(".go-deeper"); pg.wait_for_timeout(700)
    trails = pg.eval_on_selector_all(".trail",'e=>e.map(t=>t.querySelectorAll(".trail-step").length)')
    check("trail deepens with each drill", trails==[1,2], str(trails))
    pg.fill("#message-input","what is time"); pg.click("#send-btn"); pg.wait_for_timeout(700)
    check("a fresh question resets the trail",
          pg.locator(".thread-q").last.locator(".trail").count()==0)
    check("no JS errors", not errs, "; ".join(errs[:2]))
    pg.screenshot(path=os.path.join(SHOTS, f"trail.png"), full_page=True)
    pg.close()

    # ---- persistence ----
    print("\n=== persistence ===")
    pg,errs=page_with(b,GOOD)
    pg.goto(BASE); pg.wait_for_selector("#message-input")
    pg.evaluate("localStorage.removeItem('fp_thread_v1')"); pg.reload(); pg.wait_for_selector("#message-input")
    pg.fill("#message-input","why is the sky blue"); pg.click("#send-btn")
    pg.wait_for_selector(".open-deck"); pg.wait_for_timeout(400)
    before = pg.locator(".answer").count()
    check("new-thread control appears", pg.is_visible("#new-thread"))
    pg.reload(); pg.wait_for_selector("#message-input"); pg.wait_for_timeout(600)
    check("thread survives a reload", pg.locator(".answer").count()==before,
          f'{before} -> {pg.locator(".answer").count()}')
    check("restored deck still opens", pg.locator(".open-deck").count()>0)
    check("welcome removed on restore", pg.locator(".welcome").count()==0)
    pg.click(".open-deck"); pg.wait_for_selector(".card-face")
    check("restored deck is fully interactive", bool(pg.inner_text(".card-title").strip()))
    pg.keyboard.press("Escape"); pg.wait_for_timeout(200)
    pg.click("#new-thread"); pg.wait_for_selector("#message-input"); pg.wait_for_timeout(500)
    check("new thread clears the transcript", pg.locator(".answer").count()==0)
    check("welcome returns", pg.locator(".welcome").count()==1)
    check("no JS errors", not errs, "; ".join(errs[:2]))
    pg.close()
    b.close()

print("\n"+("ALL PASSED" if not fails else f"{len(fails)} FAILED: {sorted(set(fails))}"))
sys.exit(1 if fails else 0)
