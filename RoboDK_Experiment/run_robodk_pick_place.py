"""Execute explicit object-to-zone tasks in the generated RoboDK station.

Examples:
    py -3.13 run_robodk_pick_place.py --task Object_A=Zone_B
    py -3.13 run_robodk_pick_place.py --task Object_A=Zone_B Object_C=Zone_D
    py -3.13 run_robodk_pick_place.py --reset
"""

import argparse
import math
import time
from pathlib import Path

from robodk.robolink import ITEM_TYPE_FRAME, ITEM_TYPE_ROBOT, Robolink
from robodk.robomath import Mat, rotz

from robodk_adaptive.collision_guard import (
    TableCollisionError,
    guarded_joint_move,
    guarded_linear_move,
)


OBJECTS = ("Object_A", "Object_B", "Object_C")
ZONES = ("Zone_A", "Zone_B", "Zone_C", "Zone_D")
GRIPPER_TOOL_POSE = Mat([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 125], [0, 0, 0, 1]])
STATION_FILE = Path(__file__).resolve().parent / "Doosan_M0609_SSM3_Original.rdk"
TABLE_TOP = 0.0
GRIPPER_BASE_OFFSET = Mat.eye(4)
LINEAR_SPEED_MM_S = 60.0
LINEAR_ACCELERATION_MM_S2 = 120.0
JOINT_SPEED_DEG_S = 10.0
JOINT_ACCELERATION_DEG_S2 = 25.0
SIMULATION_SPEED_RATIO = 6.0
OBJECT_SIZE_MM = 80.0
ZONE_THICKNESS_MM = 20.0
SAFE_TRANSFER_HEIGHT_MM = 350.0
JAW_CENTER_TOLERANCE_MM = 10.0
JAW_CLEARANCE_MM = 5.0
CALIBRATED_JAW_OPENING_MM = 90.0
CAPTURE_OBJECT_OFFSET_Z_MM = 40.0
GRIPPER_CLOSED_VALUE = 13.0
GRIPPER_OPEN_RATIO = 0.90
GRIPPER_MOTION_STEPS = 12
GRIPPER_MOTION_TIME_S = 0.45

# -- Table-collision check / local path adjustment ---------------------
# RoboDK's real-time MoveJ/MoveL never stop a simulated motion on their own
# when a collision occurs; *_Test() is the documented way to check a
# candidate segment before dispatching it. These control how finely a
# segment is sampled and, when a transit segment would clip the table, how
# far it is progressively lifted (in mm, above TABLE_TOP) before trying
# again. Smallest/least-disruptive clearance first.
COLLISION_TEST_STEP_MM = 5.0
COLLISION_TEST_STEP_DEG = 2.0
TABLE_COLLISION_CLEARANCE_STEPS_MM = (60.0, 140.0, 240.0)
# Alternate tool yaw angles tried when the straight pick descent is blocked.
# The 80 mm block is square, so a 90/180 degree turn grasps it identically.
PICK_YAW_ALTERNATIVES_DEG = (90.0, -90.0, 180.0)
# RoboDK reports collisions per robot link id (0 = base). This station
# mounts the robot base into/at the table surface, so that overlap is
# expected geometry, not a crash, and must stay exempt even once the arm
# itself is checked against the table. If a different false positive shows
# up on a specific link in your own station (verify visually in RoboDK with
# collisions shown, or via `python main.py doctor`), add its link id here.
TABLE_COLLISION_EXEMPT_LINK_IDS = {0}


def parse_tasks(values):
    tasks = []
    for value in values:
        if "=" not in value:
            raise ValueError(f"Task must use Object_X=Zone_Y: {value}")
        source, destination = value.split("=", 1)
        if source not in OBJECTS or destination not in ZONES:
            raise ValueError(
                f"Unsupported task {value}; objects={OBJECTS}, zones={ZONES}"
            )
        tasks.append((source, destination))
    if not tasks:
        raise ValueError("At least one --task is required")
    return tasks


def target(rdk, source, destination, suffix):
    return rdk.Item(
        f"SSM3_{source}_to_{destination}_{suffix}"
    )


def zone_item(rdk, destination):
    zone = rdk.Item(f"SSM3_{destination}")
    if not zone.Valid():
        raise RuntimeError(f"Missing physical zone: {destination}")
    return zone


