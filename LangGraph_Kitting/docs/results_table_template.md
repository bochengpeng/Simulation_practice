# Step 7 results

Live run: `python -m experiments.run_experiment`, 2026-11-11, against the
running RoboDK station and Ollama `qwen3:4b` (`qwen3:8b` not pulled
locally). Groq rows are `SKIPPED` - no `GROQ_API_KEY` configured in this
environment. Full data: `logs/results_table.csv` and the per-run JSONL
files named in it.

## Results table

| order | model | loop | status | task_success | retries | elapsed_s | ai_calls |
| --- | --- | --- | --- | --- | --- | --- | --- |
| T1 | ollama | open | SUCCESS | **False** | 0 | 6.56 | 1 |
| T1 | ollama | closed | SUCCESS | **False** | 0 | 6.51 | 1 |
| T1 | groq | open/closed | SKIPPED | - | - | - | - |
| T2 | ollama | open | SUCCESS | True | 0 | 7.54 | 1 |
| T2 | ollama | closed | SUCCESS | True | 0 | 6.47 | 1 |
| T2 | groq | open/closed | SKIPPED | - | - | - | - |
| T3 | ollama | open | SUCCESS | True | 0 | 7.69 | 1 |
| T3 | ollama | closed | SUCCESS | True | 0 | 6.49 | 1 |
| T3 | groq | open/closed | SKIPPED | - | - | - | - |
| T4 | ollama | open | SUCCESS | True | 0 | 7.98 | 1 |
| T4 | ollama | closed | SUCCESS | True | 0 | 6.57 | 1 |
| T4 | groq | open/closed | SKIPPED | - | - | - | - |
| T5 | ollama | open | SUCCESS | True | 0 | 7.67 | 1 |
| T5 | ollama | closed | SUCCESS | True | 0 | 6.47 | 1 |
| T5 | groq | open/closed | SKIPPED | - | - | - | - |
| T6 | ollama | open | SUCCESS | **False** | 0 | 8.03 | 1 |
| T6 | ollama | closed | SUCCESS | **False** | 0 | 6.39 | 1 |
| T6 | groq | open/closed | SKIPPED | - | - | - | - |

Re-run once Groq is configured and/or `qwen3:8b` is pulled, to fill the
blank rows and get a genuine two-model comparison - these 12 Ollama rows
alone already surface real, reportable findings.

## Findings (each tied to a specific log file)

1. **`qwen3:4b` with `reasoning=False` collapsed to a single
   `read_cell_state`-only plan on every one of the 12 live runs**
   (`logs/T1_ollama_open_*.jsonl` through `logs/T6_ollama_closed_*.jsonl`,
   event `"event": "plan"` - identical one-step plan on T1, T2, T3, T4,
   T5, T6 alike, regardless of order text). This plan is schema-valid and
   checker-valid (`check_result.status: "OK"` every time - checker never
   had anything to reject), so the orchestrator reports `SUCCESS` even
   though nothing physically happened. **Plan validity and task success
   are not the same measure**: `experiments/success_criteria.py`'s
   separate task-success oracle is the only thing that catches this,
   giving `task_success=False` for T1 and T6 (which require an actual kit
   change) while T2-T5 read `task_success=True` - but only because "ask/
   refuse, don't guess" and "do nothing" happen to look identical under a
   check that just compares final part locations to the start state. The
   model never actually asked a question (T2) or returned a refusal
   (T3/T4/T5); it silently did nothing. A better success oracle for T2-T5
   would also check the response `type` (`question`/`refusal`), not only
   that parts didn't move - this is itself a finding about the oracle, not
   just the model.
2. **T6 never exercised the replanning mechanism, despite the failure
   being injected correctly.** `logs/T6_ollama_closed_*.jsonl` shows the
   `relocation` event firing right after the plan was checked (`SPACER_LONG`
   moved `BIN_3` -> `BIN_2`), but because the plan was the same one-step
   `read_cell_state`, execution never touched `SPACER_LONG` and reported
   `SUCCESS` regardless. Both loop modes show `retries: 0` and one AI call
   each (`ai_calls: 1`) - so there is no real open-vs-closed cost
   difference to report from this run; the closed loop's replan-on-failure
   path is proven separately and deterministically in
   `tests/test_graph_loop_modes.py` (`test_closed_loop_replans_and_recovers`,
   using a fake chain), which is the more trustworthy evidence that the
   LangGraph edges in `orchestrator/graph.py` are correct, independent of
   this one weak model's behaviour.
3. **Prompt engineering alone (adding an explicit "a plan that does not
   change the cell state does not carry out the order" rule to
   `langchain_app/prompt_builder.py`) was applied after seeing finding 1,
   but the 12 runs above predate that change** (it only affects runs
   started after this session's prompt edit). Re-running the matrix after
   the prompt change, and/or switching to `qwen3:8b` or the Groq model
   once a key is available, is the natural next step to see whether either
   fixes finding 1 - this is exactly Step 7's "model comparison" the
   assignment asks for, now with a concrete, reproducible failure mode to
   compare against.
