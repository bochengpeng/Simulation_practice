# RoboDK Doosan M0609 station

`Doosan_M0609_SSM3_Pick_and_Place.rdk` is the RoboDK version of the
pick-and-place scene. It contains the Doosan M0609, a visible
`OnRobot-2FG7-Gripper`, the actual RoboDK tool item for the official OnRobot
2FG7 parallel gripper, attached directly to the Doosan flange. Its imported
visual mechanism is a child of that gripper item and uses the source station's
mounting arrangement: the gripper base is at the robot tool flange and the
official mechanism is offset `+150 mm` inside the gripper model. The mechanism
keeps the official black jaw materials, while its housing matches the Doosan
white finish. The calibrated arrangement is restored after every release so
the black tips remain aligned with the actual gripper.

This matches the physical gripper arrangement inspected in `Pick and Place.rdk`.
The SSM3 station keeps `OnRobot-2FG7-Gripper` as the active physical gripper
tool instead of exposing the source station's helper TCP as the user-facing
gripper.
The station also
contains a shared `SSM3_Workcell` frame, a table, three physical objects, four
zones, 48 Cartesian targets, and 12 reusable assignment programs. The
generated `SSM3_Parallel_Gripper` and obsolete `Vaccum_TCP` are not used.

The Doosan base, workcell, objects, and zones are mounted on one large
`1400 x 1000 mm` table. The robot base is at the table surface, and the
objects/zones sit on the same tabletop. The four zones are separated at
`(150, -300)`, `(450, -300)`, `(150, 150)`, and `(450, 150)`. The objects
are separated along the right side at `(600, 350)`, `(600, 0)`, and
`(600, -400)`.

The objects and zones are:

```text
Object_A, Object_B, Object_C
Zone_A, Zone_B, Zone_C, Zone_D
```

Every pickup and release target uses `GRIPPER_DOWN_POSE` (`Rx(pi)`), so the
OnRobot 2FG7 jaws point vertically toward the ground. The new layout keeps
these downward-facing poses inside the Doosan's reachable workspace while
keeping the solid objects and enlarged zones separated.

The generated target sequence for every assignment is:

```text
Home -> PickApproach -> Pick -> PickApproach
     -> PlaceApproach -> Place -> PlaceApproach -> Home
```

All target poses were checked with the Doosan robot inverse kinematics. The
workcell boxes are watertight STL solids with opaque display colors, so they
are visible collision geometry rather than transparent markers. RoboDK does
not simulate rigid-body mass/friction like CoppeliaSim; the API runner uses
explicit attachment and release checks for the pick-and-place experiment.
Collision checking is enabled in the station.

The generated programs and API runner use a deliberately observable speed
profile:

```text
Linear speed:       60 mm/s
Linear acceleration: 120 mm/s²
Joint speed:        10 deg/s
Joint acceleration: 25 deg/s²
```

The API runner performs real blocking RoboDK simulation moves (`MoveJ` for
transfers and `MoveL` for the vertical pickup/place motions) instead of
instantaneous joint-state updates. Its default simulation-time ratio is `1.0`,
so the visible motion runs at normal real-time speed. You can make it slower
or faster without changing the robot limits:

```powershell
py -3.13 run_robodk_pick_place.py `
  --task Object_A=Zone_B --simulation-speed 0.25
```

Use `--simulation-speed 1.0` for normal real-time simulation.

## Run a real simulated pick-and-place

Open the station in RoboDK, then run the API runner from this folder:

```powershell
py -3.13 run_robodk_pick_place.py --task Object_A=Zone_B
```

Multiple assignments execute in the written order and only for the pairs
provided:

```powershell
py -3.13 run_robodk_pick_place.py `
  --task Object_A=Zone_B Object_C=Zone_D Object_B=Zone_A
```

The runner opens the imported black OnRobot jaws, moves down until the jaw
capture TCP is aligned with the cube top, closes the real 2FG7 mechanism, and
reparents the selected object to the Daryl-compatible `AUTO_Attach_TCP`
capture frame. `Object_A` is represented by the Daryl-compatible
`AUTO_Block` workpiece. At the destination it
reparents the object to the workcell before opening the jaws. It does not
infer assignments for objects that were not specified. The runner disables global collision checking because the imported
robot/table geometry reports conservative false-positive contacts, but
explicit collision pairs remain enabled for the robot versus every physical
object and for object-versus-object contact. Objects therefore cannot be
penetrated or overlap during a run. The runner fails explicitly unless the
object becomes a
child of the jaw capture frame during transport, is reparented to the workcell
after release, and ends within 45 mm of the requested zone center. Before
attachment and throughout transport, the cube center must remain within 10 mm
of the calibrated black-jaw capture center in the actual gripper tool frame.
The pickup sequence also actuates the imported `OnRobot-2FG7-Gripper-Mechanism`
joint itself: it opens before approach, closes to joint value 13.0 around the
cube, attaches the cube to `AUTO_Attach_TCP`, and opens only
after the cube has been detached at the destination. This follows the working
Daryl station pattern; `AttachClosest` is no longer used as a substitute for
jaw actuation.
The physical `OnRobot-2FG7-Gripper` base is mounted directly under the
Doosan robot flange, with `OnRobot-2FG7-Gripper-Mechanism` as its child.
The motion TCP is a separate helper (`SSM3_Motion_TCP`), so its 125 mm
offset is not incorrectly applied twice to the gripper housing.
For release, each `PlaceApproach` and `Place` target has the same X/Y and
the same downward-facing orientation; only Z changes. The runner checks this
and performs the final descent with `MoveL`, so the gripper tip stays vertical
to the ground while descending over the zone.

Reset the objects before a new trial:

```powershell
py -3.13 run_robodk_pick_place.py --reset
```

Reset writes the three starting poses back to the saved station, so a
subsequent task command uses the same positions even when it is run from a
separate terminal process.

The 12 generated `PickPlace_Object_X_to_Zone_Y` programs contain the robot
motions and are useful for inspecting or editing the paths in RoboDK. Use the
API runner when the simulated object must physically follow the tool, because
the runner performs the attach/release operations between motions.

## Checks

Before collecting trials:

1. Open the saved station and confirm the Doosan M0609 is selected.
2. Confirm the actual `OnRobot-2FG7-Gripper` tool is attached to the Doosan
flange and contains the imported visual mechanism. Its two fingers should
   straddle the selected object at the pick target. The black jaws use a
   downward-facing pose and should remain vertically oriented toward the table
   at both pickup and release.
3. Run one assignment and confirm that the robot does not penetrate another
   physical object. The runner enables the relevant object collision pairs
   while avoiding the station's known robot/table false positives.
4. Confirm the object is attached at `Pick` and released above the requested
   zone.
5. Run `--reset` before the next independent trial.
