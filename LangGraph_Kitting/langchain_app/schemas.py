"""The planner's answer schema (Step 2): exactly one of a plan, a question,
or a refusal. This is a first filter only (FR3/FR4) - the checker still
runs on every plan; this schema never allows coordinates or robot code.
"""

from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field, field_validator

FUNCTION_NAMES = ("move_to", "pick", "place", "locate", "read_cell_state")


class Action(BaseModel):
    function: Literal["move_to", "pick", "place", "locate", "read_cell_state"]
    args: dict = Field(default_factory=dict)


class Plan(BaseModel):
    type: Literal["plan"] = "plan"
    steps: list[Action]
    rationale: str = ""

    @field_validator("steps")
    @classmethod
    def steps_non_empty(cls, value: list[Action]) -> list[Action]:
        if not value:
            raise ValueError("a plan must have at least one step")
        return value


class Question(BaseModel):
    type: Literal["question"] = "question"
    message: str


class Refusal(BaseModel):
    type: Literal["refusal"] = "refusal"
    reason: str


class PlannerOutput(BaseModel):
    """Wrapper so `with_structured_output` can enforce the discriminated
    union: exactly one of plan, question or refusal, never a mix."""

    response: Union[Plan, Question, Refusal] = Field(discriminator="type")
