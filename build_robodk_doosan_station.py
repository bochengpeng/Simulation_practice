"""Build the SSM3 Doosan M0609 pick-and-place station in RoboDK."""

from pathlib import Path
from tempfile import mkdtemp
from shutil import rmtree

from robodk.robolink import (
    ITEM_TYPE_FRAME,
    ITEM_TYPE_OBJECT,
    ITEM_TYPE_PROGRAM,
    ITEM_TYPE_ROBOT,
    Robolink,
)
from robodk.robomath import Mat, rotx, transl


PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_STATION = PROJECT_DIR / "Doosan_M0609_SSM3_Pick_and_Place.rdk"
GRIPPER_MODEL = PROJECT_DIR / "OnRobot-2FG7-Gripper.robot"
GRIPPER_TOOL_POSE = transl(0.0, 0.0, 125.0)
GRIPPER_BASE_OFFSET = Mat.eye(4)
GRIPPER_DOWN_POSE = rotx(3.141592653589793)
DOOSAN_WHITE = [0.7921569, 0.7960784, 0.7921569, 1.0]
PROGRAM_LINEAR_SPEED = 60.0
PROGRAM_LINEAR_ACCELERATION = 120.0
PROGRAM_JOINT_SPEED = 10.0
PROGRAM_JOINT_ACCELERATION = 25.0
TABLE_TOP = 0.0
APPROACH_HEIGHT_MM = 140.0
TABLE_SIZE = (1400.0, 1000.0, 80.0)
TABLE_CENTER = (400.0, 0.0, TABLE_TOP - TABLE_SIZE[2] / 2.0)


def box_triangles(size_x, size_y, size_z):
    x = size_x / 2
    y = size_y / 2
    z = size_z / 2
    vertices = [
        [-x, -y, -z], [x, -y, -z], [x, y, -z], [-x, y, -z],
        [-x, -y, z], [x, -y, z], [x, y, z], [-x, y, z],
    ]
    faces = [
        (0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6),
        (0, 4, 5), (0, 5, 1), (1, 5, 6), (1, 6, 2),
        (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0),
    ]
    points = [
        vertices[index]
        for face in faces
        for index in face
    ]
    return Mat([
        [point[axis] for point in points]
        for axis in range(3)
    ])


def write_box_stl(path, size):
    triangles = box_triangles(*size)
    with path.open("w", encoding="ascii") as stream:
        stream.write("solid box\n")
        points = list(zip(*triangles.rows))
        for index in range(0, len(points), 3):
            stream.write(" facet normal 0 0 0\n  outer loop\n")
            for point in points[index:index + 3]:
                stream.write(
                    f"   vertex {point[0]} {point[1]} {point[2]}\n"
                )
            stream.write("  endloop\n endfacet\n")
        stream.write("endsolid box\n")


def add_box(rdk, parent, name, size, position, color, stl_dir):
    stl_path = stl_dir / f"{name}.stl"
    write_box_stl(stl_path, size)
    item = rdk.AddFile(str(stl_path), parent)
    item.setName(name)
    item.setParentStatic(parent)
    item.setPose(Mat([
        [1, 0, 0, position[0]],
        [0, 1, 0, position[1]],
        [0, 0, 1, position[2]],
        [0, 0, 0, 1],
    ]))
    item.setVisible(1)
    item.setColor(color)
    item.setColorShape(color, -1)
    return item


def delete_named(rdk, prefixes):
    for item in list(rdk.ItemList()):
        try:
            name = item.Name()
        except Exception:
            continue
        if any(name.startswith(prefix) for prefix in prefixes):
            rdk.Delete(item)


def make_target(rdk, frame, robot, name, pose):
    target = rdk.AddTarget(name, frame, robot)
    target.setAsCartesianTarget()
    target.setPose(pose)
    target.setVisible(0)
    return target


