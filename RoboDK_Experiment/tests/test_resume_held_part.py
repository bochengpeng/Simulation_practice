from RoboDK_Experiment.robodk_adaptive.models import *
from RoboDK_Experiment.robodk_adaptive.skills import SkillRegistry
from RoboDK_Experiment.robodk_adaptive.validator import PlanValidator


def spec():
    return TaskSpec(
        't', ['Part_A', 'Part_B'], ['Part_A', 'Part_B'],
        {'Part_A': 'Slot_1', 'Part_B': 'Slot_2'},
    )


def snapshot_with_held(held):
    parts = {
        'Part_A': Part('Part_A', Pose(0, 0, 50), status=PartStatus.HELD if held == 'Part_A' else PartStatus.PENDING),
        'Part_B': Part('Part_B', Pose(100, 0, 50), status=PartStatus.HELD if held == 'Part_B' else PartStatus.PENDING),
    }
    slots = {
        'Slot_1': Slot('Slot_1', Pose(0, 300, 60)),
        'Slot_2': Slot('Slot_2', Pose(100, 300, 60)),
    }
    return SceneSnapshot('scene-held', 0, RobotState([], False, 'tool', 'frame', held_object=held), parts, slots)


def test_compile_resumes_held_part_with_place_only():
    # A pick already succeeded before an earlier attempt was interrupted
    # (e.g. a recovered table collision mid-transit). The deterministic
    # compiler must not issue a second pick for the already-grasped part.
    snapshot = snapshot_with_held('Part_A')
    actions = SkillRegistry().compile(spec(), snapshot)
    first_part_actions = [a for a in actions if a.args.get('object_id') == 'Part_A']
    assert [a.skill for a in first_part_actions] == ['place']
    # Part_B, never touched, still gets the full cycle.
    second_part_actions = [a for a in actions if a.args.get('object_id') == 'Part_B']
    assert [a.skill for a in second_part_actions] == ['inspect', 'pick', 'place']


def test_compile_normal_when_nothing_held():
    snapshot = snapshot_with_held(None)
    actions = SkillRegistry().compile(spec(), snapshot)
    first_part_actions = [a for a in actions if a.args.get('object_id') == 'Part_A']
    assert [a.skill for a in first_part_actions] == ['inspect', 'pick', 'place']


def test_validator_accepts_place_only_plan_for_held_part():
    snapshot = snapshot_with_held('Part_A')
    plan = Plan(
        't', 'scene-held', Disposition.EXECUTE, '',
        [
            Action('place', {'object_id': 'Part_A', 'slot_id': 'Slot_1'}),
            Action('move_to', {'target_id': 'Clearance'}),
        ],
    )
    assert not PlanValidator(spec()).validate(plan, snapshot)


def test_validator_rejects_second_pick_on_held_part():
    snapshot = snapshot_with_held('Part_A')
    plan = Plan(
        't', 'scene-held', Disposition.EXECUTE, '',
        [
            Action('pick', {'object_id': 'Part_A'}),
            Action('place', {'object_id': 'Part_A', 'slot_id': 'Slot_1'}),
        ],
    )
    errors = PlanValidator(spec()).validate(plan, snapshot)
    assert errors and any('duplicate' in e or 'overlapping' in e for e in errors)
