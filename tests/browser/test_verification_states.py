import copy, json, sys, urllib.request
from playwright.sync_api import sync_playwright
import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")
os.makedirs(SHOTS, exist_ok=True)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE="http://127.0.0.1:8000"
GOOD=json.loads(urllib.request.urlopen(BASE+"/api/sample-deck").read())

# the exact shape seen from the live model: a descent card claiming to be
# irreducible, and a bedrock no deeper than the step that reached it
BAD=copy.deepcopy(GOOD)
BAD["verified"]=False
BAD["issues"]=[
  'Descent card "Redirection Depends On Wavelength" is tagged ATOMIC. If a claim is irreducible the decomposition stops there and that card is the bedrock.',
  'The bedrock "Charges Radiate When Driven" sits at level 3, no deeper than the descent that reached it (level 3). Bedrock must lie below the last descent step.',
]
fails=[]
def check(n,c,d=""):
    print(("  PASS  " if c else "  FAIL  ")+n+(f"   {d}" if d else ""))
    if not c: fails.append(n)

with sync_playwright() as p:
    b=p.chromium.launch()
    for name,deck,theme in [("verified",GOOD,"dark"),("unverified",BAD,"light")]:
        print(f"\n=== {name} ===")
        pg=b.new_page(viewport={"width":1280,"height":900}, device_scale_factor=2)
        errs=[]
        pg.on("pageerror", lambda e: errs.append(str(e)))
        def make_handler(d):
            def handler(route):
                route.fulfill(status=200, content_type="application/json", body=json.dumps(d))
            return handler
        pg.route("**/api/chat", make_handler(deck))
        pg.add_init_script(f"localStorage.setItem('fp_theme','{theme}')")
        pg.goto(BASE); pg.wait_for_selector("#message-input")
        pg.fill("#message-input","why is the sky blue"); pg.click("#send-btn")
        pg.wait_for_selector(".open-deck"); pg.wait_for_timeout(500)
        if name=="verified":
            check("shows 'chain checked'", pg.locator(".chain-ok").count()==1,
                  pg.inner_text(".chain-ok") if pg.locator(".chain-ok").count() else "")
            check("no warning block", pg.locator(".chain-warning").count()==0)
        else:
            check("no false 'chain checked'", pg.locator(".chain-ok").count()==0)
            check("warning block shown", pg.locator(".chain-warning").count()==1)
            check("names the ATOMIC descent violation",
                  "tagged ATOMIC" in pg.inner_text(".chain-warning-list"))
            check("names the bedrock depth violation",
                  "no deeper than the descent" in pg.inner_text(".chain-warning-list"))
            check("warning sits above the bedrock claim",
                  pg.evaluate('''()=>{const w=document.querySelector(".chain-warning").getBoundingClientRect();
                     const b=document.querySelector(".answer-bedrock").getBoundingClientRect();
                     return w.top < b.top;}'''))
        check("no h-scroll", pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
        check("no JS errors", not errs, "; ".join(errs[:2]))
        pg.screenshot(path=os.path.join(SHOTS, f"verify-{name}.png"))
        pg.close()
    b.close()
print("\n"+("ALL PASSED" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
