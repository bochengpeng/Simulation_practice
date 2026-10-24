import json

from RoboDK_Experiment.robodk_adaptive.planner import Planner
from RoboDK_Experiment.robodk_adaptive.models import *
from RoboDK_Experiment.robodk_adaptive.experiment import Experiment
from RoboDK_Experiment.robodk_adaptive.task_state import TaskState


PLAN = {
    "task_id": "t",
    "based_on_snapshot": "s",
    "disposition": "execute",
    "actions": [],
}


def test_plain_json_reply_is_parsed():
    assert Planner._response_data(json.dumps(PLAN))["task_id"] == "t"


def test_plan_inside_markdown_is_unwrapped():
    output = "Here is the plan:\n```json\n" + json.dumps(PLAN) + "\n```"
    assert Planner._response_data(output.encode("utf-8"))["task_id"] == "t"


def test_reply_without_a_plan_is_rejected():
    for output in ("", "no json here", json.dumps({"task_id": "t"})):
        try:
            Planner._response_data(output)
            assert False, f"expected ValueError for {output!r}"
        except ValueError:
            pass


def test_fast_replan_uses_local_compiler():
    parts = {
        "Part_A": Part("Part_A", Pose(0, 0, 50)),
        "Part_B": Part("Part_B", Pose(100, 0, 50)),
    }
    slots = {
        "Slot_1": Slot("Slot_1", Pose(0, 300, 60)),
        "Slot_2": Slot("Slot_2", Pose(100, 300, 60)),
    }
    snapshot = SceneSnapshot("scene-fast", 0, RobotState([], False, "tool", "frame"), parts, slots)
    spec = TaskSpec("t", ["Part_A", "Part_B"], ["Part_A", "Part_B"], {"Part_A": "Slot_1", "Part_B": "Slot_2"})
    from RoboDK_Experiment.robodk_adaptive.task_state import TaskState
    planner = Planner("llm")
    plan = planner.fast_replan(spec, TaskState(spec, snapshot), {"code": "OBJECT_MOVED"})
    assert plan.based_on_snapshot == "scene-fast"
    assert planner.calls[-1]["provider"] == "deterministic-fast-replan"


def test_selected_model_is_kept_on_planner():
    planner = Planner("llm", model="qwen3:4b")
    assert planner.model == "qwen3:4b"


def test_timeout_fallback_disables_repeated_llm_planning():
    parts = {
        "Part_A": Part("Part_A", Pose(0, 0, 50)),
        "Part_B": Part("Part_B", Pose(100, 0, 50)),
    }
    slots = {
        "Slot_1": Slot("Slot_1", Pose(0, 300, 60)),
        "Slot_2": Slot("Slot_2", Pose(100, 300, 60)),
    }
    snapshot = SceneSnapshot(
        "scene-timeout",
        0,
        RobotState([], False, "tool", "frame"),
        parts,
        slots,
    )
    spec = TaskSpec(
        "t",
        ["Part_A", "Part_B"],
        ["Part_A", "Part_B"],
        {"Part_A": "Slot_1", "Part_B": "Slot_2"},
    )
    experiment = Experiment.__new__(Experiment)
    experiment.spec = spec
    experiment.state = TaskState(spec, snapshot)
    experiment.planner = Planner("llm")
    experiment.replanning_mode = "full-llm"
    experiment.max_replans = 9
    experiment.llm_unavailable = False
    experiment.allow_local_safe_plan = True
    experiment.logger = type("Logger", (), {"event": lambda *_args: None})()
    calls = []

    def failed_plan(*_args, **_kwargs):
        calls.append("llm")
        raise RuntimeError("LLM_UNAVAILABLE: timeout")

    def local_plan(*_args, **_kwargs):
        calls.append("local")
        return experiment.planner.registry.compile(spec, snapshot)

    experiment.planner.plan = failed_plan
    experiment.planner.fast_replan = lambda *_args, **_kwargs: Plan(
        spec.task_id,
        snapshot.snapshot_id,
        Disposition.EXECUTE,
        "local",
        local_plan(),
    )

    Experiment._plan(experiment, None)
    Experiment._plan(experiment, None)

    assert calls == ["llm", "local", "local"]
    assert experiment.llm_unavailable is True


