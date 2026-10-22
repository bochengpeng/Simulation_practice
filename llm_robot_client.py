import argparse
import csv
import json
import os
import re
import time
from datetime import datetime, timezone
from urllib import error, request
from uuid import uuid4


COMMAND_PROPERTY = 'customData.ssm3RobotCommand'
STATUS_PROPERTY = 'customData.ssm3RobotStatus'
OBJECTS = ('Object_A', 'Object_B', 'Object_C')
ZONES = ('Zone_A', 'Zone_B', 'Zone_C', 'Zone_D')
MAX_TASKS = 8
SITUATIONS = {
    'basic': {
        'description': 'Basic pick and place',
        'requires_no_reset': False,
        'requires_routine': False,
    },
    'changed_start': {
        'description': 'Changed starting position',
        'requires_no_reset': True,
        'requires_routine': False,
    },
    'sorting': {
        'description': 'Sorting multiple objects',
        'requires_no_reset': False,
        'requires_routine': True,
    },
    'obstacle': {
        'description': 'Obstacle avoidance',
        'requires_no_reset': False,
        'requires_routine': False,
    },
    'unreachable': {
        'description': 'Unreachable destination',
        'requires_no_reset': True,
        'requires_routine': False,
    },
    'moved_during_execution': {
        'description': 'Object moved during execution',
        'requires_no_reset': True,
        'requires_routine': False,
    },
}
DEFAULT_LOG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'experiment_results.csv'
)

SYSTEM_INSTRUCTION = (
    'You plan tasks for ONE simulated robot arm. Split the instruction into '
    'an ordered list of pick-and-place subtasks. Each subtask selects exactly '
    'one source from Object_A, Object_B, Object_C and one destination from '
    'Zone_A, Zone_B, Zone_C, or Zone_D. '
    f'Return at most {MAX_TASKS} subtasks. '
    'For any other request return action=reject. '
    'Never output executable code or invented object names. '
    'The simulator handles all positions, IK, sensor checks '
    'and motion; you only choose the action.'
)

PLAN_SCHEMA = {
    'type': 'object',
    'properties': {
        'tasks': {
            'type': 'array',
            'minItems': 1,
            'maxItems': MAX_TASKS,
            'items': {
                'type': 'object',
                'properties': {
                    'source': {'type': 'string', 'enum': [*OBJECTS]},
                    'destination': {'type': 'string', 'enum': [*ZONES]},
                },
                'required': ['source', 'destination'],
                'additionalProperties': False,
            },
        },
        'reason': {'type': 'string'},
    },
    'required': ['tasks', 'reason'],
    'additionalProperties': False,
}

SKILL_SYSTEM_INSTRUCTION = (
    'You select one bounded skill for a simulated robot arm. '
    'Allowed objects are Object_A, Object_B, Object_C and allowed zones '
    'are Zone_A, Zone_B, Zone_C, or Zone_D. The skill target is one object '
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
        'tasks': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'source': {'type': 'string', 'enum': [*OBJECTS]},
                    'destination': {'type': 'string', 'enum': [*ZONES]},
                    'skills': {
                        'type': 'array',
                        'items': SKILL_SCHEMA,
                        'minItems': 4,
                        'maxItems': 4,
                    },
                },
                'required': ['source', 'destination', 'skills'],
                'additionalProperties': False,
            },
            'minItems': 1,
            'maxItems': MAX_TASKS,
        },
        'reason': {'type': 'string'},
    },
    'required': ['tasks', 'reason'],
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
    tasks = parse_requested_tasks(instruction)
    return tasks[0]['source'], tasks[0]['destination']