def add_pick_place_program(rdk, robot, frame, tool, source, destination,
                           targets):
    program = rdk.AddProgram(
        f"PickPlace_{source}_to_{destination}",
        robot,
    )
    program.setPoseFrame(frame)
    program.setPoseTool(tool)
    program.setSpeed(PROGRAM_LINEAR_SPEED)
    program.setAcceleration(PROGRAM_LINEAR_ACCELERATION)
    program.setSpeedJoints(PROGRAM_JOINT_SPEED)
    program.setAccelerationJoints(PROGRAM_JOINT_ACCELERATION)
    program.MoveJ(targets["approach"])
    program.MoveL(targets["pick"])
    program.MoveL(targets["approach"])
    program.MoveJ(targets["place_approach"])
    program.MoveL(targets["place"])
    program.MoveL(targets["place_approach"])
    program.MoveJ(targets["home"])
    return program


def add_parallel_gripper(rdk, gripper, stl_dir):
    gripper.setVisible(1)
    add_box(
        rdk, gripper, "SSM3_Gripper_Base",
        (55.0, 130.0, 80.0), (-20.0, 0.0, 0.0),
        [0.18, 0.18, 0.20, 1.0],
        stl_dir,
    )
    for side in (-1, 1):
        add_box(
            rdk, gripper, f"SSM3_Gripper_Finger_{side:+d}",
            (80.0, 20.0, 20.0), (40.0, side * 52.0, 0.0),
            [0.22, 0.22, 0.24, 1.0],
            stl_dir,
        )
        add_box(
            rdk, gripper, f"SSM3_Gripper_Pad_{side:+d}",
            (45.0, 8.0, 34.0), (38.0, side * 38.0, 0.0),
            [0.05, 0.05, 0.05, 1.0],
            stl_dir,
        )
    return gripper


