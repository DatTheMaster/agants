#!/usr/bin/env python3
"""Full game-lifecycle verification for the Pixi renderer against a REAL local game
server (lobby -> START -> running -> winner). Proves the Pixi path drives the lobby +
START button, info panel, legend, and winner screen — not just a mid-match replay.

Prereqs (started by the caller):
  - game server:  TPS=12 RED_BRAIN_TYPE=bot BLUE_BRAIN_TYPE=bot PORT=8083 python3 server.py
  - static:       python3 tools/replay/serve.py 8090

  python3 tools/replay/verify_lifecycle.py
"""
import json, time, urllib.request
from playwright.sync_api import sync_playwright

PAGE = "http://localhost:8090/game/?pixi=1"
API  = "http://localhost:8083"


def post_control(action):
    req = urllib.request.Request(API + "/api/control",
        data=json.dumps({"action": action}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def main():
    results = {}
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=[
            "--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--no-sandbox"])
        pg = b.new_page(viewport={"width": 1280, "height": 800})
        pg.add_init_script("window.AGANTS_BACKEND='http://localhost:8083';")
        errs = []
        pg.on("pageerror", lambda e: errs.append(e.message))
        pg.goto(PAGE, wait_until="domcontentloaded")

        # --- 1. LOBBY: Pixi mounted, lobby overlay + START button visible ---
        pg.wait_for_selector("#lobby-overlay", state="visible", timeout=15000)
        pg.wait_for_function("() => window.__pixiMounted === true", timeout=15000)
        start_visible = pg.is_visible(".lobby-start")
        pg.screenshot(path="tools/replay/shots/life-1-lobby.png")
        results["lobby_overlay_visible"] = pg.is_visible("#lobby-overlay")
        results["start_button_visible"] = start_visible
        results["pixi_mounted"] = pg.evaluate("() => !!window.__pixiMounted")
        results["using_pixi"] = pg.evaluate("() => !!window.__USE_PIXI")

        # --- 2. START via the actual lobby button ---
        pg.click(".lobby-start")
        # running once the store has ants and the lobby overlay is gone
        pg.wait_for_function(
            "() => window.__agants && window.__agants.store && window.__agants.store.ants && window.__agants.store.ants.size > 0",
            timeout=20000)
        pg.wait_for_function("() => getComputedStyle(document.getElementById('lobby-overlay')).display === 'none'", timeout=20000)
        # let it render + the info panel populate
        pg.wait_for_function("() => document.getElementById('r-pop') && document.getElementById('r-pop').textContent.trim() !== '' && document.getElementById('r-pop').textContent.trim() !== '—'", timeout=20000)
        clock1 = pg.text_content("#clock")
        ants = pg.evaluate("() => window.__agants.store.ants.size")
        pg.screenshot(path="tools/replay/shots/life-2-running.png")
        results["running_ants_in_store"] = ants
        results["lobby_hidden_when_running"] = pg.evaluate("() => getComputedStyle(document.getElementById('lobby-overlay')).display === 'none'")
        results["info_panel_pop"] = pg.text_content("#r-pop")
        results["info_panel_workers"] = pg.text_content("#r-w")
        results["info_panel_queenhp"] = pg.text_content("#r-queenhp")
        results["legend_present"] = pg.evaluate("() => !!document.getElementById('legend')")

        # clock advances (info panel is live)
        pg.wait_for_timeout(1500)
        clock2 = pg.text_content("#clock")
        results["clock_advances"] = (clock1, clock2, clock1 != clock2)

        # --- 3. Force the game to END -> winner screen (#overlay) ---
        end = post_control("end")
        results["end_winner"] = end.get("winner")
        pg.wait_for_selector("#overlay", state="visible", timeout=20000)
        results["winner_overlay_visible"] = pg.is_visible("#overlay")
        results["winner_title"] = pg.text_content("#overlay-title")
        results["winner_sub"] = pg.text_content("#overlay-sub")
        pg.screenshot(path="tools/replay/shots/life-3-winner.png")

        results["page_errors"] = errs[:5]
        b.close()

    print(json.dumps(results, indent=2))
    print("\n========== LIFECYCLE SUMMARY ==========")
    checks = {
        "Pixi mounted + active":      results.get("pixi_mounted") and results.get("using_pixi"),
        "Lobby overlay + START shown": results.get("lobby_overlay_visible") and results.get("start_button_visible"),
        "START -> running (ants render)": results.get("running_ants_in_store", 0) > 0 and results.get("lobby_hidden_when_running"),
        "Info panel populated":        results.get("info_panel_pop", "—") not in ("—", "", None),
        "Clock advances (panel live)": results.get("clock_advances", [None, None, False])[2],
        "Legend present":              results.get("legend_present"),
        "Winner screen shown":         results.get("winner_overlay_visible") and results.get("end_winner") is not None,
    }
    for name, ok in checks.items():
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\npage errors: {len(results.get('page_errors', []))} {results.get('page_errors')}")
    print("ALL PASS" if all(checks.values()) else "SOME FAILED")


if __name__ == "__main__":
    main()
