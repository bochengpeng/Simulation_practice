"""Build the SSM3 kitting station (Step 0) in RoboDK.

Starts from a *copy* of the Doosan M0609 + OnRobot-2FG7 pick-and-place
station from the earlier adaptive pick-and-place experiment (never edits
that original) and replaces its pick-and-place objects (zones, blocks,
PickPlace programs) with the kitting objects of this assignment: three
supply bins, a four-slot tray, a return position and a home target, named
exactly as in config/capability.yaml (NFR2).

Run with RoboDK already open:
    python build_kitting_station.py
"""

from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp

import yaml
from robodk.robolink import ITEM_TYPE_PROGRAM, ITEM_TYPE_ROBOT, Robolink
from robodk.robomath import Mat, rotx, transl

PROJECT_DIR = Path(__file__).resolve().parent
STATION_PATH = PROJECT_DIR / "Kitting_SSM3_Station.rdk"
CAPABILITY_PATH = PROJECT_DIR.parent / "config" / "capability.yaml"

ROBOT_NAME = "Doosan Robotics M0609 White"
GRIPPER_TOOL_POSE = transl(0.0, 0.0, 125.0)
GRIPPER_DOWN_POSE = rotx(3.141592653589793)
TABLE_TOP = 0.0
APPROACH_HEIGHT_MM = 140.0

# Positions reuse the reachable real estate proven by the earlier experiment
# (old Object_A/B/C bin area, old Zone_A-D tray area, plus one new spot for
# RETURN), so every target here is already known to be IK-reachable.
BIN_POSITIONS = {
    "BIN_1": (600.0, 350.0),
    "BIN_2": (600.0, 0.0),
    "BIN_3": (600.0, -350.0),
}
SLOT_POSITIONS = {
    "SLOT_1": (150.0, -300.0),
    "SLOT_2": (450.0, -300.0),
    "SLOT_3": (150.0, 150.0),
    "SLOT_4": (450.0, 150.0),
}
RETURN_POSITION = {"RETURN": (300.0, 350.0)}

PART_SIZE_MM = {
    "BOLT": (14.0, 14.0, 60.0),
    "SPACER_SHORT": (40.0, 40.0, 20.0),
    "SPACER_LONG": (40.0, 40.0, 40.0),
}
PART_COLOR = {
    "BOLT": [0.65, 0.65, 0.70, 1.0],
    "SPACER_SHORT": [0.85, 0.55, 0.15, 1.0],
    "SPACER_LONG": [0.15, 0.55, 0.85, 1.0],
}
BIN_COLOR = [0.30, 0.30, 0.33, 1.0]
SLOT_COLOR = [0.15, 0.55, 0.95, 1.0]
RETURN_COLOR = [0.80, 0.15, 0.15, 1.0]


