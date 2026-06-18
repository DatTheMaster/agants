# Agent Situation Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a server-side, fog-respecting `sitrep` block to every agent's `get_state` so agents can read their true situation and whether their own orders are taking effect — instrument, not coach.

**Architecture:** A new pure module `engine/sitrep.py` builds the `sitrep` dict from a `Colony` + `World`. The engine tracks each colony's soldier center-of-mass over a 40-tick ring (so "advancing vs static" is computable). `server._build_colony_state` attaches `sitrep` to the agent state and its `advisor` hints are converted from imperatives to facts. The controller renders the sitrep and drops the two controller-only behaviors added earlier this session (parity).

**Tech Stack:** Python 3 (engine + aiohttp server), plain-python assertion tests run with `python3 tests/test_sitrep.py`. No pytest in this repo — match the existing ad-hoc test style (`sys.path.insert(0, ".")`, construct `World`, assert).

## Global Constraints

- **Parity:** the sitrep is computed server-side and is identical for every agent (MCP / SDK / the DatTheMaster controller). No client may hold logic or info another lacks.
- **Fog-of-war preserved:** a colony's own state = full truth; everything enemy-derived is scouted-only via `colony.enemy_scouted_counts` (`[worker, soldier, scout, queen]` counts) + `colony.enemy_scouted_tick`, with explicit staleness. Enemy food/income is `not_observable`. Never reveal unscouted enemy state.
- **Instrument, not coach:** facts only. No "do X" strings anywhere in `sitrep` or the revised `advisor`.
- **Reuse existing values:** unit value dict is `{A_WORKER: 5, A_SOLDIER: 20, A_SCOUT: 8}` (matches `server.py` `_val`). Lanes on The Crossing (150×100): north `y < 34`, center `34 ≤ y ≤ 66`, south `y > 66`.
- **Confirmed engine fields:** `Colony` has `id, nx, ny, ants, food, income_per_s, food_collected, enemy, enemy_scouted_counts, enemy_scouted_tick, directive`. `Ant` has `x, y, type, hp, state`. `World` has `tick, colonies, structures, territory` (bytearray, per-tile owner = colony id or 255), `MAP_W`/`MAP_H` from `engine.constants`. Type consts in `engine.constants`: `A_WORKER=0, A_SOLDIER=1, A_SCOUT=2, A_QUEEN=3`.

---

### Task 1: Track soldier center-of-mass history per colony

**Files:**
- Modify: `engine/colony.py` (in `__init__`, near `self._idle_workers_since = None`)
- Modify: `engine/world.py` (add `_track_army_com`, call it from `step()` near `self._check_idle_workers()`)
- Test: `tests/test_sitrep.py` (create)

**Interfaces:**
- Produces: `colony._army_com_history` — a `collections.deque(maxlen=40)` of `(tick, com_x, com_y)` tuples for that colony's soldiers (empty entry skipped when no soldiers). `World._track_army_com()` appends one entry per colony per tick.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sitrep.py`:
```python
import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant
from engine.constants import A_SOLDIER, A_QUEEN

def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    me, enemy = w.colonies[0], w.colonies[1]
    me.ants = [a for a in me.ants if a.type == A_QUEEN]
    enemy.ants = [a for a in enemy.ants if a.type == A_QUEEN]
    return w, me, enemy

def test_com_history_tracks_soldiers():
    w, me, _ = _fresh()
    for i in range(5):
        me.ants.append(Ant(40 + i, 50, 0, A_SOLDIER, born_tick=0))
    w.step()
    assert len(me._army_com_history) >= 1, "com history not populated"
    tick, cx, cy = me._army_com_history[-1]
    assert 40 <= cx <= 45, f"com_x {cx} not the soldier mean"

if __name__ == "__main__":
    test_com_history_tracks_soldiers()
    print("Task 1 PASS")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_sitrep.py`
Expected: FAIL — `AttributeError: 'Colony' object has no attribute '_army_com_history'`

- [ ] **Step 3: Add the history field in `engine/colony.py`**

In `Colony.__init__`, immediately after the line `self._idle_workers_since = None`, add:
```python
        from collections import deque
        self._army_com_history = deque(maxlen=40)   # (tick, com_x, com_y) of own soldiers; for order-effect
```

- [ ] **Step 4: Add tracking in `engine/world.py`**

