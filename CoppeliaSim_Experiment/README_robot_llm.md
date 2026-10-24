# LLM-controlled pick-and-place prototype

This version uses two Python programs:

- `robot_controller.py` runs inside `/Robot_Arm/script`. It waits for a command and controls the arm through the existing IK, sensor, and suction routines.
- `llm_robot_client.py` runs in a separate Windows terminal. It asks the LLM for one structured action, checks the action, sends it to CoppeliaSim, and reports the result.

The scene exposes four bounded skills for a selected task:
`move(Object_X)`, `pick(Object_X)`, `move(Zone_X)`, and `place(Zone_X)`.
The source can be `Object_A`, `Object_B`, or `Object_C`; destinations can be
`Zone_A`, `Zone_B`, `Zone_C`, or `Zone_D`. The legacy
`pick_and_place(source, destination)` command remains available as a
communication-link demo.

The scene file now also contains the reusable experiment fixtures from the
SSM3 plan: `Object_A` (renamed from `Part_A`), `Object_B` (blue),
`Object_C` (cylinder), `Zone_A`, `Zone_B`, `Zone_C`, `Zone_D`,
`Obstacle_Test`, and `Home_Position`. `Obstacle_Test` is a static respondable obstacle;
disable or remove it for non-obstacle trials and restore it for the obstacle
condition. The controller and client accept every object-to-zone combination.
The client extracts explicit object-to-zone pairs from the instruction and
preserves their order. If the LLM proposes a different mapping, the client
uses a deterministic routine generated from the user's selected pairs.

The initial scene layout places the robot at the center of a compact
four-zone workspace. `Zone_A` through `Zone_D` are static respondable
placement surfaces. Each zone is approximately `0.28 x 0.28 x 0.02 m`,
large enough for several `0.10 m` objects, and the zones are separated so
their physical collision volumes do not overlap. Their saved centers are
approximately:

```text
Zone_A [-0.48, -0.98, 0.715]
Zone_B [-0.16, -0.98, 0.715]
Zone_C [-0.48, -0.66, 0.715]
Zone_D [-0.16, -0.66, 0.715]
```

The initial objects are separated on the tabletop to avoid physics overlap
before a trial:

```text
Object_A near Zone_C
Object_B near Zone_A
Object_C near the centre of the workspace
```

They are configured as **dynamic and respondable** shapes at controller
startup, with their respondable masks normalized so physical objects collide
with one another and with the support shapes. The zones are **static and
respondable**, and the obstacle is also static and respondable. During pickup,
the selected object is temporarily made non-dynamic and non-respondable only
while it is rigidly attached to the suction pad. On release, its original
dynamic/respondable state is restored immediately, so it remains a solid body
and can support or collide with later placements. Physically valid stacking is
allowed; interpenetration is not. When more than one object is placed in the
same zone, the controller selects separate drop slots instead of placing
every object at the zone centre. Keep the zone arrangement inside the tested
IK workspace.

The saved scene contains a reachable IK startup calibration pose for
`/Robot_Arm/manipSphere/target` near `[-0.276, -0.640, 1.310]`. The controller
restores this pose during initialization, so pressing Play after an
interrupted or previously moved trial does not reuse a stale unreachable
target. The expected startup status is `ready` with no command required.

Each command must produce a fresh request acknowledgement (`busy` or the
`command_started` marker) before the client accepts `done`. If the client
reports completion while the joints,
target, and sensor remain still, stop the simulation and check that
`/Robot_Arm/script` is the threaded script containing `include
robot_controller`, that no second controller is attached to the arm, and that
the current `robot_controller.py` is on CoppeliaSim's Python module path.
Running `python llm_robot_client.py --diagnose` shows the simulation state and
the controller version/heartbeat without sending a motion command.

The client resets the saved object poses, attachment state, robot home pose,
and command/status state before each trial by default. Use `--no-reset` only
for deliberate changed-position or moved-object feedback situations.

If objects appear to pass through one another or remain merged, stop and
restart the simulation after confirming that the updated
`robot_controller.py` is loaded. The controller reports
`multi-object-v8-solid-objects`, normalizes the objects' respondable masks,
and restores dynamic/respondable physics immediately after every release.
Do not permanently make objects static or non-respondable to hide an overlap:
that prevents valid collision response and stacking. Reload the saved scene
before a new trial so an earlier physically unstable placement is not reused.