def configure_physical_collisions(rdk, robot, source):
    physical_objects = [
        workpiece_item(rdk, source) for source in OBJECTS
    ]
    table = rdk.Item("SSM3_Table")
    zones = [zone_item(rdk, name) for name in ZONES[:3]]
    rdk.setCollisionActive(1)
    mounted_items = {
        robot.Name(),
        "SSM3_Motion_TCP",
        "OnRobot-2FG7-Gripper",
        "OnRobot-2FG7-Gripper-Mechanism",
        "AUTO_Attach_TCP",
    }
    for first, second, first_id, second_id in (
        rdk.CollisionActivePairList()
    ):
        if (
            first.Name() in mounted_items
            and second.Name() in mounted_items
        ):
            rdk.setCollisionActivePair(
                0, first, second, first_id, second_id
            )
    non_physical_names = {
        table.Name() if table.Valid() else "",
        *(zone.Name() for zone in zones),
    }
    for first, second, first_id, second_id in (
        rdk.CollisionActivePairList()
    ):
        first_is_robot_body = first.Name() == robot.Name()
        second_is_robot_body = second.Name() == robot.Name()
        if (
            first_is_robot_body and second.Name() in non_physical_names
        ) or (
            second_is_robot_body and first.Name() in non_physical_names
        ):
            # The user-requested "did the arm hit the table" check: the
            # robot ITEM's own links are checked against the table/zones
            # (link id 0, the base mounted into the table, stays exempt --
            # that overlap is expected geometry, not a crash). The mounted
            # tool items (gripper, TCP frames) handled below stay exempt as
            # before; place/approach still blanket-disable collisions for
            # their deliberately contact-making moves.
            link_id = first_id if first_is_robot_body else second_id
            state = 0 if link_id in TABLE_COLLISION_EXEMPT_LINK_IDS else 1
            rdk.setCollisionActivePair(
                state, first, second, first_id, second_id
            )
            continue
        if (
            first.Name() in mounted_items
            and second.Name() in non_physical_names
        ) or (
            second.Name() in mounted_items
            and first.Name() in non_physical_names
        ):
            rdk.setCollisionActivePair(
                0, first, second, first_id, second_id
            )
    for item, name in zip(physical_objects, OBJECTS):
        rdk.setCollisionActivePair(
            0 if name == source else 1,
            robot,
            item,
        )
    for index, first in enumerate(physical_objects):
        for second in physical_objects[index + 1:]:
            rdk.setCollisionActivePair(1, first, second)
    for item in physical_objects:
        if table.Valid():
            rdk.setCollisionActivePair(0, item, table)
        for zone in zones:
            rdk.setCollisionActivePair(0, item, zone)
    selected = workpiece_item(rdk, source)
    for mounted in (
        robot,
        rdk.Item("OnRobot-2FG7-Gripper"),
        rdk.Item("OnRobot-2FG7-Gripper-Mechanism"),
    ):
        if mounted.Valid():
            rdk.setCollisionActivePair(0, selected, mounted)
    object_names = {
        item.Name(): name
        for item, name in zip(physical_objects, OBJECTS)
    }
    for first, second, first_id, second_id in (
        rdk.CollisionActivePairList()
    ):
        first_name = first.Name()
        second_name = second.Name()
        if (
            first_name in mounted_items
            and second_name in object_names
        ):
            rdk.setCollisionActivePair(
                0 if object_names[second_name] == source else 1,
                first,
                second,
                first_id,
                second_id,
            )
        elif (
            second_name in mounted_items
            and first_name in object_names
        ):
            rdk.setCollisionActivePair(
                0 if object_names[first_name] == source else 1,
                first,
                second,
                first_id,
                second_id,
            )
        if first_name == robot.Name() and second_name in object_names:
            rdk.setCollisionActivePair(
                0 if object_names[second_name] == source else 1,
                first,
                second,
                first_id,
                second_id,
            )
        elif second_name == robot.Name() and first_name in object_names:
            rdk.setCollisionActivePair(
                0 if object_names[first_name] == source else 1,
                first,
                second,
                first_id,
                second_id,
            )


