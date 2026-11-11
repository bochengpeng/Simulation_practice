"""Open vs closed loop edge behaviour (Step 5) on a simulated step failure,
using PythonCell (NFR5: no RoboDK needed) and a fake planner chain so this
runs without a live model.

Reproduces the T6 shape: the plan is approved against a cell-state
snapshot that is already stale by the time execution reaches the pick -
the real cell has SPACER_LONG at BIN_2, not BIN_3, because a relocation
happened after the (stale) snapshot was taken.
"""

from langchain_app.capability_loader import load_capability
from langchain_app.schemas import Action, Plan, PlannerOutput
from orchestrator.graph import build_graph, initial_state
from station.python_cell import PythonCell

CAPABILITY = load_capability()

STALE_SNAPSHOT = {
    "parts": {"BOLT": "SLOT_1", "SPACER_SHORT": "SLOT_2", "SPACER_LONG": "BIN_3"},
    "last_target": "HOME",
    "tool_holds": None,
}


FIRST_PLAN = PlannerOutput(response=Plan(steps=[
    Action(function="move_to", args={"position": "SLOT_2"}),
    Action(function="pick", args={"part": "SPACER_SHORT", "source": "SLOT_2"}),
    Action(function="move_to", args={"position": "RETURN"}),
    Action(function="place", args={"part": "SPACER_SHORT", "slot": "RETURN"}),
    Action(function="move_to", args={"position": "BIN_3"}),
    Action(function="pick", args={"part": "SPACER_LONG", "source": "BIN_3"}),
    Action(function="move_to", args={"position": "SLOT_2"}),
    Action(function="place", args={"part": "SPACER_LONG", "slot": "SLOT_2"}),
    Action(function="move_to", args={"position": "HOME"}),
]))

# What a real replan would look like once the cell state is re-observed
# after the failure: only the remaining work, from the real location.
REPLAN = PlannerOutput(response=Plan(steps=[
    Action(function="move_to", args={"position": "BIN_2"}),
    Action(function="pick", args={"part": "SPACER_LONG", "source": "BIN_2"}),
    Action(function="move_to", args={"position": "SLOT_2"}),
    Action(function="place", args={"part": "SPACER_LONG", "slot": "SLOT_2"}),
    Action(function="move_to", args={"position": "HOME"}),
]))


class FakeChain:
    """Returns the stale-snapshot plan first, then the corrected plan -
    used only if the graph actually replans (closed loop)."""

    def __init__(self):
        self.calls = 0

    def invoke(self, inputs, config=None):
        self.calls += 1
        return FIRST_PLAN if self.calls == 1 else REPLAN


def _run(loop_mode: str):
    cell = PythonCell()
    cell.inject_failure("SPACER_LONG", "BIN_2")  # real cell is already stale
    chain = FakeChain()
    events = []
    graph = build_graph(cell, CAPABILITY, chain, logger=events.append)
    state = initial_state("T6", "Replace the short spacer in slot 2 with a long one",
                           STALE_SNAPSHOT, loop_mode)
    final = graph.invoke(state, config={"recursion_limit": 100})
    return final, chain, cell


def test_open_loop_stops_without_recovering():
    final, chain, cell = _run("open")
    assert chain.calls == 1
    assert final["final_status"] == "FAILED_NOT_RECOVERED"
    assert cell.locate("SPACER_LONG") == "BIN_2"  # nothing moved after the failure


def test_closed_loop_replans_and_recovers():
    final, chain, cell = _run("closed")
    assert chain.calls == 2
    assert final["final_status"] == "SUCCESS"
    assert cell.locate("SPACER_LONG") == "SLOT_2"
