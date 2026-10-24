from RoboDK_Experiment.robodk_adaptive.models import *
from RoboDK_Experiment.robodk_adaptive.validator import PlanValidator
from RoboDK_Experiment.robodk_adaptive import TaskState

def scene():
 parts={f'Part_{x}':Part(f'Part_{x}',Pose(i*100,0,50)) for i,x in enumerate('ABC',1)}
 slots={f'Slot_{x}':Slot(f'Slot_{x}',Pose(i*100,300,60)) for i,x in enumerate('123',1)}
 return SceneSnapshot('scene-1',0,RobotState([],False,'tool','frame'),parts,slots)
def spec(): return TaskSpec('t',['Part_A','Part_B','Part_C'],['Part_A','Part_B','Part_C'],{'Part_A':'Slot_1','Part_B':'Slot_2','Part_C':'Slot_3'})
def test_valid_plan():
 s=scene(); plan=Plan('t','scene-1',Disposition.EXECUTE,'',[Action('inspect',{'object_id':'Part_A'}),Action('pick',{'object_id':'Part_A'}),Action('place',{'object_id':'Part_A','slot_id':'Slot_1'}),Action('move_to',{'target_id':'Clearance'})]); assert not PlanValidator(spec()).validate(plan,s)
def test_unknown_skill_rejected():
 s=scene(); plan=Plan('t','scene-1',Disposition.EXECUTE,'',[Action('shell',{})]); assert PlanValidator(spec()).validate(plan,s)
def test_task_state_progress():
 s=scene(); t=TaskState(spec(),s); s.parts['Part_A'].status=PartStatus.PLACED; s.parts['Part_A'].verified_slot='Slot_1'; assert t.completed_parts==['Part_A']; assert t.remaining_parts==['Part_B','Part_C']
def test_gate_consumed_once():
 t=TaskState(spec(),scene()); assert t.event_once('e'); assert not t.event_once('e')
