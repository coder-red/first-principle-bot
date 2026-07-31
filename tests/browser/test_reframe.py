import copy, json, sys, urllib.request
from playwright.sync_api import sync_playwright
import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")
os.makedirs(SHOTS, exist_ok=True)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE="http://127.0.0.1:8000"
BASE_DECK=json.loads(urllib.request.urlopen(BASE+"/api/sample-deck").read())

ECHO=copy.deepcopy(BASE_DECK); ECHO["reframed"]=False
REFRAMED=copy.deepcopy(BASE_DECK); REFRAMED["reframed"]=True
REFRAMED["question"]="What must be true for a direction with no source in it to glow?"

fails=[]
def check(n,c,d=""):
    print(("  PASS  " if c else "  FAIL  ")+n+(f"   {d}" if d else ""))
    if not c: fails.append(n)

with sync_playwright() as p:
    b=p.chromium.launch()
    for name,deck in [("echoed question", ECHO), ("genuine reframe", REFRAMED)]:
        print(f"\n=== {name} ===")
        pg=b.new_page(viewport={"width":1280,"height":900}, device_scale_factor=2)
        errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
        def h(route, d=None): pass
        def make(d):
            def handler(route):
                route.fulfill(status=200, content_type="application/json", body=json.dumps(d))
            return handler
        pg.route("**/api/chat", make(deck))
        pg.goto(BASE); pg.wait_for_selector("#message-input")
        pg.evaluate("localStorage.removeItem('fp_thread_v1')"); pg.reload(); pg.wait_for_selector("#message-input")
        pg.fill("#message-input","why is the sky blue"); pg.click("#send-btn")
        pg.wait_for_selector(".open-deck"); pg.wait_for_timeout(400)

        shown = pg.locator(".answer-reframe").count()
        if name.startswith("echoed"):
            check("no restatement under the heading", shown==0)
            check("question appears exactly once on screen",
                  pg.inner_text("#messages").lower().count("why is the sky blue")==1,
                  str(pg.inner_text("#messages").lower().count("why is the sky blue")))
        else:
            check("reframe is shown", shown==1)
            check("labelled as a reading, not a repeat",
                  "reading this as" in pg.inner_text(".answer-reframe-label").lower())
            check("shows the reframed wording",
                  "no source in it to glow" in pg.inner_text(".answer-question"))
        check("bedrock claim still shown", pg.locator(".answer-bedrock").count()==1)
        check("modal header keeps the deck question",
              (lambda: (pg.click(".open-deck"), pg.wait_for_selector(".card-face"),
                        pg.locator(".deck-question").count()==1)[2])())
        pg.keyboard.press("Escape")
        check("no JS errors", not errs, "; ".join(errs[:2]))
        pg.screenshot(path=os.path.join(SHOTS, f"reframe-{name.split()[0]}.png"))
        pg.close()
    b.close()
print("\n"+("ALL PASSED" if not fails else f"{len(fails)} FAILED: {sorted(set(fails))}"))
sys.exit(1 if fails else 0)
