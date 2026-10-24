from .models import *

class SkillRegistry:
    names={'inspect','move_to','pick','place'}
    def compile(self, spec, snapshot):
        actions=[]
        held=snapshot.robot.held_object
        for index,part in enumerate(spec.order):
            if (
                snapshot.parts[part].status==PartStatus.PLACED
                or not snapshot.parts[part].available
            ):
                continue
            if part==held:
                # A pick already succeeded for this part before an earlier
                # attempt was interrupted (e.g. a recovered table
                # collision). Resume with the place only: a held part is
                # not pending on the table, so issuing a second pick on an
                # already-grasped object would be invalid.
                actions.append(Action('place',{'object_id':part,'slot_id':spec.destinations[part]}))
            else:
                actions += [Action('inspect',{'object_id':part}),Action('pick',{'object_id':part}),Action('place',{'object_id':part,'slot_id':spec.destinations[part]})]
            if index < len(spec.order)-1: actions.append(Action('move_to',{'target_id':'Clearance'}))
        if actions: actions.append(Action('move_to',{'target_id':'Clearance'}))
        return actions
