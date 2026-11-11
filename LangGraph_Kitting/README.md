# SSM3 kitting planner (LangChain + LangGraph)

First-version prototype for the "LangChain assignment for SSM3": order-based
kitting with kit correction, built on a RoboDK station, following the
architecture in `LangChain assignment for SSM3.md`.

## Status

Full pipeline (Steps 0-7) is code-complete and exercised live against a
running RoboDK instance and Ollama (`qwen3:4b` - `qwen3:8b` isn't pulled
locally; swap the model name in `config/capability.yaml` once it is). No
`GROQ_API_KEY` is configured in this environment, so Groq/GPT-OSS-120B runs
are wired but will log as `SKIPPED` until a key is added to `.env`.

**Placeholders, not the team's real components** (none exist anywhere in
this repository or its branches yet - see `checker/checker.py` and
`checker/baseline.py` docstrings): the checker (Task B), the function
library wrapper and the baseline (Task C). Swap them in later; nothing
else should need to change, since they're imported behind the same plain
interfaces the real ones would have.

**Assumptions flagged in code**, to reconcile once the team's Design
Document/Table 10/Table 11/capability file exist: the graph's edges
(`orchestrator/graph.py`), the log schema (`logging_/callback.py`), which
slot is protected (`SLOT_1`, from the assignment's own starter-order start
state), and what the "five functions" are (`move_to/pick/place/locate` plus
a `read_cell_state` query).

## Setup

```powershell
cd LangGraph_Kitting
pip install -r requirements.txt
copy .env.example .env   # fill in GROQ_API_KEY if you have one
```

RoboDK and Ollama must already be running (`ollama pull qwen3:4b` first if
needed).

## Running each step

```powershell
# Step 0: build the kitting station (RoboDK must be open)
python station/build_kitting_station.py
python station/check_capability_consistency.py

# Steps 1-4: a single planner call
python -c "from langchain_app.capability_loader import load_capability; from langchain_app.planner_chain import build_planner_chain, invoke_planner; c=load_capability(); chain=build_planner_chain(c,'ollama'); print(invoke_planner(chain,c,'Replace the short spacer in slot 2 with a long one', {'parts':{'BOLT':'SLOT_1','SPACER_SHORT':'SLOT_2','SPACER_LONG':'BIN_3'},'last_target':'HOME','tool_holds':None}))"

# Step 5-6: one full order through the orchestrator (writes no log file by
# default - use experiments/run_experiment.py for logged runs)

# Step 7: the full mini experiment (T1-T6 x {ollama, groq} x {open, closed})
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
- `logging_/` - Step 6: the JSONL logging callback.
- `experiments/` - Step 7: starter orders, the mini-experiment runner, the
  task-success oracle.
- `tests/` - run without RoboDK or a live model (`PythonCell` + a fake
  chain), except the station-consistency check, which needs RoboDK open.
- `docs/` - reflection, results table template, evidence checklist.
