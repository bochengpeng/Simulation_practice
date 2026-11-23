# SSM3 kitting planner (LangChain + LangGraph)

First-version prototype for the "LangChain assignment for SSM3": order-based
kitting with kit correction, built on a RoboDK station, following the
architecture in `LangChain assignment for SSM3.md`.

## Status

Full pipeline (Steps 0-7) is code-complete and exercised live against a
running RoboDK instance and Ollama (`qwen3:4b` - `qwen3:8b` isn't pulled
locally; swap the model name in `config/capability.yaml` once it is).
Gemini (`gemini-3.5-flash-lite`) is the primary model and needs
`GEMINI_API_KEY` in `.env`; Ollama is its automatic fallback. If Gemini
fails (no key, quota, network error, invalid answer) the same call is
retried on Ollama, and `logs/experiment.log` shows which model answered.
The `ollama` provider runs Ollama alone (no Gemini) for comparison.

**Placeholders, not the team's real components** (none exist anywhere in
this repository or its branches yet - see `checker/checker.py` and
`checker/baseline.py` docstrings): the checker (Task B), the function
library wrapper and the baseline (Task C). Swap them in later; nothing
else should need to change, since they're imported behind the same plain
interfaces the real ones would have.

**Assumptions flagged in code**, to reconcile once the team's Design
Document/Table 10/capability file exist: the graph's edges
(`orchestrator/graph.py`), which slot is protected (`SLOT_1`, from the
assignment's own starter-order start state), and what the "five functions"
are (`move_to/pick/place/locate` plus a `read_cell_state` query).

**Logging departs from the assignment's Step 6 wording on purpose** (by
request): instead of one JSON file per run with one record per event,
`logging_/callback.py` writes one shared human-readable text log
(`logs/experiment.log`) for the whole experiment, plus one row per run in
`logs/results_table.csv`. This is easier to read but loses the
assignment's "compute the six measures from the log alone" property - the
per-run numbers (retries, ai_calls, task_success, elapsed_s) come from the
CSV, not from parsing the text log.

## Setup

```powershell
cd LangGraph_Kitting
pip install -r requirements.txt
copy .env.example .env   # then put your GEMINI_API_KEY in .env
```

RoboDK and Ollama must already be running (`ollama pull qwen3:4b` first if
needed).

## GUI: set up and run the experiment

The easiest way to run things: double-click `run_interface.bat` (or
`python -m experiments.experiment_interface` from `LangGraph_Kitting/`).
RoboDK and Ollama must already be running. The window lets you:

- Tick which starter orders (T1-T6), providers (ollama/gemini) and loop
  modes (open/closed) to run, and pick/refresh the installed Ollama model.
- **Build station** / **Check station** - Step 0's builder and consistency
  check.
- **Run selected** - runs just the ticked combination through
  `experiments/cli.py` instead of always running the full 24-run matrix.
- **Open RoboDK station** / **Open logs folder** for the evidence files.

Detailed per-run output (plan/check/action events, final status) prints to
whatever terminal/console launched the GUI - the window itself only shows
start/finish, so it never freezes while a model call is in flight.

The same options are available without the GUI via the CLI it wraps:

```powershell
python -m experiments.cli build-station
python -m experiments.cli check-station
python -m experiments.cli run --orders T1 T6 --providers ollama --loop-modes open closed --ollama-model qwen3:4b
```

## Running each step

```powershell
# Step 0: build the kitting station (RoboDK must be open)
python station/build_kitting_station.py
python station/check_capability_consistency.py

# Steps 1-4: a single planner call
python -c "from langchain_app.capability_loader import load_capability; from langchain_app.planner_chain import build_planner_chain, invoke_planner; c=load_capability(); chain=build_planner_chain(c,'ollama'); print(invoke_planner(chain,c,'Replace the short spacer in slot 2 with a long one', {'parts':{'BOLT':'SLOT_1','SPACER_SHORT':'SLOT_2','SPACER_LONG':'BIN_3'},'last_target':'HOME','tool_holds':None}))"

# Step 5-6: one full order through the orchestrator (pass a
# logging_.callback.RunLogger as both `logger=` and `callback_handler=`
# to orchestrator.graph.build_graph for a logged run)

# Step 7: the full mini experiment (T1-T6 x {ollama, gemini} x {open, closed})
python -m experiments.run_experiment

# Tests (no RoboDK or live model needed)
python -m pytest tests/ -v
```

## Layout

See `config/capability.yaml` for the single source of truth (positions,
parts, functions, rules, model settings). Everything else - the prompt,
the checker, the station-consistency check - reads from it (NFR2).

- `station/` - Step 0: the RoboDK station builder, the `RoboDKCell`/
  `PythonCell` adapters, the consistency check.
- `langchain_app/` - Steps 1-3: prompt builder, chat model factory,
  structured-output schema, the planner chain.
- `checker/` - Step 4 (placeholder checker + baseline).
- `orchestrator/` - Step 5: the LangGraph `StateGraph`.
- `logging_/` - Step 6: the shared text-log callback (`RunLogger`).
- `experiments/` - Step 7: starter orders, the mini-experiment runner, the
  task-success oracle, the CLI (`cli.py`) and the GUI (`experiment_interface.py`).
- `tests/` - run without RoboDK or a live model (`PythonCell` + a fake
  chain), except the station-consistency check, which needs RoboDK open.
- `docs/` - reflection, results table template, evidence checklist.
