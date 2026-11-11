"""Step 2: a valid plan parses; a malformed/nonsense answer raises a
caught invalid-format error, never a crash.
"""

import pytest
from pydantic import ValidationError

from langchain_app.schemas import PlannerOutput


def test_valid_plan_parses():
    output = PlannerOutput.model_validate({
        "response": {
            "type": "plan",
            "steps": [{"function": "move_to", "args": {"position": "HOME"}}],
        },
    })
    assert output.response.type == "plan"
    assert output.response.steps[0].function == "move_to"


def test_question_and_refusal_parse():
    question = PlannerOutput.model_validate({
        "response": {"type": "question", "message": "Which spacer?"},
    })
    assert question.response.type == "question"

    refusal = PlannerOutput.model_validate({
        "response": {"type": "refusal", "reason": "Not allowed"},
    })
    assert refusal.response.type == "refusal"


def test_empty_steps_plan_is_rejected():
    with pytest.raises(ValidationError):
        PlannerOutput.model_validate({"response": {"type": "plan", "steps": []}})


def test_unknown_type_is_rejected():
    with pytest.raises(ValidationError):
        PlannerOutput.model_validate({"response": {"type": "nonsense"}})


def test_unknown_function_name_is_rejected():
    with pytest.raises(ValidationError):
        PlannerOutput.model_validate({
            "response": {
                "type": "plan",
                "steps": [{"function": "delete_everything", "args": {}}],
            },
        })
