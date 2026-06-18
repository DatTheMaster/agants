import time, json
from playwright.sync_api import sync_playwright
URL="http://localhost:8099/game/?pixi=1&replay=true"
con=[]; errs=[]
with sync_playwright() as p:
    b=p.chromium.launch(headless=True,args=["--use-gl=swiftshader","--enable-unsafe-swiftshader","--no-sandbox"])
    pg=b.new_page(viewport={"width":1600,"height":900})
    pg.on("console", lambda m: con.append(m.text))
    pg.on("pageerror", lambda e: errs.append(e.message))
    pg.goto(URL, wait_until="domcontentloaded")
    heap0=None
    time.sleep(3)
    try: heap0=pg.evaluate("()=>performance.memory && performance.memory.usedJSHeapSize")
    except: pass
    time.sleep(50)  # > one full replay loop (~42s) to exercise the map-rebuild guard + RT cleanup
    res=pg.evaluate("""()=>{const A=window.__agants;
      const heap=performance.memory?performance.memory.usedJSHeapSize:0;
      // structure selection test: select first structure, confirm ring attaches
      let structSel=false, structCount=A.store.structures.size;
      const keys=[...A.store.structures.keys()];
      if(keys.length){A.hud.selectedId=null;A.hud.selectedStructKey=keys[0];A.hud._updateSelection();
        structSel=!!A.hud.selectionRing.parent;}
      // sample a RED ant tint
      let redTint=null;
      for(const c of A.stage.antLayer.children){ if(c.tint!==undefined){redTint=c.tint;break;} }
      return {heap, terrainSprites:A.tileGrid.sprites.length, structCount, structSel,
        antBrightnessFilter:!!(A.stage.antLayer.filters&&A.stage.antLayer.filters.length),
        antBodies:A.stage.antLayer.children.length, fps:Math.round(A.app.ticker.FPS)};}""")
    pg.screenshot(path="tools/replay/shots/polish-verify.png")
    b.close()
mapbakes=sum(1 for c in con if "map baked" in c)
real_errs=[e for e in con if "[error]" in e.lower()] + errs
print("MAP BAKED count:", mapbakes, "(want 1)")
print("RESULT:", json.dumps(res))
print("heap0:", heap0)
print("PAGE ERRORS:", len(errs), errs[:3])
print("CONSOLE errors:", [c for c in con if "404" not in c and "GPU stall" not in c and "fx.json" not in c and "map baked" not in c and "mounted" not in c][:5])
