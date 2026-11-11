# Reflection (DRAFT - personalize before it goes in the portfolio)

This is seeded from what was actually built in this session so the
structure and the concrete facts are real; the module book wants
student-authored reflection, so read each answer and rewrite it in your
own words/judgement before it goes into the portfolio. Delete this line
once you have.

## 1. Which project-related knowledge or skills did you need to develop?

- LangChain's `with_structured_output` + a Pydantic discriminated union, to
  force a model to answer with exactly one of a plan/question/refusal
  instead of free text.
- LangGraph's `StateGraph`: conditional edges, a bounded retry counter
  carried in graph state, and the difference between `.invoke()` (run to
  completion) and `.stream()` (step through node-by-node - needed to inject
  a mid-run failure at the right point for T6).
- The RoboDK Python API: building geometry from scratch (`AddFile` of a
  generated STL, `AddTarget`, `setParentStatic` for "this part is now held
  by the tool/sitting in this slot" instead of tracking location in a
  separate data structure).
- Ollama's "thinking" models (`qwen3:4b`) wrap JSON answers in reasoning
  text by default; `ChatOllama(reasoning=False)` turns that off.

## 2. Why did you select LangChain and LangGraph?

[Fill in from the assignment's own rationale and your own view: LangChain
replaces only the prompt/model-call/parser plumbing, LangGraph replaces
the orchestrator loop, and both leave the checker/cell as plain Python -
match this against what you actually found easier or harder once it was
running.]

## 3. What did you actually learn?

A concrete, reproducible one from this session: **the Pydantic schema and
the checker are not enough on their own to guarantee the plan does
anything.** A live run against `qwen3:4b` produced a one-step plan
(`read_cell_state` only) that is perfectly valid - schema-valid and
checker-valid - and "succeeded" without moving a single part. This is why
Step 7's task-success measure is computed separately from checker
validity (see `experiments/success_criteria.py`): a run can be 100% plan-
valid and 0% task-successful. [Add your own second and third points - e.g.
what the checker vs. schema rejection log from Step 4 actually looked
like, once you have real T2/T3 runs to look at.]

## 4. Where did you apply it in the project?

[Point to the branch (`langgraph_planner`), the station file, and how this
plugs into the Design Document's architecture without changing it - the
cell/checker/function-library interfaces are unchanged from the design,
only their LangChain/LangGraph implementation is new.]

## 5. How did the literature you studied influence the design of your planner or loop?

[Fill in - this needs your actual course/literature references.]

## 6. Did your learning needs change as the project developed?

[Fill in - e.g. did building the RoboDK station first reveal LangChain
needs you didn't anticipate, or vice versa?]

## 7. What would you need to learn next if the project continued?

- A real goal-completion check or a smarter checker, since the current
  placeholder only checks safety/structure (see finding in #3).
- LangGraph's `interrupt()` for a real operator console, instead of the
  terminal/scripted-answer stand-in here.
- Whatever the team's actual checker/function-library/baseline turn out to
  need once they exist, to replace the placeholders in `checker/`.

## Ethics point (AI risk, Section 7 ACM candidate)

T2 ("Put a spacer in slot 3") is the designed ambiguity test: two parts
(`SPACER_SHORT`, `SPACER_LONG`) match "a spacer," and the correct behaviour
is to ask the operator rather than guess. The assignment notes that most
models tested by the team guessed instead of asking - record what
`qwen3:4b` and the Groq model actually do here once you have the logs; a
model that silently picks one part is the concrete AI-risk instance to
write up.
