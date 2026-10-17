import json
import math
from uuid import uuid4

from typing import Any, Callable

self: Any
require: Callable[[str], Any]

COMMAND_PROPERTY = 'customData.ssm3RobotCommand'
STATUS_PROPERTY = 'customData.ssm3RobotStatus'
CONTROLLER_VERSION = 'multi-object-v5'
OBJECT_NAMES = ('Object_A', 'Object_B', 'Object_C')
ZONE_NAMES = ('Zone_A', 'Zone_B', 'Zone_C', 'Zone_D')

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
    self.tip = self.sim.getObject('/tip')
    self.home_target_pose = self.sim.getObjectPose(
        self.manip_sphere, self.sim.handle_world
    )

    self.objects = {
        name: find_object([name])
        for name in OBJECT_NAMES
    }
    self.zones = {
        name: find_object([name])
        for name in ZONE_NAMES
    }
    self.part = None
    self.place_zone = None
    self.part_name = None
    self.place_zone_name = None
    self.sensor = find_object(['Sensor'])

    # The sensor is inside the suction pad. Parent the picked cube to it.
    self.pad = self.sim.getObjectParent(self.sensor)
    self.object_physics = {
        name: (
            self.sim.getBoolProperty(handle, 'dynamic'),
            self.sim.getBoolProperty(handle, 'respondable'),
        )
        for name, handle in self.objects.items()
    }
    self.attached = False
    self.controller_id = uuid4().hex
    self.heartbeat = 0

    # The robot waits for a request instead of running automatically on Play.
    self.sim.removeProperty(self.sim.handle_scene, COMMAND_PROPERTY,
                            {'noError': True})
    self.sim.removeProperty(self.sim.handle_scene, STATUS_PROPERTY,
                            {'noError': True})

    # Set up inverse kinematics for the arm.
    base = self.sim.getObject('/Robot_Arm')
    tip = self.tip
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

    publish_status('ready', message='Waiting for a pick-and-place command')
    self.sim.addLog(
        self.sim.verbosity_scriptinfos,
        'LLM robot controller initialized: waiting for command'
    )