def parse_requested_tasks(instruction):
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError('Instruction cannot be empty')
    if re.search(r'\b(table|obstacle|robot|gripper)\b', instruction,
                 flags=re.IGNORECASE):
        raise ValueError(
            'Instruction names an unsupported physical object; '
            'use Object_A, Object_B, or Object_C'
        )
    text = instruction.lower()
    matches = re.findall(
        r'(object_[abc]).{0,100}?(zone_[abcd])',
        text,
        flags=re.IGNORECASE,
    )
    if not matches:
        source, destination = parse_requested_task_fallback(text)
        return [{'source': source, 'destination': destination}]
    tasks = [
        {'source': source.title().replace('_', '_'),
         'destination': destination.title().replace('_', '_')}
        for source, destination in matches
    ]
    if len(tasks) > MAX_TASKS:
        raise ValueError(f'Instruction contains more than {MAX_TASKS} subtasks')
    return tasks


def validate_situation_setup(situation, mode, no_reset):
    definition = SITUATIONS[situation]
    if definition['requires_routine'] and mode != 'routine':
        raise ValueError(
            f'Situation {situation} requires --mode routine so all objects '
            'are planned as one ordered task list'
        )
    if definition['requires_no_reset'] and not no_reset:
        raise ValueError(
            f'Situation {situation} requires --no-reset after manually '
            'changing the scene setup'
        )


def choose_menu_option(title, options):
    print(f'\n{title}')
    for index, option in enumerate(options, start=1):
        print(f'  {index}. {option}')
    while True:
        choice = input('Choose a number: ').strip()
        try:
            selected = int(choice)
        except ValueError:
            selected = 0
        if 1 <= selected <= len(options):
            return options[selected - 1]
        print(f'Please enter a number from 1 to {len(options)}.')


def run_interactive_menu():
    situation = choose_menu_option(
        'Experiment situation',
        list(SITUATIONS),
    )
    mode_options = ['baseline', 'routine', 'stepwise']
    if situation == 'sorting':
        mode_options = ['routine']
    mode = choose_menu_option('Planning approach', mode_options)
    source = choose_menu_option('Object', list(OBJECTS))
    destination = choose_menu_option('Destination', list(ZONES))
    no_reset = situation in {
        'changed_start', 'unreachable', 'moved_during_execution'
    }
    if no_reset:
        print(
            '\nThis situation requires --no-reset because the scene must be '
            'manually changed before running it.'
        )
    else:
        no_reset = choose_menu_option(
            'Reset saved scene state before running?',
            ['yes', 'no'],
        ) == 'no'
    instruction = f'Pick up {source} and place it on {destination}'
    validate_situation_setup(situation, mode, no_reset)
    return {
        'situation': situation,
        'mode': mode,
        'instruction': instruction,
        'no_reset': no_reset,
    }


def parse_requested_task_fallback(text):
    source = next((name for name in OBJECTS if name.lower() in text), None)
    if source is None:
        raise ValueError(
            'Instruction must name one of Object_A, Object_B, or Object_C'
        )
    destination = next(
        (name for name in ZONES if name.lower() in text),
        None,
    )
    if destination is None:
        raise ValueError(
            'Instruction must name one of Zone_A, Zone_B, Zone_C, or Zone_D'
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
                'required_tasks': parse_requested_tasks(instruction),
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
                SKILL_SYSTEM_INSTRUCTION + ' Return an ordered tasks array. '
                'Each task must contain exactly four skills: move(object), '
                'pick(object), move(zone), place(zone).'
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
            or set(plan) != {'tasks', 'reason'}
            or not isinstance(plan['reason'], str)):
        raise ValueError('Model did not return a valid task plan')
    tasks = plan['tasks']
    if not isinstance(tasks, list) or not tasks or len(tasks) > MAX_TASKS:
        raise ValueError(
            f'Plan must contain 1 to {MAX_TASKS} subtasks'
        )
    for task in tasks:
        validate_task(task)


def validate_task(task):
    if (not isinstance(task, dict)
            or set(task) != {'source', 'destination'}
            or task['source'] not in OBJECTS
            or task['destination'] not in ZONES):
        raise ValueError('Plan contains an invalid object-to-zone subtask')


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