## First experiment situations

Use these six labels for the first data collection. Keep the object, zone,
prompt, model settings, and success criteria fixed within a situation. Run
each situation five times for each comparison condition (`baseline`,
`routine` as the open-loop plan, and `stepwise` as feedback planning).

| Label | Situation | Setup |
| --- | --- | --- |
| `basic` | Basic pick and place | Use the saved scene and one explicit object-to-zone instruction. |
| `changed_start` | Changed starting position | Move one object to another reachable tabletop position, then use `--no-reset`. |
| `sorting` | Sorting or ordered multi-object transfer | Use one or more explicit object-to-zone assignments and `--mode routine`; the client validates every pair and preserves its order. |
| `obstacle` | Obstacle avoidance | Enable `Obstacle_Test` and keep it between the robot approach path and the target. |
| `unreachable` | Unreachable destination | Move one supported zone outside the IK workspace, then use `--no-reset`; the request must fail explicitly. |
| `moved_during_execution` | Object moved during execution | Start with a changed object pose and use `--no-reset`; record whether feedback detects or recovers from the changed state. |

The last three setup changes are scene-level manipulations, not LLM
instructions. Sorting instructions must explicitly name each object and its
destination. Do not rely on the LLM to infer or reorder the mapping.
The client rejects `unreachable`, `changed_start`, and
`moved_during_execution` unless `--no-reset` is supplied, and requires
`--mode routine` for `sorting`.

Example commands:

```powershell
python llm_robot_client.py --situation basic --mode baseline `
  "Pick up Object_A and place it on Zone_B"
python llm_robot_client.py --situation sorting --mode routine `
  "Pick up Object_A and place it on Zone_B, then pick up Object_B and place it on Zone_C, then pick up Object_C and place it on Zone_A"
python llm_robot_client.py --situation changed_start --no-reset --mode stepwise `
  "Pick up Object_A and place it on Zone_B"
```

`Obstacle_Test` is a smaller fixed physical obstacle. It is static and
respondable, with a saved size of approximately `0.10 x 0.10 x 0.16 m` and a
center near `[-0.72, -0.82, 0.795]`. The controller checks the sensor path
before each object or zone approach. If the straight path crosses the
obstacle's clearance footprint, it raises the sensor above the obstacle and
then continues to the requested position. If either detour waypoint is not
reachable, the command fails explicitly instead of forcing the arm through the
obstacle. The LLM does not control this recovery; it remains deterministic in
`robot_controller.py`.

## Set up

1. Copy `robot_controller.py` and `llm_robot_client.py` into the same Windows
   project folder. Keep a copy of your working original script.
2. Make sure `/Robot_Arm/script` loads the updated `robot_controller.py` from that Windows folder. If you use a small loader script in CoppeliaSim already, keep it and update only its path if necessary. Do not run two controllers on the same arm.
3. Install [Ollama for Windows](https://ollama.com/download/windows). In PowerShell, download the free local model once:

   ```powershell
   ollama pull qwen3:4b
   ```

   The download is about 2.5 GB. Ollama must be running while you send prompts. A better equipped computer can use `qwen3:8b`; set `$env:OLLAMA_MODEL = 'qwen3:8b'` after downloading it.

4. Install the CoppeliaSim client for the Python interpreter that runs `llm_robot_client.py`:

   ```powershell
   py -3.13 -m pip install coppeliasim-zmqremoteapi-client
   ```

5. Press **Play** in CoppeliaSim, then in PowerShell run the communication
   link demo:

   ```powershell
   py -3.13 llm_robot_client.py --demo
   ```

   `--demo` checks the command link and runs the known action without calling the LLM. Reset the cube to its starting position and restart the simulation before the next physical run.

6. Run the fixed Python baseline. This sends the same four skills as the
   LLM modes, but without an LLM:

   ```powershell
   py -3.13 llm_robot_client.py --mode baseline `
     'Pick up Object_B and place it on Zone_C'
   ```

