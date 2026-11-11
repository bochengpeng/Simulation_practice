"""PLACEHOLDER checker (Task B).

The assignment says not to rewrite the team's checker in LangChain - but
no team checker exists anywhere in this repository or its branches yet, so
this is a stand-in: a small, pure, rule-based validator with the same
shape a real one would have (a plain function over (plan, cell_state) that
returns a categorized result, no I/O, no RoboDK/LangChain import). Delete
this module and import the team's real checker once it exists; nothing
else in this project should need to change (orchestrator/graph.py only
calls `check(...)`).

This is a second, independent layer on top of the Pydantic schema - the
schema is a first filter only (Step 2); this is where correctness rules
actually get enforced (Step 4's watch-out: two layers are fine, one is not).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from langchain_app.schemas import Plan


@dataclass
class CheckResult:
    status: str  # "OK" | "REJECT"
    code: Optional[str] = None
    reason: str = ""
    step_index: Optional[int] = None


def check(plan: Plan, cell_state: dict, capability: dict) -> CheckResult:
    positions = capability["positions"]
    rules = capability["rules"]
    allowed_parts = set(rules["allowed_parts"])
    protected_slots = set(rules.get("protected_slots", []))

    if len(plan.steps) > rules["max_steps_per_plan"]:
        return CheckResult(
            status="REJECT",
            code="OVER_BUDGET",
            reason=f"plan has {len(plan.steps)} steps, "
                   f"the limit is {rules['max_steps_per_plan']}",
        )

    location = dict(cell_state["parts"])
    tool_name = "TOOL"

    for index, action in enumerate(plan.steps):
        args = action.args or {}

        if action.function == "move_to":
            position = args.get("position")
            if position not in positions:
                return CheckResult("REJECT", "UNKNOWN_POSITION",
                                    f"move_to references unknown position {position!r}",
                                    index)
            continue

        if action.function in ("locate", "read_cell_state"):
            part = args.get("part")
            if action.function == "locate" and part not in allowed_parts:
                return CheckResult("REJECT", "UNKNOWN_PART",
                                    f"locate references unknown part {part!r}", index)
            continue

        if action.function == "pick":
            part, source = args.get("part"), args.get("source")
            if part not in allowed_parts:
                return CheckResult("REJECT", "UNKNOWN_PART",
                                    f"pick references unknown part {part!r}", index)
            if source not in positions:
                return CheckResult("REJECT", "UNKNOWN_POSITION",
                                    f"pick references unknown position {source!r}", index)
            if location.get(part) != source:
                return CheckResult(
                    "REJECT", "OUT_OF_ORDER",
                    f"pick({part!r}, {source!r}) but {part} is at "
                    f"{location.get(part)!r} in the current cell state",
                    index,
                )
            location[part] = tool_name
            continue

        if action.function == "place":
            part, slot = args.get("part"), args.get("slot")
            if part not in allowed_parts:
                return CheckResult("REJECT", "UNKNOWN_PART",
                                    f"place references unknown part {part!r}", index)
            if slot not in positions:
                return CheckResult("REJECT", "UNKNOWN_POSITION",
                                    f"place references unknown position {slot!r}", index)
            if location.get(part) != tool_name:
                return CheckResult(
                    "REJECT", "OUT_OF_ORDER",
                    f"place({part!r}, ...) but the gripper is not holding {part!r}",
                    index,
                )
            if slot in protected_slots:
                return CheckResult(
                    "REJECT", "PROTECTED_SLOT",
                    f"{slot} is protected and the order does not explicitly name it",
                    index,
                )
            if positions[slot].get("kind") == "tray_slot" and rules.get("one_part_per_slot"):
                occupant = next(
                    (p for p, loc in location.items() if loc == slot and p != part),
                    None,
                )
                if occupant is not None:
                    return CheckResult(
                        "REJECT", "RULE_OVERRIDE",
                        f"{slot} already holds {occupant!r}; one-part-per-slot "
                        "would be broken",
                        index,
                    )
            location[part] = slot
            continue

        return CheckResult("REJECT", "UNKNOWN_FUNCTION",
                            f"{action.function!r} is not an allowlisted function", index)

    return CheckResult(status="OK")
