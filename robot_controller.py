import json
import math
from uuid import uuid4

from typing import Any, Callable

self: Any
require: Callable[[str], Any]

COMMAND_PROPERTY = 'customData.ssm3RobotCommand'
STATUS_PROPERTY = 'customData.ssm3RobotStatus'
CONTROLLER_VERSION = 'multi-object-v8-solid-objects'
OBJECT_NAMES = ('Object_A', 'Object_B', 'Object_C')
ZONE_NAMES = ('Zone_A', 'Zone_B', 'Zone_C', 'Zone_D')
STARTUP_TARGET_POSE = [
    -0.27599991760514114,
    -0.6399999948511791,
    1.3100001510135082,
    3.622934103621334e-16,
    2.3927407430606268e-16,
    -1.4568521819311956e-09,
    1.0,
]

def find_object(names):
    for name in names:
        handle = self.sim.getObject('/' + name, {'noError': True})
        if handle is not None and handle >= 0:
            return handle

    raise RuntimeError('Cannot find object: ' + ' or '.join(names))


def configure_physical_shape(handle, dynamic, respondable):
    self.sim.setBoolProperty(handle, 'dynamic', dynamic)
    self.sim.setBoolProperty(handle, 'respondable', respondable)

    # A saved scene can retain restrictive respondable masks even after the
    # dynamic/respondable flags are corrected. Normalize the mask when this
    # simulator build exposes the shape parameter.
    mask_param = getattr(
        self.sim, 'shapeintparam_respondable_mask', None
    )
    set_int_param = getattr(self.sim, 'setObjectInt32Param', None)
    if mask_param is not None and callable(set_int_param):
        set_int_param(handle, mask_param, 0xFFFF)