def workpiece_item(rdk, source):
    names = (
        ("AUTO_Block", "Object_A")
        if source == "Object_A"
        else (f"SSM3_{source}",)
    )
    for name in names:
        item = rdk.Item(name)
        if item.Valid():
            return item
    raise RuntimeError(
        f"Could not find the RoboDK workpiece for {source}; "
        f"tried {', '.join(names)}"
    )


def reset_objects(rdk, frame):
    initial_poses = {
        "Object_A": (600.0, 350.0, TABLE_TOP + 50.0),
        "Object_B": (600.0, 0.0, TABLE_TOP + 50.0),
        "Object_C": (600.0, -400.0, TABLE_TOP + 50.0),
    }
    for name, position in initial_poses.items():
        item = workpiece_item(rdk, name)
        item.setParentStatic(frame)
        pose = item.Pose()
        pose[0, 3], pose[1, 3], pose[2, 3] = position
        item.setPose(pose)
    rdk.Save(str(STATION_FILE))


def jaw_capture_offset(tool, selected):
    relative_pose = tool.PoseAbs().inv() * selected.PoseAbs()
    return (
        relative_pose[0, 3],
        relative_pose[1, 3],
        relative_pose[2, 3],
    )


def gripper_joint_values(mechanism):
    joints = mechanism.Joints().list()
    low, high, _ = mechanism.JointLimits()
    low_values = low.list()
    high_values = high.list()
    if not joints or not low_values or not high_values:
        raise RuntimeError(
            f"{mechanism.Name()} does not expose a controllable jaw joint"
        )
    return joints, float(low_values[0]), float(high_values[0])


def move_gripper(mechanism, opened):
    joints, low, high = gripper_joint_values(mechanism)
    start = float(joints[0])
    destination = (
        low + GRIPPER_OPEN_RATIO * (high - low)
        if opened else GRIPPER_CLOSED_VALUE
    )
    if destination < low or destination > high:
        raise RuntimeError(
            f"Gripper target {destination:.2f} is outside "
            f"{mechanism.Name()} joint limits [{low:.2f}, {high:.2f}]"
        )
    for step in range(1, GRIPPER_MOTION_STEPS + 1):
        current = list(joints)
        current[0] = start + (destination - start) * (
            step / GRIPPER_MOTION_STEPS
        )
        mechanism.setJoints(current)
        time.sleep(GRIPPER_MOTION_TIME_S / GRIPPER_MOTION_STEPS)
    return destination


def validate_jaw_alignment(capture_tcp, selected, stage):
    offset = jaw_capture_offset(capture_tcp, selected)
    if (
        abs(offset[0]) > JAW_CENTER_TOLERANCE_MM
        or abs(offset[1]) > JAW_CENTER_TOLERANCE_MM
        or abs(offset[2] - CAPTURE_OBJECT_OFFSET_Z_MM)
        > JAW_CENTER_TOLERANCE_MM
    ):
        raise RuntimeError(
            f"{selected.Name()} is outside the black-jaw capture center at "
            f"{stage}; tool-frame offset={offset}, expected z="
            f"{CAPTURE_OBJECT_OFFSET_Z_MM:.1f} mm"
        )
    required_opening = OBJECT_SIZE_MM + 2.0 * JAW_CLEARANCE_MM
    if required_opening > CALIBRATED_JAW_OPENING_MM:
        raise RuntimeError(
            "The configured cube exceeds the calibrated black-jaw clearance"
        )


def validate_jaw_capture(capture_tcp, selected, stage):
    validate_jaw_alignment(capture_tcp, selected, stage)
    if selected.Parent().Name() != capture_tcp.Name():
        raise RuntimeError(
            f"{selected.Name()} is not held by the jaw capture frame "
            f"at {stage}; parent={selected.Parent().Name()}"
        )


def validate_vertical_descent(approach, place):
    for axis in (0, 1):
        if abs(approach.Pose()[axis, 3] - place.Pose()[axis, 3]) > 0.001:
            raise RuntimeError(
                f"Release path is not vertical: {approach.Name()} and "
                f"{place.Name()} have different XY coordinates"
            )
    for row in range(3):
        for column in range(3):
            if abs(
                approach.Pose()[row, column]
                - place.Pose()[row, column]
            ) > 0.001:
                raise RuntimeError(
                    f"Release path changes gripper orientation between "
                    f"{approach.Name()} and {place.Name()}"
                )


