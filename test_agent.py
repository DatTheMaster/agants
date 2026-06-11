"""Session-37 fix verification. Requires game already running."""
import sys, json, time, requests

BASE = "http://localhost:8083"
COLONY = 0
TOKEN = "43f31473-3bd3-4594-bbbb-cf8130af1a24"
MID = "1855da67"

def state():
    r = requests.get(f"{BASE}/api/matches/{MID}/state/{COLONY}",
                     headers={"Authorization": f"Bearer {TOKEN}"})
    return r.json()

def directive(patch):
    r = requests.post(f"{BASE}/api/directive/{COLONY}",
                      headers={"Authorization": f"Bearer {TOKEN}"},
                      json=patch)
    return r.status_code, r.json()

def command(body):
    r = requests.post(f"{BASE}/api/command/{COLONY}",
                      headers={"Authorization": f"Bearer {TOKEN}"},
                      json=body)
    return r.status_code, r.json()

def snap(s):
    tiers = s.get("tiers", {})
    eco = s.get("directive", {}).get("economy", {})
    units = s.get("units", [])
    workers = [u for u in units if u["type"] == "worker"]
    return (f"tick={s.get('tick')} food={int(s.get('food',0))} "
            f"tiers=W{tiers.get('worker',0)}/Sc{tiers.get('scout',0)}/Sol{tiers.get('soldier',0)} "
            f"workers={len(workers)} reserve={eco.get('upgrade_reserve',{})}")

# ─── check game is live ───────────────────────────────────────────────────────
s = state()
print(f"Game state: {snap(s)}")
if s.get("phase") != "running":
    print("ERROR: game not running"); sys.exit(1)

# ─── TEST 1: upgrade_reserve auto-clear ───────────────────────────────────────
print("\n══ TEST 1: upgrade_reserve auto-clear ══")

# Disable auto_upgrade so we control timing; set scout reserve
code, data = directive({"economy": {"auto_upgrade": False, "upgrade_reserve": {"scout": 400}}})
print(f"  set auto_upgrade=False + reserve{{scout:400}}: {code}")

time.sleep(0.5)
s = state()
print(f"  after patch: {snap(s)}")

# Wait until food ≥ 400 (scout upgrade costs ~300-400)
print("  Waiting for food ≥ 400 to buy scout upgrade...")
for i in range(120):
    time.sleep(1.0)
    s = state()
    f = int(s.get("food", 0))
    if i % 10 == 0:
        print(f"    {snap(s)}")
    if f >= 400:
        break
else:
    print("  (didn't reach 400♦ — buying anyway for the test)")

# Buy scout upgrade
code, data = command({"type": "buy_upgrade", "unit": "scout"})
print(f"  buy_upgrade(scout): {code} → {data}")

# Watch for scout tier to increment
print("  Watching for scout upgrade to fire...")
pre = s.get("tiers", {}).get("scout", 0)
for i in range(60):
    time.sleep(0.5)
    s = state()
    tiers = s.get("tiers", {})
    sc_tier = tiers.get("scout", 0)
    ur = s.get("directive", {}).get("economy", {}).get("upgrade_reserve", {})
    if sc_tier > pre:
        print(f"  ✓ Scout upgraded T{pre}→T{sc_tier} at tick {s.get('tick')}!")
        if "scout" not in ur:
            print(f"  ✓ upgrade_reserve auto-cleared → {ur}")
        else:
            print(f"  ✗ BUG: 'scout' still in upgrade_reserve: {ur}")
        break
    if i % 10 == 0:
        print(f"    {snap(s)}")
else:
    print(f"  (upgrade didn't fire — current state: {snap(s)})")

# Re-enable auto_upgrade
directive({"economy": {"auto_upgrade": True}})

# ─── TEST 2: dirt gather fix ──────────────────────────────────────────────────
print("\n══ TEST 2: dirt gather fix ══")

# Enable dirt gathering
code, _ = directive({"economy": {"gather_dirt": True}})
print(f"  enable gather_dirt: {code}")

s = state()
# Look for dirt node in viable_dirt_nodes
dirt_nodes = s.get("viable_dirt_nodes", [])
if not dirt_nodes:
    # Fall back to known game coords
    print("  No viable_dirt_nodes in state — using known coords (22,50)")
    dx, dy = 22, 50
else:
    dn = dirt_nodes[0]
    pos = dn.get("pos", [22, 50])
    dx, dy = pos[0], pos[1]
    print(f"  Found dirt node at ({dx},{dy}) amt={dn.get('amt')}")

# Pick a free (not-carrying) worker
units = s.get("units", [])
worker = next((u for u in units if u["type"] == "worker"
               and not u.get("carrying") and u.get("state") != "returning"), None)
if not worker:
    worker = next((u for u in units if u["type"] == "worker"), None)

if not worker:
    print("  No workers found — cannot test")
else:
    wid = worker["id"]
    print(f"  Worker {wid}: pos=({worker['x']},{worker['y']}) "
          f"state={worker['state']} carrying={worker.get('carrying')}")

    code, data = command({"type": "unit_command", "ant_id": wid, "command": "gather", "x": dx, "y": dy})
    print(f"  unit_command gather({dx},{dy}): {code} → {data}")

    prev_dirt = int(s.get("dirt", 0))
    print(f"  Colony dirt baseline: {prev_dirt}")
    print(f"  Watching for {max(60, abs(worker['x']-dx)+abs(worker['y']-dy)//2)} ticks...")

    idle_streak = 0
    dirt_picked = False
    for i in range(90):
        time.sleep(1.0)
        s = state()
        units = s.get("units", [])
        w = next((u for u in units if u["id"] == wid), None)
        colony_dirt = int(s.get("dirt", 0))

        if w:
            wpos = (w["x"], w["y"])
            wst = w["state"]
            carrying = w.get("carrying")
            ctype = w.get("carrying_type", "food")
            dist = abs(wpos[0]-dx) + abs(wpos[1]-dy)

            print(f"    t+{i+1}s tick={s.get('tick')}: state={wst} carrying={carrying}({ctype}) "
                  f"pos={wpos} dist_to_node={dist} colony_dirt={colony_dirt}")

            # Detect the cycle bug: idle repeatedly at/near target
            if wst == "idle" and dist <= 5:
                idle_streak += 1
                if idle_streak >= 3:
                    print(f"  ✗ BUG: worker cycling idle near dirt node (streak={idle_streak})")
                    break
            else:
                idle_streak = 0

            # Detect success
            if carrying and ctype == "dirt":
                print(f"  ✓ Worker carrying dirt! Fix confirmed (dist_to_node={dist})")
                dirt_picked = True
                break

            if colony_dirt > prev_dirt:
                print(f"  ✓ Colony dirt increased {prev_dirt}→{colony_dirt}! Fix confirmed.")
                prev_dirt = colony_dirt
                dirt_picked = True
        else:
            print(f"    t+{i+1}s: worker {wid} gone (died?)")
            break

    if not dirt_picked and idle_streak < 3:
        print(f"  (observation ended — worker may still be en route; check manually)")

print(f"\nMatch: {MID}  |  http://localhost:8083/game\n")
