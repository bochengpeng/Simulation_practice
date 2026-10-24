# Adaptive RoboDK experiment

This layer is separate from the original `run_robodk_pick_place.py` runner and never modifies the source station. It copies the station to `Doosan_M0609_SSM3_Experiment.rdk`, uses simulation mode only, and serializes all adapter calls.

## Commands

For a simple graphical interface, double-click:

```text
start_experiment_interface.bat
```

The interface lets you select Parts A/B/C, choose deterministic or Ollama
planning, choose an installed Ollama model, automatic or manual disturbances,
select a scenario and trial count, then use buttons for robot diagnostics,
reset, selected-part runs, and
full experiments. It also opens the RoboDK station and log folder. Detailed
experiment logs remain in the terminal that launched the interface; the GUI
only shows operation start and completion status.

```powershell
python main.py doctor
python main.py reset
python main.py run --parts A B C --mode deterministic --disturbance-mode automatic
python main.py experiment --parts A B C --mode deterministic --trials 10
python main.py experiment --scenario sequential-relocation --parts A B C --mode deterministic
python main.py experiment --scenario missing-b --parts A B C --mode deterministic
python main.py experiment --scenario unreachable-c --parts A B C --mode deterministic
python main.py experiment --scenario no-change --parts A B C --mode deterministic
```