Add this method to `World` (next to `_check_idle_workers`):
```python
    def _track_army_com(self):
        """Record each colony's soldier center-of-mass this tick (for sitrep order-effect)."""
        for c in self.colonies:
            xs = [a.x for a in c.ants if a.type == A_SOLDIER]
            ys = [a.y for a in c.ants if a.type == A_SOLDIER]
            if xs:
                c._army_com_history.append((self.tick, sum(xs) / len(xs), sum(ys) / len(ys)))
```
And call it in `step()` — add right after the existing `self._check_idle_workers()` line:
```python
        self._track_army_com()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 tests/test_sitrep.py`
Expected: `Task 1 PASS`

- [ ] **Step 6: Commit**

```bash
git add engine/colony.py engine/world.py tests/test_sitrep.py
git commit -m "engine: track per-colony soldier center-of-mass history (sitrep groundwork)"
```

---

### Task 2: `engine/sitrep.py` — `orders` section (order-effect)

**Files:**
- Create: `engine/sitrep.py`
- Test: `tests/test_sitrep.py` (append)

**Interfaces:**
- Consumes: `colony._army_com_history` (Task 1); `colony.directive["military"]` (`attack_target`, `rally_point`, `rally_release_at`, `auto_attack`), `colony.directive["spawn"]`.
- Produces: `build_sitrep(colony, world) -> dict` with key `"orders"` → list of `{"intent": str, "status": str, "detail": str, ...}`. (Other keys `standing`/`field` added in Tasks 3–4.) `_orders(colony, world) -> list`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sitrep.py`:
```python
from engine.sitrep import build_sitrep

def test_orders_attack_no_effect_when_frozen():
    w, me, _ = _fresh()
    for i in range(12):
        me.ants.append(Ant(48 + (i % 3) - 1, 50, 0, A_SOLDIER, born_tick=0))
    me.directive["military"].update({
        "attack_target": [136, 50], "rally_point": [48, 50],
        "rally_release_at": None, "auto_attack": True})
    for _ in range(45):                       # frozen at rally (no-release rally still set)
        # pin them in place to simulate a stuck army
        for a in me.ants:
            if a.type == A_SOLDIER: a.x, a.y = 48, 50
        w.step()
    orders = build_sitrep(me, w)["orders"]
    atk = next(o for o in orders if o["intent"] == "attack_target")
    assert atk["status"] == "no_effect", f"expected no_effect, got {atk}"

def test_orders_attack_advancing():
    w, me, _ = _fresh()
    sol = [Ant(40, 50, 0, A_SOLDIER, born_tick=0) for _ in range(6)]
    me.ants += sol
    me.directive["military"].update({"attack_target": [136, 50], "rally_point": None,
                                      "rally_release_at": None, "auto_attack": True})
    for t in range(45):
        for a in sol: a.x = min(135, a.x + 1)   # march east toward target
        w.step()
    orders = build_sitrep(me, w)["orders"]
    atk = next(o for o in orders if o["intent"] == "attack_target")
    assert atk["status"] == "advancing", f"expected advancing, got {atk}"

if __name__ == "__main__":
    test_com_history_tracks_soldiers()
    test_orders_attack_no_effect_when_frozen()
    test_orders_attack_advancing()
    print("Task 2 PASS")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_sitrep.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.sitrep'`

- [ ] **Step 3: Create `engine/sitrep.py`**

