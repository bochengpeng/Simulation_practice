from __future__ import annotations
from .models import *
import uuid

class TaskState:
    def __init__(self, spec:TaskSpec, snapshot:SceneSnapshot):
        spec.validate()
        self.spec=spec
        self.snapshot=snapshot
        self.completed_verified=set()
        self.completed_actions=[]
        self.consumed_events=set()
        self.failures=[]
        self.retry_count=0
        self.replan_count=0
        self.active_plan_id=None
    @property
    def completed_parts(self):
        return [
            p for p in self.spec.order
            if p in self.completed_verified
            or self.snapshot.parts[p].status==PartStatus.PLACED
        ]
    @property
    def remaining_parts(self): return [p for p in self.spec.order if p not in self.completed_parts]
    def event_once(self,event_id):
        if event_id in self.consumed_events: return False
        self.consumed_events.add(event_id); return True
    def apply_snapshot(self,snapshot):
        for part in self.completed_verified:
            if part in snapshot.parts:
                snapshot.parts[part].status=PartStatus.PLACED
                snapshot.parts[part].verified_slot=self.spec.destinations[part]
                snapshot.slots[self.spec.destinations[part]].occupant=part
        self.snapshot=snapshot

    def mark_completed(self, part):
        self.completed_verified.add(part)
        self.snapshot.parts[part].status=PartStatus.PLACED
        self.snapshot.parts[part].verified_slot=self.spec.destinations[part]
        self.snapshot.slots[self.spec.destinations[part]].occupant=part

    def record_failure(self,failure): self.failures.append(failure)

    def fresh_task_id(self): return self.spec.task_id

    def verify(self):
        for part in self.spec.requested_parts:
            p=self.snapshot.parts[part]; slot=self.spec.destinations[part]
            if p.status!=PartStatus.PLACED or p.verified_slot!=slot: return False
        return True

    def summary(self):
        return {
            'task_id': self.spec.task_id,
            'completed_parts': self.completed_parts,
            'remaining_order': self.remaining_parts,
            'verified_slots': {
                slot.slot_id: slot.occupant
                for slot in self.snapshot.slots.values()
            },
            'snapshot_id': self.snapshot.snapshot_id,
            'replan_count': self.replan_count,
        }