def ik_is_invalid(joints):
    return (
        joints.RowsCount() == 1
        and joints.ColsCount() == 1
        and joints[0, 0] == 0
    )


class RoboDKMover:
    """Concrete ``collision_guard.Mover`` for the live RoboDK station: a
    thin wrapper over the real robot/table items so the collision-guard
    retry policy (framework-agnostic, unit-tested on its own) can drive the
    live simulation.
    """

    def __init__(self, rdk, robot, table):
        self._rdk = rdk
        self._robot = robot
        self._table = table

    def current_joints(self):
        return self._robot.Joints()

    def current_pose(self):
        return self._robot.Pose()

    def solve_ik(self, pose):
        return self._robot.SolveIK(pose)

    def is_reachable(self, joints) -> bool:
        return not ik_is_invalid(joints)

    def test_joint_move(self, start_joints, end_joints) -> int:
        return self._robot.MoveJ_Test(start_joints, end_joints, COLLISION_TEST_STEP_DEG)

    def test_linear_move(self, start_joints, end_pose) -> int:
        return self._robot.MoveL_Test(start_joints, end_pose, COLLISION_TEST_STEP_MM)

    def _collision_pairs(self):
        self._rdk.Collisions()
        return self._rdk.CollisionPairs()

    def colliding_items(self) -> list[str]:
        return [
            f"{first.Name()}[{first_id}]<->{second.Name()}[{second_id}]"
            for first, second, first_id, second_id in self._collision_pairs()
        ]

    def table_collision(self) -> bool:
        pairs = self._collision_pairs()
        if not pairs:
            return bool(self._robot.Collision(self._table))
        return any(
            item.Name() == self._table.Name() or item.Name().startswith("SSM3_Zone_")
            for first, second, _, _ in pairs
            for item in (first, second)
        )

    def restore_joints(self, joints) -> None:
        self._robot.setJoints(joints)

    def dispatch_joint_move(self, joints) -> None:
        self._robot.MoveJ(joints)

    def dispatch_linear_move(self, pose) -> None:
        self._robot.MoveL(pose)

    def raised_to(self, pose, z_mm: float):
        result = pose.copy()
        result[2, 3] = max(result[2, 3], TABLE_TOP + z_mm)
        return result

    def z_of(self, pose) -> float:
        return pose[2, 3] - TABLE_TOP

    def yawed(self, pose, deg: float):
        return pose * rotz(math.radians(deg))


def retreat_to_safe_height(rdk, robot):
    """Best effort: lift straight up to the safe transfer height so a failed
    task never leaves the arm low over (or in contact with) the table."""
    table = rdk.Item("SSM3_Table")
    if not table.Valid():
        return
    mover = RoboDKMover(rdk, robot, table)
    try:
        guarded_joint_move(
            mover,
            mover.raised_to(mover.current_pose(), SAFE_TRANSFER_HEIGHT_MM),
            TABLE_COLLISION_CLEARANCE_STEPS_MM,
            "Safe retreat",
        )
    except Exception as exc:
        print(f"Safe retreat after collision failed: {exc}", flush=True)


def execute_task(
    rdk, robot, tool, mechanism, capture_tcp, frame, source, destination
):
    try:
        return _execute_task(
            rdk, robot, tool, mechanism, capture_tcp, frame, source, destination
        )
    except TableCollisionError:
        retreat_to_safe_height(rdk, robot)
        raise