def main():
    rdk = Robolink()
    stl_dir = Path(mkdtemp(prefix="robodk_ssm3_"))
    robot = rdk.Item("Doosan Robotics M0609 White", ITEM_TYPE_ROBOT)
    if not robot.Valid():
        raise RuntimeError(
            "The attached station must contain the Doosan M0609 robot"
        )
    for old_name in (
        "SSM3_Parallel_Gripper",
        "Vaccum_TCP",
        "OnRobot-2FG7-Gripper",
        "OnRobot-2FG7-Gripper-TCP",
        "SSM3_Motion_TCP",
        "SSM3_JawCapture_TCP",
        "AUTO_Attach_TCP",
        "AUTO_Block",
        "OnRobot-2FG7-Gripper-Visual",
    ):
        old_tool = rdk.Item(old_name)
        if old_tool.Valid():
            rdk.Delete(old_tool)
    if not GRIPPER_MODEL.exists():
        raise RuntimeError(f"Missing gripper model: {GRIPPER_MODEL}")

    delete_named(
        rdk,
        (
            "SSM3_Workcell",
            "SSM3_Table",
            "SSM3_Zone_",
            "SSM3_Object_",
            "SSM3_Gripper_",
            "PickPlace_",
        ),
    )
    workcell = rdk.AddFrame("SSM3_Workcell")
    workcell.setPose(Mat.eye(4))
    imported = rdk.AddFile(str(GRIPPER_MODEL), robot)
    if not imported.Valid():
        raise RuntimeError("Could not load OnRobot-2FG7-Gripper")
    tool = rdk.Item("OnRobot 2FG7 Gripper Base")
    if not tool.Valid():
        raise RuntimeError("The OnRobot model did not provide a gripper base")
    imported.setName("OnRobot-2FG7-Gripper-Mechanism")
    tool.setName("OnRobot-2FG7-Gripper")
    tool.setColor(DOOSAN_WHITE)
    motion_tool = robot.AddTool(GRIPPER_TOOL_POSE, "SSM3_Motion_TCP")
    if not motion_tool.Valid():
        raise RuntimeError("Could not create the motion TCP")
    # Match Daryl exactly: the imported physical base is the direct child of
    # the robot flange; the motion TCP is a separate helper tool.
    tool.setParentStatic(robot)
    tool.setPose(GRIPPER_BASE_OFFSET)
    jaw_capture = rdk.AddFrame("AUTO_Attach_TCP", robot)
    if not jaw_capture.Valid():
        raise RuntimeError("Could not create AUTO_Attach_TCP")
    jaw_capture.setPose(GRIPPER_TOOL_POSE)
    robot.setFrame(workcell)
    robot.setTool(motion_tool)
    robot.setJoints([0, 0, 90, 0, 0, 0])

    table = add_box(
        rdk, workcell, "SSM3_Table",
        TABLE_SIZE, TABLE_CENTER,
        [0.35, 0.35, 0.38, 1.0],
        stl_dir,
    )
    table.setVisible(1)

    zone_centers = {
        "Zone_A": (150.0, -300.0),
        "Zone_B": (450.0, -300.0),
        "Zone_C": (150.0, 150.0),
        "Zone_D": (450.0, 150.0),
    }
    zones = {}
    for name, (x, y) in zone_centers.items():
        zones[name] = add_box(
            rdk, workcell, f"SSM3_{name}",
            (240.0, 240.0, 20.0), (x, y, TABLE_TOP + 10.0),
            [0.15, 0.55, 0.95, 1.0],
            stl_dir,
        )

    object_centers = {
        "Object_A": (600.0, 350.0),
        "Object_B": (600.0, 0.0),
        "Object_C": (600.0, -400.0),
    }
    objects = {}
    for name, (x, y) in object_centers.items():
        object_name = "AUTO_Block" if name == "Object_A" else f"SSM3_{name}"
        objects[name] = add_box(
            rdk, workcell, object_name,
            (80.0, 80.0, 80.0), (x, y, TABLE_TOP + 50.0),
            [0.85, 0.25, 0.12, 1.0],
            stl_dir,
        )

    home_pose = robot.Pose()
    targets = {}
    for source, (source_x, source_y) in object_centers.items():
        targets[source] = {}
        for destination, (zone_x, zone_y) in zone_centers.items():
            source_approach = GRIPPER_DOWN_POSE.copy()
            source_approach[0, 3] = source_x
            source_approach[1, 3] = source_y
            source_approach[2, 3] = TABLE_TOP + 90.0 + APPROACH_HEIGHT_MM
            source_pick = source_approach.copy()
            source_pick[2, 3] = TABLE_TOP + 90.0

            zone_approach = GRIPPER_DOWN_POSE.copy()
            zone_approach[0, 3] = zone_x
            zone_approach[1, 3] = zone_y
            zone_approach[2, 3] = TABLE_TOP + 100.0 + APPROACH_HEIGHT_MM
            zone_place = zone_approach.copy()
            zone_place[2, 3] = TABLE_TOP + 100.0

            prefix = f"SSM3_{source}_to_{destination}"
            assignment_targets = {
                "home": make_target(
                    rdk, workcell, robot, f"{prefix}_Home", home_pose
                ),
                "approach": make_target(
                    rdk, workcell, robot, f"{prefix}_PickApproach",
                    source_approach,
                ),
                "pick": make_target(
                    rdk, workcell, robot, f"{prefix}_Pick", source_pick
                ),
                "place_approach": make_target(
                    rdk, workcell, robot, f"{prefix}_PlaceApproach",
                    zone_approach,
                ),
                "place": make_target(
                    rdk, workcell, robot, f"{prefix}_Place", zone_place
                ),
            }
            targets[source][destination] = assignment_targets
            add_pick_place_program(
                rdk, robot, workcell, GRIPPER_TOOL_POSE, source, destination,
                assignment_targets,
            )

    rdk.setCollisionActive(1)
    rdk.Render(True)
    rdk.Save(str(OUTPUT_STATION))
    print(f"Saved: {OUTPUT_STATION}")
    print("Objects:", ", ".join(objects))
    print("Zones:", ", ".join(zones))
    print("Programs:", len(rdk.ItemList(ITEM_TYPE_PROGRAM)))
    rmtree(stl_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
