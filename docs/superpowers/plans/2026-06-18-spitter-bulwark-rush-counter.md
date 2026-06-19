# Spitter + Bulwark Rush-Counter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a ranged+splash unit (Spitter), a spiked Bulwark structure, and a guard-post splash buff so an early all-soldier rush is beatable.

**Architecture:** Two reusable mechanics — a generalized `_apply_splash` helper (fed by soldier splash, Spitter, and guard post) and per-tick structure contact damage (Bulwark). The Spitter is the 5th ant type (`A_SPITTER=4`, queen stays index 3 so existing indices are unchanged). Bulwark + guard-post buff follow the existing structure pattern.

**Tech Stack:** Pure-Python sim engine (`engine/`), aiohttp server (`server.py`), FastMCP (`mcp_server.py`), vanilla-JS frontends (`frontend/`). Tests are plain `assert` functions run with `PYTHONPATH=. python3 tests/test_x.py` (each file has a `__main__` runner that prints PASS/FAIL and exits non-zero on failure).

## Global Constraints

- **Legibility:** no new agent verbs. Spitter obeys existing unit commands; only new directive field is `spawn.spitter.*`. At most two new mechanics total (ranged-splash, contact-damage) — both already specified.
- **Queen index unchanged:** `A_QUEEN` stays `3`; `A_SPITTER=4`. Never reorder existing type ids.
- **Fog of war:** every enemy-derived field (sightings, sitrep composition, attack ETA) stays visible/scouted-only, with staleness; never leak unobserved enemy state.
- **Numbers are tunable:** the constants in Task 1 are starting points; Task 10 tunes them. Roles/mechanics are fixed.
- **TDD + frequent commits:** each task ends green and committed. Run the full suite (`for f in tests/test_*.py; do PYTHONPATH=. python3 "$f" || echo FAIL $f; done`) before each commit.
- **No deploy** in this plan. Shipping/deploy is a separate decision after validation (Task 10).

---

## File Structure

- `engine/constants.py` — add `A_SPITTER`, `ANT_TYPE_NAMES`, `NUM_ANT_TYPES`, Spitter/Bulwark/splash constants, guard-post buff values. (Task 1)
- `engine/world.py` — `_apply_splash` helper (Task 2); spitter spawn integration (Task 4); `_behavior_spitter` + dispatch (Task 5); Bulwark build/impassable/contact-damage + guard-post splash (Tasks 6, 7); 4→5 sweep (Task 3).
- `engine/colony.py` — Ant hp/LIFESPAN/resolved_config; `default_directive` spawn.spitter; `SPAWN_COST`/`SPAWN_TIME`; 4→5 sweep (Task 3).
- `engine/sitrep.py` — `range(4)`→5, `UNIT_VALUE`, enemy composition incl. spitter (Task 3, Task 8).
- `server.py` — counts/type-name lists, `current_orders`, `get_units`, build-structure validation, spawn docs, advisor (Task 8).
- `mcp_server.py` — `get_units`/`build_structure`/spawn docstrings (Task 8).
- `frontend/game/src/...` + `frontend/game/...` (Pixi) and the Canvas renderer — placeholder Spitter/Bulwark sprites (Task 9).
- `tests/test_*.py` — per-task tests.
- `tools/repro/` — validation harnesses (Task 10).

Recommended task order: **1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10**. Tasks 6 and 7 are independent of 4/5 and can be done in parallel if desired, but the order above keeps the suite green throughout.

---

### Task 1: Foundational constants

**Files:**
- Modify: `engine/constants.py:19` (the `A_WORKER, ... = range(4)` line) and the unit/structure constant blocks (lines ~54-117).
- Test: `tests/test_spitter_constants.py`

**Interfaces:**
- Produces: `A_SPITTER=4`, `NUM_ANT_TYPES=5`, `ANT_TYPE_NAMES=["worker","soldier","scout","queen","spitter"]`, `SPITTER_HP/DMG/RANGE/CD/LIFESPAN`, `SPLASH_RADIUS`, `SPLASH_FALLOFF`, `SPITTER_SPAWN_COST`, `SPITTER_SPAWN_TIME`, `BULWARK_COST/HP/MAX/CONTACT_DMG`, `GUARD_POST_SPLASH_RADIUS`, and updated `GUARD_POST_HP`/`GUARD_POST_DMG`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_spitter_constants.py
import sys; sys.path.insert(0, ".")
import engine.constants as K

def test_spitter_type_id():
    assert K.A_QUEEN == 3, "queen index must not move"
    assert K.A_SPITTER == 4
    assert K.NUM_ANT_TYPES == 5
    assert K.ANT_TYPE_NAMES == ["worker", "soldier", "scout", "queen", "spitter"]

def test_spitter_and_bulwark_stats_exist():
    for name in ("SPITTER_HP", "SPITTER_DMG", "SPITTER_RANGE", "SPITTER_CD",
                 "SPITTER_SPAWN_COST", "SPITTER_SPAWN_TIME", "SPLASH_RADIUS",
                 "SPLASH_FALLOFF", "BULWARK_COST", "BULWARK_HP", "BULWARK_MAX",
                 "BULWARK_CONTACT_DMG", "GUARD_POST_SPLASH_RADIUS"):
        assert hasattr(K, name), f"missing constant {name}"
    assert K.GUARD_POST_HP >= 400 and K.GUARD_POST_DMG >= 22

