from __future__ import annotations
from .models import *
class Preflight:
    def __init__(self,adapter): self.adapter=adapter
    def check(self,plan,snapshot):
        if not self.adapter.is_simulation(): return ['RoboDK is not in simulation mode']
        if plan.based_on_snapshot!=snapshot.snapshot_id: return ['snapshot changed before preflight']
        # The adapter's motion tests are isolated/transactional when available.
        try: return list(self.adapter.preflight(plan.actions))
        except Exception as exc: return [f'preflight exception: {exc}']
