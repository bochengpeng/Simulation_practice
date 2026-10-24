import csv

from RoboDK_Experiment.robodk_adaptive.experiment import Experiment
from RoboDK_Experiment.robodk_adaptive.models import *
from RoboDK_Experiment.robodk_adaptive.planner import Planner
from RoboDK_Experiment.robodk_adaptive.run_logger import RunLogger

TIMEOUT = "LLM_UNAVAILABLE: Ollama request failed: Ollama exceeded the 25s planning timeout"
MALFORMED = "LLM_UNAVAILABLE: Ollama request failed: invalid action schema"


class WorkingAdapter:
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


def spec():
    return TaskSpec('t', ['Part_A'], ['Part_A'], {'Part_A': 'Slot_1'})


def failing_plan(message):
    def plan(self, spec, state, feedback=None):
        raise RuntimeError(message)
    return plan


def run(tmp_path, monkeypatch, message, allow):
    monkeypatch.setattr(Planner, 'plan', failing_plan(message))
    adapter = WorkingAdapter()
    experiment = Experiment(
        adapter, spec(), mode='llm', disturbance_mode='none',
        log_dir=str(tmp_path), allow_local_safe_plan=allow,
    )
    return adapter, experiment, experiment.run()


def read_summary(tmp_path):
    with (tmp_path / 'trial_summaries.csv').open(newline='', encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


def test_timeout_with_local_safe_plan_off_fails_the_trial(tmp_path, monkeypatch):
    adapter, experiment, state = run(tmp_path, monkeypatch, TIMEOUT, allow=False)

    assert adapter.place_calls == 0  # the robot never moves on a local plan
    assert experiment.summary['success'] is False
    assert experiment.summary['failure_code'] == 'LLM_TIMEOUT'
    assert state.failures[-1].code == 'LLM_TIMEOUT'
    row = read_summary(tmp_path)[-1]
    assert row['success'] == 'False'
    assert row['failure_code'] == 'LLM_TIMEOUT'
    assert row['allow_local_safe_plan'] == 'off'


def test_other_llm_failure_with_local_safe_plan_off_also_fails(tmp_path, monkeypatch):
    adapter, experiment, _ = run(tmp_path, monkeypatch, MALFORMED, allow=False)

    assert adapter.place_calls == 0
    assert experiment.summary['failure_code'] == 'LLM_UNAVAILABLE'
    assert read_summary(tmp_path)[-1]['allow_local_safe_plan'] == 'off'


def test_timeout_with_local_safe_plan_on_continues_and_succeeds(tmp_path, monkeypatch):
    adapter, experiment, _ = run(tmp_path, monkeypatch, TIMEOUT, allow=True)

    assert adapter.place_calls == 1
    assert experiment.summary['success'] is True
    row = read_summary(tmp_path)[-1]
    assert row['success'] == 'True'
    assert row['allow_local_safe_plan'] == 'on'


def test_summary_csv_without_the_new_column_is_migrated_not_misaligned(tmp_path):
    path = tmp_path / 'trial_summaries.csv'
    path.write_text(
        'timestamp_utc,task_id,success,model\n2026-01-01T00:00:00,old-task,True,m1\n',
        encoding='utf-8',
    )

    RunLogger(tmp_path, 'run').write_summary(
        {'task_id': 'new-task', 'success': False, 'allow_local_safe_plan': 'off', 'model': 'm2'}
    )

    old, new = read_summary(tmp_path)
    assert old['task_id'] == 'old-task' and old['model'] == 'm1'
    assert old['allow_local_safe_plan'] == ''  # recorded before the option existed
    assert new['task_id'] == 'new-task' and new['allow_local_safe_plan'] == 'off'
    assert new['model'] == 'm2'