```python
"""Builds the agent Situation Report (sitrep) — an honest, fog-respecting, factual read
of a colony's situation and the effect of its own orders. Instrument, not coach: facts
only, never prescriptions. See docs/superpowers/specs/2026-06-18-agent-situation-report-design.md
"""
from engine.constants import A_WORKER, A_SOLDIER, A_SCOUT, A_QUEEN

UNIT_VALUE = {A_WORKER: 5, A_SOLDIER: 20, A_SCOUT: 8}
ADVANCE_EPS = 3        # tiles of net COM movement over the window below which an attack is "no_effect"


def _orders(colony, world):
    mil = colony.directive["military"]
    out = []

    # attack_target / auto_attack — is the army advancing toward the objective?
    target = mil.get("attack_target")
    if not target and mil.get("auto_attack") and colony.enemy:
        target = [colony.enemy.nx, colony.enemy.ny]
    if target:
        hist = colony._army_com_history
        soldiers_in_siege = sum(
            1 for a in colony.ants if a.type == A_SOLDIER and colony.enemy
            and abs(a.x - colony.enemy.nx) + abs(a.y - colony.enemy.ny) <= 12)
        if soldiers_in_siege > 0:
            status, detail = "engaged", f"{soldiers_in_siege} soldiers in siege range of the objective"
        elif len(hist) >= 2:
            (_, x_old, _), (_, x_now, _) = hist[0], hist[-1]
            moved = x_now - x_old
            toward = target[0] - x_now
            progressing = (moved > 0 and toward > 0) or (moved < 0 and toward < 0)
            if progressing and abs(moved) >= ADVANCE_EPS:
                status, detail = "advancing", f"army center-of-mass x {x_old:.0f}->{x_now:.0f} toward {target}"
            else:
                status = "no_effect"
                detail = (f"army center-of-mass x {x_old:.0f}->{x_now:.0f} over last {len(hist)}t "
                          f"(not advancing toward {target})")
                rp = mil.get("rally_point")
                if rp and not mil.get("rally_release_at"):
                    detail += " — a rally_point with no rally_release_at is holding the army"
        else:
            status, detail = "unknown", "no soldiers yet"
        out.append({"intent": "attack_target", "target": list(target), "status": status, "detail": detail})

    # rally — staged vs release
    rp = mil.get("rally_point")
    if rp:
        pt = rp[0] if isinstance(rp[0], (list, tuple)) else rp
        rx, ry = int(pt[0]), int(pt[1])
        staged = sum(1 for a in colony.ants if a.type == A_SOLDIER
                     and abs(a.x - rx) + abs(a.y - ry) <= 4)
        release = mil.get("rally_release_at")
        if not release:
            status, detail = "holding", f"{staged} staged at ({rx},{ry}); no rally_release_at set (holds indefinitely)"
        elif staged >= release:
            status, detail = "released", f"{staged}/{release} staged — release threshold met"
        else:
            status, detail = "filling", f"{staged}/{release} staged at ({rx},{ry})"
        out.append({"intent": "rally", "point": [rx, ry], "status": status, "detail": detail})

    # spawn — target vs actual ratios
    counts = [sum(1 for a in colony.ants if a.type == t) for t in range(4)]
    total = counts[A_WORKER] + counts[A_SOLDIER] + counts[A_SCOUT]
    if total > 0:
        spawn = colony.directive.get("spawn", {})
        parts, drift = [], False
        for name, idx in (("worker", A_WORKER), ("soldier", A_SOLDIER), ("scout", A_SCOUT)):
            tgt = spawn.get(name, {}).get("target_ratio")
            if tgt is None:
                continue
            actual = counts[idx] / total
            parts.append(f"{name} target {tgt:.2f}/actual {actual:.2f}")
            if abs(actual - tgt) > 0.2:
                drift = True
        if parts:
            out.append({"intent": "spawn", "status": "drifting" if drift else "on_target",
                        "detail": "; ".join(parts)})
    return out


def build_sitrep(colony, world):
    return {"orders": _orders(colony, world)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_sitrep.py`
Expected: `Task 2 PASS`

- [ ] **Step 5: Commit**

```bash
git add engine/sitrep.py tests/test_sitrep.py
git commit -m "engine: sitrep orders section — order-effect readout (advancing/no_effect/holding/drift)"
```

---

### Task 3: `standing` section (you vs enemy, fog-gated)

**Files:**
- Modify: `engine/sitrep.py` (add `_standing`, extend `build_sitrep`)
- Test: `tests/test_sitrep.py` (append)

**Interfaces:**
- Consumes: `colony.enemy_scouted_counts`, `colony.enemy_scouted_tick`, `world.tick`, `world.territory`, `colony.food`, `colony.income_per_s`, `colony.nx/ny`, `colony.enemy`.
- Produces: `build_sitrep(...)["standing"]` → `{"military": {...}, "economy": {...}, "territory": {...}, "queen": {...}}`. `_standing(colony, world) -> dict`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sitrep.py`:
```python
def test_standing_enemy_unknown_until_scouted():
    w, me, enemy = _fresh()
    me.ants += [Ant(40, 50, 0, A_SOLDIER, born_tick=0) for _ in range(5)]
    me.enemy_scouted_tick = -9999       # never scouted
    st = build_sitrep(me, w)["standing"]
    assert st["military"]["enemy"] == "unknown", st["military"]
    assert st["military"]["verdict"] == "unknown"
    assert st["economy"]["enemy"] == "not_observable"
    assert st["military"]["you"] == 5 * 20      # 5 soldiers * 20