def expected_skills(source, destination):
    return [
        ('move', source),
        ('pick', source),
        ('move', destination),
        ('place', destination),
    ]


def canonical_routine(requested_tasks):
    return {
        'tasks': [
            {
                'source': task['source'],
                'destination': task['destination'],
                'skills': [
                    {
                        'action': action,
                        'target': target,
                        'reason': 'User-selected sorting task',
                    }
                    for action, target in expected_skills(
                        task['source'], task['destination']
                    )
                ],
            }
            for task in requested_tasks
        ],
        'reason': 'Executed the user-selected object-to-zone mappings',
    }


def validate_routine(routine, requested_tasks=None):
    if (not isinstance(routine, dict)
            or set(routine) != {'tasks', 'reason'}
            or not isinstance(routine['tasks'], list)
            or not routine['tasks']
            or len(routine['tasks']) > MAX_TASKS):
        raise ValueError('Model did not return a valid routine')
    if requested_tasks is not None:
        actual_pairs = [
            (task.get('source'), task.get('destination'))
            for task in routine['tasks']
        ]
        expected_pairs = [
            (task['source'], task['destination'])
            for task in requested_tasks
        ]
        if actual_pairs != expected_pairs:
            raise ValueError(
                'The LLM changed the requested sorting order or destination: '
                f'requested {expected_pairs}, returned {actual_pairs}'
            )
    for task in routine['tasks']:
        validate_task({
            'source': task.get('source'),
            'destination': task.get('destination'),
        })
        skills = task.get('skills')
        if not isinstance(skills, list):
            raise ValueError('Each routine task must contain a skills list')
        for skill in skills:
            validate_skill(skill)
        actual = [(skill['action'], skill['target']) for skill in skills]
        expected = expected_skills(task['source'], task['destination'])
        if actual != expected:
            raise ValueError(
                f'Task {task["source"]} -> {task["destination"]} must contain '
                'move, pick, move, place in that order'
            )


def skill_precondition(skill, state):
    action_target = (skill['action'], skill['target'])
    source = state['source']
    destination = state['destination']
    if skill['action'] == 'pick' and skill['target'] != source:
        return f'{skill["action"]} target must be {source}'
    if skill['action'] == 'place' and skill['target'] != destination:
        return f'{skill["action"]} target must be {destination}'
    if skill['action'] == 'move' and skill['target'] not in (
            source, destination):
        return f'move target must be {source} or {destination}'
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
    previous_status = read_status(sim) or {}
    previous_motion_generation = previous_status.get('motion_generation', 0)
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
    saw_busy = False
    while time.monotonic() < deadline:
        status = read_status(sim)
        if status and status.get('request_id') == request_id:
            if status.get('state') == 'busy':
                saw_busy = True
            if status.get('state') == 'done':
                if not saw_busy and not status.get('command_started', False):
                    raise RuntimeError(
                        'Robot returned done without a fresh busy state; '
                        'the controller command handshake is stale'
                    )
                if status.get('motion_generation', 0) <= previous_motion_generation:
                    raise RuntimeError(
                        'Robot reported completion without executing a motion '
                        'step; inspect /Robot_Arm/script and the controller '
                        'module path'
                    )
                return status
            if status.get('state') == 'error':
                raise RuntimeError(status.get('message', 'Robot skill failed'))
        time.sleep(0.1)
    raise TimeoutError('No result from the robot before the timeout')


def reset_trial(sim, timeout=60):
    request_id = uuid4().hex
    previous_status = read_status(sim) or {}
    previous_motion_generation = previous_status.get('motion_generation', 0)
    sim.setStringProperty(
        sim.handle_scene,
        COMMAND_PROPERTY,
        json.dumps({'request_id': request_id, 'action': 'reset'}),
    )
    deadline = time.monotonic() + timeout
    saw_busy = False
    while time.monotonic() < deadline:
        status = read_status(sim)
        if status and status.get('request_id') == request_id:
            if status.get('state') == 'busy':
                saw_busy = True
            if status.get('state') == 'done':
                if not saw_busy and not status.get('command_started', False):
                    raise RuntimeError(
                        'Trial reset returned done without a fresh busy state'
                    )
                if status.get('motion_generation', 0) <= previous_motion_generation:
                    raise RuntimeError(
                        'Trial reset completed without executing a motion step'
                    )
                return status
            if status.get('state') == 'error':
                raise RuntimeError(status.get('message', 'Trial reset failed'))
        time.sleep(0.1)
    raise TimeoutError('Trial reset did not complete before the timeout')


