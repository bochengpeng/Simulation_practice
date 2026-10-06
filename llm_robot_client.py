"""Run on Windows, outside CoppeliaSim, while the scene is playing.

Examples:
    python llm_robot_client.py --demo
    python llm_robot_client.py "Pick up Part_A and put it on PlaceZone" --dry-run
    python llm_robot_client.py "Pick up Part_A and put it on PlaceZone"
    python llm_robot_client.py --provider openai "Pick up Part_A and put it on PlaceZone"
"""

import argparse
import json
import os
import time
from typing import Literal
from urllib import error, request
from uuid import uuid4


COMMAND_PROPERTY = 'customData.ssm3RobotCommand'
STATUS_PROPERTY = 'customData.ssm3RobotStatus'
SOURCE = 'Part_A'
DESTINATION = 'PlaceZone'

SYSTEM_INSTRUCTION = (
    'You select high-level actions for ONE simulated robot arm. '
    'The only supported action is to pick Part_A and place it '
    'on PlaceZone. Return action=pick_and_place, source=Part_A, '
    'destination=PlaceZone only when the user requests exactly '
    'that task. For any other request return action=reject. '
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
                    'Part_A is still attached; stop and inspect the simulation'
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
    part = sim.getObject('/Part_A')
    zone = sim.getObject('/PlaceZone')
    world = sim.handle_world
    return {
        'Part_A_position_m': sim.getObjectPosition(part, world),
        'PlaceZone_position_m': sim.getObjectPosition(zone, world),
    }


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


def validate_plan(plan):
    if (not isinstance(plan, dict)
            or set(plan) != {'action', 'source', 'destination', 'reason'}
            or any(not isinstance(value, str) for value in plan.values())):
        raise ValueError('Model did not return a valid action object')
    if plan['action'] == 'reject':
        raise ValueError('Unsupported instruction: ' + plan['reason'])
    if (plan['action'] != 'pick_and_place'
            or plan['source'] != SOURCE
            or plan['destination'] != DESTINATION):
        raise ValueError('Model output is outside the allowed robot actions')


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
    parser = argparse.ArgumentParser(description='LLM to CoppeliaSim pick-and-place')
    parser.add_argument('instruction', nargs='?',
                        help='For example: Pick up Part_A and put it on PlaceZone')
    parser.add_argument('--demo', action='store_true',
                        help='Send the known good action without calling an LLM')
    parser.add_argument('--diagnose', action='store_true',
                        help='Print simulation state and controller status; do not move')
    parser.add_argument('--provider', choices=['ollama', 'openai'],
                        default='ollama', help='LLM provider (default: ollama)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show the LLM plan without moving the robot')
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
    if args.demo:
        plan = {
            'action': 'pick_and_place',
            'source': SOURCE,
            'destination': DESTINATION,
            'reason': 'Demo of the communication link',
        }
    else:
        if args.provider == 'ollama':
            plan = plan_with_ollama(args.instruction, scene)
        else:
            plan = plan_with_openai(args.instruction, scene)

    print('Plan:', json.dumps(plan, indent=2))
    validate_plan(plan)
    if args.dry_run:
        print('Dry run: no command sent.')
        return

    result = execute(sim, plan)
    print('Robot result:', json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
