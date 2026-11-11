"""Internal consistency of config/capability.yaml (no RoboDK needed - the
RoboDK-vs-file check is station/check_capability_consistency.py, which
needs a running station and is run separately per Step 0's own checklist).
"""

from langchain_app.capability_loader import load_capability


def test_every_part_home_bin_is_a_known_position():
    capability = load_capability()
    for part, info in capability["parts"].items():
        assert info["home_bin"] in capability["positions"], part


def test_protected_slots_are_known_positions():
    capability = load_capability()
    for slot in capability["rules"]["protected_slots"]:
        assert slot in capability["positions"]


def test_allowed_parts_match_declared_parts():
    capability = load_capability()
    assert set(capability["rules"]["allowed_parts"]) == set(capability["parts"])


def test_every_bin_holds_a_declared_part():
    capability = load_capability()
    for name, info in capability["positions"].items():
        if info.get("kind") == "bin":
            assert info["holds"] in capability["parts"], name


def test_functions_match_the_five_allowlisted_functions():
    capability = load_capability()
    assert set(capability["functions"]) == {
        "move_to", "pick", "place", "locate", "read_cell_state",
    }