def sysCall_init():
    self.sim = require('sim')
    self.simIK = require('simIK')

    # The manipSphere target is the target used by the working IK script.
    self.manip_sphere = self.sim.getObject(
        '/Robot_Arm/manipSphere/target'
    )
    self.tip = self.sim.getObject('/tip')
    # Always start from the known reachable calibration pose. A previous
    # interrupted trial can leave the saved target outside the IK workspace.
    self.sim.setObjectPose(
        self.manip_sphere,
        STARTUP_TARGET_POSE,
        self.sim.handle_world
    )
    self.home_target_pose = list(STARTUP_TARGET_POSE)

    self.objects = {
        name: find_object([name])
        for name in OBJECT_NAMES
    }
    self.zones = {
        name: find_object([name])
        for name in ZONE_NAMES
    }
    for handle in self.objects.values():
        configure_physical_shape(handle, True, True)
    for handle in self.zones.values():
        configure_physical_shape(handle, False, True)
    self.part = None
    self.place_zone = None
    self.part_name = None
    self.place_zone_name = None
    self.placed_objects_by_zone = {
        name: [] for name in ZONE_NAMES
    }
    self.sensor = find_object(['Sensor'])
    self.obstacle = self.sim.getObject(
        '/Obstacle_Test', {'noError': True}
    )
    if self.obstacle is not None and self.obstacle >= 0:
        configure_physical_shape(self.obstacle, False, True)
    self.obstacle_clearance = 0.06

    # The sensor is inside the suction pad. Parent the picked cube to it.
    self.pad = self.sim.getObjectParent(self.sensor)
    self.object_physics = {
        name: (
            self.sim.getBoolProperty(handle, 'dynamic'),
            self.sim.getBoolProperty(handle, 'respondable'),
        )
        for name, handle in self.objects.items()
    }
    self.object_initial_poses = {
        name: self.sim.getObjectPose(handle, self.sim.handle_world)
        for name, handle in self.objects.items()
    }
    self.attached = False
    self.controller_id = uuid4().hex
    self.heartbeat = 0
    self.motion_generation = 0

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
    extra.setdefault('motion_generation', self.motion_generation)
    extra.setdefault(
        'target_position',
        self.sim.getObjectPosition(self.manip_sphere, self.sim.handle_world)
    )
    extra.setdefault(
        'sensor_position',
        self.sim.getObjectPosition(self.sensor, self.sim.handle_world)
    )
    extra.setdefault(
        'tip_position',
        self.sim.getObjectPosition(self.tip, self.sim.handle_world)
    )
    payload = {
        'state': state,
        'controller_version': CONTROLLER_VERSION,
        'request_id': request_id,
        'message': message,
        'attached': self.attached,
        'controller_id': self.controller_id,
        'heartbeat': self.heartbeat,
        'sim_time': self.sim.getSimulationTime(),
        'reachable': extra.pop('reachable', state != 'error'),
        'collision_detected': extra.pop('collision_detected', False),
        'placement_error_m': extra.pop('placement_error_m', None),
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
    self.motion_generation += 1
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


def reset_trial():
    if self.attached:
        self.sim.setObjectParent(
            self.part, self.sim.handle_world, True
        )
        self.attached = False
    for name, handle in self.objects.items():
        self.sim.setObjectParent(handle, self.sim.handle_world, True)
        self.sim.setObjectPose(
            handle, self.object_initial_poses[name], self.sim.handle_world
        )
        dynamic, respondable = self.object_physics[name]
        configure_physical_shape(handle, dynamic, respondable)
        if dynamic:
            self.sim.resetDynamicObject(handle)
    self.part = None
    self.place_zone = None
    self.part_name = None
    self.place_zone_name = None
    self.placed_objects_by_zone = {
        name: [] for name in ZONE_NAMES
    }
    move_to(self.home_target_pose)
    self.sim.wait(0.5)


def attach_part():
    # Disable physics only while the object is rigidly parented to the pad.
    # release_part restores the original solid-body state before verification.
    configure_physical_shape(self.part, False, False)
    self.sim.setObjectParent(self.part, self.pad, True)

    self.attached = True

    self.sim.addLog(
        self.sim.verbosity_scriptinfos,
        f'{self.part_name} attached'
    )


def release_part():
    self.sim.setObjectParent(self.part, self.sim.handle_world, True)

    part_dynamic = self.object_physics[self.part_name][0]
    part_respondable = self.object_physics[self.part_name][1]
    configure_physical_shape(
        self.part, part_dynamic, part_respondable
    )
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
    move_sensor_with_obstacle_avoidance(sensor_position, position, label)


def move_sensor_direct(position, label):
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


def obstacle_detour(position):
    if self.obstacle is None:
        return None

    obstacle_position = self.sim.getObjectPosition(
        self.obstacle, self.sim.handle_world
    )
    obstacle_size, _ = self.sim.getShapeBB(self.obstacle)
    half_x = obstacle_size[0] / 2 + self.obstacle_clearance
    half_y = obstacle_size[1] / 2 + self.obstacle_clearance
    start = self.sim.getObjectPosition(self.sensor, self.sim.handle_world)
    end = position

    def inside_xy(point):
        return (
            abs(point[0] - obstacle_position[0]) <= half_x
            and abs(point[1] - obstacle_position[1]) <= half_y
        )

    # A direct vertical approach is safe when neither endpoint is inside the
    # obstacle footprint. For a crossing path, route above the obstacle.
    if not inside_xy(start) and not inside_xy(end):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length_sq = dx * dx + dy * dy
        if length_sq > 1e-9:
            t = max(0.0, min(1.0, (
                (obstacle_position[0] - start[0]) * dx
                + (obstacle_position[1] - start[1]) * dy
            ) / length_sq))
            closest = [start[0] + t * dx, start[1] + t * dy]
            if (
                abs(closest[0] - obstacle_position[0]) > half_x
                or abs(closest[1] - obstacle_position[1]) > half_y
            ):
                return None

    obstacle_top = obstacle_position[2] + obstacle_size[2] / 2
    payload_clearance = 0.0
    if self.attached and self.part is not None:
        part_size, _ = self.sim.getShapeBB(self.part)
        part_position = self.sim.getObjectPosition(
            self.part, self.sim.handle_world
        )
        part_sensor_offset_z = part_position[2] - start[2]
        payload_clearance = max(
            0.0,
            -part_sensor_offset_z + part_size[2] / 2
        )
    waypoint_z = max(
        start[2],
        end[2],
        obstacle_top + self.obstacle_clearance + payload_clearance
    )
    end_waypoint_z = waypoint_z if inside_xy(end) else end[2]
    return [
        [start[0], start[1], waypoint_z],
        [end[0], end[1], end_waypoint_z],
    ]


def move_sensor_with_obstacle_avoidance(start, position, label):
    detour = obstacle_detour(position)
    if detour is None:
        move_sensor_direct(position, label)
        return

    self.sim.addLog(
        self.sim.verbosity_scriptwarnings,
        f'Obstacle detected on path to {label}; using elevated detour'
    )
    for index, waypoint in enumerate(detour):
        move_sensor_direct(
            waypoint,
            f'{label} obstacle detour waypoint {index + 1}'
        )
    move_sensor_direct(position, label)


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


def get_drop_position(part_size, zone_size, zone_position):
    placed_count = len(
        self.placed_objects_by_zone[self.place_zone_name]
    )
    slot_offsets = (
        (0.0, 0.0),
        (-0.09, 0.0),
        (0.09, 0.0),
        (0.0, -0.09),
        (0.0, 0.09),
    )
    offset = slot_offsets[min(placed_count, len(slot_offsets) - 1)]
    max_x_offset = max(
        0.0,
        zone_size[0] / 2 - part_size[0] / 2 - 0.01
    )
    max_y_offset = max(
        0.0,
        zone_size[1] / 2 - part_size[1] / 2 - 0.01
    )
    return [
        zone_position[0] + min(abs(offset[0]), max_x_offset)
        * (1 if offset[0] >= 0 else -1),
        zone_position[1] + min(abs(offset[1]), max_y_offset)
        * (1 if offset[1] >= 0 else -1),
    ]


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
    drop_position = get_drop_position(part_size, zone_size, zone_position)
    desired_part_position = [
        drop_position[0],
        drop_position[1],
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
    drop_position = get_drop_position(part_size, zone_size, zone_position)
    desired_part_position = [
        drop_position[0],
        drop_position[1],
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
    expected_part = [drop_position[0], drop_position[1], final_part_z]
    placement_error = math.dist(actual_part, expected_part)
    if placement_error > 0.035:
        raise RuntimeError(
            f'{self.part_name} did not reach {self.place_zone_name}; '
            'leaving it attached for inspection'
        )

    # Keep the released object dynamic and respondable so it remains a solid
    # body and can physically support or collide with later placements.
    release_part()
    self.placed_objects_by_zone[self.place_zone_name].append(
        self.part_name
    )
    self.sim.wait(0.5)
    move_up(0.10)
    return_home()
    return placement_error


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
        return place_part()
    else:
        raise ValueError(
            'Allowed skills are move/pick(Object_A|Object_B|Object_C) '
            'and move/place(Zone_A|Zone_B|Zone_C)'
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
            reset_command = set(request) == {
                'request_id', 'action'
            }
            legacy_command = set(request) == {
                'request_id', 'action', 'source', 'destination'
            }
            skill_command = set(request) == {
                'request_id', 'action', 'target', 'source', 'destination'
            }
            if not reset_command and not legacy_command and not skill_command:
                raise ValueError('Unexpected or missing command fields')
            if reset_command and request['action'] != 'reset':
                raise ValueError('Only reset is supported without a task')
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

            if reset_command:
                publish_status(
                    'busy', request_id, 'Resetting trial',
                    command_started=True
                )
                reset_trial()
                publish_status(
                    'done', request_id, 'Trial reset',
                    reachable=True,
                    collision_detected=False,
                    command_started=True,
                )
                continue
            if legacy_command:
                select_task(request['source'], request['destination'])
                message = (
                    f"Moving {request['source']} to "
                    f"{request['destination']}"
                )
                publish_status(
                    'busy', request_id, message,
                    command_started=True
                )
                final_position = pick_and_place(
                    request['source'], request['destination']
                )
                placement_error = 0.0
            else:
                select_task(request['source'], request['destination'])
                message = f"Executing {request['action']}({request['target']})"
                publish_status(
                    'busy', request_id, message,
                    command_started=True
                )
                placement_error = execute_skill(
                    request['action'], request['target']
                )
                final_position = self.sim.getObjectPosition(
                    self.part, self.sim.handle_world
                )
            publish_status(
                'done', request_id, message,
                part_position=final_position,
                placement_error_m=placement_error,
                reachable=True,
                collision_detected=False,
                command_started=True,
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