def test_standing_enemy_scouted_value_and_staleness():
    w, me, enemy = _fresh()
    me.ants += [Ant(40, 50, 0, A_SOLDIER, born_tick=0) for _ in range(5)]
    w.tick = 1000
    me.enemy_scouted_tick = 980
    me.enemy_scouted_counts = [4, 3, 1, 1]      # w,s,sc,q -> 4*5 + 3*20 + 1*8 = 88 (matches army_value: incl workers)
    st = build_sitrep(me, w)["standing"]
    assert st["military"]["enemy"] == 88, st["military"]
    assert st["military"]["enemy_stale_ticks"] == 20
    assert st["military"]["verdict"] == "leading"   # you 100 > enemy 88

if __name__ == "__main__":
    test_com_history_tracks_soldiers()
    test_orders_attack_no_effect_when_frozen()
    test_orders_attack_advancing()
    test_standing_enemy_unknown_until_scouted()
    test_standing_enemy_scouted_value_and_staleness()
    print("Task 3 PASS")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_sitrep.py`
Expected: FAIL — `KeyError: 'standing'`

- [ ] **Step 3: Add `_standing` to `engine/sitrep.py`**

Add this function and extend `build_sitrep`:
```python
def _verdict(you, enemy, margin_floor=1):
    if enemy is None:
        return "unknown", None
    margin = you - enemy
    if abs(margin) <= margin_floor:
        return "even", margin
    return ("leading" if margin > 0 else "trailing"), margin


def _standing(colony, world):
    from engine.constants import MAP_W, MAP_H
    counts = [sum(1 for a in colony.ants if a.type == t) for t in range(4)]
    you_mil = counts[A_SOLDIER] * UNIT_VALUE[A_SOLDIER] + counts[A_SCOUT] * UNIT_VALUE[A_SCOUT] \
        + counts[A_WORKER] * UNIT_VALUE[A_WORKER]

    # enemy military: scouted-only (coarse counts), with staleness
    sc = getattr(colony, "enemy_scouted_counts", [0, 0, 0, 0])
    seen_tick = getattr(colony, "enemy_scouted_tick", -9999)
    if seen_tick < 0:
        enemy_mil, stale, mverdict, mmargin = "unknown", None, "unknown", None
    else:
        enemy_mil = sc[A_SOLDIER] * UNIT_VALUE[A_SOLDIER] + sc[A_SCOUT] * UNIT_VALUE[A_SCOUT] \
            + sc[A_WORKER] * UNIT_VALUE[A_WORKER]
        stale = world.tick - seen_tick
        mverdict, mmargin = _verdict(you_mil, enemy_mil)

    # economy: enemy not directly observable; honest proxy from scouted counts
    economy = {"you": {"food": int(colony.food), "income_per_s": round(colony.income_per_s, 1)},
               "enemy": "not_observable",
               "enemy_proxy": {"scouted_workers": sc[A_WORKER], "scouted_soldiers": sc[A_SOLDIER]}}

    # territory: your owned tiles (full); enemy fog-gated -> unknown
    you_tiles = sum(1 for o in getattr(world, "territory", b"") if o == colony.id)
    you_pct = round(you_tiles / (MAP_W * MAP_H) * 100, 1)

    # queen: your hp full; threat = enemy soldiers on your turf (observable); enemy queen hp only if sieged
    queen = next((a for a in colony.ants if a.type == A_QUEEN), None)
    qhp = int(queen.hp) if queen else 0
    threat = 0
    enemy_qhp = "unknown"
    if colony.enemy:
        threat = sum(1 for a in colony.enemy.ants if a.type == A_SOLDIER
                     and abs(a.x - colony.nx) + abs(a.y - colony.ny) <= 15)
        in_siege = sum(1 for a in colony.ants if a.type == A_SOLDIER
                       and abs(a.x - colony.enemy.nx) + abs(a.y - colony.enemy.ny) <= 12)
        if in_siege > 0:
            eq = next((a for a in colony.enemy.ants if a.type == A_QUEEN), None)
            if eq:
                enemy_qhp = int(eq.hp)

    return {
        "military": {"you": you_mil, "enemy": enemy_mil, "enemy_seen_tick": seen_tick if seen_tick >= 0 else None,
                     "enemy_stale_ticks": stale, "verdict": mverdict, "margin": mmargin},
        "economy": economy,
        "territory": {"you_pct": you_pct, "enemy_pct": "unknown", "verdict": "unknown"},
        "queen": {"your_hp": qhp, "your_hp_pct": round(qhp / 900 * 100), "threat_soldiers_near_nest": threat,
                  "enemy_queen_hp": enemy_qhp},
    }
