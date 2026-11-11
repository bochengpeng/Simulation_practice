"""RoboDKCell: the thin adapter between the five functions and the RoboDK
kitting station built by build_kitting_station.py.

Everything that decides whether a plan is *allowed* to run lives outside
this class (the checker). This class only knows how to physically carry
out one already-approved function call, or to report what it sees.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from robodk.robolink import ITEM_TYPE_ROBOT, Robolink

PROJECT_DIR = Path(__file__).resolve().parent
STATION_PATH = PROJECT_DIR / "Kitting_SSM3_Station.rdk"
CAPABILITY_PATH = PROJECT_DIR.parent / "config" / "capability.yaml"

ROBOT_NAME = "Doosan Robotics M0609 White"
TOOL_NAME = "SSM3_Motion_TCP"
FRAME_NAME = "SSM3_Workcell"

ALLOWED_FUNCTIONS = ("move_to", "pick", "place", "locate", "read_cell_state")


class CellExecutionError(RuntimeError):
    """A function call could not be carried out as asked."""


class RoboDKCell:
    def __init__(self):
        with CAPABILITY_PATH.open("r", encoding="utf-8") as stream:
            self.capability = yaml.safe_load(stream)
        self.rdk = Robolink()
        station = self.rdk.Item("Kitting_SSM3_Station", 1)
        if not station.Valid():
            raise CellExecutionError(
                "Kitting_SSM3_Station is not open in RoboDK. "
                "Run build_kitting_station.py first."
            )
        self.robot = self.rdk.Item(ROBOT_NAME, ITEM_TYPE_ROBOT)
        self.tool = self.rdk.Item(TOOL_NAME)
        self.frame = self.rdk.Item(FRAME_NAME)
        if not (self.robot.Valid() and self.tool.Valid() and self.frame.Valid()):
            raise CellExecutionError("Station is missing robot/tool/frame items")
        self.robot.setPoseFrame(self.frame)
        self.robot.setPoseTool(self.tool)
        self._last_target = None

    # -- required items -----------------------------------------------

    def _item(self, name: str):
        item = self.rdk.Item(name)
        if not item.Valid():
            raise CellExecutionError(f"Unknown item in station: {name!r}")
        return item

    # -- lifecycle -------------------------------------------------------

    def reset(self) -> None:
        """Start state of the starter orders: SLOT_1 holds BOLT, SLOT_2
        holds SPACER_SHORT, SLOT_3/SLOT_4 and the gripper are empty, and
        every other part is in its home bin."""
        self.robot.MoveJ(self._item("HOME"))
        self._last_target = "HOME"
        self._item("BOLT").setParentStatic(self._item("SLOT_1"))
        self._item("SPACER_SHORT").setParentStatic(self._item("SLOT_2"))
        self._item("SPACER_LONG").setParentStatic(self._item("BIN_3"))

    # -- the five functions ------------------------------------------

    def move_to(self, position: str) -> None:
        target = self._item(position)
        self.robot.MoveJ(target)
        self._last_target = position

    def pick(self, part: str, source: str) -> None:
        part_item = self._item(part)
        current_parent = part_item.Parent().Name()
        if current_parent != source:
            raise CellExecutionError(
                f"pick({part!r}, {source!r}) but {part} is at {current_parent!r}"
            )
        self.move_to(source)
        part_item.setParentStatic(self.tool)

    def place(self, part: str, slot: str) -> None:
        part_item = self._item(part)
        if part_item.Parent().Name() != self.tool.Name():
            raise CellExecutionError(f"place({part!r}, ...) but the gripper is empty")
        self.move_to(slot)
        part_item.setParentStatic(self._item(slot))

    def locate(self, part: str) -> str:
        return self._item(part).Parent().Name()

    def read_cell_state(self) -> dict:
        return self.read_state()

    def read_state(self) -> dict:
        parts = {
            part: self.locate(part)
            for part in self.capability["parts"]
        }
        tool_holds = next(
            (part for part, parent in parts.items() if parent == self.tool.Name()),
            None,
        )
        return {
            "parts": parts,
            "last_target": self._last_target,
            "tool_holds": tool_holds,
        }

    # -- dispatch, test hook -------------------------------------------

    def run_function(self, name: str, args: dict) -> dict:
        if name not in ALLOWED_FUNCTIONS:
            raise CellExecutionError(f"Function not allowlisted: {name!r}")
        result = getattr(self, name)(**(args or {}))
        return {"function": name, "args": args, "result": result}

    def inject_failure(self, part: str, new_parent: str) -> None:
        """Test T6: move a part to another parent while a plan waits."""
        self._item(part).setParentStatic(self._item(new_parent))