def _execute_task(
    rdk, robot, tool, mechanism, capture_tcp, frame, source, destination
):
    approach = target(rdk, source, destination, "PickApproach")
    pick = target(rdk, source, destination, "Pick")
    place_approach = target(rdk, source, destination, "PlaceApproach")
    place = target(rdk, source, destination, "Place")
    for item in (approach, pick, place_approach, place):
        if not item.Valid():
            raise RuntimeError(f"Missing target: {item.Name()}")
    validate_vertical_descent(place_approach, place)

    table = rdk.Item("SSM3_Table")
    mover = RoboDKMover(rdk, robot, table) if table.Valid() else None
    collision_adjustments = 0

    def move_to(item):
        nonlocal collision_adjustments
        joints = robot.SolveIK(item.Pose())
        if ik_is_invalid(joints):
            raise RuntimeError(f"Target is not reachable: {item.Name()}")
        if item.Name().endswith("Approach"):
            safe_pose = item.Pose().copy()
            safe_pose[2, 3] = max(
                safe_pose[2, 3],
                TABLE_TOP + SAFE_TRANSFER_HEIGHT_MM,
            )
            safe_joints = robot.SolveIK(safe_pose)
            if ik_is_invalid(safe_joints):
                raise RuntimeError(
                    f"Safe approach is not reachable: {item.Name()}"
                )
            if item.Name().endswith("PlaceApproach"):
                rdk.setCollisionActive(0)
                try:
                    robot.MoveJ(safe_joints)
                finally:
                    rdk.setCollisionActive(1)
            elif mover is not None:
                outcome = guarded_joint_move(
                    mover, safe_pose, TABLE_COLLISION_CLEARANCE_STEPS_MM, item.Name()
                )
                collision_adjustments += outcome.attempts
            else:
                robot.MoveJ(safe_joints)
            return
        if item.Name().endswith("Pick"):
            if mover is not None:
                outcome = guarded_linear_move(
                    mover, item.Pose(), item.Name(), PICK_YAW_ALTERNATIVES_DEG
                )
                collision_adjustments += outcome.attempts
            else:
                robot.MoveL(item)
        elif item.Name().endswith("Place"):
            # The final descent intentionally brings the carried block and
            # black jaws into contact with the destination zone. RoboDK's
            # imported mesh collision checker reports that expected contact
            # as a stop even after the specific collision pairs are disabled.
            rdk.setCollisionActive(0)
            try:
                robot.MoveL(item)
            finally:
                rdk.setCollisionActive(1)
        elif mover is not None:
            outcome = guarded_joint_move(
                mover, item.Pose(), TABLE_COLLISION_CLEARANCE_STEPS_MM, item.Name()
            )
            collision_adjustments += outcome.attempts
        else:
            robot.MoveJ(item)

    selected = workpiece_item(rdk, source)
    already_holding = (
        selected.Parent().Valid() and selected.Parent().Name() == capture_tcp.Name()
    )
    if not already_holding:
        move_gripper(mechanism, opened=True)
        move_to(approach)
        move_to(pick)
        validate_jaw_alignment(capture_tcp, selected, "pre-attachment pickup pose")
        move_gripper(mechanism, opened=False)
        selected.setParent(capture_tcp)
        selected.setPose(
            Mat([
                [1, 0, 0, 0],
                [0, 1, 0, 0],
                [0, 0, 1, CAPTURE_OBJECT_OFFSET_Z_MM],
                [0, 0, 0, 1],
            ])
        )
        validate_jaw_capture(capture_tcp, selected, "pickup")
    else:
        # Resuming after a recovered table collision from an earlier
        # attempt: the object is already grasped. Re-verify the grasp
        # instead of re-opening the gripper or re-running the pick descent
        # -- a held part is not pending on the table.
        validate_jaw_capture(capture_tcp, selected, "resumed pickup (already held)")

    move_to(approach)
    validate_jaw_capture(capture_tcp, selected, "post-pick lift")
    move_to(place_approach)
    validate_jaw_capture(capture_tcp, selected, "destination approach")
    move_to(place)
    validate_jaw_capture(capture_tcp, selected, "release pose")
    zone = zone_item(rdk, destination)
    zone_pose = zone.PoseAbs()
    expected_object_pose = Mat([
        [1, 0, 0, zone_pose[0, 3]],
        [0, 1, 0, zone_pose[1, 3]],
        [
            0, 0, 1,
            zone_pose[2, 3] + ZONE_THICKNESS_MM / 2
            + OBJECT_SIZE_MM / 2,
        ],
        [0, 0, 0, 1],
    ])
    selected.setParentStatic(frame)
    selected.setPoseAbs(expected_object_pose)
    if selected.Parent().Name() != frame.Name():
        raise RuntimeError(
            f"{source} was not released into {frame.Name()} at the destination"
        )
    destination_pose = selected.PoseAbs()
    destination_offset = math.dist(
        [destination_pose[0, 3], destination_pose[1, 3], destination_pose[2, 3]],
        [
            expected_object_pose[0, 3],
            expected_object_pose[1, 3],
            expected_object_pose[2, 3],
        ],
    )
    if destination_offset > 45.0:
        raise RuntimeError(
            f"{source} was released {destination_offset:.1f} mm from "
            f"{destination} (maximum 45 mm)"
        )
    move_gripper(mechanism, opened=True)
    move_to(place_approach)
    move_to(target(rdk, source, destination, "Home"))
    selected.setParentStatic(frame)
    selected.setPoseAbs(expected_object_pose)
    final_pose = selected.PoseAbs()
    final_offset = math.dist(
        [final_pose[0, 3], final_pose[1, 3], final_pose[2, 3]],
        [
            expected_object_pose[0, 3],
            expected_object_pose[1, 3],
            expected_object_pose[2, 3],
        ],
    )
    if final_offset > 1.0:
        raise RuntimeError(
            f"{source} moved after release by {final_offset:.1f} mm"
        )
    return collision_adjustments


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--task", nargs="+", help="Ordered assignments such as Object_A=Zone_B"
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Reset the three objects to their saved starting poses"
    )
    parser.add_argument(
        "--simulation-speed", type=float, default=SIMULATION_SPEED_RATIO,
        help=(
            "RoboDK simulation speed ratio; values below 1 slow the visible "
            "simulation (default: 1.0)"
        ),
    )
    args = parser.parse_args()

    rdk = Robolink()
    if not STATION_FILE.is_file():
        raise RuntimeError(f"Missing RoboDK station: {STATION_FILE}")

    if args.simulation_speed <= 0:
        raise ValueError("--simulation-speed must be greater than zero")
    rdk.setSimulationSpeed(args.simulation_speed)

    frame = rdk.Item("SSM3_Workcell", ITEM_TYPE_FRAME)
    robot = rdk.Item("Doosan Robotics M0609 White", ITEM_TYPE_ROBOT)
    tool = rdk.Item("OnRobot-2FG7-Gripper")
    mechanism = rdk.Item("OnRobot-2FG7-Gripper-Mechanism")
    capture_tcp = rdk.Item("AUTO_Attach_TCP")
    if (
        not frame.Valid()
        or not robot.Valid()
        or not tool.Valid()
        or not mechanism.Valid()
        or not capture_tcp.Valid()
    ):
        rdk.AddFile(str(STATION_FILE))
        frame = rdk.Item("SSM3_Workcell", ITEM_TYPE_FRAME)
        robot = rdk.Item("Doosan Robotics M0609 White", ITEM_TYPE_ROBOT)
        tool = rdk.Item("OnRobot-2FG7-Gripper")
        mechanism = rdk.Item("OnRobot-2FG7-Gripper-Mechanism")
        capture_tcp = rdk.Item("AUTO_Attach_TCP")
        if (
            not frame.Valid()
            or not robot.Valid()
            or not tool.Valid()
            or not mechanism.Valid()
            or not capture_tcp.Valid()
        ):
            raise RuntimeError(
                "Could not load the required RoboDK station items from "
                f"{STATION_FILE}"
            )

    robot.setFrame(frame)
    robot.setPoseTool(GRIPPER_TOOL_POSE)
    robot.setJoints([0, 0, 90, 0, 0, 0])
    robot.setSpeed(LINEAR_SPEED_MM_S, LINEAR_ACCELERATION_MM_S2)
    robot.setSpeedJoints(JOINT_SPEED_DEG_S)
    robot.setAccelerationJoints(JOINT_ACCELERATION_DEG_S2)
    if args.reset:
        reset_objects(rdk, frame)
        print("Objects reset")
        return

    tasks = parse_tasks(args.task or ["Object_A=Zone_B"])
    try:
        for source, destination in tasks:
            configure_physical_collisions(rdk, robot, source)
            print(f"Executing {source} -> {destination}")
            execute_task(
                rdk, robot, tool, mechanism, capture_tcp, frame,
                source, destination,
            )
    finally:
        rdk.setCollisionActive(1)
    print("Completed:", ", ".join(f"{s} -> {d}" for s, d in tasks))


if __name__ == "__main__":
    main()
