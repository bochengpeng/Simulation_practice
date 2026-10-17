# LLM-controlled pick-and-place prototype

This version uses two Python programs:

- `robot_controller.py` runs inside `/Robot_Arm/script`. It waits for a command and controls the arm through the existing IK, sensor, and suction routines.
- `llm_robot_client.py` runs in a separate Windows terminal. It asks the LLM for one structured action, checks the action, sends it to CoppeliaSim, and reports the result.

The scene exposes four bounded skills for a selected task:
`move(Object_X)`, `pick(Object_X)`, `move(Zone_X)`, and `place(Zone_X)`.
The source can be `Object_A`, `Object_B`, or `Object_C`; the destination can
be `Zone_A`, `Zone_B`, `Zone_C`, or `Zone_D`. The legacy
`pick_and_place(source, destination)` command remains available as a
communication-link demo.

The scene file now also contains the reusable experiment fixtures from the
SSM3 plan: `Object_A` (renamed from `Part_A`), `Object_B` (blue),
`Object_C` (cylinder), `Zone_A`, `Zone_B`, `Zone_C`, `Zone_D` (renamed from
`PlaceZone`), `Obstacle_Test`, and `Home_Position`. `Obstacle_Test` is a static respondable obstacle;
disable or remove it for non-obstacle trials and restore it for the obstacle
condition. The controller and client accept every object-to-zone combination
in this four-by-four task space. The LLM selects the source and destination
first, then emits or receives the ordered skills for that selected pair.

The initial scene layout is deliberately separated for repeatable trials:
`Object_A`, `Object_B`, and `Object_C` are separated within the arm's
reachable workspace. `Zone_D` is 0.45 m square, and `Zone_A`, `Zone_B`, and
`Zone_C` are 0.42 m square; the zones are arranged in a reachable row.
Reload the saved scene before each
trial so a previous placement does not become the next trial's starting state.

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

Reload the scene (or reset the selected object and the robot pose) before every trial;
stopping and starting alone may preserve a moved object in the current scene.
Each run appends `experiment_results.csv` with mode, plan validity, LLM calls,
executed skills, success, elapsed time, recovery/human-intervention fields,
and any error. Use `--dry-run` to measure plan validity without moving the
arm. Stepwise mode rejects out-of-order skills and performs the next required
bounded skill as a recovery action, recording it in `recovery_attempts`. The
controller also rechecks and corrects the pickup approach if the cube has
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
script completed these checks, not that all possible collisions along the
path were verified.

The scene's IK and `moveToPose` remain responsible for arm motion. This
prototype does not implement continuous collision checking, a general
waypoint planner, or visual recognition. Those require separate simulation
checks before adding arbitrary new targets or locations. CoppeliaSim currently
labels string properties as deprecated; they are used here as a small
compatibility bridge with the existing 4.10 scene.
