from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any
import json, math, time

class PartStatus(str, Enum):
    PENDING='pending'; HELD='held'; PLACED='placed_verified'; BLOCKED='blocked'; UNKNOWN='unknown'
class Disposition(str, Enum):
    EXECUTE='execute'; STOP='stop'; CLARIFY='clarify'

@dataclass(frozen=True)
class Pose:
    x: float; y: float; z: float; rx: float=0; ry: float=0; rz: float=0
    def distance(self, other:'Pose')->float: return math.dist((self.x,self.y,self.z),(other.x,other.y,other.z))
    def changed(self, other:'Pose', pos_tol:float, rot_tol:float)->bool:
        def angle_delta(first, second):
            return abs((first - second + 180.0) % 360.0 - 180.0)
        return (
            self.distance(other) > pos_tol
            or max(
                angle_delta(first, second)
                for first, second in zip(
                    (self.rx, self.ry, self.rz),
                    (other.rx, other.ry, other.rz),
                )
            ) > rot_tol
        )

@dataclass
class Part:
    object_id:str; pose:Pose; status:PartStatus=PartStatus.PENDING; parent:str='Workcell'; verified_slot:str|None=None; available:bool=True
@dataclass
class Slot:
    slot_id:str; pose:Pose; occupant:str|None=None; reserved_by:str|None=None
@dataclass
class RobotState:
    joints:list[float]=field(default_factory=list); moving:bool=False; active_tool:str=''; frame:str=''; held_object:str|None=None; checkpoint:str|None=None
@dataclass
class SceneSnapshot:
    snapshot_id:str; timestamp:float; robot:RobotState; parts:dict[str,Part]; slots:dict[str,Slot]; config_version:str='1'
    def to_dict(self): return asdict(self)
@dataclass
class Action:
    skill:str; args:dict[str,Any]
@dataclass
class Plan:
    task_id:str; based_on_snapshot:str; disposition:Disposition; reason:str; actions:list[Action]; plan_id:str=''
    def to_dict(self): return {'task_id':self.task_id,'based_on_snapshot':self.based_on_snapshot,'disposition':self.disposition.value,'reason':self.reason,'actions':[asdict(a) for a in self.actions],'plan_id':self.plan_id}
@dataclass
class TaskSpec:
    task_id:str; requested_parts:list[str]; order:list[str]; destinations:dict[str,str]; goal:str=''
    def validate(self):
        if not self.requested_parts or self.order!=self.requested_parts: raise ValueError('requested order must be non-empty and authoritative')
        if set(self.destinations)!=set(self.requested_parts): raise ValueError('every requested part needs a destination')
@dataclass
class Failure:
    code:str; reason:str; event_id:str|None=None; affected_object:str|None=None; failed_plan_id:str|None=None

class ExecutionFailure(RuntimeError):
    """Raised by Executor when a dispatched action fails. Carries a
    structured ``code``/``reason`` (e.g. 'TABLE_COLLISION') instead of a
    bare message so Experiment can turn it into a Failure, feed it back
    into the planner, and resume the same already-approved plan rather
    than crash the whole run."""
    def __init__(self, code:str, reason:str, action_skill:str|None=None):
        super().__init__(reason)
        self.code=code; self.reason=reason; self.action_skill=action_skill

def json_default(value):
    if isinstance(value,Enum): return value.value
    if hasattr(value,'__dict__'): return value.__dict__
    raise TypeError(type(value).__name__)

def dumps(value): return json.dumps(value, default=json_default, sort_keys=True)