def load_capability() -> dict:
    with CAPABILITY_PATH.open("r", encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def box_triangles(size_x, size_y, size_z):
    x, y, z = size_x / 2, size_y / 2, size_z / 2
    vertices = [
        [-x, -y, -z], [x, -y, -z], [x, y, -z], [-x, y, -z],
        [-x, -y, z], [x, -y, z], [x, y, z], [-x, y, z],
    ]
    faces = [
        (0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6),
        (0, 4, 5), (0, 5, 1), (1, 5, 6), (1, 6, 2),
        (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0),
    ]
    points = [vertices[i] for face in faces for i in face]
    return Mat([[point[axis] for point in points] for axis in range(3)])


def write_box_stl(path, size):
    triangles = box_triangles(*size)
    with path.open("w", encoding="ascii") as stream:
        stream.write("solid box\n")
        points = list(zip(*triangles.rows))
        for index in range(0, len(points), 3):
            stream.write(" facet normal 0 0 0\n  outer loop\n")
            for point in points[index:index + 3]:
                stream.write(f"   vertex {point[0]} {point[1]} {point[2]}\n")
            stream.write("  endloop\n endfacet\n")
        stream.write("endsolid box\n")


def add_box(rdk, parent, name, size, position_xyz, color, stl_dir):
    stl_path = stl_dir / f"{name}.stl"
    write_box_stl(stl_path, size)
    item = rdk.AddFile(str(stl_path), parent)
    item.setName(name)
    item.setParentStatic(parent)
    item.setPose(Mat([
        [1, 0, 0, position_xyz[0]],
        [0, 1, 0, position_xyz[1]],
        [0, 0, 1, position_xyz[2]],
        [0, 0, 0, 1],
    ]))
    item.setVisible(1)
    item.setColor(color)
    item.setColorShape(color, -1)
    return item


def make_target(rdk, frame, robot, name, pose):
    target = rdk.AddTarget(name, frame, robot)
    target.setAsCartesianTarget()
    target.setPose(pose)
    target.setVisible(1)
    return target


def delete_named(rdk, prefixes):
    to_delete = []
    for item in list(rdk.ItemList()):
        try:
            name = item.Name()
        except Exception:
            continue
        if any(name.startswith(prefix) for prefix in prefixes):
            to_delete.append(item)
    for item in to_delete:
        try:
            if item.Valid():
                rdk.Delete(item)
        except Exception:
            pass


def main():
    capability = load_capability()
    rdk = Robolink()

    existing = rdk.Item("Kitting_SSM3_Station", 1)
    if existing.Valid():
        rdk.Delete(existing)

    station = rdk.AddFile(str(STATION_PATH))
    if not station.Valid():
        raise RuntimeError(f"Could not open {STATION_PATH}")
    station.setName("Kitting_SSM3_Station")

    robot = rdk.Item(ROBOT_NAME, ITEM_TYPE_ROBOT)
    if not robot.Valid():
        raise RuntimeError(f"Station is missing the robot '{ROBOT_NAME}'")

    # Strip the old pick-and-place-only objects, and any kitting objects
    # from a previous run of this script (idempotent rebuild). The robot,
    # gripper, motion tool, workcell frame and table stay.
    delete_named(rdk, (
        "SSM3_Zone_",
        "SSM3_Object_",
        "PickPlace_",
        "TEST3",
        "AUTO_Block",
        "BIN_",
        "SLOT_",
        "RETURN",
        "HOME",
        "BOLT",
        "SPACER_SHORT",
        "SPACER_LONG",
    ))

    workcell = rdk.Item("SSM3_Workcell")
    if not workcell.Valid():
        raise RuntimeError("Station is missing the 'SSM3_Workcell' frame")

    robot.setJoints([0, 0, 90, 0, 0, 0])
    home_pose = robot.Pose()

    stl_dir = Path(mkdtemp(prefix="kitting_ssm3_"))
    try:
        make_target(rdk, workcell, robot, "HOME", home_pose)

        for name, (x, y) in BIN_POSITIONS.items():
            add_box(rdk, workcell, f"{name}_Model", (150.0, 150.0, 60.0),
                     (x, y, TABLE_TOP + 30.0), BIN_COLOR, stl_dir)
            pose = GRIPPER_DOWN_POSE.copy()
            pose[0, 3], pose[1, 3] = x, y
            pose[2, 3] = TABLE_TOP + 90.0 + APPROACH_HEIGHT_MM
            make_target(rdk, workcell, robot, name, pose)
            part = capability["positions"][name]["holds"]
            size = PART_SIZE_MM[part]
            part_pose = (x, y, TABLE_TOP + 60.0 + size[2] / 2.0)
            part_item = add_box(rdk, workcell, part, size, part_pose,
                                 PART_COLOR[part], stl_dir)
            part_item.setParentStatic(rdk.Item(name))

        for name, (x, y) in SLOT_POSITIONS.items():
            add_box(rdk, workcell, f"{name}_Model", (240.0, 240.0, 20.0),
                     (x, y, TABLE_TOP + 10.0), SLOT_COLOR, stl_dir)
            pose = GRIPPER_DOWN_POSE.copy()
            pose[0, 3], pose[1, 3] = x, y
            pose[2, 3] = TABLE_TOP + 100.0 + APPROACH_HEIGHT_MM
            make_target(rdk, workcell, robot, name, pose)

        for name, (x, y) in RETURN_POSITION.items():
            add_box(rdk, workcell, f"{name}_Model", (240.0, 240.0, 20.0),
                     (x, y, TABLE_TOP + 10.0), RETURN_COLOR, stl_dir)
            pose = GRIPPER_DOWN_POSE.copy()
            pose[0, 3], pose[1, 3] = x, y
            pose[2, 3] = TABLE_TOP + 100.0 + APPROACH_HEIGHT_MM
            make_target(rdk, workcell, robot, name, pose)
    finally:
        rmtree(stl_dir, ignore_errors=True)

    robot.setFrame(workcell)
    rdk.setCollisionActive(1)
    rdk.Render(True)
    rdk.Save(str(STATION_PATH))
    print(f"Saved: {STATION_PATH}")
    print("Programs remaining:", len(rdk.ItemList(ITEM_TYPE_PROGRAM)))


if __name__ == "__main__":
    main()
