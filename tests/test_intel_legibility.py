import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant
from engine.constants import A_QUEEN, A_SOLDIER, A_SCOUT, A_WORKER
from engine.sitrep import build_sitrep
import server


def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    me, enemy = w.colonies[0], w.colonies[1]
    me.ants = [a for a in me.ants if a.type == A_QUEEN]
    enemy.ants = [a for a in enemy.ants if a.type == A_QUEEN]
    return w, me, enemy


def test_enemy_army_has_composition_when_seen():
    w, me, enemy = _fresh()
    # enemy cluster in midfield, our soldiers right next to them so they're visible
    for _ in range(6):
        enemy.ants.append(Ant(78, 50, enemy.id, A_SOLDIER))
    for _ in range(2):
        enemy.ants.append(Ant(78, 51, enemy.id, A_SCOUT))
    for _ in range(5):
        me.ants.append(Ant(76, 50, me.id, A_SOLDIER))
    for _ in range(3):
        w.step()
    ea = build_sitrep(me, w)["field"]["enemy_army"]
    assert ea["seen"] is True, ea
    assert "composition" in ea, "enemy_army missing composition"
    comp = ea["composition"]
    assert set(comp) == {"soldiers", "scouts", "workers", "spitters", "raiders"}, comp
    assert comp["soldiers"] >= 1 and comp["scouts"] >= 1, comp


def test_sightings_have_staleness_and_are_capped():
    w, me, enemy = _fresh()
    for _ in range(6):
        enemy.ants.append(Ant(78, 50, enemy.id, A_SOLDIER))
    for _ in range(5):
        me.ants.append(Ant(76, 50, me.id, A_SOLDIER))
    for _ in range(80):
        w.step()
    # storage capped at 8
    assert len(me.enemy_sightings) <= 8, f"sightings not capped: {len(me.enemy_sightings)}"
    sl = server.format_enemy_sightings(me, w.tick)
    assert sl, "no formatted sightings"
    assert len(sl) <= 8
    first = sl[0]
    for k in ("pos", "total", "soldiers", "workers", "scouts", "seen_tick", "ticks_ago"):
        assert k in first, f"sighting missing {k}: {first}"
    # most-recent first => non-decreasing ticks_ago
    ages = [s["ticks_ago"] for s in sl]
    assert ages == sorted(ages), f"sightings not most-recent-first: {ages}"
    assert all(a >= 0 for a in ages)


def test_enemy_army_unseen_respects_fog():
    """No visible enemies (they're home, far away) => enemy_army.seen False, no leak."""
    w, me, enemy = _fresh()
    # enemy soldiers parked in their own base, our units all at home — not visible
    for _ in range(10):
        enemy.ants.append(Ant(134, 50, enemy.id, A_SOLDIER))
    w.step()
    ea = build_sitrep(me, w)["field"]["enemy_army"]
    assert ea["seen"] is False, f"fog leak: {ea}"
    assert "composition" not in ea


def test_attack_eta_none_when_enemy_unseen():
    """Enemy soldiers parked in their own base (not in our fog) => no attack ETA leak."""
    w, me, enemy = _fresh()
    for _ in range(8):
        enemy.ants.append(Ant(134, 50, enemy.id, A_SOLDIER))
    w.step()
    assert server.compute_enemy_attack_eta(me, w) is None, "attack ETA leaked an unseen force"


def test_attack_eta_populates_for_visible_force():
    """A visible enemy soldier force yields a fog-legitimate ETA from our nest (14,50)."""
    w, me, enemy = _fresh()
    # enemy soldiers near our nest, with our soldiers adjacent so they're in our fog
    for _ in range(5):
        enemy.ants.append(Ant(40, 50, enemy.id, A_SOLDIER))
    for _ in range(3):
        me.ants.append(Ant(41, 50, me.id, A_SOLDIER))
    for _ in range(3):
        w.step()
    eta = server.compute_enemy_attack_eta(me, w)
    assert eta is not None, "visible enemy force produced no ETA"
    assert eta["source"] in ("visible", "sighting"), eta
    assert eta["soldiers"] >= 1 and eta["eta_t"] >= 0 and eta["dist"] >= 0, eta


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception as e:
            failed += 1; print(f"FAIL {fn.__name__}: {e}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