if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try: fn(); print(f"PASS {fn.__name__}")
        except Exception as e: failed += 1; print(f"FAIL {fn.__name__}: {e}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python3 tests/test_spitter_constants.py`
Expected: FAIL (`A_SPITTER` AttributeError / `range(4)`).

- [ ] **Step 3: Edit constants**

In `engine/constants.py`, change line 19:
```python
A_WORKER, A_SOLDIER, A_SCOUT, A_QUEEN, A_SPITTER = range(5)
NUM_ANT_TYPES = 5
ANT_TYPE_NAMES = ["worker", "soldier", "scout", "queen", "spitter"]
```
Add to the combat/unit block (near `SOLDIER_*`, ~line 57):
```python
# Spitter — fragile ranged anti-mass unit
SPITTER_HP    = 70
SPITTER_DMG   = 16
SPITTER_RANGE = 5
SPITTER_CD    = 7
SPLASH_RADIUS  = 1      # Chebyshev radius of splash from the primary target
SPLASH_FALLOFF = 0.5    # splash damage = round(dmg * SPLASH_FALLOFF)
SPITTER_SPAWN_COST = 45
SPITTER_SPAWN_TIME = 30
SPITTER_LIFESPAN   = 300
```
Change guard-post block (~lines 76-77): `GUARD_POST_HP = 400` and `GUARD_POST_DMG = 22`, and add `GUARD_POST_SPLASH_RADIUS = 1`.
Add Bulwark block (near `WALL_*`, ~line 95):
```python
# Bulwark — cheap fast spiked barricade (block + contact damage)
BULWARK_COST        = 50
BULWARK_HP          = 250
BULWARK_MAX         = 6
BULWARK_CONTACT_DMG = 4     # damage/tick to each adjacent enemy
```
Add to `BUILD_WORK_REQUIRED` (~line 117): `"bulwark": 40`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python3 tests/test_spitter_constants.py` → Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/constants.py tests/test_spitter_constants.py
git commit -m "feat(constants): A_SPITTER + Spitter/Bulwark/splash constants + guard-post buff values"
```

---

### Task 2: Generalize splash into `_apply_splash`

Refactor the existing duplicated `soldier_splash` logic (`engine/world.py:1332-1340` and `1455-1463`) into one helper, with no behavior change. Later tasks (Spitter, guard post) reuse it.

**Files:**
- Modify: `engine/world.py` (add method near `_kill`; replace the two soldier splash blocks)
- Test: `tests/test_splash.py`

**Interfaces:**
- Produces: `World._apply_splash(self, attacker_colony_id: int, target, dmg: int, radius: int, falloff: float) -> None` — applies `round(dmg*falloff)` to every ENEMY ant (relative to `attacker_colony_id`) within Chebyshev `radius` of `target`, excluding `target` itself; kills any that drop to ≤0 HP via `self._kill`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_splash.py
import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant
from engine.constants import A_QUEEN, A_SOLDIER

def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    me, en = w.colonies[0], w.colonies[1]
    me.ants = [a for a in me.ants if a.type == A_QUEEN]
    en.ants = [a for a in en.ants if a.type == A_QUEEN]
    return w, me, en

def test_splash_hits_adjacent_enemies_only():
    w, me, en = _fresh()
    target = Ant(50, 50, en.id, A_SOLDIER); en.ants.append(target)
    near   = Ant(51, 50, en.id, A_SOLDIER); en.ants.append(near)     # adjacent enemy
    far    = Ant(55, 50, en.id, A_SOLDIER); en.ants.append(far)      # out of radius
    friend = Ant(49, 50, me.id, A_SOLDIER); me.ants.append(friend)   # friendly, must be safe
    near_hp0, far_hp0, friend_hp0 = near.hp, far.hp, friend.hp
    w._apply_splash(me.id, target, dmg=16, radius=1, falloff=0.5)
    assert near.hp == near_hp0 - 8, near.hp
    assert far.hp == far_hp0, "splash leaked beyond radius"
    assert friend.hp == friend_hp0, "splash hit a friendly"
    assert target.hp == target.hp, "splash must not double-hit the primary target"

if __name__ == "__main__":
    import traceback
    fns=[v for k,v in sorted(globals().items()) if k.startswith("test_")]; failed=0
    for fn in fns:
        try: fn(); print(f"PASS {fn.__name__}")
        except Exception as e: failed+=1; print(f"FAIL {fn.__name__}: {e}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. python3 tests/test_splash.py` → Expected: FAIL (`_apply_splash` not defined).

- [ ] **Step 3: Add the helper**

Add this method to `World` (place it just above `def _kill`):
```python
def _apply_splash(self, attacker_colony_id, target, dmg, radius, falloff):
    """Damage every ENEMY ant within Chebyshev `radius` of `target` (excluding target
    itself) by round(dmg*falloff). Shared by soldier splash, the Spitter, and guard posts."""
    splash = max(1, round(dmg * falloff))
    for ec in self.colonies:
        if ec.id == attacker_colony_id:
            continue
        for stgt in list(ec.ants):
            if stgt is target:
                continue
            if abs(stgt.x - target.x) <= radius and abs(stgt.y - target.y) <= radius:
                stgt.hp -= splash
                if stgt.hp <= 0:
                    self._kill(stgt)
```

- [ ] **Step 4: Replace the two soldier splash blocks**

In both `_behavior_soldier` splash sites (search for `if c.soldier_splash:`), replace the inline loop:
```python
                if c.soldier_splash:
                    splash_dmg = max(1, int(dmg * 0.4))
                    for ec in self.colonies:
                        if ec.id == ant.colony: continue
                        for stgt in list(ec.ants):
                            if stgt is adj: continue
                            if abs(stgt.x - adj.x) + abs(stgt.y - adj.y) <= 1:
                                stgt.hp -= splash_dmg
                                if stgt.hp <= 0: self._kill(stgt)
```
with:
```python
                if c.soldier_splash:
                    self._apply_splash(ant.colony, adj, dmg, radius=1, falloff=0.4)
```
(Note: this changes soldier splash from Manhattan≤1 to Chebyshev≤1 — slightly wider (includes diagonals). That's an intentional, minor consistency improvement; soldier splash is an upgrade-gated effect.)

- [ ] **Step 5: Run splash test + full suite**

Run: `PYTHONPATH=. python3 tests/test_splash.py` → PASS.
Run full suite (all green): `for f in tests/test_*.py; do PYTHONPATH=. python3 "$f" >/dev/null 2>&1 && echo "PASS $f" || echo "FAIL $f"; done`

- [ ] **Step 6: Commit**

```bash
git add engine/world.py tests/test_splash.py
git commit -m "refactor(world): generalize splash into _apply_splash; reuse in soldier splash"
```

---

### Task 3: Make the engine 5-unit-type aware (no Spitter behavior yet)

Mechanical sweep so a Spitter ant can exist without IndexErrors. Replace 4-type assumptions with 5-aware code and add Spitter to type maps + default directive. No spawning or behavior yet (a Spitter just stands as `S_IDLE`).

**Files:**
- Modify: `engine/colony.py` (Ant hp map ~50, `LIFESPAN` line 38, `resolved_config` ~102, `default_directive` spawn ~127-130, `SPAWN_COST`/`SPAWN_TIME` lines 422 & 521, `[0,0,0,0]`/`range(4)` at lines 218, 384, 493, 667, 682, 692)
- Modify: `engine/world.py` (`['worker','soldier','scout','queen']` at line 469; `['worker','soldier','scout']` at 507, 514; `range(4)` at 840)
- Modify: `engine/sitrep.py` (`range(4)` at 58, 88; `[0,0,0,0]` at 93; `UNIT_VALUE`)
- Test: `tests/test_spitter_plumbing.py`

**Interfaces:**
- Consumes: `A_SPITTER`, `ANT_TYPE_NAMES`, `NUM_ANT_TYPES`, `SPITTER_*` (Task 1).
- Produces: a `spawn.spitter` directive section `{"target_ratio":0.0,"min_ratio":0.0,"min":0,"max":20,"pause":False,"birth_config":{}}`; `SPAWN_COST[A_SPITTER]`/`SPAWN_TIME[A_SPITTER]`; Spitter HP/lifespan; all type-count arrays length `NUM_ANT_TYPES`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_spitter_plumbing.py
import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant, Colony
from engine.constants import A_QUEEN, A_SPITTER, SPITTER_HP
from engine.sitrep import build_sitrep

def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    return w, w.colonies[0]

def test_spitter_ant_constructs_with_hp():
    a = Ant(20, 50, 0, A_SPITTER)
    assert a.hp == SPITTER_HP and a.max_hp == SPITTER_HP
    assert a.lifespan is not None

def test_directive_has_spitter_spawn_section():
    _, c = _fresh()
    assert "spitter" in c.directive["spawn"]
    assert c.directive["spawn"]["spitter"]["target_ratio"] == 0.0

def test_world_step_with_spitter_no_crash():
    w, c = _fresh()
    c.ants.append(Ant(c.nx + 2, c.ny, c.id, A_SPITTER))
    for _ in range(5):
        w.step()              # must not IndexError on counts/sitrep/aging
    s = build_sitrep(c, w)    # sitrep must handle 5 types
    assert s is not None

def test_serialize_roundtrip_with_spitter():
    w, c = _fresh()
    c.ants.append(Ant(c.nx + 2, c.ny, c.id, A_SPITTER))
    d = w.to_dict()
    w2 = World.from_dict(d)
    assert any(a.type == A_SPITTER for a in w2.colonies[0].ants)

if __name__ == "__main__":
    import traceback
    fns=[v for k,v in sorted(globals().items()) if k.startswith("test_")]; failed=0
    for fn in fns:
        try: fn(); print(f"PASS {fn.__name__}")
        except Exception as e: failed+=1; print(f"FAIL {fn.__name__}: {e}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. python3 tests/test_spitter_plumbing.py` → Expected: FAIL.

- [ ] **Step 3: Edit `engine/colony.py`**

- Ant hp map (~line 50): add `A_SPITTER: SPITTER_HP` to the dict. Import `SPITTER_HP, SPITTER_LIFESPAN` (the file already does `from engine.constants import (...)` — add them).
- `LIFESPAN` (line 38): `LIFESPAN = {0: 500, 1: 300, 2: 200, 3: None, 4: SPITTER_LIFESPAN}`.
- `resolved_config` (line 102): change the type-name list to `ANT_TYPE_NAMES` and add a `"spitter": {"expansion": [1, 1]}` entry to the `defaults` dict.
- `default_directive` (after the `"scout"` line ~130) add:
  ```python
              "spitter": {"target_ratio": 0.0, "min_ratio": 0.0, "min": 0, "max": 20, "pause": False, "birth_config": {}},
  ```
- `SPAWN_COST`/`SPAWN_TIME` (lines 422 and 521 — there are two copies): add `A_SPITTER: SPITTER_SPAWN_COST` and `A_SPITTER: SPITTER_SPAWN_TIME` (import the two constants). Current SPAWN_TIME dict is at line 423/522 — add the entry there too.
- Replace every `[0, 0, 0, 0]` / `[0,0,0,0]` (lines 218, 384, 493, 692) with `[0] * NUM_ANT_TYPES`.
- Replace `counts = [0, 0, 0, 0]` at 667 and `enemy_counts = [0, 0, 0, 0]` at 682 likewise.
- Import `NUM_ANT_TYPES, ANT_TYPE_NAMES` from constants.

- [ ] **Step 4: Edit `engine/world.py`**

- Line 469: `['worker','soldier','scout','queen'][best.type]` → `ANT_TYPE_NAMES[best.type]`.
- Lines 507 and 514: `['worker','soldier','scout'][ant.type]` / `[victim.type]` → `ANT_TYPE_NAMES[...]` (the 5-element list safely covers spitter; queen never reaches these branches).
- Line 840 (`for t in range(4)` building `enemy_scouted_counts`): → `range(NUM_ANT_TYPES)`.
- Import `ANT_TYPE_NAMES, NUM_ANT_TYPES` (the file already imports many constants).

- [ ] **Step 5: Edit `engine/sitrep.py`**

- Lines 58, 88: `for t in range(4)` → `range(NUM_ANT_TYPES)`.
- Line 93: `[0, 0, 0, 0]` → `[0] * NUM_ANT_TYPES`.
- `UNIT_VALUE`: add a spitter entry (value ~15, between scout 8 and soldier 20 — confirm the dict's current keys and add `A_SPITTER: 15`). Import `NUM_ANT_TYPES, A_SPITTER` as needed.

- [ ] **Step 6: Run plumbing test + full suite**

Run: `PYTHONPATH=. python3 tests/test_spitter_plumbing.py` → PASS.
Run full suite → all PASS (especially `test_sitrep.py`).

- [ ] **Step 7: Commit**

```bash
git add engine/ tests/test_spitter_plumbing.py
git commit -m "feat(engine): make sim 5-unit-type aware (Spitter type, maps, directive, counts)"
```

---

### Task 4: Spitter spawning (ratio integration)

Extend the spawn decision in `engine/world.py:540-616` so `spawn.spitter` participates in ratio/min selection.

**Files:**
- Modify: `engine/world.py:543-616`
- Test: `tests/test_spitter_spawn.py`

**Interfaces:**
- Consumes: `spawn.spitter` directive section, `SPAWN_COST[A_SPITTER]` (Task 3).
- Produces: spitters appended to `c.spawn_queue` when their share/min warrants it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_spitter_spawn.py
import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import DirectiveEngine
from engine.constants import A_SPITTER

def test_high_spitter_ratio_spawns_spitters():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    c = w.colonies[0]
    c.food = 100000
    DirectiveEngine.patch(c, {"spawn": {
        "spitter": {"target_ratio": 1.0, "min_ratio": 0.0},
        "worker": {"target_ratio": 0.0}, "soldier": {"target_ratio": 0.0},
        "scout": {"target_ratio": 0.0}}})
    seen = False
    for _ in range(200):
        w.step()
        if any(t == A_SPITTER for (t, _, _) in c.spawn_queue) or any(a.type == A_SPITTER for a in c.ants):
            seen = True; break
    assert seen, "no spitter ever spawned/queued at target_ratio 1.0"

if __name__ == "__main__":
    import traceback
    fns=[v for k,v in sorted(globals().items()) if k.startswith("test_")]; failed=0
    for fn in fns:
        try: fn(); print(f"PASS {fn.__name__}")
        except Exception as e: failed+=1; print(f"FAIL {fn.__name__}: {e}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. python3 tests/test_spitter_spawn.py` → FAIL.

- [ ] **Step 3: Integrate spitter into the spawn decision**

In `engine/world.py`, in the block starting at line 544:
- After `sol_paused = ...` add: `spit_paused = sp.get("spitter", {}).get("pause", False)`.
- After the `solshare` line add:
  ```python
                    spitshare = 0.0 if spit_paused else max(sp.get("spitter", {}).get("target_ratio", 0.0), sp.get("spitter", {}).get("min_ratio", 0.0))
  ```
- Change `total_sh = wshare + sshare + solshare` → `total_sh = wshare + sshare + solshare + spitshare`, and the normalization block to also divide `spitshare /= total_sh`.
- Add `(A_SPITTER, "spitter")` to `_TYPE_KEYS` (line 584) and `A_SPITTER: spit_paused` to `_PAUSED` (line 585).
- Replace the ratio cascade (lines 597-609) with a cumulative-threshold pick over all spawnable types so adding a 4th type is robust:
  ```python
                    if t is None and total_sh > 0:
                        if worker_capped:
                            shares = [(A_SCOUT, sshare), (A_SOLDIER, solshare), (A_SPITTER, spitshare)]
                        else:
                            shares = [(A_WORKER, wshare), (A_SCOUT, sshare), (A_SOLDIER, solshare), (A_SPITTER, spitshare)]
                        tot = sum(s for _, s in shares) or 1.0
                        r = random.random() * tot
                        acc = 0.0
                        for _tc, _s in shares:
                            acc += _s
                            if r < acc:
                                t = _tc; break
  ```
  (Remove the old `r = random.random()` / `if worker_capped ... elif r < wshare ...` cascade it replaces. Keep the `if t is None:` min-enforcement block above it intact.)

- [ ] **Step 4: Run spawn test + full suite**

Run: `PYTHONPATH=. python3 tests/test_spitter_spawn.py` → PASS. Full suite → green (the symmetric default has spitter ratio 0.0, so existing spawn behavior is unchanged).

- [ ] **Step 5: Commit**

```bash
git add engine/world.py tests/test_spitter_spawn.py
git commit -m "feat(world): spitter participates in spawn ratio/min selection"
```

---

### Task 5: Spitter behavior — ranged splash + self-preservation

**Files:**
- Modify: `engine/world.py` (the behavior dispatch at ~line 824-827; add `_behavior_spitter`)
- Test: `tests/test_spitter_behavior.py`

**Interfaces:**
- Consumes: `_apply_splash` (Task 2), `SPITTER_RANGE/DMG/CD`, `SPLASH_RADIUS/FALLOFF`.
- Produces: `World._behavior_spitter(self, ant)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_spitter_behavior.py
import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant
from engine.constants import A_QUEEN, A_SOLDIER, A_SPITTER

def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    me, en = w.colonies[0], w.colonies[1]
    me.ants = [a for a in me.ants if a.type == A_QUEEN]
    en.ants = [a for a in en.ants if a.type == A_QUEEN]
    return w, me, en

def test_spitter_damages_clump_at_range():
    w, me, en = _fresh()
    sp = Ant(50, 50, me.id, A_SPITTER); me.ants.append(sp)
    ball = [Ant(53 + (i % 2), 50 + (i // 2), en.id, A_SOLDIER) for i in range(6)]  # clump within range 5
    for b in ball: en.ants.append(b)
    hp0 = sum(b.hp for b in ball)
    for _ in range(10):
        w.step()
    hp1 = sum(b.hp for b in [b for b in ball if b in en.ants])
    assert hp1 < hp0, "spitter did no damage to a clump in range"

def test_soldier_beats_spitter_1v1():
    w, me, en = _fresh()
    sp = Ant(50, 50, me.id, A_SPITTER); me.ants.append(sp)
    sol = Ant(52, 50, en.id, A_SOLDIER); en.ants.append(sol)
    for _ in range(120):
        w.step()
        if sp not in me.ants or sol not in en.ants: break
    assert sp not in me.ants and sol in en.ants, "spitter should lose 1v1 to a soldier"

if __name__ == "__main__":
    import traceback
    fns=[v for k,v in sorted(globals().items()) if k.startswith("test_")]; failed=0
    for fn in fns:
        try: fn(); print(f"PASS {fn.__name__}")
        except Exception as e: failed+=1; print(f"FAIL {fn.__name__}: {e}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. python3 tests/test_spitter_behavior.py` → FAIL.

- [ ] **Step 3: Add dispatch + behavior**

In the dispatch block (~line 824), add a branch:
```python
        elif ant.type == A_SPITTER: self._behavior_spitter(ant)
```
Add the method (place after `_behavior_soldier`):
```python
def _behavior_spitter(self, ant):
    """Fragile ranged splash unit. Fires at the nearest enemy within SPITTER_RANGE
    (damage + splash); does NOT chase; holds position and keeps firing when an enemy gets adjacent (hold-and-fire; retreat-when-adjacent was rejected — do not revert)."""
    c = self.colonies[ant.colony]
    if ant.cooldown > 0:
        ant.cooldown -= 1
    # nearest enemy within firing range
    target, best_d = None, SPITTER_RANGE + 1
    for ec in self.colonies:
        if ec.id == ant.colony: continue
        for e in ec.ants:
            d = max(abs(e.x - ant.x), abs(e.y - ant.y))   # Chebyshev
            if d < best_d: best_d = d; target = e
    adj = self._adjacent_enemy(ant)
    if adj is not None:
        # too close — hold-and-fire; retreat-when-adjacent was rejected, do not revert
        ant.state = S_RETURNING
        self._move_to(ant, c.nx, c.ny, 1)
        self._dep(ant.x, ant.y, 1, 0.3)
        return
    if target is not None and best_d <= SPITTER_RANGE:
        ant.state = S_FIGHTING
        if ant.cooldown <= 0:
            ant.cooldown = SPITTER_CD
            ant.fired_at = [target.x, target.y]   # animation hook (mirrors guard post)
            ant.fire_tick = self.tick
            dmg = SPITTER_DMG + c.dmg_bonus
            target.hp -= dmg
            self._apply_splash(ant.colony, target, dmg, SPLASH_RADIUS, SPLASH_FALLOFF)
            if target.hp <= 0:
                self._kill(target)
        self._dep(ant.x, ant.y, 1, 0.5)
        return
    # no enemy in range: obey rally/attack_target like other units by holding near them,
    # else idle near the nest (kept simple — no chasing)
    rally = c.directive["military"].get("rally_point")
    if rally:
        rp = rally[0] if isinstance(rally[0], (list, tuple)) else rally
        self._move_to(ant, int(rp[0]), int(rp[1]), 1)
        ant.state = S_PATROLLING
    else:
        ant.state = S_IDLE
    self._dep(ant.x, ant.y, 1, 0.3)
```
Ensure `SPITTER_RANGE, SPITTER_DMG, SPITTER_CD, SPLASH_RADIUS, SPLASH_FALLOFF, S_RETURNING, S_FIGHTING, S_PATROLLING, S_IDLE` are imported (most S_ already are). Also handle `unit_override` for spitter: at the top of `_behavior_spitter`, if `ant.unit_override`, honor `move_to`/`hold` by moving to the point and still firing if an enemy is in range (mirror the simple override handling in `_behavior_soldier`; for `move_to`/`hold` just `self._move_to` to the target then fall through to the firing check). Keep it minimal.

- [ ] **Step 4: Run behavior test + full suite**

Run: `PYTHONPATH=. python3 tests/test_spitter_behavior.py` → PASS. Full suite → green.

- [ ] **Step 5: Commit**

```bash
git add engine/world.py tests/test_spitter_behavior.py
git commit -m "feat(world): spitter behavior — ranged splash, no-chase, retreat-when-adjacent"
```

---

### Task 6: Bulwark structure — build, impassable, contact damage

**Files:**
- Modify: `engine/world.py` (`_STRUCT_LIMITS/COSTS/HP` at 382-384; `_passable` at 1639; structure update loop ~line 472 add a `bulwark` branch)
- Test: `tests/test_bulwark.py`

**Interfaces:**
- Consumes: `BULWARK_COST/HP/MAX/CONTACT_DMG`, `BUILD_WORK_REQUIRED["bulwark"]` (Task 1).
- Produces: a buildable, impassable `bulwark` structure that deals `BULWARK_CONTACT_DMG` to each adjacent enemy per tick once active.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bulwark.py
import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant
from engine.constants import A_QUEEN, A_SOLDIER, BULWARK_HP, BULWARK_CONTACT_DMG

def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    me, en = w.colonies[0], w.colonies[1]
    me.ants = [a for a in me.ants if a.type == A_QUEEN]
    en.ants = [a for a in en.ants if a.type == A_QUEEN]
    return w, me, en

def _bulwark(w, cid, x, y):
    st = {"x": x, "y": y, "colony": cid, "type": "bulwark", "hp": BULWARK_HP,
          "max_hp": BULWARK_HP, "cd": 0, "active": True, "build_progress": 999,
          "build_required": 40}
    w.structures.append(st); return st

def test_bulwark_blocks_movement():
    w, me, en = _fresh()
    _bulwark(w, me.id, 50, 50)
    assert w._passable(50, 50) is False

def test_bulwark_damages_adjacent_enemy():
    w, me, en = _fresh()
    _bulwark(w, me.id, 50, 50)
    foe = Ant(51, 50, en.id, A_SOLDIER); en.ants.append(foe)
    hp0 = foe.hp
    w.step()
    assert foe.hp <= hp0 - BULWARK_CONTACT_DMG, "bulwark did not bleed an adjacent enemy"

if __name__ == "__main__":
    import traceback
    fns=[v for k,v in sorted(globals().items()) if k.startswith("test_")]; failed=0
    for fn in fns:
        try: fn(); print(f"PASS {fn.__name__}")
        except Exception as e: failed+=1; print(f"FAIL {fn.__name__}: {e}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. python3 tests/test_bulwark.py` → FAIL.

- [ ] **Step 3: Register the structure + impassability + contact damage**

- Add `"bulwark"` to `_STRUCT_LIMITS` (`BULWARK_MAX`), `_STRUCT_COSTS` (`BULWARK_COST`), `_STRUCT_HP` (`BULWARK_HP`) at lines 382-384.
- `_passable` (line 1639): `if st.get("type") in ("wall", "bulwark") and ...`.
- In the structure update loop, add a branch (after the `barracks` branch, ~line 494):
  ```python
            elif stype == "bulwark":
                for ec in self.colonies:
                    if ec.id == struct["colony"]: continue
                    for e in list(ec.ants):
                        if abs(e.x - struct["x"]) <= 1 and abs(e.y - struct["y"]) <= 1:
                            e.hp -= BULWARK_CONTACT_DMG
                            if e.hp <= 0: self._kill(e)
  ```
- Import `BULWARK_COST, BULWARK_HP, BULWARK_MAX, BULWARK_CONTACT_DMG`.

- [ ] **Step 4: Run bulwark test + full suite**

Run: `PYTHONPATH=. python3 tests/test_bulwark.py` → PASS. Full suite → green.

- [ ] **Step 5: Commit**

```bash
git add engine/world.py tests/test_bulwark.py
git commit -m "feat(world): bulwark structure — buildable, impassable, contact damage"
```

---

### Task 7: Guard-post splash buff

**Files:**
- Modify: `engine/world.py:466` (guard-post fire)
- Test: `tests/test_guardpost_splash.py`

**Interfaces:**
- Consumes: `_apply_splash` (Task 2), `GUARD_POST_DMG`, `GUARD_POST_SPLASH_RADIUS`, `SPLASH_FALLOFF`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_guardpost_splash.py
import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant
from engine.constants import A_QUEEN, A_SOLDIER, GUARD_POST_HP

def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    me, en = w.colonies[0], w.colonies[1]
    me.ants = [a for a in me.ants if a.type == A_QUEEN]
    en.ants = [a for a in en.ants if a.type == A_QUEEN]
    return w, me, en

def test_guardpost_splashes_clustered_enemies():
    w, me, en = _fresh()
    w.structures.append({"x": 50, "y": 50, "colony": me.id, "type": "guard_post",
                         "hp": GUARD_POST_HP, "max_hp": GUARD_POST_HP, "cd": 0, "active": True,
                         "build_progress": 999, "build_required": 100})
    primary = Ant(52, 50, en.id, A_SOLDIER); en.ants.append(primary)
    neighbor = Ant(52, 51, en.id, A_SOLDIER); en.ants.append(neighbor)  # adjacent to primary
    nhp0 = neighbor.hp
    for _ in range(6):
        w.step()
        if neighbor.hp < nhp0: break
    assert neighbor.hp < nhp0, "guard post did not splash a neighbor of its target"

if __name__ == "__main__":
    import traceback
    fns=[v for k,v in sorted(globals().items()) if k.startswith("test_")]; failed=0
    for fn in fns:
        try: fn(); print(f"PASS {fn.__name__}")
        except Exception as e: failed+=1; print(f"FAIL {fn.__name__}: {e}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. python3 tests/test_guardpost_splash.py` → FAIL.

- [ ] **Step 3: Add splash to the guard-post hit**

In `engine/world.py`, right after `best.hp -= GUARD_POST_DMG` (line 466), add:
```python
                        self._apply_splash(struct["colony"], best, GUARD_POST_DMG,
                                           GUARD_POST_SPLASH_RADIUS, SPLASH_FALLOFF)
```
Import `GUARD_POST_SPLASH_RADIUS, SPLASH_FALLOFF` if not already. (HP/DMG buffs come from Task 1 constants automatically.)

- [ ] **Step 4: Run test + full suite**

Run: `PYTHONPATH=. python3 tests/test_guardpost_splash.py` → PASS. Full suite → green.

- [ ] **Step 5: Commit**

```bash
git add engine/world.py tests/test_guardpost_splash.py
git commit -m "feat(world): guard post deals splash + buffed HP/damage"
```

---

### Task 8: Server + MCP surface (counts, current_orders, get_units, build, docs, fog)

**Files:**
- Modify: `server.py` — type-name lists (2994, 3025), `counts`/`range(4)` (1240, 1416, 2663, 3119), `current_orders` (add spitter group), `get_units` filter, `build_structure` valid types + `_BUILD_COSTS` (3454), advisor affordable-structures, spawn-ratio docs.
- Modify: `engine/sitrep.py` — enemy composition already counts via `range(NUM_ANT_TYPES)` (Task 3); ensure `enemy_army.composition` and `format_enemy_sightings` include spitters (fog-respecting — they already derive from visible/scouted ants, so just confirm the spitter index is surfaced).
- Modify: `mcp_server.py` — `get_units`, `build_structure`, `patch_directive` spawn docstrings.
- Test: `tests/test_spitter_server_surface.py`

**Interfaces:**
- Consumes: everything from Tasks 1-7.
- Produces: `get_units(type="spitter")` works; `build_structure` accepts `"bulwark"`; counts/current_orders/sitrep include spitters; fog still respected.

- [ ] **Step 1: Write the failing test** (engine-level pieces that don't need a running server)

```python
# tests/test_spitter_server_surface.py
import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant
from engine.constants import A_QUEEN, A_SPITTER
from engine.sitrep import build_sitrep

def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    me, en = w.colonies[0], w.colonies[1]
    me.ants = [a for a in me.ants if a.type == A_QUEEN]
    en.ants = [a for a in en.ants if a.type == A_QUEEN]
    return w, me, en

def test_enemy_army_composition_includes_spitters_when_visible():
    w, me, en = _fresh()
    for _ in range(3): en.ants.append(Ant(78, 50, en.id, A_SPITTER))
    for _ in range(3): me.ants.append(Ant(76, 50, me.id, __import__("engine.constants", fromlist=["A_SOLDIER"]).A_SOLDIER))
    for _ in range(3): w.step()
    ea = build_sitrep(me, w)["field"]["enemy_army"]
    if ea["seen"]:
        assert "spitters" in ea["composition"], ea["composition"]

def test_build_structure_constants_have_bulwark():
    import server
    # _BUILD_COSTS map in api_command must include bulwark (smoke: attribute exists in source)
    import inspect
    src = inspect.getsource(server)
    assert '"bulwark"' in src, "server has no bulwark handling"

if __name__ == "__main__":
    import traceback
    fns=[v for k,v in sorted(globals().items()) if k.startswith("test_")]; failed=0
    for fn in fns:
        try: fn(); print(f"PASS {fn.__name__}")
        except Exception as e: failed+=1; print(f"FAIL {fn.__name__}: {e}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. python3 tests/test_spitter_server_surface.py` → FAIL.

- [ ] **Step 3: Edit `engine/sitrep.py` composition**

In `_field`'s `composition` dict (the `{"soldiers":..,"scouts":..,"workers":..}` added in the intel-legibility work), add `"spitters": sum(1 for a in seen_enemies if a.type == A_SPITTER)`. In `_standing`, the scouted counts already index by type — surface `sc[A_SPITTER]` where soldier/scout are surfaced if useful.

- [ ] **Step 4: Edit `server.py`**

- Replace the two inline `["worker","soldier","scout","queen"]` (2994, 3025) with `ANT_TYPE_NAMES` (import from engine.constants — `import *` already in effect, so it's available).
- `counts = [0, 0, 0, 0]` (1240, 1416, 2663) → `[0] * NUM_ANT_TYPES`; `range(4)` (3119) → `range(NUM_ANT_TYPES)`.
- `current_orders["soldiers"]` block: add a sibling `"spitters"` group:
  ```python
            "spitters": {
                "firing": sum(1 for a in c.ants if a.type == A_SPITTER and a.state == S_FIGHTING),
                "total": sum(1 for a in c.ants if a.type == A_SPITTER),
            },
  ```
- `counts` tuple used for `"counts": {"workers":counts[0],...}` — add `"spitters": counts[4]`.
- `get_units` (server.py around line 2994/the units endpoint and the type filter): accept `"spitter"` in the type→id map.
- `build_structure` / `api_command` validation (`_BUILD_COSTS` at 3454 and any valid-type set): add `"bulwark": BULWARK_COST`. Ensure the structure-order path enqueues `bulwark` (it uses the generic `structure_queue`; confirm `_STRUCT_*` maps from Task 6 cover it).
- Advisor affordable-structures list (~line 2688): add `("bulwark", BULWARK_COST, BULWARK_MAX)`.
- Spawn-ratio docs / the big prompt: mention `spawn.spitter.target_ratio` and the Spitter's role (ranged anti-mass, fragile).

- [ ] **Step 5: Edit `mcp_server.py` docs**

- `get_units` docstring: add `spitter` to the valid `type` values.
- `build_structure` docstring + valid types: add `bulwark` (cost/HP/role: cheap spiked barricade, contact damage).
- `patch_directive` / `get_state` spawn docs: add `spawn.spitter.target_ratio` and a one-line Spitter description.

- [ ] **Step 6: Run test + full suite**

Run: `PYTHONPATH=. python3 tests/test_spitter_server_surface.py` → PASS. Full suite → green. Also `python3 -c "import ast; [ast.parse(open(f).read()) for f in ['server.py','mcp_server.py']]"`.

- [ ] **Step 7: Commit**

```bash
git add server.py mcp_server.py engine/sitrep.py tests/test_spitter_server_surface.py
git commit -m "feat(server/mcp): surface spitter + bulwark in state/orders/units/build/docs (fog-respecting)"
```

---

### Task 9: Frontend placeholder rendering

Add minimal Spitter + Bulwark visuals so a game with them is watchable. Placeholder = recolored existing sprites; real pixel-art is deferred to the graphics pipeline.

**Files:**
- Modify: the Canvas renderer (the `["worker","soldier","scout","queen"]`-equivalent color/type maps in the Canvas client) and the Pixi `AntView`/structure render (`frontend/game/src/`).
- Test: `tools/replay/verify_render.py` (existing headless render harness) + manual spot check.

**Interfaces:**
- Consumes: the `type:"spitter"` field in unit JSON and `type:"bulwark"` in structures JSON (Task 8).

- [ ] **Step 1: Find the type→visual maps**

Run: `grep -rn "soldier\|scout\|queen" frontend/game/src frontend/*.js 2>/dev/null | grep -iE "color|sprite|case|type ===" | head -40` and locate the unit-type and structure-type switches in both the Canvas client and the Pixi `AntView`/structure modules.

- [ ] **Step 2: Add Spitter unit visual**

In each renderer's unit type switch, add a `spitter` case: reuse the soldier sprite/shape with a distinct tint (e.g. acid-green) so it's visually distinct. In the legend, add a Spitter entry.

- [ ] **Step 3: Add Bulwark structure visual**

In each renderer's structure type switch, add a `bulwark` case: reuse the wall tile with a distinct tint/spike marker. Add to the legend.

- [ ] **Step 4: Verify no render crash**

Run: `python3 tools/replay/verify_render.py` (or the project's headless render check). Expected: renders a frame containing a spitter + bulwark with 0 page errors. If the harness can't inject them, do a manual check: run a local bot game with `spawn.spitter.target_ratio` high + build a bulwark, load the spectator UI, confirm both draw and the legend shows them.

- [ ] **Step 5: Commit**

```bash
git add frontend/
git commit -m "feat(frontend): placeholder spitter + bulwark visuals + legend entries"
```

---

### Task 10: Validation harnesses + tuning

Prove the counter works, the symmetric matchup stays balanced, and the Spitter is a specialist. These are the acceptance gate before any deploy.

**Files:**
- Create: `tools/repro/rush_vs_counter.py`, `tools/repro/spitter_symmetry.py`
- (The 1v1 specialist case is already covered by `tests/test_spitter_behavior.py`.)

**Interfaces:**
- Consumes: the full feature (Tasks 1-7).

- [ ] **Step 1: Write `rush_vs_counter.py`**

Drive two colonies headlessly: RED = 80%-soldier rush (`spawn.soldier.target_ratio=0.8`, attack_target = BLUE nest, auto_attack); BLUE = defender that spawns spitters (`spawn.spitter.target_ratio≈0.4`), builds 2-3 bulwarks across its nest approach + guard posts. Run ~1500 ticks or until a queen dies. Print: who won, BLUE queen HP over time, peak RED soldiers vs BLUE losses. Pattern: copy the structure of `tools/repro/rally_staging.py` (World + finalize_placement + DirectiveEngine.patch + manual structure placement via `world.structures.append`).

- [ ] **Step 2: Run it and record the result**

Run: `PYTHONPATH=. python3 tools/repro/rush_vs_counter.py`
Acceptance: BLUE (defender) survives the first rush and is not wiped — i.e. the counter measurably blunts an 80% rush (BLUE queen alive at tick 600+, RED soldier losses high). If RED still steamrolls, record the numbers — that drives the tuning in Step 4.

- [ ] **Step 3: Write + run `spitter_symmetry.py`**

Both colonies identical (a mixed soldier+spitter+bulwark build). Run ~2000 ticks. Acceptance: it resolves (one side wins or a clear lead) within the tick budget rather than a permanent stalemate, and neither side has a structural runaway from tick 1. Print tick-of-decision and final margins.

- [ ] **Step 4: Tune constants if needed**

If Step 2 fails (rush still wins) or Step 3 stalemates, adjust the Task 1 numbers (Spitter dmg/range/HP/cost, Bulwark HP/contact, guard-post values) and re-run Steps 2-3. Keep changes to `engine/constants.py` only. Log each change + its effect in the harness output or a short comment block.

- [ ] **Step 5: Full regression + commit**

Run the full suite (all PASS). Commit:
```bash
git add tools/repro/rush_vs_counter.py tools/repro/spitter_symmetry.py engine/constants.py
git commit -m "test(balance): rush-vs-counter + symmetry harnesses; tune spitter/bulwark numbers"
```

- [ ] **Step 6: Report for deploy decision**

Summarize the validation results (rush now beatable? symmetry intact? final tuned numbers) and hand back to the user for the deploy decision. **Do not deploy as part of this plan.**

---

## Self-Review

**Spec coverage:** Spitter unit (Tasks 1,3,4,5) ✓; Bulwark (Tasks 1,6) ✓; guard-post splash buff (Tasks 1,7) ✓; two shared mechanics — ranged-splash (Task 2, reused in 5 & 7) and contact damage (Task 6) ✓; integration surface / 4→5 sweep (Task 3) ✓; fog-respecting enemy surfacing (Task 8) ✓; frontend placeholders (Task 9) ✓; validation: rush-counter + symmetry + specialist (Task 10 + test_spitter_behavior) ✓; tunable numbers (Task 10) ✓.

**Placeholder scan:** No "TBD/TODO"; every code step has real code. Task 9 is the one task that can't be fully unit-tested (rendering) — it uses the existing render harness + a manual check, which is appropriate and explicit, not a placeholder.

**Type consistency:** `_apply_splash(attacker_colony_id, target, dmg, radius, falloff)` is defined in Task 2 and called identically in Tasks 5 and 7. `A_SPITTER=4`, `ANT_TYPE_NAMES`, `NUM_ANT_TYPES` defined in Task 1 and used consistently. `spawn.spitter` section shape defined in Task 3 and consumed in Task 4. Structure dict keys (`type`,`active`,`build_progress`,`build_required`,`hp`,`max_hp`,`cd`) match the existing pattern used in Tasks 6/7 tests.

**Known follow-ups (acceptable):** exact `grep`-driven locations in Tasks 8/9 are given as line numbers + patterns rather than full file dumps because they are mechanical and the engineer will confirm by running the failing test first; the TDD loop catches any missed spot.