7. Run the one-skill-per-turn LLM interface:

   ```powershell
   py -3.13 llm_robot_client.py --mode stepwise `
     'Pick up Object_B and place it on Zone_C'
   ```

8. Run the short-routine LLM interface:

   ```powershell
   py -3.13 llm_robot_client.py --mode routine `
     'Pick up Object_B and place it on Zone_C'
   ```

For an interactive chooser instead of typing the full command, run:

```powershell
py -3.13 llm_robot_client.py --menu
```

The menu lets you choose the situation, planning approach, object, and
destination. It automatically requires `routine` for the sorting situation
and `--no-reset` for situations that require a manually changed scene. The
selected object and destination are used exactly as entered; the menu does
not add assignments for other objects. To execute multiple assignments, enter
all pairs explicitly in the command-line instruction. The `robot_chat.bat`
launcher opens this menu directly.

The client resets the saved object poses, attachment state, robot home pose,
and command/status state before each trial by default. Use `--no-reset` only
for deliberate changed-position or moved-object feedback situations.
Each run appends `experiment_results.csv` with situation, model, hardware,
mode, plan validity, reachability, collision, placement error, LLM calls,
robot tool calls, action count, correct order, planning time, token count,
per-tool results, completed subtasks, failed subtask, executed skills,
success, elapsed time, recovery/human-intervention fields, and any error.
`tokens_used` is left blank until the selected model/API exposes a reliable
token count. Use `--dry-run` to measure
plan validity without moving the arm. Stepwise mode asks the LLM for one
bounded skill per turn, validates its preconditions, executes one robot tool,
and includes the latest tool result in the next planning context. If the LLM
chooses an invalid next skill, the client performs the next required bounded
recovery skill and records it in `recovery_attempts`.

Routine mode asks the LLM for a short structured routine, normally
`move(object) -> pick(object) -> move(zone) -> place(zone)`. The client
executes each skill as a separate allow-listed robot-tool call, validates the
`done` result before continuing, records every result, and stops immediately
on an error. This is the paper's short-routine condition operationalized as
structured JSON rather than arbitrary model-generated Python; arbitrary code
execution is intentionally not permitted.

Single- and multi-task instructions are supported in routine mode. For
example, a single explicit assignment:

```powershell
python llm_robot_client.py --mode routine `
  'Pick up Object_C and place it on Zone_C'
```

For multiple assignments:

```powershell
python llm_robot_client.py --mode routine `
  "Pick up Object_A and place it on Zone_B, then pick up Object_C and place it on Zone_A"
```

The LLM returns an ordered `tasks` list. Each task expands to four skills, and
the next task starts only after the previous placement succeeds. The client
does not reload the scene between subtasks. The object-to-zone pairs and their
order from the user instruction are authoritative: the client replaces any
LLM routine that changes, adds, or reorders a pair with a deterministic routine
containing only the requested assignments. A safe maximum of eight subtasks is
enforced; if any subtask fails, execution stops immediately and the scene is
preserved for inspection. Reload the scene before starting a new trial.

Stepwise mode remains the one-subtask-per-turn comparison condition. For a
multi-task instruction, use routine mode so the complete ordered task list is
planned first; this keeps the multi-task execution comparable and ensures
each subtask is completed before the next one begins.

The controller also rechecks and corrects the pickup approach if the cube has
shifted slightly before the proximity check. After a validated release, the
controller returns the IK target to its startup pose through the normal IK
motion path; it does not directly overwrite joint angles.

By default this uses `qwen3:4b` on your own computer. No OpenAI API key or API
credits are needed. A local model still needs your computer's memory,
processing power and electricity; it may respond slowly on a laptop without a
capable GPU.

## What is checked

The outside program only accepts the exact supported action and object names.
The scene script checks reachability, sensor detection, attachment state, and
the final cube position before release. It reports `ready`, `busy`, `done`, or
`error` through a CoppeliaSim string property. A `done` report means the
script completed the current skill's checks, not that all possible collisions
along the path were verified. In routine mode, each such `done` report is the
intermediate result for the next routine step.

The scene's IK and `moveToPose` remain responsible for arm motion. This
prototype does not implement continuous collision checking, a general
waypoint planner, or visual recognition. Those require separate simulation
checks before adding arbitrary new targets or locations. CoppeliaSim currently
labels string properties as deprecated; they are used here as a small
compatibility bridge with the existing 4.10 scene.
