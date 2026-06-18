import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b=p.chromium.launch(headless=True,args=["--use-gl=swiftshader","--enable-unsafe-swiftshader","--no-sandbox"])
    pg=b.new_page(viewport={"width":1600,"height":900})
    errs=[]; pg.on("pageerror", lambda e: errs.append(e.message))
    pg.goto("http://localhost:8099/game/?pixi=1&replay=true", wait_until="domcontentloaded")
    time.sleep(13)
    st=pg.evaluate("""()=>{const A=window.__agants;return{ants:A.store.ants.size,bodies:A.stage.antLayer.children.length,
       mapW:Math.round(A.camera.worldW),structs:A.stage.structureLayer.children.length,fps:Math.round(A.app.ticker.FPS)};}""")
    pg.screenshot(path="tools/replay/shots/fix-verify-clean.png")
    b.close()
    print("STATE:",st,"pageerrors:",len(errs))
