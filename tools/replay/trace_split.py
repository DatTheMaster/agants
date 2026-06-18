import time
from playwright.sync_api import sync_playwright
URL = "http://localhost:8099/game/?pixi=1&replay=true"
con=[]; errs=[]
with sync_playwright() as p:
    b=p.chromium.launch(headless=True,args=["--use-gl=swiftshader","--enable-unsafe-swiftshader","--no-sandbox"])
    pg=b.new_page(viewport={"width":1600,"height":900})
    pg.on("console", lambda m: con.append(f"[{m.type}] {m.text}"))
    pg.on("pageerror", lambda e: errs.append((e.message, e.stack)))
    pg.goto(URL, wait_until="domcontentloaded")
    time.sleep(12)
    b.close()
print("=== CONSOLE (first 25) ==="); [print(" ", c[:200]) for c in con[:25]]
print("\n=== PAGE ERRORS (with stack) ===")
seen=set()
for m,s in errs:
    if m in seen: continue
    seen.add(m); print("MSG:", m); print("STACK:\n", (s or "(none)")[:1800]); print("-"*60)
