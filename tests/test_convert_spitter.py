import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant
from engine.constants import A_QUEEN, A_SPITTER, A_SOLDIER, SPITTER_HP

def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    me, en = w.colonies[0], w.colonies[1]
    me.ants = [a for a in me.ants if a.type == A_QUEEN]
    en.ants = [a for a in en.ants if a.type == A_QUEEN]
    return w, me, en

def test_convert_spitter_no_crash():
    """Converting a spitter must not raise IndexError (bug: 3-element list indexed by ant.type=4)."""
    w, me, en = _fresh()
    queen = next(a for a in me.ants if a.type == A_QUEEN)
    # Place spitter right next to the queen so distance check passes
    spitter = Ant(queen.x, queen.y, me.id, A_SPITTER)
    spitter.hp = SPITTER_HP
    me.ants.append(spitter)
    me.food = 1000  # plenty to cover any convert cost
    # Queue a convert: spitter -> soldier
    me.convert_queue.append({"id": spitter.id, "to": "soldier"})
    # Run a few steps — should not raise
    for _ in range(5):
        w.step()
    # After conversion the ant's type should have changed (queue was processed)
    assert spitter.type == A_SOLDIER, f"Expected A_SOLDIER after convert, got {spitter.type}"

def test_convert_spitter_produces_correct_type():
    """After converting a spitter to worker, ant type and HP reset correctly."""
    from engine.constants import A_WORKER, WORKER_HP
    w, me, en = _fresh()
    queen = next(a for a in me.ants if a.type == A_QUEEN)
    spitter = Ant(queen.x, queen.y, me.id, A_SPITTER)
    spitter.hp = SPITTER_HP
    me.ants.append(spitter)
    me.food = 1000
    me.convert_queue.append({"id": spitter.id, "to": "worker"})
    for _ in range(5):
        w.step()
    assert spitter.type == A_WORKER, f"Expected A_WORKER, got {spitter.type}"
    assert spitter.hp == WORKER_HP, f"Expected {WORKER_HP} HP, got {spitter.hp}"

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