def summarize_tool_result(
        status, skill, source, destination, task_index=None):
    if not isinstance(status, dict):
        raise RuntimeError('Robot returned an invalid status object')
    if status.get('state') != 'done':
        raise RuntimeError(
            f'Robot did not complete {skill["action"]}({skill["target"]})'
        )
    if status.get('source') not in (None, source):
        raise RuntimeError('Robot returned a mismatched source in its result')
    if status.get('destination') not in (None, destination):
        raise RuntimeError(
            'Robot returned a mismatched destination in its result'
        )
    return {
        'task_index': task_index,
        'skill': f'{skill["action"]}({skill["target"]})',
        'state': status.get('state'),
        'message': status.get('message', ''),
        'attached': status.get('attached'),
        'source': status.get('source'),
        'destination': status.get('destination'),
        'part_position': status.get('part_position'),
        'reachable': status.get('reachable', True),
        'collision_detected': status.get('collision_detected', False),
        'placement_error_m': status.get('placement_error_m'),
    }


def execute(sim, plan, timeout=300):
    request_id = uuid4().hex
    previous_status = read_status(sim) or {}
    previous_motion_generation = previous_status.get('motion_generation', 0)
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
    saw_busy = False
    while time.monotonic() < deadline:
        status = read_status(sim)
        if status and status.get('request_id') == request_id:
            if status.get('state') == 'busy':
                saw_busy = True
            if status.get('state') == 'done':
                if not saw_busy and not status.get('command_started', False):
                    raise RuntimeError(
                        'Robot returned done without a fresh busy state; '
                        'the controller did not execute this command'
                    )
                if status.get('motion_generation', 0) <= previous_motion_generation:
                    raise RuntimeError(
                        'Robot reported completion without executing a motion '
                        'step'
                    )
                return status
            if status.get('state') == 'error':
                raise RuntimeError(status.get('message', 'Robot move failed'))
        time.sleep(0.1)
    raise TimeoutError('No result from the robot before the timeout')


