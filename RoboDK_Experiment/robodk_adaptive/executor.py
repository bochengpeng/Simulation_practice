from __future__ import annotations
from .models import *
from .collision_guard import TableCollisionError
class Executor:
    def __init__(self,adapter,observer,logger=None): self.adapter=adapter; self.observer=observer; self.logger=logger
    def execute(self,plan,state):
        if plan.disposition!=Disposition.EXECUTE: return
        for action in plan.actions:
            try:
                if action.skill=='inspect': self.adapter.inspect(action.args['object_id'])
                elif action.skill=='move_to': self.adapter.move_to(action.args['target_id'])
                elif action.skill=='pick': self.adapter.pick(action.args['object_id'])
                elif action.skill=='place': self.adapter.place(action.args['object_id'],action.args['slot_id'])
                else: raise RuntimeError('INVALID_PLAN')
            except TableCollisionError as exc:
                raise ExecutionFailure('TABLE_COLLISION',str(exc),action.skill) from exc
            except Exception as exc:
                raise ExecutionFailure('EXECUTION_ERROR',f'Execution failed at {action.skill}: {exc}',action.skill) from exc
            fresh,_=self.observer.read(); state.apply_snapshot(fresh); state.completed_actions.append(action.skill)
            recovery=getattr(self.adapter,'last_collision_recovery',None)
            if recovery:
                if self.logger: self.logger.event('collision_avoidance',{**recovery,'skill':action.skill})
                self.adapter.last_collision_recovery=None
            if self.logger: self.logger.event('action',{'skill':action.skill,'args':action.args,'snapshot_id':state.snapshot.snapshot_id})
