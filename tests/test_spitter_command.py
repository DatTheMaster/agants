"""Spitters must be controllable: the MCP command_type/cancel_spawn wrappers now
accept 'spitter', and that only works because the engine honors spitter unit
overrides and spitter spawn cancellation. Lock both in."""
import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant
from engine.constants import A_QUEEN, A_SPITTER, SPITTER_SPAWN_TIME, SPITTER_SPAWN_COST


def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    me, en = w.colonies[0], w.colonies[1]
    me.ants = [a for a in me.ants if a.type == A_QUEEN]
    en.ants = [a for a in en.ants if a.type == A_QUEEN]
    return w, me, en


def test_spitter_obeys_move_to_override():
    """A move_to override repositions a spitter toward the target (no enemy in range)."""
    w, me, en = _fresh()
    sp = Ant(50, 50, me.id, A_SPITTER); me.ants.append(sp)
    sp.unit_override = {"cmd": "move_to", "x": 60, "y": 50}
    x0 = sp.x
    for _ in range(8):
        w.step()
    assert sp.x > x0, f"spitter did not move toward override target (x {x0} -> {sp.x})"


def test_cancel_spawn_spitter_refunds():
    """set_strategy(cancel_spawn='spitter') clears queued spitters and refunds food —
    the engine path the MCP cancel_spawn('spitter') wrapper drives."""
    w, me, en = _fresh()
    me.food = 500.0
    # queue two spitters directly (type, ticks_left, food_cost)
    me.spawn_queue = [(A_SPITTER, SPITTER_SPAWN_TIME, SPITTER_SPAWN_COST),
                      (A_SPITTER, SPITTER_SPAWN_TIME, SPITTER_SPAWN_COST)]
    food_before = me.food
    me.set_strategy({"cancel_spawn": "spitter"})
    assert all(e[0] != A_SPITTER for e in me.spawn_queue), "spitters still queued after cancel"
    assert me.food == food_before + 2 * SPITTER_SPAWN_COST, \
        f"food not refunded: {food_before} -> {me.food}"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]; failed = 0
    for fn in fns:
        try: fn(); print(f"PASS {fn.__name__}")
        except Exception as e: failed += 1; print(f"FAIL {fn.__name__}: {e}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
