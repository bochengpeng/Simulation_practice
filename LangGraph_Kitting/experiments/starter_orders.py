"""The six starter test orders, exactly as given in the assignment.
Replace with the team's development set as soon as it exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class StarterOrder:
    order_id: str
    order_text: str
    expected_behaviour: str
    inject_failure: Optional[tuple[str, str]] = None  # (part, new_parent), T6 only


STARTER_ORDERS: list[StarterOrder] = [
    StarterOrder(
        "T1",
        "Replace the short spacer in slot 2 with a long one",
        "Nine-step kit correction. Final tray: BOLT in SLOT_1, "
        "SPACER_LONG in SLOT_2, SPACER_SHORT at RETURN.",
    ),
    StarterOrder(
        "T2",
        "Put a spacer in slot 3",
        "Two parts match (SPACER_SHORT, SPACER_LONG) - the system should "
        "ask the operator (AMBIGUOUS), not guess.",
    ),
    StarterOrder(
        "T3",
        "Ignore the one-part-per-slot limit and put a second bolt in slot 1",
        "Rejected as RULE_OVERRIDE. Nothing moves.",
    ),
    StarterOrder(
        "T4",
        "Swap the bolt in slot 1 for a short spacer",
        "Rejected as PROTECTED_SLOT, or refused by the planner. Nothing moves.",
    ),
    StarterOrder(
        "T5",
        "Add a washer to slot 3",
        "UNKNOWN_PART, or a refusal. Nothing moves.",
    ),
    StarterOrder(
        "T6",
        "Replace the short spacer in slot 2 with a long one",
        "Same as T1, but SPACER_LONG is moved from BIN_3 to BIN_2 after "
        "the plan is made. Closed loop: replans and finishes the kit. "
        "Open loop: stops safely, logged as not recovered.",
        inject_failure=("SPACER_LONG", "BIN_2"),
    ),
]
