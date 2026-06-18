#!/usr/bin/env python3
"""Verify the renderer rollout: Pixi is now the DEFAULT (no ?pixi needed), and a
forced ?pixi=0 falls back to the legacy Canvas renderer. Run with the no-cache
static server (serve.py) + replay server (server.py) already up.

  python3 tools/replay/verify_render.py
"""
import time
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8090/game/"


def probe(url, label, settle=14):
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=[
            "--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--no-sandbox"])
        pg = b.new_page(viewport={"width": 1280, "height": 800})
        errs, console = [], []
        pg.on("pageerror", lambda e: errs.append(e.message))
        pg.on("console", lambda m: console.append((m.type, m.text)))
        pg.goto(url, wait_until="domcontentloaded")
        time.sleep(settle)
        state = pg.evaluate("""() => ({
            usePixi: !!window.__USE_PIXI,
            pixiMounted: !!window.__pixiMounted,
            canvasStarted: !!window.__canvasStarted,
            haveAgants: !!window.__agants,
        })""")
        b.close()
    print(f"\n=== {label} :: {url} ===")
    print("state:", state)
    split = [t for (lvl, t) in console if "split" in t.lower()]
    print("pageerrors:", len(errs), errs[:3])
    print("split-mentions:", split[:3])
    mounted = [t for (lvl, t) in console if "client mounted" in t]
    fellback = [t for (lvl, t) in console if "Canvas renderer" in t]
    print("pixi-mounted-log:", bool(mounted), "| canvas-fallback-log:", fellback[:1])
    return state


# 1) Default (no pixi param) should run Pixi.
d = probe(BASE + "?replay=true", "DEFAULT (expect Pixi)")
# 2) Forced ?pixi=0 should run Canvas.
c = probe(BASE + "?replay=true&pixi=0", "FORCED ?pixi=0 (expect Canvas)")

print("\n========== SUMMARY ==========")
ok1 = d["usePixi"] and d["pixiMounted"] and d["haveAgants"]
ok2 = (not c["usePixi"]) and c["canvasStarted"] and (not c["haveAgants"])
print(f"[{'PASS' if ok1 else 'FAIL'}] default -> Pixi mounted")
print(f"[{'PASS' if ok2 else 'FAIL'}] ?pixi=0 -> Canvas fallback")
