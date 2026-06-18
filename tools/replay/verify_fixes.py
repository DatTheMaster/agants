#!/usr/bin/env python3
"""Verify the three fix-pass changes in a real (bundled, swiftshader) browser:
  1. Camera no longer pans into a void (clampOffset center-lock/bounds).
  2. The newly-packed ant clips load + animate (multi-frame AnimatedSprites, not idle fallback).
  3. No NEW console errors (fx.json 404 expected; the intermittent Pixi 'split' should not recur).
Assumes: replay server on ws://localhost:8765 (launched from repo root) + http on :8099."""
import time, json
from playwright.sync_api import sync_playwright

URL = "http://localhost:8099/game/?pixi=1&replay=true"
console_errs = []
page_errs = []

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=[
        "--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--no-sandbox"])
    pg = b.new_page(viewport={"width": 1600, "height": 900})
    pg.on("console", lambda m: console_errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: page_errs.append(e.message))
    pg.goto(URL, wait_until="domcontentloaded")
    time.sleep(14)  # let map build + ants stream + clips play

    state = pg.evaluate("""() => {
        const A = window.__agants; if (!A) return {error: 'no __agants'};
        const atlas = A.antViews && A.antViews.atlas;
        const anims = atlas && atlas.animations ? Object.keys(atlas.animations) : [];
        // count live ant bodies that are multi-frame AnimatedSprites (real clip, not idle/placeholder)
        let animated = 0, total = 0, multiframe = 0, sampleClips = {};
        const layer = A.stage.antLayer;
        for (const c of layer.children) {
            total++;
            const f = c.totalFrames || (c.textures && c.textures.length) || 1;
            if (c.play) animated++;
            if (f > 1) multiframe++;
        }
        // camera void test: shove the camera far, clamp, confirm it's bounded
        const sw = A.app.renderer.width / A.app.renderer.resolution;
        const sh = A.app.renderer.height / A.app.renderer.resolution;
        const mapW = A.camera.worldW * A.stage.world.scale.x, mapH = A.camera.worldH * A.stage.world.scale.y;
        A.stage.world.position.set(99999, 99999); A.camera.clamp();
        const clampedHi = {x: A.stage.world.position.x, y: A.stage.world.position.y};
        A.stage.world.position.set(-99999, -99999); A.camera.clamp();
        const clampedLo = {x: A.stage.world.position.x, y: A.stage.world.position.y};
        A.camera.reset();
        return {
            antsSize: A.store.ants.size, antBodies: total, animatedSprites: animated, multiframeBodies: multiframe,
            atlasAnimCount: anims.length, hasCarry: anims.includes('worker_carry'),
            hasScoutWalk: anims.includes('scout_walk'), hasSoldierAttack: anims.includes('soldier_attack'),
            hasPellet: anims.includes('food_pellet'),
            screen: {sw, sh}, map: {mapW: Math.round(mapW), mapH: Math.round(mapH)},
            clampedHi, clampedLo, fps: Math.round(A.app.ticker.FPS),
        };
    }""")

    pg.screenshot(path="tools/replay/shots/fix-verify.png")
    b.close()

print("=== STATE ===")
print(json.dumps(state, indent=2))
print("\n=== CONSOLE ERRORS ===", len(console_errs))
for e in sorted(set(console_errs)): print("  -", e[:160])
print("=== PAGE ERRORS ===", len(page_errs))
for e in sorted(set(page_errs)): print("  -", e[:160])

# verdict
ok = True
if isinstance(state, dict) and not state.get("error"):
    hi, lo = state["clampedHi"], state["clampedLo"]
    sw, sh = state["screen"]["sw"], state["screen"]["sh"]
    mapW, mapH = state["map"]["mapW"], state["map"]["mapH"]
    # bounded: not the 99999 extreme; for world<viewport must be centered (screen-map)/2
    void_bad = abs(hi["x"]) > 99000 or abs(lo["x"]) > 99000
    if mapW <= sw:
        centered = abs(hi["x"] - (sw - mapW) / 2) < 1 and abs(lo["x"] - (sw - mapW) / 2) < 1
        print(f"\nCAMERA: world({mapW})<=screen({sw}) -> center-lock expected; hi.x={hi['x']:.0f} lo.x={lo['x']:.0f} centered={centered}")
        ok = ok and centered
    else:
        print(f"\nCAMERA: world({mapW})>screen({sw}); clamped hi.x={hi['x']:.0f} lo.x={lo['x']:.0f} (bounded, not void)")
        ok = ok and not void_bad
    print(f"CLIPS: atlas anims={state['atlasAnimCount']} carry={state['hasCarry']} scout_walk={state['hasScoutWalk']} "
          f"soldier_attack={state['hasSoldierAttack']} pellet={state['hasPellet']}; "
          f"live bodies={state['antBodies']} multiframe={state['multiframeBodies']}")
    real_console = [e for e in set(console_errs) if 'fx.json' not in e and '404' not in e]
    print(f"NEW console errors (excl fx.json 404): {len(real_console)} ; page errors: {len(page_errs)}")
print("\nVERDICT:", "PASS" if ok and not page_errs else "CHECK ABOVE")
