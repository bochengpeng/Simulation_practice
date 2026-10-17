import argparse
import csv
import json
import os
import time
from datetime import datetime, timezone
from urllib import error, request
from uuid import uuid4


COMMAND_PROPERTY = 'customData.ssm3RobotCommand'
STATUS_PROPERTY = 'customData.ssm3RobotStatus'
OBJECTS = ('Object_A', 'Object_B', 'Object_C')
ZONES = ('Zone_A', 'Zone_B', 'Zone_C', 'Zone_D')
DEFAULT_LOG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'experiment_results.csv'
)

SYSTEM_INSTRUCTION = (
    'You select high-level actions for ONE simulated robot arm. '
    'Select exactly one source from Object_A, Object_B, Object_C and '
    'one destination from Zone_A, Zone_B, Zone_C, Zone_D. Return '
    'action=pick_and_place with the selected source and destination. '
    'For any other request return action=reject. '
    'Never output executable code or invented object names. '
    'The simulator handles all positions, IK, sensor checks '
    'and motion; you only choose the action.'
)

PLAN_SCHEMA = {
    'type': 'object',
    'properties': {
        'action': {'type': 'string', 'enum': ['pick_and_place', 'reject']},
        'source': {'type': 'string'},
        'destination': {'type': 'string'},
        'reason': {'type': 'string'},
    },
    'required': ['action', 'source', 'destination', 'reason'],
    'additionalProperties': False,
}

SKILL_SYSTEM_INSTRUCTION = (
    'You select one bounded skill for a simulated robot arm. '
    'Allowed objects are Object_A, Object_B, Object_C and allowed zones '
    'are Zone_A, Zone_B, Zone_C, Zone_D. The skill target is one object '
    'or one zone. Skills have preconditions: move(object) must happen '
    'before pick(object), pick must happen before move(zone), and '
    'move(zone) must happen before place(zone). Use feedback '
    'to correct an earlier invalid choice. Never output code or invented names.'
)

SKILL_SCHEMA = {
    'type': 'object',
    'properties': {
        'action': {'type': 'string', 'enum': ['move', 'pick', 'place']},
        'target': {
            'type': 'string',
            'enum': [*OBJECTS, *ZONES],
        },
        'reason': {'type': 'string'},
    },
    'required': ['action', 'target', 'reason'],
    'additionalProperties': False,
}

ROUTINE_SCHEMA = {
    'type': 'object',
    'properties': {
        'skills': {
            'type': 'array',
            'items': SKILL_SCHEMA,
            'minItems': 1,
            'maxItems': 8,
        },
        'reason': {'type': 'string'},
    },
    'required': ['skills', 'reason'],
    'additionalProperties': False,
}


def read_status(sim):
    value = sim.getStringProperty(
        sim.handle_scene, STATUS_PROPERTY, {'noError': True}
    )
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode('utf-8')
    return json.loads(value)


def describe_simulation(sim):
    state = sim.getSimulationState()
    if state == sim.simulation_stopped:
        state_name = 'stopped'
    elif state == sim.simulation_paused:
        state_name = 'paused'
    else:
        state_name = f'running/transitional ({state})'

    raw = sim.getStringProperty(
        sim.handle_scene, STATUS_PROPERTY, {'noError': True}
    )
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8', errors='replace')
    return state_name, raw


