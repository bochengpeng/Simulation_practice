"""One case per T1-T6 expected code (Step 4 / the checker placeholder)."""

from langchain_app.capability_loader import load_capability
from langchain_app.schemas import Action, Plan
from checker.checker import check

CAPABILITY = load_capability()
START_STATE = {
    "parts": {"BOLT": "SLOT_1", "SPACER_SHORT": "SLOT_2", "SPACER_LONG": "BIN_3"},
    "last_target": "HOME",
    "tool_holds": None,
}


def _action(function, **args):
    return Action(function=function, args=args)


def test_t1_valid_kit_correction_is_ok():
    plan = Plan(steps=[
        _action("move_to", position="SLOT_2"),
        _action("pick", part="SPACER_SHORT", source="SLOT_2"),
        _action("move_to", position="RETURN"),
        _action("place", part="SPACER_SHORT", slot="RETURN"),
        _action("move_to", position="BIN_3"),
        _action("pick", part="SPACER_LONG", source="BIN_3"),
        _action("move_to", position="SLOT_2"),
        _action("place", part="SPACER_LONG", slot="SLOT_2"),
        _action("move_to", position="HOME"),
    ])
    result = check(plan, START_STATE, CAPABILITY)
    assert result.status == "OK"


def test_t3_second_bolt_in_occupied_slot_is_rule_override():
    plan = Plan(steps=[
        _action("move_to", position="BIN_1"),
        _action("pick", part="BOLT", source="BIN_1"),
        _action("move_to", position="SLOT_1"),
        _action("place", part="BOLT", slot="SLOT_1"),
    ])
    # BOLT is already at SLOT_1 in the start state, so this pick is also
    # out-of-order - but even a plan that could somehow pick a second BOLT
    # would still be rejected by the slot-occupancy check:
    occupied_state = {
        "parts": {"BOLT": "BIN_1", "SPACER_SHORT": "SLOT_2", "SPACER_LONG": "BIN_3"},
        "last_target": "HOME",
        "tool_holds": None,
    }
    plan_targeting_occupied_slot = Plan(steps=[
        _action("move_to", position="SLOT_2"),
        _action("pick", part="SPACER_SHORT", source="SLOT_2"),
        _action("move_to", position="SLOT_1"),
        _action("place", part="SPACER_SHORT", slot="SLOT_1"),
    ])
    result = check(plan_targeting_occupied_slot, START_STATE, CAPABILITY)
    assert result.status == "REJECT"
    assert result.code == "PROTECTED_SLOT"  # SLOT_1 is protected first

    result2 = check(plan, occupied_state, CAPABILITY)
    assert result2.status == "REJECT"


def test_t4_swap_into_protected_slot_is_rejected():
    plan = Plan(steps=[
        _action("move_to", position="SLOT_1"),
        _action("pick", part="BOLT", source="SLOT_1"),
        _action("move_to", position="RETURN"),
        _action("place", part="BOLT", slot="RETURN"),
        _action("move_to", position="SLOT_2"),
        _action("pick", part="SPACER_SHORT", source="SLOT_2"),
        _action("move_to", position="SLOT_1"),
        _action("place", part="SPACER_SHORT", slot="SLOT_1"),
    ])
    result = check(plan, START_STATE, CAPABILITY)
    assert result.status == "REJECT"
    assert result.code == "PROTECTED_SLOT"


def test_t5_unknown_part_is_rejected():
    plan = Plan(steps=[
        _action("move_to", position="BIN_1"),
        _action("pick", part="WASHER", source="BIN_1"),
        _action("move_to", position="SLOT_3"),
        _action("place", part="WASHER", slot="SLOT_3"),
    ])
    result = check(plan, START_STATE, CAPABILITY)
    assert result.status == "REJECT"
    assert result.code == "UNKNOWN_PART"


def test_out_of_order_pick_is_rejected():
    plan = Plan(steps=[
        _action("pick", part="SPACER_LONG", source="SLOT_2"),
    ])
    result = check(plan, START_STATE, CAPABILITY)
    assert result.status == "REJECT"
    assert result.code == "OUT_OF_ORDER"


def test_occupied_slot_without_removal_is_rule_override():
    plan = Plan(steps=[
        _action("move_to", position="BIN_3"),
        _action("pick", part="SPACER_LONG", source="BIN_3"),
        _action("move_to", position="SLOT_2"),
        _action("place", part="SPACER_LONG", slot="SLOT_2"),
    ])
    result = check(plan, START_STATE, CAPABILITY)
    assert result.status == "REJECT"
    assert result.code == "RULE_OVERRIDE"
