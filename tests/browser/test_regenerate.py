import json, sys, urllib.request
from playwright.sync_api import sync_playwright
import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")
os.makedirs(SHOTS, exist_ok=True)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE="http://127.0.0.1:8000"
DECK=json.loads(urllib.request.urlopen(BASE+"/api/sample-deck").read())
fails=[]
def check(n,c,d=""):
    print(("  PASS  " if c else "  FAIL  ")+n+(f"   {d}" if d else "")); 
    if not c: fails.append(n)

with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={"width":1280,"height":900})
    errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
    def h(r): r.fulfill(status=200, content_type="application/json", body=json.dumps(DECK))
    pg.route("**/api/chat", h)
    pg.goto(BASE); pg.wait_for_selector("#message-input")
    pg.evaluate("localStorage.removeItem('fp_thread_v1')"); pg.reload(); pg.wait_for_selector("#message-input")

    print("\n=== regenerate a plain question ===")
    pg.fill("#message-input","why is the sky blue"); pg.click("#send-btn")
    pg.wait_for_selector(".open-deck"); pg.wait_for_timeout(400)
    pg.click(".regenerate-btn"); pg.wait_for_timeout(900)
    check("one answer on screen after regenerate", pg.locator(".answer").count()==1,
          str(pg.locator(".answer").count()))
    check("question is not printed twice", pg.locator(".thread-q").count()==1,
          str(pg.locator(".thread-q").count()))
    stored = pg.evaluate("JSON.parse(localStorage.getItem('fp_thread_v1')||'{}')")
    check("one turn stored after regenerate", len(stored.get("turns",[]))==1,
          f'{len(stored.get("turns",[]))} turns')
    pg.reload(); pg.wait_for_selector("#message-input"); pg.wait_for_timeout(600)
    check("reload shows one answer, not two", pg.locator(".answer").count()==1,
          str(pg.locator(".answer").count()))

    print("\n=== regenerate a drill-down ===")
    pg.evaluate("localStorage.removeItem('fp_thread_v1')"); pg.reload(); pg.wait_for_selector("#message-input")
    pg.fill("#message-input","why is the sky blue"); pg.click("#send-btn")
    pg.wait_for_selector(".open-deck"); pg.click(".open-deck"); pg.wait_for_selector(".card-face")
    pg.keyboard.press("Home"); pg.wait_for_timeout(300)
    pg.click(".go-deeper"); pg.wait_for_timeout(800)
    before = pg.eval_on_selector_all(".trail",'e=>e.map(t=>t.querySelectorAll(".trail-step").length)')
    pg.click(".regenerate-btn"); pg.wait_for_timeout(900)
    after = pg.eval_on_selector_all(".trail",'e=>e.map(t=>t.querySelectorAll(".trail-step").length)')
    check("regenerating a drill does not grow the trail", before==after, f"{before} -> {after}")
    pg.reload(); pg.wait_for_selector("#message-input"); pg.wait_for_timeout(600)
    check("drill still marked after reload", pg.locator(".thread-q.is-drill").count()==1,
          str(pg.locator(".thread-q.is-drill").count()))
    check("no JS errors", not errs, "; ".join(errs[:2]))
    b.close()
print("\n"+("ALL PASSED" if not fails else f"{len(fails)} FAILED: {sorted(set(fails))}"))
sys.exit(1 if fails else 0)
