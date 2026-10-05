import math

from typing import Any, Callable

self: Any
require: Callable[[str], Any]

def find_object(names):
    for name in names:
        handle = self.sim.getObject('/' + name, {'noError': True})
        if handle is not None and handle >= 0:
            return handle

    raise RuntimeError('Cannot find object: ' + ' or '.join(names))


def sysCall_init():
    self.sim = require('sim')
    self.simIK = require('simIK')

    # The manipSphere target is the target used by the working IK script.
    self.manip_sphere = self.sim.getObject(
        '/Robot_Arm/manipSphere/target'
    )

    self.part = find_object(['Part_A'])
    self.place_zone = find_object(['PlaceZone'])
    self.sensor = find_object(['Sensor'])

    # The sensor is inside the suction pad. Parent the picked cube to it.
    self.pad = self.sim.getObjectParent(self.sensor)
    self.attached = False

    # Set up inverse kinematics for the arm.
    base = self.sim.getObject('/Robot_Arm')
    tip = self.sim.getObject('/tip')
    target = self.manip_sphere

    self.ik_env = self.simIK.createEnvironment()
    self.ik_group = self.simIK.createGroup(self.ik_env)

    self.simIK.setGroupCalculation(
        self.ik_env,
        self.ik_group,
        self.simIK.method_damped_least_squares,
        0.1,
        50
    )

    self.simIK.addElementFromScene(
        self.ik_env,
        self.ik_group,
        base,
        tip,
        target,
        self.simIK.constraint_pose
    )


def sysCall_actuation():
    # Move the arm joints so the tip follows the target.
    result, *_ = self.simIK.handleGroup(
        self.ik_env,
        self.ik_group,
        {'syncWorlds': True}
    )

    if result != self.simIK.result_success:
        self.sim.addLog(
            self.sim.verbosity_scriptwarnings,
            'IK could not reach the target'
        )


def move_to(pose):
    self.sim.moveToPose({
        'object': self.manip_sphere,
        'targetPose': pose,
        'maxVel': [
            0.12,
            0.12,
            0.12,
            math.radians(30)
        ],
        'maxAccel': [
            0.25,
            0.25,
            0.25,
            math.radians(60)
        ],
        'maxJerk': [
            0.50,
            0.50,
            0.50,
            math.radians(120)
        ]
    })


def move_up(distance):
    pose = self.sim.getObjectPose(
        self.manip_sphere,
        self.sim.handle_world
    )
    pose[2] += distance
    move_to(pose)


def attach_part():
    self.sim.setBoolProperty(self.part, 'dynamic', False)
    self.sim.setBoolProperty(self.part, 'respondable', False)
    self.sim.setObjectParent(self.part, self.pad, True)

    self.attached = True

    self.sim.addLog(
        self.sim.verbosity_scriptinfos,
        'Part_A attached'
    )


def release_part():
    self.sim.setObjectParent(self.part, self.sim.handle_world, True)

    self.sim.setBoolProperty(self.part, 'respondable', True)
    self.sim.setBoolProperty(self.part, 'dynamic', True)
    self.sim.resetDynamicObject(self.part)

    self.attached = False

    self.sim.addLog(
        self.sim.verbosity_scriptinfos,
        'Part_A released'
    )


def sysCall_thread():
    part_size, _ = self.sim.getShapeBB(self.part)
    zone_size, _ = self.sim.getShapeBB(self.place_zone)

    # Move up before travelling.
    move_up(0.10)

    # Position the sensor above Part_A.
    part_position = self.sim.getObjectPosition(
        self.part,
        self.sim.handle_world
    )
    part_top = part_position[2] + part_size[2] / 2

    desired_sensor_position = [
        part_position[0],
        part_position[1],
        part_top + 0.04
    ]

    sensor_position = self.sim.getObjectPosition(
        self.sensor,
        self.sim.handle_world
    )

    pose = self.sim.getObjectPose(
        self.manip_sphere,
        self.sim.handle_world
    )

    # Shift the target by the sensor's position error.
    for axis in range(3):
        pose[axis] += (
                desired_sensor_position[axis] - sensor_position[axis]
        )

    move_to(pose)

    # Correct any remaining positioning error.
    sensor_position = self.sim.getObjectPosition(
        self.sensor,
        self.sim.handle_world
    )

    correction = [
        desired_sensor_position[i] - sensor_position[i]
        for i in range(3)
    ]

    pose = self.sim.getObjectPose(
        self.manip_sphere,
        self.sim.handle_world
    )

    for axis in range(3):
        pose[axis] += correction[axis]

    move_to(pose)

    # Lower in small steps until the sensor detects the cube.
    detected = False

    for _ in range(30):
        result, *_ = self.sim.checkProximitySensor(
            self.sensor,
            self.part
        )

        if result == 1:
            detected = True
            attach_part()
            break

        pose = self.sim.getObjectPose(
            self.manip_sphere,
            self.sim.handle_world
        )
        pose[2] -= 0.002
        move_to(pose)

    if not detected:
        sensor_pos = self.sim.getObjectPosition(
            self.sensor,
            self.sim.handle_world
        )
        part_pos = self.sim.getObjectPosition(
            self.part,
            self.sim.handle_world
        )
        relative_pos = self.sim.getObjectPosition(
            self.part,
            self.sensor
        )

        raise RuntimeError(
            f'Pickup failed. Sensor: {sensor_pos}; '
            f'cube: {part_pos}; '
            f'cube relative to sensor: {relative_pos}'
        )

    self.sim.wait(0.4)

    # Move above PlaceZone.
    zone_position = self.sim.getObjectPosition(
        self.place_zone,
        self.sim.handle_world
    )
    current_part_position = self.sim.getObjectPosition(
        self.part,
        self.sim.handle_world
    )
    pose = self.sim.getObjectPose(
        self.manip_sphere,
        self.sim.handle_world
    )

    zone_top = zone_position[2] + zone_size[2] / 2
    final_part_z = zone_top + part_size[2] / 2 + 0.003
    above_part_z = final_part_z + 0.08

    pose[0] += zone_position[0] - current_part_position[0]
    pose[1] += zone_position[1] - current_part_position[1]
    pose[2] += above_part_z - current_part_position[2]

    move_to(pose)

    # Lower the cube onto PlaceZone.
    current_part_position = self.sim.getObjectPosition(
        self.part,
        self.sim.handle_world
    )
    pose = self.sim.getObjectPose(
        self.manip_sphere,
        self.sim.handle_world
    )
    pose[2] += final_part_z - current_part_position[2]

    move_to(pose)
    self.sim.wait(0.3)

    # Release the cube, then move the pad away.
    release_part()
    self.sim.wait(0.5)
    move_up(0.10)

    self.sim.addLog(
        self.sim.verbosity_scriptinfos,
        'Automatic pick-and-place completed'
    )


def sysCall_cleanup():
    if getattr(self, 'attached', False):
        release_part()

    if hasattr(self, 'ik_env'):
        self.simIK.eraseEnvironment(self.ik_env)
