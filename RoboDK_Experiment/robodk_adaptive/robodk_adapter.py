from __future__ import annotations

import shutil
import uuid
import random
import math
from pathlib import Path

from .models import *

OBJECT_ITEM_NAMES = {
    "Part_A": "AUTO_Block",
    "Part_B": "SSM3_Object_B",
    "Part_C": "SSM3_Object_C",
}
ZONE_ITEM_NAMES = {
    "Slot_1": "SSM3_Zone_A",
    "Slot_2": "SSM3_Zone_B",
    "Slot_3": "SSM3_Zone_C",
}
SOURCE_NAMES = {
    "Part_A": "Object_A",
    "Part_B": "Object_B",
    "Part_C": "Object_C",
}
DESTINATION_NAMES = {
    "Slot_1": "Zone_A",
    "Slot_2": "Zone_B",
    "Slot_3": "Zone_C",
}
# Candidate grid for automatic relocation: the free band between the two zone
# rows (zones span y -420..-180 and 30..270). Every candidate is still
# checked against the zone footprints, IK and a dry-run collision test of the
# real approach/descent before it is used, so this is only a search area.
RELOCATION_X_RANGE_MM = (400.0, 620.0)
RELOCATION_Y_RANGE_MM = (-110.0, -40.0)
RELOCATION_Z_MM = 50.0
RELOCATION_GRID_MM = 10.0
RELOCATION_MIN_SEPARATION_MM = 140.0
# Zones are 240x240 mm; keep the 80 mm block (half-width 40) plus a 20 mm
# margin clear of the footprint, measured per axis rather than radially.
ZONE_HALF_SIZE_MM = 120.0
RELOCATION_ZONE_MARGIN_MM = 60.0
RELOCATION_MAX_ATTEMPTS = 100