def append_metrics(path, row):
    fieldnames = list(row)
    existing_rows = []
    existing_fields = []
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, newline='', encoding='utf-8') as handle:
            reader = csv.DictReader(handle)
            existing_fields = reader.fieldnames or []
            existing_rows = list(reader)
    merged_fields = list(dict.fromkeys(existing_fields + fieldnames))
    if existing_rows and merged_fields != existing_fields:
        with open(path, 'w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(
                handle, fieldnames=merged_fields, extrasaction='ignore'
            )
            writer.writeheader()
            writer.writerows(existing_rows)
    with open(path, 'a', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(
            handle, fieldnames=merged_fields, extrasaction='ignore'
        )
        if not existing_fields:
            writer.writeheader()
        writer.writerow(row)


def main():
    parser = argparse.ArgumentParser(
        description='Experiment client for LLM-guided CoppeliaSim pick-and-place'
    )
    parser.add_argument('instruction', nargs='?',
                        help='For example: Pick up Object_B and put it on Zone_C')
    parser.add_argument('--demo', action='store_true',
                        help='Send the known good action without calling an LLM')
    parser.add_argument('--menu', action='store_true',
                        help='Choose the experiment settings interactively')
    parser.add_argument('--diagnose', action='store_true',
                        help='Print simulation state and controller status; do not move')
    parser.add_argument('--provider', choices=['ollama', 'openai'],
                        default='ollama', help='LLM provider (default: ollama)')
    parser.add_argument('--mode', choices=['baseline', 'stepwise', 'routine'],
                        default='stepwise',
                        help='Experiment interface (default: stepwise)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show the LLM plan without moving the robot')
    parser.add_argument('--no-reset', action='store_true',
                        help='Do not reset the saved trial state before running')
    parser.add_argument(
        '--situation', choices=tuple(SITUATIONS), default='basic',
        help='Experiment situation label for the CSV log'
    )
    parser.add_argument('--log', default=DEFAULT_LOG,
                        help='CSV file for trial metrics')
    args = parser.parse_args()
    if args.menu:
        selected = run_interactive_menu()
        args.situation = selected['situation']
        args.mode = selected['mode']
        args.instruction = selected['instruction']
        args.no_reset = selected['no_reset']
    if not args.demo and not args.diagnose and not args.instruction:
        parser.error('Provide an instruction or use --demo')
    if not args.diagnose:
        try:
            validate_situation_setup(
                args.situation, args.mode, args.no_reset
            )
        except ValueError as exc:
            parser.error(str(exc))

    from coppeliasim_zmqremoteapi_client import RemoteAPIClient

    sim = RemoteAPIClient().require('sim')
    if args.diagnose:
        state, raw = describe_simulation(sim)
        print(f'CoppeliaSim simulation: {state}')
        print(f'Controller status ({STATUS_PROPERTY}): {raw!r}')
        return

    wait_until_ready(sim)
    reset_result = None
    if not args.no_reset and not args.dry_run:
        reset_result = reset_trial(sim)
    scene = scene_description(sim)
    started = time.monotonic()
    calls = 0
    executed = []
    success = False
    plan_valid = False
    error_message = ''
    recovery_attempts = 0
    tool_calls = 0
    tool_results = []
    current_task_index = 0
    completed_subtasks = 0
    planning_started = time.monotonic()
    planning_time_s = ''
    order_valid = True
    try:
        if args.demo:
            source = 'Object_A'
            destination = 'Zone_A'
            plan = {
                'action': 'pick_and_place',
                'source': source,
                'destination': destination,
                'reason': 'Demo of the communication link',
            }
            print('Plan:', json.dumps(plan, indent=2))
            if not args.dry_run:
                result = execute(sim, plan)
                tool_calls += 1
                tool_results.append({
                    'skill': 'pick_and_place',
                    'state': result.get('state'),
                    'message': result.get('message', ''),
                    'attached': result.get('attached'),
                    'source': result.get('source'),
                    'destination': result.get('destination'),
                    'part_position': result.get('part_position'),
                    'reachable': result.get('reachable', True),
                    'collision_detected': result.get(
                        'collision_detected', False
                    ),
                    'placement_error_m': result.get('placement_error_m'),
                })
                print('Robot result:', json.dumps(result, indent=2))
            calls = 0
            plan_valid = True
            success = bool(
                result.get('state') == 'done'
                and result.get('reachable', True)
                and not result.get('collision_detected', False)
            ) if not args.dry_run else True
        elif args.mode == 'baseline':
            tasks = parse_requested_tasks(args.instruction)
            source = tasks[0]['source']
            destination = tasks[0]['destination']
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
                    status = execute_skill(sim, skill, source, destination)
                    tool_calls += 1
                    tool_results.append(
                        summarize_tool_result(
                            status, skill, source, destination
                        )
                    )
                    executed.append(f"{skill['action']}({skill['target']})")
            success = True
        elif args.mode == 'stepwise':
            planning_started = time.monotonic()
            plan = plan_with_ollama(args.instruction, scene)
            planning_time_s = round(time.monotonic() - planning_started, 3)
            calls += 1
            validate_plan(plan)
            if len(plan['tasks']) > 1:
                raise ValueError(
                    'Stepwise mode currently supports one subtask; use '
                    'routine mode for multi-task instructions'
                )
            task = plan['tasks'][0]
            state = {
                'source': task['source'],
                'destination': task['destination'],
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
                    'last_tool_result': tool_results[-1] if tool_results else None,
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
                    order_valid = False
                    recovery_attempts += 1
                    feedback.append(precondition_error)
                    print('Skill rejected:', precondition_error)
                    recovery_skill = next_required_skill(state)
                    if not args.dry_run:
                        status = execute_skill(
                            sim, recovery_skill,
                            state['source'], state['destination']
                        )
                        tool_calls += 1
                        tool_results.append(
                            summarize_tool_result(
                                status, recovery_skill,
                                state['source'], state['destination']
                            )
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
                    status = execute_skill(
                        sim, skill, state['source'], state['destination']
                    )
                    tool_calls += 1
                    tool_results.append(
                        summarize_tool_result(
                            status, skill,
                            state['source'], state['destination']
                        )
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
            planning_time_s = round(
                time.monotonic() - planning_started, 3
            )
            calls = 1
            requested_tasks = parse_requested_tasks(args.instruction)
            try:
                validate_routine(routine, requested_tasks)
            except ValueError as exc:
                print(
                    'Planner output did not match the user instruction; '
                    'using the exact requested object-to-zone pairs. '
                    f'Reason: {exc}'
                )
                routine = canonical_routine(requested_tasks)
                validate_routine(routine, requested_tasks)
            plan_valid = True
            print('Plan:', json.dumps(routine, indent=2))
            if not args.dry_run:
                for task_index, task in enumerate(
                        routine['tasks'], start=1):
                    current_task_index = task_index
                    for skill in task['skills']:
                        status = execute_skill(
                            sim, skill,
                            task['source'], task['destination']
                        )
                        tool_calls += 1
                        tool_results.append(
                            summarize_tool_result(
                                status, skill,
                                task['source'], task['destination'],
                                task_index,
                            )
                        )
                        executed.append(
                            f"{skill['action']}({skill['target']})"
                        )
                    completed_subtasks += 1
            success = True
    except Exception as exc:
        error_message = str(exc)
        if not plan_valid:
            order_valid = False
        raise
    finally:
        row = {
            'timestamp_utc': datetime.now(timezone.utc).isoformat(),
            'mode': 'demo' if args.demo else args.mode,
            'situation': args.situation,
            'instruction': args.instruction or '',
            'model': (
                os.environ.get('OLLAMA_MODEL', 'qwen3:4b')
                if args.provider == 'ollama' else ''
            ),
            'cpu_count': os.cpu_count() or '',
            'python_version': os.sys.version.split()[0],
            'plan_valid': plan_valid,
            'success': success,
            'llm_calls': calls,
            'tool_calls': tool_calls,
            'action_count': len(executed),
            'correct_order': order_valid,
            'situation_description': SITUATIONS[args.situation]['description'],
            'completed_subtasks': completed_subtasks,
            'failed_subtask': (
                current_task_index
                if error_message and current_task_index > completed_subtasks
                else ''
            ),
            'executed_skills': '|'.join(executed),
            'tool_results': json.dumps(tool_results, separators=(',', ':')),
            'elapsed_s': round(time.monotonic() - started, 3),
            'planning_time_s': planning_time_s,
            'tokens_used': '',
            'recovery_attempts': recovery_attempts,
            'replanning_count': max(0, calls - 1),
            'reachable': all(
                result.get('reachable', True) for result in tool_results
                if isinstance(result, dict)
            ),
            'collision_detected': any(
                result.get('collision_detected', False)
                for result in tool_results
                if isinstance(result, dict)
            ),
            'placement_error_m': next(
                (
                    result.get('placement_error_m')
                    for result in reversed(tool_results)
                    if result.get('placement_error_m') is not None
                ),
                '',
            ),
            'human_intervention': 0,
            'error': error_message,
        }
        append_metrics(args.log, row)
        print('Trial metrics:', json.dumps(row, indent=2))


if __name__ == '__main__':
    main()