def wait_until_ready(sim, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = read_status(sim)
        if sim.getSimulationState() == sim.simulation_stopped:
            raise RuntimeError('Simulation is stopped. Press Play in CoppeliaSim first.')
        if sim.getSimulationState() == sim.simulation_paused:
            raise RuntimeError('Simulation is paused. Resume it in CoppeliaSim first.')
        if status and status.get('state') in ('ready', 'done', 'error'):
            if status.get('attached'):
                raise RuntimeError(
                    'An object is still attached; stop and inspect the simulation'
                )
            if status.get('heartbeat', 0) > 0:
                return status
        time.sleep(0.1)
    state, raw = describe_simulation(sim)
    raise TimeoutError(
        f'Robot controller did not become ready. Simulation: {state}; '
        f'{STATUS_PROPERTY}: {raw!r}. If the simulation is running but '
        'the status is None, check the CoppeliaSim console for an error in '
        '/Robot_Arm/script and verify that both Python files were replaced. '
        'If the status exists but heartbeat stays at 0, the controller thread '
        'is not running; check the console for /Robot_Arm/script:error.'
    )


def scene_description(sim):
    world = sim.handle_world
    return {
        'objects': {
            name: {
                'position_m': sim.getObjectPosition(
                    sim.getObject('/' + name), world
                ),
                'size_m': sim.getShapeBB(
                    sim.getObject('/' + name)
                )[0],
            }
            for name in OBJECTS
        },
        'zones': {
            name: {
                'position_m': sim.getObjectPosition(
                    sim.getObject('/' + name), world
                ),
                'size_m': sim.getShapeBB(
                    sim.getObject('/' + name)
                )[0],
            }
            for name in ZONES
        },
    }


def parse_requested_task(instruction):
    text = instruction.lower()
    source = next(
        (name for name in OBJECTS if name.lower() in text),
        'Object_A',
    )
    destination = next(
        (name for name in ZONES if name.lower() in text),
        'Zone_D',
    )
    return source, destination


def plan_with_ollama(instruction, scene):
    """Query a model running locally in Ollama; no paid API is needed."""
    payload = {
        'model': os.environ.get('OLLAMA_MODEL', 'qwen3:4b'),
        'messages': [
            {'role': 'system', 'content': SYSTEM_INSTRUCTION},
            {'role': 'user', 'content': json.dumps({
                'instruction': instruction, 'scene': scene,
            })},
        ],
        'stream': False,
        'think': False,
        'format': PLAN_SCHEMA,
        'options': {'temperature': 0},
    }
    http_request = request.Request(
        'http://127.0.0.1:11434/api/chat',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with request.urlopen(http_request, timeout=180) as response:
            result = json.load(response)
    except error.HTTPError as exc:
        details = exc.read().decode('utf-8', errors='replace')[:300]
        raise RuntimeError(
            f'Ollama returned HTTP {exc.code}: {details}. '
            'Check that the requested model has been downloaded.'
        ) from exc
    except error.URLError as exc:
        raise RuntimeError(
            'Cannot reach Ollama. Install and start Ollama, then run '
            '`ollama pull qwen3:4b` in PowerShell.'
        ) from exc
    try:
        return json.loads(result['message']['content'])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('Local model returned an invalid plan') from exc


def plan_skill_with_ollama(instruction, scene, state):
    payload = {
        'model': os.environ.get('OLLAMA_MODEL', 'qwen3:4b'),
        'messages': [
            {'role': 'system', 'content': SKILL_SYSTEM_INSTRUCTION},
            {'role': 'user', 'content': json.dumps({
                'instruction': instruction,
                'scene': scene,
                'state': state,
            })},
        ],
        'stream': False,
        'think': False,
        'format': SKILL_SCHEMA,
        'options': {'temperature': 0},
    }
    http_request = request.Request(
        'http://127.0.0.1:11434/api/chat',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with request.urlopen(http_request, timeout=180) as response:
            result = json.load(response)
        return json.loads(result['message']['content'])
    except error.HTTPError as exc:
        details = exc.read().decode('utf-8', errors='replace')[:300]
        raise RuntimeError(f'Ollama returned HTTP {exc.code}: {details}') from exc
    except error.URLError as exc:
        raise RuntimeError(
            'Cannot reach Ollama. Install and start Ollama, then run '
            '`ollama pull qwen3:4b` in PowerShell.'
        ) from exc
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('Local model returned an invalid skill') from exc


def plan_routine_with_ollama(instruction, scene):
    payload = {
        'model': os.environ.get('OLLAMA_MODEL', 'qwen3:4b'),
        'messages': [
            {'role': 'system', 'content': (
                SKILL_SYSTEM_INSTRUCTION + ' Return a short complete routine '
                'in the skills array, normally move, pick, move, place.'
            )},
            {'role': 'user', 'content': json.dumps({
                'instruction': instruction, 'scene': scene,
            })},
        ],
        'stream': False,
        'think': False,
        'format': ROUTINE_SCHEMA,
        'options': {'temperature': 0},
    }
    http_request = request.Request(
        'http://127.0.0.1:11434/api/chat',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with request.urlopen(http_request, timeout=180) as response:
            result = json.load(response)
        return json.loads(result['message']['content'])
    except error.HTTPError as exc:
        details = exc.read().decode('utf-8', errors='replace')[:300]
        raise RuntimeError(f'Ollama returned HTTP {exc.code}: {details}') from exc
    except error.URLError as exc:
        raise RuntimeError(
            'Cannot reach Ollama. Install and start Ollama, then run '
            '`ollama pull qwen3:4b` in PowerShell.'
        ) from exc
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('Local model returned an invalid routine') from exc


def validate_plan(plan):
    if (not isinstance(plan, dict)
            or set(plan) != {'action', 'source', 'destination', 'reason'}
            or any(not isinstance(value, str) for value in plan.values())):
        raise ValueError('Model did not return a valid action object')
    if plan['action'] == 'reject':
        raise ValueError('Unsupported instruction: ' + plan['reason'])
    if (plan['action'] != 'pick_and_place'
            or plan['source'] not in OBJECTS
            or plan['destination'] not in ZONES):
        raise ValueError('Model output is outside the allowed robot actions')


def validate_skill(skill):
    if (not isinstance(skill, dict)
            or set(skill) != {'action', 'target', 'reason'}
            or any(not isinstance(value, str) for value in skill.values())):
        raise ValueError('Model did not return a valid skill object')
    if (skill['action'], skill['target']) not in {
            *[(action, target) for action in ('move', 'pick')
              for target in OBJECTS],
            *[(action, target) for action in ('move', 'place')
              for target in ZONES]}:
        raise ValueError('Model output is outside the allowed robot skills')


def validate_routine(routine, source, destination):
    if (not isinstance(routine, dict)
            or set(routine) != {'skills', 'reason'}
            or not isinstance(routine['skills'], list)):
        raise ValueError('Model did not return a valid routine')
    for skill in routine['skills']:
        validate_skill(skill)
    expected = [
        ('move', source),
        ('pick', source),
        ('move', destination),
        ('place', destination),
    ]
    actual = [
        (skill['action'], skill['target'])
        for skill in routine['skills']
    ]
    if actual != expected:
        raise ValueError(
            f'Routine must contain move({source}), pick({source}), '
            f'move({destination}), place({destination}) in that order'
        )


def skill_precondition(skill, state):
    action_target = (skill['action'], skill['target'])
    source = state['source']
    destination = state['destination']
    if skill['action'] in ('move', 'pick') and skill['target'] != source:
        return f'{skill["action"]} target must be {source}'
    if skill['action'] in ('move', 'place') and skill['target'] != destination:
        return f'{skill["action"]} target must be {destination}'
    if action_target == ('move', source):
        if state['object_positioned']:
            return f'move({source}) is already complete; choose pick({source})'
        return None
    if action_target == ('pick', source):
        if not state['object_positioned']:
            return f'Pick requires move({source}) first'
        if state['holding_part']:
            return f'pick({source}) is already complete; choose move({destination})'
    if action_target == ('move', destination):
        if not state['holding_part']:
            return f'Move to {destination} requires pick({source}) first'
        if state['at_destination']:
            return f'move({destination}) is already complete; choose place({destination})'
    if action_target == ('place', destination):
        if not state['holding_part']:
            return f'Place requires pick({source}) first'
        if not state['at_destination']:
            return f'Place requires move({destination}) first'
    return None


def next_required_skill(state):
    if not state['object_positioned']:
        return {'action': 'move', 'target': state['source'], 'reason': 'recovery'}
    if not state['holding_part']:
        return {'action': 'pick', 'target': state['source'], 'reason': 'recovery'}
    if not state['at_destination']:
        return {'action': 'move', 'target': state['destination'], 'reason': 'recovery'}
    return {'action': 'place', 'target': state['destination'], 'reason': 'recovery'}


def update_skill_state(skill, state):
    action_target = (skill['action'], skill['target'])
    if action_target == ('move', state['source']):
        state['object_positioned'] = True
    elif action_target == ('pick', state['source']):
        state['holding_part'] = True
    elif action_target == ('move', state['destination']):
        state['at_destination'] = True
    elif action_target == ('place', state['destination']):
        state['holding_part'] = False


def task_state_complete(state):
    return (
        state['object_positioned']
        and not state['holding_part']
        and state['at_destination']
    )


def execute_skill(sim, skill, source, destination, timeout=300):
    request_id = uuid4().hex
    command = {
        'request_id': request_id,
        'action': skill['action'],
        'target': skill['target'],
        'source': source,
        'destination': destination,
    }
    sim.setStringProperty(
        sim.handle_scene, COMMAND_PROPERTY, json.dumps(command)
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = read_status(sim)
        if status and status.get('request_id') == request_id:
            if status.get('state') == 'done':
                return status
            if status.get('state') == 'error':
                raise RuntimeError(status.get('message', 'Robot skill failed'))
        time.sleep(0.1)
    raise TimeoutError('No result from the robot before the timeout')


def execute(sim, plan, timeout=300):
    request_id = uuid4().hex
    command = {
        'request_id': request_id,
        'action': plan['action'],
        'source': plan['source'],
        'destination': plan['destination'],
    }
    # The simulation script owns movement. The client sends one request only.
    sim.setStringProperty(
        sim.handle_scene, COMMAND_PROPERTY, json.dumps(command)
    )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = read_status(sim)
        if status and status.get('request_id') == request_id:
            if status.get('state') == 'done':
                return status
            if status.get('state') == 'error':
                raise RuntimeError(status.get('message', 'Robot move failed'))
        time.sleep(0.1)
    raise TimeoutError('No result from the robot before the timeout')


def main():
    parser = argparse.ArgumentParser(
        description='Experiment client for LLM-guided CoppeliaSim pick-and-place'
    )
    parser.add_argument('instruction', nargs='?',
                        help='For example: Pick up Object_B and put it on Zone_C')
    parser.add_argument('--demo', action='store_true',
                        help='Send the known good action without calling an LLM')
    parser.add_argument('--diagnose', action='store_true',
                        help='Print simulation state and controller status; do not move')
    parser.add_argument('--provider', choices=['ollama', 'openai'],
                        default='ollama', help='LLM provider (default: ollama)')
    parser.add_argument('--mode', choices=['baseline', 'stepwise', 'routine'],
                        default='stepwise',
                        help='Experiment interface (default: stepwise)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show the LLM plan without moving the robot')
    parser.add_argument('--log', default=DEFAULT_LOG,
                        help='CSV file for trial metrics')
    args = parser.parse_args()
    if not args.demo and not args.diagnose and not args.instruction:
        parser.error('Provide an instruction or use --demo')

    from coppeliasim_zmqremoteapi_client import RemoteAPIClient

    sim = RemoteAPIClient().require('sim')
    if args.diagnose:
        state, raw = describe_simulation(sim)
        print(f'CoppeliaSim simulation: {state}')
        print(f'Controller status ({STATUS_PROPERTY}): {raw!r}')
        return

    wait_until_ready(sim)
    scene = scene_description(sim)
    started = time.monotonic()
    calls = 0
    executed = []
    success = False
    plan_valid = False
    error_message = ''
    recovery_attempts = 0
    try:
        if args.demo:
            source = 'Object_A'
            destination = 'Zone_D'
            plan = {
                'action': 'pick_and_place',
                'source': source,
                'destination': destination,
                'reason': 'Demo of the communication link',
            }
            print('Plan:', json.dumps(plan, indent=2))
            if not args.dry_run:
                result = execute(sim, plan)
                print('Robot result:', json.dumps(result, indent=2))
            calls = 0
            plan_valid = True
            success = True
        elif args.mode == 'baseline':
            source, destination = parse_requested_task(args.instruction)
            routine = [
                {'action': 'move', 'target': source, 'reason': 'fixed baseline'},
                {'action': 'pick', 'target': source, 'reason': 'fixed baseline'},
                {'action': 'move', 'target': destination, 'reason': 'fixed baseline'},
                {'action': 'place', 'target': destination, 'reason': 'fixed baseline'},
            ]
            print('Plan:', json.dumps(routine, indent=2))
            plan_valid = True
            if not args.dry_run:
                for skill in routine:
                    execute_skill(sim, skill, source, destination)
                    executed.append(f"{skill['action']}({skill['target']})")
            success = True
        elif args.mode == 'stepwise':
            plan = plan_with_ollama(args.instruction, scene)
            calls += 1
            validate_plan(plan)
            state = {
                'source': plan['source'],
                'destination': plan['destination'],
                'object_positioned': False,
                'holding_part': False,
                'at_destination': False,
            }
            feedback = []
            for _ in range(8):
                if args.provider != 'ollama':
                    raise NotImplementedError(
                        'The experiment modes currently support Ollama only'
                    )
                planner_state = {
                    'completed_skills': state,
                    'feedback': feedback[-3:],
                }
                skill = plan_skill_with_ollama(
                    args.instruction, scene, planner_state
                )
                calls += 1
                validate_skill(skill)
                plan_valid = True
                print('Skill:', json.dumps(skill, indent=2))
                precondition_error = skill_precondition(skill, state)
                if precondition_error:
                    recovery_attempts += 1
                    feedback.append(precondition_error)
                    print('Skill rejected:', precondition_error)
                    recovery_skill = next_required_skill(state)
                    if not args.dry_run:
                        execute_skill(
                            sim, recovery_skill,
                            state['source'], state['destination']
                        )
                    executed.append(
                        f"{recovery_skill['action']}({recovery_skill['target']})"
                    )
                    update_skill_state(recovery_skill, state)
                    if task_state_complete(state):
                        success = True
                        break
                    continue
                if not args.dry_run:
                    execute_skill(
                        sim, skill, state['source'], state['destination']
                    )
                    executed.append(f"{skill['action']}({skill['target']})")
                update_skill_state(skill, state)
                if task_state_complete(state):
                    success = True
                    break
            if not success:
                raise RuntimeError(
                    'Stepwise planner did not produce a complete valid routine'
                )
        else:
            if args.provider != 'ollama':
                raise NotImplementedError(
                    'The experiment modes currently support Ollama only'
                )
            routine = plan_routine_with_ollama(args.instruction, scene)
            calls = 1
            if (not isinstance(routine, dict)
                    or not isinstance(routine.get('skills'), list)
                    or len(routine['skills']) < 4
                    or not all(
                        isinstance(item, dict) for item in routine['skills'][:4]
                    )):
                raise ValueError('Routine must contain four ordered skills')
            source = routine['skills'][0]['target']
            destination = routine['skills'][2]['target']
            if source not in OBJECTS or destination not in ZONES:
                raise ValueError('Routine selected an invalid object or zone')
            validate_routine(routine, source, destination)
            plan_valid = True
            print('Plan:', json.dumps(routine, indent=2))
            if not args.dry_run:
                for skill in routine['skills']:
                    execute_skill(sim, skill, source, destination)
                    executed.append(f"{skill['action']}({skill['target']})")
            success = True
    except Exception as exc:
        error_message = str(exc)
        raise
    finally:
        row = {
            'timestamp_utc': datetime.now(timezone.utc).isoformat(),
            'mode': 'demo' if args.demo else args.mode,
            'instruction': args.instruction or '',
            'plan_valid': plan_valid,
            'success': success,
            'llm_calls': calls,
            'executed_skills': '|'.join(executed),
            'elapsed_s': round(time.monotonic() - started, 3),
            'recovery_attempts': recovery_attempts,
            'human_intervention': 0,
            'error': error_message,
        }
        log_exists = os.path.exists(args.log)
        with open(args.log, 'a', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=row)
            if not log_exists:
                writer.writeheader()
            writer.writerow(row)
        print('Trial metrics:', json.dumps(row, indent=2))


if __name__ == '__main__':
    main()