```
Change `build_sitrep` to:
```python
def build_sitrep(colony, world):
    return {"standing": _standing(colony, world), "orders": _orders(colony, world)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_sitrep.py`
Expected: `Task 3 PASS`

- [ ] **Step 5: Commit**

```bash
git add engine/sitrep.py tests/test_sitrep.py
git commit -m "engine: sitrep standing section — you-vs-enemy, fog-gated enemy + not_observable economy"
```

---

### Task 4: `field` section (scouted battlefield facts)

**Files:**
- Modify: `engine/sitrep.py` (add `_field`, extend `build_sitrep`)
- Test: `tests/test_sitrep.py` (append)

**Interfaces:**
- Consumes: `colony.fog_visible` (set of visible tile keys), `colony.enemy.ants`, `colony.ants`, `colony.enemy_scouted_tick`, `world.structures`, `world.tick`.
- Produces: `build_sitrep(...)["field"]` → `{"front_line": {"north","center","south"}, "enemy_army": {...}, "enemy_structures": [...]}`. `_field(colony, world) -> dict`. Lanes: north `y<34`, center `34..66`, south `y>66`.
- Note: `fog_visible` stores tile keys as `y * MAP_W + x` ints (confirm against `World._update_fog`; if it stores `(x, y)` tuples, adjust the membership test in Step 3 accordingly — this is the one field-format detail to verify against the code before implementing).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sitrep.py`:
```python
def test_field_enemy_unseen_then_seen():
    w, me, enemy = _fresh()
    me.fog_visible = set()
    enemy.ants.append(Ant(120, 50, 1, A_SOLDIER, born_tick=0))
    f = build_sitrep(me, w)["field"]
    assert f["enemy_army"]["seen"] is False, f["enemy_army"]
    # now make that tile visible
    from engine.constants import MAP_W
    me.fog_visible = {50 * MAP_W + 120}
    w.tick = 200
    f = build_sitrep(me, w)["field"]
    assert f["enemy_army"]["seen"] is True
    assert f["enemy_army"]["pos"] == [120, 50]

def test_field_front_line_per_lane():
    w, me, enemy = _fresh()
    me.ants.append(Ant(74, 50, 0, A_SOLDIER, born_tick=0))   # center lane
    enemy.ants.append(Ant(76, 50, 1, A_SOLDIER, born_tick=0))
    from engine.constants import MAP_W
    me.fog_visible = {50 * MAP_W + 76}
    f = build_sitrep(me, w)["field"]
    assert f["front_line"]["center"] == 74, f["front_line"]
    assert f["front_line"]["north"] is None

if __name__ == "__main__":
    for fn in (test_com_history_tracks_soldiers, test_orders_attack_no_effect_when_frozen,
               test_orders_attack_advancing, test_standing_enemy_unknown_until_scouted,
               test_standing_enemy_scouted_value_and_staleness,
               test_field_enemy_unseen_then_seen, test_field_front_line_per_lane):
        fn()
    print("Task 4 PASS")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_sitrep.py`
Expected: FAIL — `KeyError: 'field'`

- [ ] **Step 3: Add `_field` to `engine/sitrep.py`**

```python
def _lane(y):
    return "north" if y < 34 else ("center" if y <= 66 else "south")


def _field(colony, world):
    from engine.constants import MAP_W
    vis = getattr(colony, "fog_visible", set())

    def visible(x, y):
        return (y * MAP_W + x) in vis

    # currently-visible enemy units (fog-compliant)
    seen_enemies = []
    if colony.enemy:
        seen_enemies = [a for a in colony.enemy.ants if a.type != A_QUEEN and visible(a.x, a.y)]

    if seen_enemies:
        cx = sum(a.x for a in seen_enemies) / len(seen_enemies)
        cy = sum(a.y for a in seen_enemies) / len(seen_enemies)
        enemy_army = {"seen": True, "size": len(seen_enemies), "pos": [round(cx), round(cy)],
                      "seen_tick": world.tick, "age_ticks": 0}
    else:
        enemy_army = {"seen": False}

    # front line per lane: furthest-forward own soldier in contact (<=2 tiles) with a visible enemy
    front = {"north": None, "center": None, "south": None}
    if colony.enemy and seen_enemies:
        for a in colony.ants:
            if a.type != A_SOLDIER:
                continue
            if any(abs(a.x - e.x) + abs(a.y - e.y) <= 2 for e in seen_enemies):
                lane = _lane(a.y)
                # "furthest forward" = nearest the enemy nest along x
                better = front[lane] is None or (abs(a.x - colony.enemy.nx) < abs(front[lane] - colony.enemy.nx))
                if better:
                    front[lane] = a.x

    enemy_structs = [{"type": st.get("type", "guard_post"), "x": st["x"], "y": st["y"], "seen_tick": world.tick}
                     for st in world.structures
                     if st["colony"] != colony.id and visible(st["x"], st["y"])]

    return {"front_line": front, "enemy_army": enemy_army, "enemy_structures": enemy_structs}
```
Change `build_sitrep` to:
```python
def build_sitrep(colony, world):
    return {"standing": _standing(colony, world), "orders": _orders(colony, world),
            "field": _field(colony, world)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_sitrep.py`
Expected: `Task 4 PASS`

- [ ] **Step 5: Commit**

```bash
git add engine/sitrep.py tests/test_sitrep.py
git commit -m "engine: sitrep field section — scouted front line, enemy army, enemy structures"
```

---

### Task 5: Wire sitrep into agent state + de-prescriptivize the advisor

**Files:**
- Modify: `server.py` (`_build_colony_state`: import + attach `sitrep` in the return dict ~line 2753; rewrite advisor imperatives ~lines 2657–2730)
- Test: `tests/test_sitrep.py` (append a server-integration check)

**Interfaces:**
- Consumes: `build_sitrep` from `engine/sitrep.py`.
- Produces: `_build_colony_state(...)` return dict gains key `"sitrep"`. Advisor strings contain no tool-call imperatives.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sitrep.py`:
```python
def test_advisor_has_no_imperatives():
    import re, pathlib
    src = pathlib.Path("server.py").read_text()
    # the advisor block must not push tool-call syntax at the agent
    block = src[src.index("advisor = []"): src.index("\"viable_food_nodes\"")]
    for bad in ("buy_upgrade(", "redistribute_workers(", "build_structure available"):
        assert bad not in block, f"advisor still prescriptive: {bad!r}"
```
(Run via adding `test_advisor_has_no_imperatives()` to the `__main__` block and bumping the print to `Task 5 PASS`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_sitrep.py`
Expected: FAIL on the assertion (current advisor contains `buy_upgrade(` / `build_structure available`).

- [ ] **Step 3: Attach the sitrep**

At the top of `server.py` with the other imports add:
```python
from engine.sitrep import build_sitrep
```
In `_build_colony_state`, in the returned dict (the `return { ... }` near line 2753), add the key:
```python
            "sitrep": build_sitrep(c, w),
```

- [ ] **Step 4: De-prescriptivize the advisor lines**

In `_build_colony_state`, replace the prescriptive advisor strings with factual ones. Specific edits:
- The dirt line: replace
  `advisor.append(f"{int(c.dirt)}◆ dirt unspent — build_structure available: {', '.join(affordable)}")`
  with
  `advisor.append(f"{int(c.dirt)}◆ dirt unspent; affordable structures: {', '.join(affordable)}")`
- The idle-workers line: replace the existing
  `advisor.append(f"{idle_workers}/{counts[0]} workers idle — check viable_food_nodes; "` (+ continuation)
  with a single factual line:
  `advisor.append(f"{idle_workers}/{counts[0]} workers idle")`
- The upgrade line near `food {int(c.food)} covers {u} upgrade`: replace any `buy_upgrade('{u}')` imperative with the fact only, e.g.
  `advisor.append(f"{u} upgrade affordable: {co}◆ (you have {int(c.food)})")`
- Search the whole advisor block for remaining `redistribute_workers()` / `buy_upgrade(` / imperative phrasing and reword to a bare fact (keep the number, drop the command).

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 tests/test_sitrep.py`
Expected: `Task 5 PASS`

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_sitrep.py
git commit -m "server: attach sitrep to agent state + convert advisor from imperatives to facts"
```

---

### Task 6: Parity cleanup + controller renders sitrep

**Files:**
- Modify: `controller/controller.py` (`patch_directive` ~line 224: remove rally-clear guard; `format_state` ~line 438: remove the ⚠ idle-worker special-case; add sitrep rendering)
- Test: manual (controller has no unit-test harness; verify by inspection + a dry render)

**Interfaces:**
- Consumes: `state["sitrep"]` from Task 5.
- Produces: controller state summary includes a compact sitrep render; `patch_directive` sends patches unchanged.

- [ ] **Step 1: Remove the controller-only rally guard**

In `controller/controller.py`, revert `patch_directive` to the plain pass-through (delete the guard block added this session):
```python
    async def patch_directive(self, patches: dict) -> dict:
        return await self._tool_request("POST", self._mpath(f"/directive/{self.colony_id}"), patches)
```
(The engine fix — no-release rally no longer overrides attack_target — already covers this for every agent.)

- [ ] **Step 2: Remove the controller-only idle-worker alert, render sitrep instead**

In the state-summary builder, delete the `⚠ IDLE WORKERS: ...` block added this session. Replace the `units`/idle handling with a sitrep render that every agent's server already provides. After the `advisor`/`events` lines, add:
```python
    sr = state.get("sitrep")
    if sr:
        st = sr.get("standing", {})
        m = st.get("military", {}); e = st.get("economy", {}).get("you", {}); q = st.get("queen", {})
        lines.append(f"SITREP standing: military you {m.get('you')} vs enemy {m.get('enemy')} "
                     f"({m.get('verdict')}); food {e.get('food')} income {e.get('income_per_s')}/s; "
                     f"queen {q.get('your_hp_pct')}% threat {q.get('threat_soldiers_near_nest')}")
        for o in sr.get("orders", []):
            lines.append(f"SITREP order [{o['intent']}]: {o['status']} — {o['detail']}")
        fld = sr.get("field", {})
        ea = fld.get("enemy_army", {})
        if ea.get("seen"):
            lines.append(f"SITREP field: enemy army ~{ea['size']} @ {ea['pos']} (age {ea['age_ticks']}t); "
                         f"front_line {fld.get('front_line')}")
        else:
            lines.append(f"SITREP field: enemy army not currently in sight; front_line {fld.get('front_line')}")
```

- [ ] **Step 3: Verify by inspection + a syntax check**

Run: `python3 -c "import ast; ast.parse(open('controller/controller.py').read()); print('controller OK')"`
Expected: `controller OK`. Confirm no remaining `IDLE WORKERS` string and no rally-clear guard:
Run: `grep -nE "IDLE WORKERS|rally_point.*None.*rally_release_at" controller/controller.py || echo "clean"`
Expected: `clean`

- [ ] **Step 4: Commit**

```bash
git add controller/controller.py
git commit -m "controller: render server sitrep; drop controller-only rally guard + idle alert (parity)"
```

---

### Task 7: Full suite + deploy notes

**Files:** none (verification + handoff)

- [ ] **Step 1: Run the full engine test suite**

Run: `python3 tests/test_sitrep.py`
Expected: `Task 5 PASS` (final print) with all asserts passing. Also re-run the rally regression and unit tests:
Run: `python3 TEMP/test_rally_override.py` (expect 3 PASS) and `node --test frontend/game/src 2>&1 | grep -E "# (tests|pass|fail)"` (expect 16 pass, unchanged).

- [ ] **Step 2: Deploy (coordinated, batches the pending engine changes)**

This change ships with `deploy.sh`, which rsyncs code to the remote and restarts the game server — it interrupts any live match once, and also carries the already-committed-but-undeployed engine changes (rally-override fix + `workers_idle` notification). Confirm with the user before running (a live match may be in progress):
```bash
bash deploy.sh            # sync + restart agants.service
```
Then restart the controller so it renders the sitrep. Verify live:
```bash
curl -s https://api.datthemaster.com/agants/api/state/0 | python3 -m json.tool | grep -A2 '"sitrep"'
```
Expected: a `sitrep` object present in the agent state.

---

## Notes for the executor
- Run tests from the repo root (`/home/deshiel/projects/agants`) so `sys.path.insert(0, ".")` and relative paths resolve.
- The one format detail to verify before Task 4 Step 3: how `colony.fog_visible` keys are stored (int `y*MAP_W+x` vs `(x,y)` tuple) — check `World._update_fog` in `engine/world.py` and match the membership test.
- `deploy.sh` is a production action on a live game host (`192.168.1.100`) — never run it without explicit user approval naming the target.
