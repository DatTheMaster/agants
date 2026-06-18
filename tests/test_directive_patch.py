import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import DirectiveEngine


def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    return w, w.colonies[0]


def test_flat_leaf_keys_route_into_section():
    """REGRESSION: a flat patch like {"auto_attack": true, "rally_point": [...]} must land
    in directive["military"], not the top level (where nothing reads it). This was the root
    cause of 'auto_attack did nothing / rally never staged / changes didn't take effect'."""
    w, c = _fresh()
    DirectiveEngine.patch(c, {
        "auto_attack": True, "rally_point": [40, 50], "rally_release_at": 8,
        "stance": "aggressive", "priority_food": [30, 80],
    })
    mil = c.directive["military"]
    assert mil["auto_attack"] is True, "auto_attack not routed into military"
    assert mil["rally_point"] == [40, 50], mil["rally_point"]
    assert mil["rally_release_at"] == 8
    assert mil["stance"] == "aggressive"
    assert c.directive["economy"]["priority_food"] == [30, 80]
    # no junk left at the top level
    for k in ("auto_attack", "rally_point", "rally_release_at", "stance", "priority_food"):
        assert k not in c.directive, f"{k} leaked to directive top level"


def test_dot_and_nested_forms_still_work():
    w, c = _fresh()
    DirectiveEngine.patch(c, {"military.auto_attack": True})
    assert c.directive["military"]["auto_attack"] is True
    DirectiveEngine.patch(c, {"military": {"rally_release_at": 5}})
    assert c.directive["military"]["rally_release_at"] == 5
    # nested merge must not wipe sibling military fields
    assert c.directive["military"]["auto_attack"] is True


def test_nested_section_dict_not_treated_as_flat_leaf():
    """A real section dict for a flat-routable name shouldn't be mis-handled."""
    w, c = _fresh()
    DirectiveEngine.patch(c, {"economy": {"priority_food": [10, 32]}})
    assert c.directive["economy"]["priority_food"] == [10, 32]


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