class RoboDKAdapter:
    def __init__(self, station_path):
        self.station_path = Path(station_path)
        self.pending_pick = None
        self._rdk = None
        self._missing = set()
        self._unreachable = set()
        self.last_relocation = None
        self.last_collision_recovery = None
        self._random = random.SystemRandom()

    def connect(self):
        from robodk.robolink import Robolink

        if self._rdk is None:
            # Keep RoboDK's child stdout/stderr from being inherited by the
            # CLI/GUI process.  An inherited pipe can keep a finished command
            # alive even after the API socket has been disconnected.
            self._rdk = Robolink(close_std_out=True, quit_on_close=False)
        if not self._item("Doosan Robotics M0609 White").Valid():
            self._rdk.AddFile(str(self.station_path))
            deadline = time.time() + 10.0
            while time.time() < deadline:
                if self._item("Doosan Robotics M0609 White").Valid():
                    break
                time.sleep(0.1)
        self._configure_fast_simulation()
        return self._rdk

    def _configure_fast_simulation(self):
        """Use faster simulation-only motion without changing the station file."""
        robot = self._item("Doosan Robotics M0609 White")
        if not robot.Valid():
            return
        self._rdk.setSimulationSpeed(20.0)
        robot.setSpeed(250.0, 500.0)
        robot.setSpeedJoints(45.0)
        robot.setAccelerationJoints(100.0)

    def _item(self, name):
        if self._rdk is None:
            self.connect()
        return self._rdk.Item(name)

    def is_simulation(self):
        from robodk.robolink import RUNMODE_SIMULATE

        return self._rdk is not None and self._rdk.RunMode() == RUNMODE_SIMULATE

    def doctor(self):
        result = {
            "station": str(self.station_path),
            "simulation_mode": False,
            "items": {},
            "python_api": "unknown",
        }
        try:
            self.connect()
            result["simulation_mode"] = self.is_simulation()
            result["python_api"] = "connected"
            required = {
                "robot": "Doosan Robotics M0609 White",
                "frame": "SSM3_Workcell",
                "tool": "OnRobot-2FG7-Gripper",
                "capture": "AUTO_Attach_TCP",
            }
            result["items"] = {
                role: bool(self._item(name).Valid())
                for role, name in required.items()
            }
            result["motion_validation"] = hasattr(
                self._rdk, "CollisionActivePairList"
            )
            if not result["simulation_mode"]:
                result["error"] = "RoboDK is not in simulation mode"
        except Exception as exc:
            result["error"] = str(exc)
        return result

    def close(self):
        if self._rdk is not None:
            try:
                self._rdk.Disconnect()
            except (ConnectionError, OSError):
                # RoboDK may close its API socket first when a CLI-launched
                # instance exits.  The connection is already unusable.
                pass
            self._rdk = None

    def observe(self):
        self.connect()
        robot = self._item("Doosan Robotics M0609 White")
        parts = {}
        for object_id, item_name in OBJECT_ITEM_NAMES.items():
            item = self._item(item_name)
            pose = item.PoseAbs()
            parent = item.Parent()
            available = item.Valid() and object_id not in self._missing
            held = parent.Valid() and "AUTO_Attach" in parent.Name()
            status = (
                PartStatus.HELD
                if held
                else PartStatus.PENDING
                if available
                else PartStatus.UNKNOWN
            )
            parts[object_id] = Part(
                object_id,
                Pose(float(pose[0, 3]), float(pose[1, 3]), float(pose[2, 3])),
                status,
                parent.Name() if parent.Valid() else "",
                None,
                available,
            )
        slots = {}
        for slot_id, item_name in ZONE_ITEM_NAMES.items():
            item = self._item(item_name)
            pose = item.PoseAbs()
            slots[slot_id] = Slot(
                slot_id,
                Pose(float(pose[0, 3]), float(pose[1, 3]), float(pose[2, 3])),
            )
        held = next(
            (part.object_id for part in parts.values()
             if part.status == PartStatus.HELD),
            None,
        )
        joints = robot.Joints().list() if robot.Valid() else []
        return SceneSnapshot(
            f"scene-{uuid.uuid4().hex[:8]}",
            time.time(),
            RobotState(
                joints,
                bool(robot.Busy()) if robot.Valid() else False,
                "OnRobot-2FG7-Gripper",
                "SSM3_Workcell",
                held,
            ),
            parts,
            slots,
        )

    def reset(self):
        self.connect()
        robot = self._item("Doosan Robotics M0609 White")
        if robot.Valid():
            robot.setJoints([0, 0, 90, 0, 0, 0])
        frame = self._item("SSM3_Workcell")
        positions = {
            "Part_A": (600.0, 350.0, 50.0),
            "Part_B": (600.0, 0.0, 50.0),
            "Part_C": (600.0, -350.0, 50.0),
        }
        from run_robodk_pick_place import workpiece_item

        for part, position in positions.items():
            item = workpiece_item(self._rdk, SOURCE_NAMES[part])
            item.setParentStatic(frame)
            pose = item.Pose()
            pose[0, 3], pose[1, 3], pose[2, 3] = position
            item.setPose(pose)
        self.pending_pick = None
        self._missing.clear()
        self._unreachable.clear()
        self._rdk.Save(str(self.station_path))

    def inspect(self, object_id):
        return self.observe().parts[object_id]

    def move_to(self, target_id):
        if target_id not in ("Home", "Clearance"):
            raise ValueError("target not allowlisted")
        robot = self._item("Doosan Robotics M0609 White")
        if not robot.Valid():
            raise RuntimeError("robot is unavailable")
        robot.setJoints([0, 0, 90, 0, 0, 0])

    def pick(self, object_id):
        if object_id in self._missing:
            raise RuntimeError(f"{object_id} is unavailable")
        self.pending_pick = object_id

    def _bind_current_pick_targets(self, object_id, slot_id):
        from run_robodk_pick_place import target

        item = self._item(OBJECT_ITEM_NAMES[object_id])
        pose = item.PoseAbs()
        source = SOURCE_NAMES[object_id]
        destination = DESTINATION_NAMES[slot_id]
        for suffix, z in (("PickApproach", 230.0), ("Pick", 90.0)):
            goal = target(self._rdk, source, destination, suffix)
            bound = goal.Pose()
            bound[0, 3] = pose[0, 3]
            bound[1, 3] = pose[1, 3]
            bound[2, 3] = z
            goal.setPose(bound)

    def place(self, object_id, slot_id):
        if self.pending_pick != object_id:
            raise RuntimeError("no matching pick")
        if object_id in self._unreachable:
            raise RuntimeError(f"{object_id} is unreachable")
        from run_robodk_pick_place import (
            configure_physical_collisions,
            execute_task,
        )

        self.connect()
        robot = self._item("Doosan Robotics M0609 White")
        tool = self._item("OnRobot-2FG7-Gripper")
        mechanism = self._item("OnRobot-2FG7-Gripper-Mechanism")
        capture_tcp = self._item("AUTO_Attach_TCP")
        frame = self._item("SSM3_Workcell")
        held_item = self._item(OBJECT_ITEM_NAMES[object_id])
        already_holding = (
            held_item.Parent().Valid()
            and "AUTO_Attach" in held_item.Parent().Name()
        )
        if not already_holding:
            # Resuming after a recovered table collision: the object is
            # already grasped, so its PoseAbs() is now wherever the
            # gripper currently is, not its resting pose on the table.
            # Rebinding Pick/PickApproach from that would corrupt the
            # targets execute_task still needs for its retract leg.
            self._bind_current_pick_targets(object_id, slot_id)
        configure_physical_collisions(self._rdk, robot, SOURCE_NAMES[object_id])
        adjustments = execute_task(
            self._rdk,
            robot,
            tool,
            mechanism,
            capture_tcp,
            frame,
            SOURCE_NAMES[object_id],
            DESTINATION_NAMES[slot_id],
        )
        self.last_collision_recovery = (
            {"object": object_id, "clearance_adjustments": adjustments}
            if adjustments
            else None
        )
        self.pending_pick = None

    def preflight(self, actions):
        self.connect()
        robot = self._item("Doosan Robotics M0609 White")
        if not robot.Valid():
            return ["robot item is unavailable"]
        errors = []
        original_poses = {}
        for action in actions:
            if action.skill not in ("pick", "place"):
                continue
            object_id = action.args.get("object_id")
            slot_id = action.args.get("slot_id")
            if action.skill == "pick":
                slot_id = slot_id or {
                    "Part_A": "Slot_1",
                    "Part_B": "Slot_2",
                    "Part_C": "Slot_3",
                }.get(object_id)
            if object_id not in OBJECT_ITEM_NAMES or slot_id not in ZONE_ITEM_NAMES:
                errors.append(f"invalid motion binding for {object_id}/{slot_id}")
                continue
            if object_id in self._missing:
                errors.append(f"{object_id} is unavailable")
                continue
            if object_id in self._unreachable:
                errors.append(f"{object_id} is configured unreachable")
                continue
            from run_robodk_pick_place import target
            for suffix in ("PickApproach", "Pick"):
                target_item = target(
                    self._rdk,
                    SOURCE_NAMES[object_id],
                    DESTINATION_NAMES[slot_id],
                    suffix,
                )
                original_poses[target_item.Name()] = target_item.Pose()
            self._bind_current_pick_targets(object_id, slot_id)

            for suffix in ("PickApproach", "Pick"):
                joints = robot.SolveIK(
                    target(
                        self._rdk,
                        SOURCE_NAMES[object_id],
                        DESTINATION_NAMES[slot_id],
                        suffix,
                    ).Pose()
                )
                if (
                    joints.RowsCount() == 1
                    and joints.ColsCount() == 1
                    and joints[0, 0] == 0
                ):
                    errors.append(f"unreachable {object_id} {suffix}")
        for name, pose in original_poses.items():
            self._item(name).setPose(pose)
        return errors

    def relocate(self, part, mode="automatic"):
        if mode == "manual":
            input(
                f"Robot stopped at Clearance. Move {part} only, then press Enter. "
            )
            return
        from run_robodk_pick_place import workpiece_item

        item = workpiece_item(self._rdk, SOURCE_NAMES[part])
        x, y = self._random_relocation_xy(part)
        pose = item.Pose()
        pose[0, 3], pose[1, 3], pose[2, 3] = x, y, RELOCATION_Z_MM
        item.setPose(pose)
        self.last_relocation = {
            "part": part,
            "x_mm": round(x, 1),
            "y_mm": round(y, 1),
            "z_mm": RELOCATION_Z_MM,
        }
        return dict(self.last_relocation)

    def _random_relocation_xy(self, part):
        """Choose a separated, IK-reachable XY location for an automatic move."""
        robot = self._item("Doosan Robotics M0609 White")
        from run_robodk_pick_place import target

        slot_id = {
            "Part_A": "Slot_1",
            "Part_B": "Slot_2",
            "Part_C": "Slot_3",
        }[part]
        pick_target = target(
            self._rdk,
            SOURCE_NAMES[part],
            DESTINATION_NAMES[slot_id],
            "Pick",
        )
        pick_pose = pick_target.Pose()
        existing = []
        for other, item_name in OBJECT_ITEM_NAMES.items():
            if other == part or other in self._missing:
                continue
            other_item = self._item(item_name)
            if other_item.Valid():
                other_pose = other_item.Pose()
                existing.append((other_pose[0, 3], other_pose[1, 3]))
        zones = []
        for zone_name in ZONE_ITEM_NAMES.values():
            zone = self._item(zone_name)
            if zone.Valid():
                zone_pose = zone.Pose()
                zones.append((zone_pose[0, 3], zone_pose[1, 3]))

        for _ in range(RELOCATION_MAX_ATTEMPTS):
            x = self._random.randrange(
                int(RELOCATION_X_RANGE_MM[0] / RELOCATION_GRID_MM),
                int(RELOCATION_X_RANGE_MM[1] / RELOCATION_GRID_MM) + 1,
            ) * RELOCATION_GRID_MM
            y = self._random.randrange(
                int(RELOCATION_Y_RANGE_MM[0] / RELOCATION_GRID_MM),
                int(RELOCATION_Y_RANGE_MM[1] / RELOCATION_GRID_MM) + 1,
            ) * RELOCATION_GRID_MM
            if any(
                math.hypot(x - other_x, y - other_y)
                < RELOCATION_MIN_SEPARATION_MM
                for other_x, other_y in existing
            ):
                continue
            zone_clearance = ZONE_HALF_SIZE_MM + RELOCATION_ZONE_MARGIN_MM
            if any(
                abs(x - zone_x) < zone_clearance
                and abs(y - zone_y) < zone_clearance
                for zone_x, zone_y in zones
            ):
                continue
            candidate = pick_pose.copy()
            candidate[0, 3], candidate[1, 3] = x, y
            joints = robot.SolveIK(candidate)
            if (
                joints.RowsCount() == 1
                and joints.ColsCount() == 1
                and joints[0, 0] == 0
            ):
                continue
            if self._pick_path_blocked_by(part, x, y, candidate):
                continue
            return x, y
        raise RuntimeError(
            f"Could not find an IK-reachable random relocation for {part} "
            f"after {RELOCATION_MAX_ATTEMPTS} attempts"
        )

    def _pick_path_blocked_by(self, part, x, y, pick_pose):
        """Dry-run the real approach and descent with the part temporarily at
        (x, y). Returns the colliding item names, or [] when the path is
        clear. The part and the robot are always put back."""
        from run_robodk_pick_place import (
            SAFE_TRANSFER_HEIGHT_MM,
            RoboDKMover,
            configure_physical_collisions,
            workpiece_item,
        )
        from .collision_guard import probe_descent

        table = self._item("SSM3_Table")
        if not table.Valid():
            return []
        robot = self._item("Doosan Robotics M0609 White")
        item = workpiece_item(self._rdk, SOURCE_NAMES[part])
        original_pose = item.Pose()
        moved = original_pose.copy()
        moved[0, 3], moved[1, 3], moved[2, 3] = x, y, RELOCATION_Z_MM
        item.setPose(moved)
        try:
            configure_physical_collisions(self._rdk, robot, SOURCE_NAMES[part])
            mover = RoboDKMover(self._rdk, robot, table)
            approach = mover.raised_to(pick_pose, SAFE_TRANSFER_HEIGHT_MM)
            return probe_descent(mover, approach, pick_pose)
        finally:
            item.setPose(original_pose)

    def mark_missing(self, part):
        self._missing.add(part)

    def mark_unreachable(self, part):
        self._unreachable.add(part)

    @classmethod
    def make_copy(cls, source, destination):
        shutil.copy2(source, destination)
        return cls(destination)
