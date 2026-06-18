import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b=p.chromium.launch(headless=True,args=["--use-gl=swiftshader","--enable-unsafe-swiftshader","--no-sandbox"])
    pg=b.new_page(viewport={"width":1600,"height":900})
    errs=[]; pg.on("pageerror", lambda e: errs.append(e.message))
    pg.goto("http://localhost:8099/game/?pixi=1&replay=true", wait_until="domcontentloaded")
    time.sleep(13)
    # zoom in a bit to test seams at fractional zoom (the camera method)
    pg.evaluate("()=>{const A=window.__agants; A.camera.zoom(800,450,1.7);}")
    time.sleep(1)
    pg.screenshot(path="tools/replay/shots/grid-check-zoom.png")
    pg.evaluate("()=>{const A=window.__agants; A.camera.reset();}")
    time.sleep(1)
    pg.screenshot(path="tools/replay/shots/grid-check-full.png")
    b.close()
    print("pageerrors:", len(errs))