def test_run_logger_summary_has_stable_fields(tmp_path):
    from RoboDK_Experiment.robodk_adaptive.run_logger import RunLogger
    logger = RunLogger(tmp_path, "task-test")
    logger.write_summary({
        "task_id": "task-test",
        "success": True,
        "model": "qwen3:4b",
        "elapsed_s": 1.25,
    })
    header = (tmp_path / "trial_summaries.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "ai_time_s" in header
    assert "elapsed_s" in header


def _prompt_fixture():
    parts = {
        "Part_A": Part("Part_A", Pose(600, 350, 50)),
        "Part_B": Part("Part_B", Pose(600, 0, 50)),
    }
    slots = {
        "Slot_1": Slot("Slot_1", Pose(150, -300, 60)),
        "Slot_2": Slot("Slot_2", Pose(450, -300, 60)),
    }
    snapshot = SceneSnapshot("scene-prompt", 0, RobotState([], False, "tool", "frame"), parts, slots)
    spec = TaskSpec("task-AB", ["Part_A", "Part_B"], ["Part_A", "Part_B"], {"Part_A": "Slot_1", "Part_B": "Slot_2"})
    from RoboDK_Experiment.robodk_adaptive.task_state import TaskState
    return spec, TaskState(spec, snapshot)


def test_model_is_not_asked_to_copy_ids_it_tends_to_garble():
    # qwen3:4b returned "scene-9c0:211e" for "scene-9c0a211e", so every plan
    # failed validation as "stale snapshot". The ids are filled in locally.
    from RoboDK_Experiment.robodk_adaptive.planner import PLAN_SCHEMA
    spec, state = _prompt_fixture()
    prompt = Planner("llm").build_prompt(spec, state)
    assert "task_id" not in prompt and "snapshot_id" not in prompt
    assert "scene-prompt" not in json.dumps(prompt)
    assert "task_id" not in PLAN_SCHEMA["properties"] and "based_on_snapshot" not in PLAN_SCHEMA["properties"]
    assert PLAN_SCHEMA["required"] == ["disposition", "actions"]


def test_failure_feedback_in_the_prompt_leaves_out_internal_ids():
    # The model once blended the feedback's event id into the snapshot id.
    spec, state = _prompt_fixture()
    feedback = {"code": "OBJECT_MOVED", "reason": "Part_A moved", "event_id": "task-AB:relocate:Part_A",
                "affected_object": "Part_A", "failed_plan_id": "plan-001"}
    prompt = Planner("llm").build_prompt(spec, state, feedback)
    assert prompt["failure"] == {"code": "OBJECT_MOVED", "reason": "Part_A moved", "affected_object": "Part_A"}
    assert "relocate" not in json.dumps(prompt)


def test_schema_and_instruction_only_offer_dispositions_the_parser_accepts():
    # The prompt once offered "replan", which Disposition rejects, so models
    # that followed it were reported as LLM_UNAVAILABLE.
    from RoboDK_Experiment.robodk_adaptive.planner import PLAN_SCHEMA
    spec, state = _prompt_fixture()
    assert set(PLAN_SCHEMA["properties"]["disposition"]["enum"]) == {d.value for d in Disposition}
    assert "replan" not in Planner("llm").build_prompt(spec, state)["instruction"].replace("replanning", "")


def test_prompt_spells_out_the_action_format_and_an_example():
    spec, state = _prompt_fixture()
    prompt = Planner("llm").build_prompt(spec, state)
    assert prompt["action_schema"]["move_to"] == ["move_to", "Clearance or Home"]
    assert prompt["action_schema"]["place"] == ["place", "current_part", "destination_slot"]
    assert prompt["example_actions"][2] == ["place", "Part_A", "Slot_1"]


def test_prompt_asks_for_the_current_part_only():
    # Only the current part's actions are executed (Experiment._prefix), so
    # planning the others just makes the model generate tokens that are thrown away.
    spec, state = _prompt_fixture()
    prompt = Planner("llm").build_prompt(spec, state)
    assert prompt["current_part"] == "Part_A" and prompt["destination_slot"] == "Slot_1"
    assert "ONLY current_part" in prompt["instruction"]
    assert {a[1] for a in prompt["example_actions"] if a[0] != "move_to"} == {"Part_A"}
    assert "requested_order" not in prompt

    state.mark_completed("Part_A")
    later = Planner("llm").build_prompt(spec, state)
    assert later["current_part"] == "Part_B" and later["destination_slot"] == "Slot_2"


def test_prompt_is_compact_and_has_no_coordinates():
    spec, state = _prompt_fixture()
    prompt = Planner("llm").build_prompt(spec, state)
    text = json.dumps(prompt)
    assert "600" not in text and '"x"' not in text and "rx" not in text  # no poses
    assert prompt["scene"]["parts"]["Part_A"] == {"status": "pending", "available": True}
    assert prompt["scene"]["slots"]["Slot_1"] == {"occupant": None}
    assert prompt["scene"]["held_object"] is None
    assert len(text) < 1800


def test_reply_does_not_ask_for_a_reason():
    from RoboDK_Experiment.robodk_adaptive.planner import PLAN_SCHEMA
    assert "reason" not in PLAN_SCHEMA["properties"] and "reason" not in PLAN_SCHEMA["required"]
