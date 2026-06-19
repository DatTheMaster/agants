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
