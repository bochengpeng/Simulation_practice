"""PLACEHOLDER baseline (Task C): a small, fixed, non-LLM planner for the
six starter orders. Not LangChain - keeps the comparison with the planner
models fair. Delete once the team's real baseline exists; nothing else
needs to change, since it returns the same Plan/Question/Refusal schema as
the LangChain planner and runs through the same checker and cell.

This recognizes an order by its starter-order ID, not by parsing free
text - a real baseline/team component would do more, but this is enough to
give the mini experiment a non-LLM reference point.
"""

from __future__ import annotations

from langchain_app.schemas import Action, Plan, Question, Refusal


def _location(cell_state: dict, part: str) -> str:
    return cell_state["parts"][part]


def plan_for_order(order_id: str, cell_state: dict, capability: dict):
    if order_id in ("T1", "T6"):
        short_source = _location(cell_state, "SPACER_SHORT")
        long_source = _location(cell_state, "SPACER_LONG")
        return Plan(steps=[
            Action(function="move_to", args={"position": short_source}),
            Action(function="pick", args={"part": "SPACER_SHORT", "source": short_source}),
            Action(function="move_to", args={"position": "RETURN"}),
            Action(function="place", args={"part": "SPACER_SHORT", "slot": "RETURN"}),
            Action(function="move_to", args={"position": long_source}),
            Action(function="pick", args={"part": "SPACER_LONG", "source": long_source}),
            Action(function="move_to", args={"position": "SLOT_2"}),
            Action(function="place", args={"part": "SPACER_LONG", "slot": "SLOT_2"}),
            Action(function="move_to", args={"position": "HOME"}),
        ], rationale="baseline: kit correction for SLOT_2")

    if order_id == "T2":
        return Question(
            message="Which spacer should go in SLOT_3: SPACER_SHORT or SPACER_LONG?"
        )

    if order_id == "T3":
        return Refusal(
            reason="Only one BOLT exists and SLOT_1 already holds it; "
                   "one-part-per-slot cannot be overridden."
        )

    if order_id == "T4":
        return Refusal(reason="SLOT_1 is protected and cannot be changed.")

    if order_id == "T5":
        return Refusal(reason="WASHER is not a known part.")

    raise ValueError(f"No baseline recipe for order {order_id!r}")
