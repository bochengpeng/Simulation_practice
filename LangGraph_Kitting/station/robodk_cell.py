"""RoboDKCell: the thin adapter between the five functions and the RoboDK
kitting station built by build_kitting_station.py.

Everything that decides whether a plan is *allowed* to run lives outside
this class (the checker). This class only knows how to physically carry
out one already-approved function call, or to report what it sees.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from robodk.robolink import COLLISION_OFF, ITEM_TYPE_ROBOT, Robolink
from robodk.robomath import transl

from station.layout import seat_center_z

PROJECT_DIR = Path(__file__).resolve().parent
STATION_PATH = PROJECT_DIR / "Kitting_SSM3_Station.rdk"
CAPABILITY_PATH = PROJECT_DIR.parent / "config" / "capability.yaml"

ROBOT_NAME = "Doosan Robotics M0609 White"
TOOL_NAME = "SSM3_Motion_TCP"
GRIPPER_NAME = "OnRobot-2FG7-Gripper-Mechanism"
GRIPPER_LINK_IDS = 6
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
        self._allow_gripper_contact()
        self._last_target = None

    def _allow_gripper_contact(self) -> None:
        """Gripping and releasing means the fingers reach the bin/slot/return
        plate by design. Switch off only those gripper-vs-plate pairs; the
        arm-vs-table and every other pair stay checked."""
        gripper = self.rdk.Item(GRIPPER_NAME)
        if not gripper.Valid():
            return
        plates = [i for i in self.rdk.ItemList() if i.Name().endswith("_Model")]
        for plate in plates:
            # Every gripper link (base and each finger) has its own collision id.
            for link_id in range(GRIPPER_LINK_IDS):
                self.rdk.setCollisionActivePair(COLLISION_OFF, gripper, plate, link_id, 0)

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
        every other part is in its home bin. Parts are physically seated
        on their positions, not just relabelled."""
        # Snap to the HOME joints rather than driving there, so a previous run
        # that stopped mid-motion (even inside a collision) cannot block this one.
        self.robot.setJoints(self._item("HOME").Joints())
        self._last_target = "HOME"
        self._seat("BOLT", "SLOT_1")
        self._seat("SPACER_SHORT", "SLOT_2")
        self._seat("SPACER_LONG", "BIN_3")

    # -- geometry helpers ----------------------------------------------

    def _pose_in_frame(self, item):
        return self.frame.PoseAbs().inv() * item.PoseAbs()

    def _seat(self, part: str, position: str) -> None:
        """Make `position` the part's parent AND rest the part upright on
        that position's surface. Re-parenting alone does not keep a part's
        place when the parent is a target, so the pose is set explicitly."""
        target = self._item(position)
        kind = self.capability["positions"][position]["kind"]
        pose = self._pose_in_frame(target)
        z = seat_center_z(kind, part)
        part_item = self._item(part)
        part_item.setParentStatic(target)
        part_item.setPoseAbs(self.frame.PoseAbs() * transl(pose[0, 3], pose[1, 3], z))

    def _descend(self, position: str, part: str) -> None:
        """Linear move straight down from the hover target to the part's
        centre height, which is where the gripper closes or opens."""
        kind = self.capability["positions"][position]["kind"]
        low = self._pose_in_frame(self._item(position)).copy()
        low[2, 3] = seat_center_z(kind, part)
        self.robot.MoveL(low)

    def _retreat(self, position: str) -> None:
        self.robot.MoveL(self._pose_in_frame(self._item(position)))

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
        self._descend(source, part)
        # The gripper closes with the part centred on the TCP, so making the
        # part a child of the tool carries it with the arm.
        part_item.setParentStatic(self.tool)
        self._retreat(source)

    def place(self, part: str, slot: str) -> None:
        part_item = self._item(part)
        if part_item.Parent().Name() != self.tool.Name():
            raise CellExecutionError(f"place({part!r}, ...) but the gripper is empty")
        self.move_to(slot)
        self._descend(slot, part)
        self._seat(part, slot)
        self._retreat(slot)

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
