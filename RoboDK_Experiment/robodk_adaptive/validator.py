from __future__ import annotations
from .models import *

ALLOWED_SKILLS={'inspect','move_to','pick','place'}
TARGETS={'Home','Clearance'}

class PlanValidator:
    def __init__(self,spec:TaskSpec): self.spec=spec
    def validate(self,plan:Plan,snapshot:SceneSnapshot):
        self.spec.validate(); errors=[]
        if plan.task_id!=self.spec.task_id: errors.append('task_id mismatch')
        if plan.based_on_snapshot!=snapshot.snapshot_id: errors.append('stale snapshot')
        if plan.disposition!=Disposition.EXECUTE:
            if plan.actions: errors.append('stop/clarify plans must have no actions')
            return errors
        pending=set(self.spec.order)-{p.object_id for p in snapshot.parts.values() if p.status==PartStatus.PLACED}
        # A part already attached to the gripper (e.g. resumed after a
        # recovered table collision) does not need a fresh pick in this
        # candidate; seed the simulated "holding" state from the snapshot
        # instead of assuming an empty tool.
        picked=snapshot.robot.held_object; seen_places=set(); checkpoint=False
        for i,action in enumerate(plan.actions):
            if action.skill not in ALLOWED_SKILLS: errors.append(f'unknown skill {action.skill}'); continue
            args=action.args
            if action.skill=='inspect':
                if args.get('object_id') not in self.spec.requested_parts: errors.append('inspect unknown object')
            elif action.skill=='move_to':
                if args.get('target_id') not in TARGETS: errors.append('move_to target is not allowlisted')
                if args.get('target_id')=='Clearance': checkpoint=True
            elif action.skill=='pick':
                obj=args.get('object_id')
                if obj not in pending: errors.append(f'pick not pending: {obj}')
                if picked is not None: errors.append('duplicate/overlapping pick')
                picked=obj
            elif action.skill=='place':
                obj=args.get('object_id'); slot=args.get('slot_id')
                if obj!=picked: errors.append('place must follow its pick')
                if obj not in pending or slot!=self.spec.destinations.get(obj): errors.append('invalid destination')
                if slot in seen_places or (slot in snapshot.slots and snapshot.slots[slot].occupant): errors.append('target occupied or repeated')
                seen_places.add(slot); picked=None
        if picked is not None: errors.append('picked object not placed')
        if errors: return errors
        # A checkpoint is required between cycles; compiler plans may end at Clearance.
        cycles=sum(1 for a in plan.actions if a.skill=='pick')
        if cycles>1 and not checkpoint: return ['missing verified Clearance checkpoint']
        return []
