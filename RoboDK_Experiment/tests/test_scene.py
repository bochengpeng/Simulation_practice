from RoboDK_Experiment.robodk_adaptive.models import *
from RoboDK_Experiment.robodk_adaptive.scene_state import SceneObserver
class Fake:
 def __init__(self): self.s=SceneSnapshot('a',0,RobotState(),{'Part_A':Part('Part_A',Pose(0,0,0))},{})
 def observe(self): return self.s

def test_pose_change_detection():
 f=Fake(); o=SceneObserver(f); o.read(); f.s=SceneSnapshot('b',1,RobotState(),{'Part_A':Part('Part_A',Pose(3,0,0))},{}); _,changed=o.read(); assert changed