Use `--disturbance-mode manual` to stop at each Clearance gate and move only the named pending part before pressing Enter. `--parts B C` and `--parts C` are supported; destinations remain Slot_2/Slot_3 and Slot_3 respectively. `--mode llm` asks a local Ollama model for the plan through Ollama's HTTP API (`OLLAMA_HOST`, default `http://localhost:11434`). Choose an installed model in the interface before each run (the Refresh button re-reads `ollama list`), or pass `--model qwen3:4b` on the CLI; with neither, `OLLAMA_MODEL` or else the first installed model is used. `OLLAMA_TIMEOUT_S` sets the planning timeout (default 180 s; a local model may need a minute or more, including loading it). `--replanning-mode hybrid-fast` (the default) calls the model once for the run-level strategy, then immediately rebuilds validated local plans for later parts and relocation feedback; `--replanning-mode full-llm` calls the model again for every planning/replanning event. Hybrid-fast is recommended when reaction speed and reliability matter. Install Ollama and pull a model first (for example `ollama pull qwen3:4b`). Ollama constrains the reply to the plan's JSON schema and thinking is switched off for thinking models. To keep calls short the model plans only the current part (only that part's actions run anyway), writes each action as a compact `[skill, arg, ...]` array that the planner expands, and is not asked to copy `task_id` or the snapshot id: small models garble random ids (`scene-9c0:211e` for `scene-9c0a211e`), which made every plan fail as a stale snapshot. Both ids are filled in from the snapshot the prompt was built from, so a scene that changes while the model is thinking is still reported as stale. The model is loaded in the background when a run starts. The plan is then checked by the same strict validator, and no model-generated code is executed. If Ollama is slow, not running, or has no usable model, the experiment reports a continuation status and immediately uses the validated local safe plan; later parts do not repeat the failed request.

The local safe plan fallback is optional. In the interface, untick **Allow local safe plan** (CLI: `--no-allow-local-safe-plan`; default is on) to make an Ollama timeout or unusable response fail the trial instead: the robot does not move, the trial is recorded with failure code `LLM_TIMEOUT` (timed out) or `LLM_UNAVAILABLE` (any other Ollama failure, such as Ollama not running or the model not installed), and any remaining trials still run. This only governs the fallback after an Ollama failure; the deliberate local replans of `hybrid-fast` after a successful first call are unchanged. Every row in `logs/trial_summaries.csv` records the setting in the `allow_local_safe_plan` column (`on`/`off`). Rows written before the column existed are kept and left blank in it.

Logs are written as JSONL and CSV files under `logs`. Each trial also appends
one stable summary row to `logs/trial_summaries.csv`, including success/failure,
failure feedback, completed parts, action count, replan count, selected model,
AI call count, AI response time, and total elapsed time. The ordered action
sequence and plan feedback remain in the per-task JSONL log. The adapter exposes only
`inspect`, `move_to(Home|Clearance)`, `pick`, and `place`, and the validator
rejects unknown identities, stale snapshots, wrong order, occupied slots, and
invalid dispositions. Preflight and execution remain simulation-only. The
original station and runner are unchanged.

If a previous run left RoboDK or Python running, close only the processes
started by this experiment and rerun `python main.py reset`. Current CLI and
GUI runs disconnect their API connection cleanly and suppress inherited
RoboDK output handles, so successful commands return to the prompt normally.

Adaptive runs apply simulation-only motion settings when connecting to RoboDK:
20x simulation speed, 250 mm/s linear speed, and 45 deg/s joint speed. These
settings reduce waiting during robot execution and are not saved back into the
station file. Deterministic planning is still the fastest option; LLM planning
can add model-response time before the first motion.

The automatic relocation fixture is applied only after a candidate plan has
been created. On every relocation gate it samples a new random XY position
inside the free band between the zone rows, keeps a separation margin from
the other parts and from every zone footprint, and accepts the position only
when RoboDK `SolveIK` confirms the pick pose is reachable and a dry-run
(`MoveJ_Test` to the safe approach, then `MoveL_Test` down to the pick) shows
the approach and descent are collision-free. The Z height remains 50 mm. Automatic relocation is
a simulator-side pose change and does not wait for an extra robot move to
Clearance; this keeps disturbance injection quick. Manual relocation still
moves the robot to Clearance before prompting the operator. The selected
random pose is printed in the terminal and stored in the relocation JSONL
event. The event IDs are consumed once per run. A sequential A/B/C run therefore reports three
replanning events; a no-change run reports zero. `missing-b` preserves any
completed earlier part and stops before attempting B, while `unreachable-c`
preserves A/B and reports an unreachable preflight failure.

The RoboDK API is optional for unit tests. Run `python -m pytest -q` for the fake-adapter tests. `doctor` reports missing RoboDK connectivity or license/runtime limitations instead of silently falling back to real motion.

## Table-collision check and local recovery

Robot-vs-table collision checking was previously disabled everywhere in
this station (RoboDK's real-time `MoveJ`/`MoveL` never stop a simulated
motion by themselves on collision; only the `*_Test` variants predict one
before dispatch). It is now enabled for the robot arm's own links against
the table and the three placement zones -- link id 0 (the base, mounted
into the table) stays exempt, since that overlap is expected geometry, not
a crash; see `TABLE_COLLISION_EXEMPT_LINK_IDS` in `run_robodk_pick_place.py`
if your station has a different false positive on a specific link. The
gripper/TCP items and the deliberately contact-making Place/PlaceApproach
moves are unchanged.

Before every transit move, the candidate segment is tested
(`MoveJ_Test`/`MoveL_Test`) rather than driven blind:

- If it is clear, it is dispatched exactly as before -- no added latency.
- If a joint transit (approach/retract/home) would hit the table, the
  robot locally adjusts its path: lift straight up from where it is,
  translate at that height to above the target, then descend -- retried
  at progressively higher clearance (`TABLE_COLLISION_CLEARANCE_STEPS_MM`,
  default 60/140/240 mm). Once a detour tests clear it is dispatched for
  real and execution simply continues the same already-approved plan; no
  new LLM call is needed for this.
  The detour never goes below the current height, and what was hit is read
  while RoboDK still has the robot parked at the collision point (before it
  is restored), so the colliding item pair is named in the error.
- A vertical pick descent has no safe "go around" that preserves the
  taught approach. If it is blocked, the arm re-approaches the same XY with
  the tool turned 90/-90/180 degrees (`PICK_YAW_ALTERNATIVES_DEG`; the block
  is square) and only dispatches a path that tests clear.
- If every clearance step or alternate angle still collides (or the
  collision is with something other than the table), execution raises a
  `TABLE_COLLISION` failure naming the colliding items instead of crashing
  the process, after first lifting the arm back to the safe transfer height
  (`retreat_to_safe_height`). `Experiment` catches this the
  same way it already handles a relocation gate: it re-observes the actual
  scene (including a part that is now held mid-cycle, which is resumed
  with a place only -- never a second pick on an already-grasped object)
  and requests a fresh plan for the same remaining work, bounded by the
  existing replan budget (`--trials`/`max_replans`). If the same
  `TABLE_COLLISION` repeats `MAX_IDENTICAL_COLLISIONS` (2) times in a row on
  the same part, the run stops with that failure instead of spending every
  replan on an identical, unchanged scene.

Logged events: `collision_avoidance` (an adjustment was used, with the
clearance-attempt count) and `execution_failure` (code, reason, the action
that failed, and which part) appear in the per-task JSONL log alongside
`plan`, `action` and `relocation`.

Unit-tested without RoboDK in `tests/test_collision_guard.py` (the
retry/escalation policy, via a plain fake) and
`tests/test_resume_held_part.py` (resuming a held part without a second
pick). The RoboDK-specific wiring in `run_robodk_pick_place.py`
(`RoboDKMover`, the re-enabled collision pairs, the actual motion dispatch)
could only be checked for syntax/import correctness here -- verify on your
station with `python main.py doctor`, then a normal run with collisions
shown in RoboDK's view, before relying on it.

Validated locally:

- `python -m pytest -q` (run from the parent directory of this folder, as
  the tests import `RoboDK_Experiment.robodk_adaptive`): 29 passed after the
  relocation/collision-recovery changes. The RoboDK-side behavior of the
  dry-run relocation probe and the alternate-yaw descent has not been
  verified in a live station yet.
- `python main.py doctor`: simulation mode, required robot/frame/tool/capture
  identities, and motion-validation API reported available.
- Deterministic no-change A/B/C: completed successfully with zero replans.
- Deterministic sequential-relocation A/B/C: completed successfully with
  three replans.
- Single-part C subset: completed successfully with zero replans.
- Missing-B and unreachable-C scenarios stopped with `success=False` while
  preserving earlier progress.
- Ollama 0.40.10 with `qwen3:4b`: two live `full-llm` sequential-relocation A/B/C trials
  completed successfully (6 model calls each, 46-52 s of model time, 80-89 s
  in total; before the speed changes the same run took 312 s), and with
  Ollama unreachable a trial fails in about 2 s with the local safe plan off
  and continues on it when on.
- LLM no-change Part A completed successfully with zero replans.
- LLM sequential-relocation Part A completed successfully with one feedback
  replan (`OBJECT_MOVED`).
- Hybrid-fast replanning uses the fresh scene snapshot and deterministic
  allowlisted compiler immediately after `OBJECT_MOVED`, avoiding a second
  LLM request.
