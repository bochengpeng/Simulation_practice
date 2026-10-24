from RoboDK_Experiment.robodk_adaptive.collision_guard import TableCollisionError
from RoboDK_Experiment.robodk_adaptive.experiment import MAX_IDENTICAL_COLLISIONS, Experiment
from RoboDK_Experiment.robodk_adaptive.models import *


class AlwaysCollidingAdapter:
    """Every place() hits the same obstacle on an unchanged scene."""

    def __init__(self):
        self.place_calls = 0

    def observe(self):
        parts = {'Part_A': Part('Part_A', Pose(500, -100, 50))}
        slots = {'Slot_1': Slot('Slot_1', Pose(150, -300, 60))}
        return SceneSnapshot('scene-1', 0, RobotState([], False, 'tool', 'frame'), parts, slots)

    def is_simulation(self):
        return True

    def preflight(self, actions):
        return []

    def inspect(self, object_id):
        return self.observe().parts[object_id]

    def move_to(self, target_id):
        pass

    def pick(self, object_id):
        pass

    def place(self, object_id, slot_id):
        self.place_calls += 1
        raise TableCollisionError('Pick: the linear descent would collide (colliding: SSM3_Zone_B[0]<->Robot[3])')


def test_repeated_identical_table_collision_stops_instead_of_using_every_replan(tmp_path):
    adapter = AlwaysCollidingAdapter()
    spec = TaskSpec('t', ['Part_A'], ['Part_A'], {'Part_A': 'Slot_1'})
    experiment = Experiment(
        adapter, spec, disturbance_mode='none', log_dir=str(tmp_path), max_replans=9
    )

    state = experiment.run()

    assert adapter.place_calls == MAX_IDENTICAL_COLLISIONS
    assert state.replan_count < 9
    assert state.failures[-1].code == 'TABLE_COLLISION'
    assert 'SSM3_Zone_B' in state.failures[-1].reason
    assert experiment.summary['success'] is False
