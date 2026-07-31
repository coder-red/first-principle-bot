import io, sys, pathlib
from playwright.sync_api import sync_playwright
import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")
os.makedirs(SHOTS, exist_ok=True)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
src = io.open(os.path.join(ROOT, 'skill', 'first-principles', 'assets', 'deck.html'), encoding='utf-8').read()
# the Artifact runtime wraps the file in a doctype/head/body skeleton
wrapped = "<!doctype html><html><head><meta charset='utf-8'></head><body>" + src + "</body></html>"
out = pathlib.Path(SHOTS) / "deckpreview.html"; out.write_text(wrapped, encoding="utf-8")

fails=[]
def check(n,c,d=""):
    print(("  PASS  " if c else "  FAIL  ")+n+(f"   {d}" if d else ""))
    if not c: fails.append(n)

with sync_playwright() as p:
    b=p.chromium.launch()
    for theme,w in [("dark",900),("light",900),("dark",380)]:
        print(f"\n=== {theme}@{w} ===")
        pg=b.new_page(viewport={"width":w,"height":900}, device_scale_factor=2)
        errs=[]
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type=="error" else None)
        pg.emulate_media(color_scheme=theme)
        pg.goto(out.resolve().as_uri()); pg.wait_for_timeout(400)
        check("no JS errors", not errs, "; ".join(errs[:2]))
        check("renders a card title", bool(pg.inner_text(".title").strip()), pg.inner_text(".title"))
        check("rail has one tick per card", pg.locator(".tick").count()==7)
        check("rail ticks sit at differing depths",
              len(set(pg.eval_on_selector_all(".tick","e=>e.map(t=>Math.round(t.getBoundingClientRect().top))")))>2)
        check("bedrock caption present", "bedrock" in pg.inner_text(".caption").lower())
        check("no external requests possible", "http" not in src.split("<script>")[0].replace("https://json-schema",""))
        check("no body h-scroll", pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth"),
              f'{pg.evaluate("document.documentElement.scrollWidth")} vs {w}')
        # walk to bedrock
        for _ in range(4): pg.keyboard.press("ArrowRight"); pg.wait_for_timeout(80)
        check("arrow keys walk to bedrock", pg.get_attribute("#card","data-phase")=="bedrock",
              pg.inner_text(".title"))
        check("bedrock shows ATOMIC chip", "atomic" in pg.inner_text("#card .chip").lower(), pg.inner_text("#card .chip"))
        check("chain ladder indents",
              (lambda xs: xs==sorted(xs) and xs[0]<xs[-1])(
                  pg.eval_on_selector_all("#card .rung .node","e=>e.map(n=>Math.round(n.getBoundingClientRect().left))")))
        if w>700: pg.screenshot(path=os.path.join(SHOTS, f"skill-deck-{theme}.png"), full_page=True)
        # prose view
        pg.click("#tab-prose"); pg.wait_for_timeout(200)
        check("prose view lists every step", pg.locator(".step").count()==7)
        pg.close()
    b.close()
print("\n"+("ALL PASSED" if not fails else f"FAILED: {sorted(set(fails))}"))
sys.exit(1 if fails else 0)