def publish_status(state, request_id=None, message='', **extra):
    payload = {
        'state': state,
        'controller_version': CONTROLLER_VERSION,
        'request_id': request_id,
        'message': message,
        'attached': self.attached,
        'controller_id': self.controller_id,
        'heartbeat': self.heartbeat,
        'sim_time': self.sim.getSimulationTime(),
        **extra,
    }
    self.last_status = payload
    self.sim.setStringProperty(
        self.sim.handle_scene, STATUS_PROPERTY, json.dumps(payload)
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


def return_home():
    move_to(self.home_target_pose)
    self.sim.wait(0.5)
    actual_pose = self.sim.getObjectPose(
        self.manip_sphere, self.sim.handle_world
    )
    position_error = math.dist(actual_pose[:3], self.home_target_pose[:3])
    if position_error > 0.03:
        raise RuntimeError(
            'Robot did not return to the startup pose '
            f'(target position error {position_error:.3f} m)'
        )


def attach_part():
    self.sim.setBoolProperty(self.part, 'dynamic', False)
    self.sim.setBoolProperty(self.part, 'respondable', False)
    self.sim.setObjectParent(self.part, self.pad, True)

    self.attached = True

    self.sim.addLog(
        self.sim.verbosity_scriptinfos,
        f'{self.part_name} attached'
    )


def release_part():
    self.sim.setObjectParent(self.part, self.sim.handle_world, True)

    self.sim.setBoolProperty(
        self.part, 'respondable',
        self.object_physics[self.part_name][1]
    )
    part_dynamic = self.object_physics[self.part_name][0]
    self.sim.setBoolProperty(self.part, 'dynamic', part_dynamic)
    if part_dynamic:
        self.sim.resetDynamicObject(self.part)

    self.attached = False

    self.sim.addLog(
        self.sim.verbosity_scriptinfos,
        f'{self.part_name} released'
    )


def select_task(source, destination):
    if source not in self.objects:
        raise ValueError(
            f'Unsupported object {source}; '
            f'allowed objects: {", ".join(OBJECT_NAMES)}'
        )
    if destination not in self.zones:
        raise ValueError(
            f'Unsupported zone {destination}; '
            f'allowed zones: {", ".join(ZONE_NAMES)}'
        )
    if self.attached and self.part_name != source:
        raise RuntimeError(
            f'{self.part_name} is still attached; cannot switch to {source}'
        )
    self.part_name = source
    self.place_zone_name = destination
    self.part = self.objects[source]
    self.place_zone = self.zones[destination]


def move_sensor_to(position, label):
    sensor_position = self.sim.getObjectPosition(
        self.sensor, self.sim.handle_world
    )
    sensor_pose = self.sim.getObjectPose(
        self.sensor, self.sim.handle_world
    )
    sensor_pose[0:3] = position

    # The IK target is not colocated with the suction sensor in this model.
    # Preserve the current target-to-sensor transform when choosing its pose.
    target_relative_to_sensor = self.sim.getObjectPose(
        self.manip_sphere, self.sensor
    )
    target_pose = self.sim.multiplyPoses(
        sensor_pose, target_relative_to_sensor
    )
    for _ in range(3):
        move_to(target_pose)
        actual_sensor = self.sim.getObjectPosition(
            self.sensor, self.sim.handle_world
        )
        correction = [
            position[axis] - actual_sensor[axis]
            for axis in range(3)
        ]
        remaining_error = math.dist(actual_sensor, position)
        if remaining_error <= 0.025:
            return
        target_pose = self.sim.getObjectPose(
            self.manip_sphere, self.sim.handle_world
        )
        for axis in range(3):
            target_pose[axis] += correction[axis]

    raise RuntimeError(
        f'Arm could not reach {label} '
        f'(position error {remaining_error:.3f} m)'
    )


def move_over_part(object_name=None):
    if object_name is not None:
        select_task(object_name, self.place_zone_name or ZONE_NAMES[0])
    part_size, _ = self.sim.getShapeBB(self.part)
    move_up(0.10)
    part_position = self.sim.getObjectPosition(
        self.part, self.sim.handle_world
    )
    part_top = part_position[2] + part_size[2] / 2
    move_sensor_to([
        part_position[0],
        part_position[1],
        part_top + 0.04
    ], self.part_name)


def pickup_sensor_position():
    part_size, _ = self.sim.getShapeBB(self.part)
    part_position = self.sim.getObjectPosition(
        self.part, self.sim.handle_world
    )
    return [
        part_position[0],
        part_position[1],
        part_position[2] + part_size[2] / 2 + 0.04
    ]


def pick_part():
    if self.attached:
        raise RuntimeError(f'{self.part_name} is already attached')

    sensor_position = self.sim.getObjectPosition(
        self.sensor, self.sim.handle_world
    )
    pickup_position = pickup_sensor_position()
    pickup_error = math.dist(sensor_position, pickup_position)
    if pickup_error > 0.035:
        self.sim.addLog(
            self.sim.verbosity_scriptinfos,
            f'Pickup approach correction required ({pickup_error:.3f} m)'
        )
        move_over_part()
        sensor_position = self.sim.getObjectPosition(
            self.sensor, self.sim.handle_world
        )
        pickup_error = math.dist(
            sensor_position, pickup_sensor_position()
        )
        if pickup_error > 0.035:
            raise RuntimeError(
                'Pick precondition not satisfied after approach correction '
                f'(sensor error {pickup_error:.3f} m)'
            )

    for _ in range(30):
        result, *_ = self.sim.checkProximitySensor(
            self.sensor, self.part
        )
        if result == 1:
            attach_part()
            self.sim.wait(0.4)
            return

        pose = self.sim.getObjectPose(
            self.manip_sphere, self.sim.handle_world
        )
        pose[2] -= 0.002
        move_to(pose)

    sensor_pos = self.sim.getObjectPosition(
        self.sensor, self.sim.handle_world
    )
    part_pos = self.sim.getObjectPosition(
        self.part, self.sim.handle_world
    )
    relative_pos = self.sim.getObjectPosition(
        self.part, self.sensor
    )
    raise RuntimeError(
        f'Pickup failed. Sensor: {sensor_pos}; '
        f'cube: {part_pos}; cube relative to sensor: {relative_pos}'
    )


def move_over_zone():
    if not self.attached:
        raise RuntimeError(
            f'Cannot move to {self.place_zone_name} before picking '
            f'{self.part_name}'
        )

    part_size, _ = self.sim.getShapeBB(self.part)
    zone_size, _ = self.sim.getShapeBB(self.place_zone)
    zone_position = self.sim.getObjectPosition(
        self.place_zone, self.sim.handle_world
    )
    current_part_position = self.sim.getObjectPosition(
        self.part, self.sim.handle_world
    )
    final_part_z = (
        zone_position[2] + zone_size[2] / 2
        + part_size[2] / 2 + 0.003
    )
    sensor_position = self.sim.getObjectPosition(
        self.sensor, self.sim.handle_world
    )
    part_sensor_offset = [
        current_part_position[axis] - sensor_position[axis]
        for axis in range(3)
    ]
    desired_part_position = [
        zone_position[0],
        zone_position[1],
        final_part_z + 0.08
    ]
    move_sensor_to([
        desired_part_position[axis] - part_sensor_offset[axis]
        for axis in range(3)
    ], f'{self.place_zone_name} approach')


def place_part():
    if not self.attached:
        raise RuntimeError(
            f'Cannot place {self.part_name} because it is not attached'
        )

    part_size, _ = self.sim.getShapeBB(self.part)
    zone_size, _ = self.sim.getShapeBB(self.place_zone)
    zone_position = self.sim.getObjectPosition(
        self.place_zone, self.sim.handle_world
    )
    current_part_position = self.sim.getObjectPosition(
        self.part, self.sim.handle_world
    )
    sensor_position = self.sim.getObjectPosition(
        self.sensor, self.sim.handle_world
    )
    final_part_z = (
        zone_position[2] + zone_size[2] / 2
        + part_size[2] / 2 + 0.003
    )
    part_sensor_offset = [
        current_part_position[axis] - sensor_position[axis]
        for axis in range(3)
    ]
    desired_part_position = [
        zone_position[0],
        zone_position[1],
        final_part_z
    ]
    move_sensor_to([
        desired_part_position[axis] - part_sensor_offset[axis]
        for axis in range(3)
    ], self.place_zone_name)
    self.sim.wait(0.3)

    actual_part = self.sim.getObjectPosition(
        self.part, self.sim.handle_world
    )
    expected_part = [zone_position[0], zone_position[1], final_part_z]
    if math.dist(actual_part, expected_part) > 0.035:
        raise RuntimeError(
            f'{self.part_name} did not reach {self.place_zone_name}; '
            'leaving it attached for inspection'
        )

    release_part()
    self.sim.wait(0.5)
    move_up(0.10)
    return_home()


def execute_skill(action, target):
    if action == 'move' and target in OBJECT_NAMES:
        if self.part_name != target:
            raise RuntimeError(
                f'No active task for {target}; send a task selection first'
            )
        move_over_part()
    elif action == 'pick' and target in OBJECT_NAMES:
        if self.part_name != target:
            raise RuntimeError(
                f'No active task for {target}; send a task selection first'
            )
        pick_part()
    elif action == 'move' and target in ZONE_NAMES:
        if self.place_zone_name != target:
            raise RuntimeError(
                f'No active task for {target}; send a task selection first'
            )
        move_over_zone()
    elif action == 'place' and target in ZONE_NAMES:
        if self.place_zone_name != target:
            raise RuntimeError(
                f'No active task for {target}; send a task selection first'
            )
        place_part()
    else:
        raise ValueError(
            'Allowed skills are move/pick(Object_A|Object_B|Object_C) '
            'and move/place(Zone_A|Zone_B|Zone_C|Zone_D)'
        )


def pick_and_place(source, destination):
    select_task(source, destination)
    execute_skill('move', source)
    execute_skill('pick', source)
    execute_skill('move', destination)
    execute_skill('place', destination)
    self.sim.addLog(
        self.sim.verbosity_scriptinfos,
        'Pick-and-place routine completed'
    )
    return self.sim.getObjectPosition(self.part, self.sim.handle_world)
    return self.sim.getObjectPosition(self.part, self.sim.handle_world)


def sysCall_thread():
    next_heartbeat = 0.0
    while True:
        raw = self.sim.getStringProperty(
            self.sim.handle_scene, COMMAND_PROPERTY, {'noError': True}
        )
        if raw is None:
            now = self.sim.getSimulationTime()
            if now >= next_heartbeat:
                self.heartbeat += 1
                status = dict(self.last_status)
                status['heartbeat'] = self.heartbeat
                status['sim_time'] = now
                self.last_status = status
                self.sim.setStringProperty(
                    self.sim.handle_scene, STATUS_PROPERTY, json.dumps(status)
                )
                next_heartbeat = now + 0.25
            self.sim.wait(0.05)
            continue

        # Remove the request once, before executing the potentially long move.
        self.sim.removeProperty(self.sim.handle_scene, COMMAND_PROPERTY)
        request_id = None
        try:
            if isinstance(raw, bytes):
                raw = raw.decode('utf-8')
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError('Command must be a JSON object')
            request_id = request.get('request_id')
            if not isinstance(request_id, str) or not request_id:
                raise ValueError('Missing request_id')
            legacy_command = set(request) == {
                'request_id', 'action', 'source', 'destination'
            }
            skill_command = set(request) == {
                'request_id', 'action', 'target', 'source', 'destination'
            }
            if not legacy_command and not skill_command:
                raise ValueError('Unexpected or missing command fields')
            if legacy_command and request['action'] != 'pick_and_place':
                raise ValueError('Legacy action must be pick_and_place')
            if skill_command and not (
                    isinstance(request['action'], str)
                    and isinstance(request['target'], str)
                    and isinstance(request['source'], str)
                    and isinstance(request['destination'], str)):
                raise ValueError(
                    'Skill action, target, source, and destination must be strings'
                )
            if legacy_command and self.attached:
                raise RuntimeError(
                    f'{self.part_name} is still attached after a failed move; '
                    'stop and inspect the scene'
                )

            if legacy_command:
                select_task(request['source'], request['destination'])
                message = (
                    f"Moving {request['source']} to "
                    f"{request['destination']}"
                )
                publish_status('busy', request_id, message)
                final_position = pick_and_place(
                    request['source'], request['destination']
                )
            else:
                select_task(request['source'], request['destination'])
                message = f"Executing {request['action']}({request['target']})"
                publish_status('busy', request_id, message)
                execute_skill(request['action'], request['target'])
                final_position = self.sim.getObjectPosition(
                    self.part, self.sim.handle_world
                )
            publish_status(
                'done', request_id, message,
                part_position=final_position,
                source=self.part_name,
                destination=self.place_zone_name,
            )
        except Exception as exc:
            publish_status('error', request_id, str(exc))
            self.sim.addLog(
                self.sim.verbosity_scriptwarnings,
                f'Pick-and-place failed: {exc}'
            )


def sysCall_cleanup():
    if getattr(self, 'attached', False):
        release_part()

    if hasattr(self, 'ik_env'):
        self.simIK.eraseEnvironment(self.ik_env)
