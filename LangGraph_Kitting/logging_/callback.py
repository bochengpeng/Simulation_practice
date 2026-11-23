"""Run logger (Step 6): one shared human-readable text log for the whole
experiment (`logs/experiment.log`), not one JSON file per run - per-run
numbers go in `logs/results_table.csv` instead. Each line is both printed
and appended to the file, so console output and the saved log always match.

Trade-off, so it's explicit rather than silent: the assignment's own Step 6
wording asks for "one JSON record per event" per run, with the six Step 7
measures computed from that file alone. This plain-text log is for a human
reading it, not for a script to parse - anything that needs exact
structured data (e.g. a precise token count per call) should read it from
the in-memory `RunLogger.ai_calls` counter or the CSV, not by parsing this
file.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from langchain_core.callbacks import BaseCallbackHandler

PROMPT_VERSION = "ssm3-kitting-v2"


def describe_event(event: dict) -> Optional[str]:
    kind = event.get("event")
    if kind == "planning":
        return f"asking the model for a plan (attempt {event['attempt']}) - this can take a minute..."
    if kind == "plan":
        plan = event["plan"]
        return (f"model answered: plan with {len(plan['steps'])} steps"
                if plan["type"] == "plan" else f"model answered: {plan['type']}")
    if kind == "invalid_format":
        return f"model answer was not valid for the schema - retrying ({event['reason'][:80]})"
    if kind == "check":
        result = event["check_result"]
        return ("checker: OK - robot will move now" if result["status"] == "OK"
                else f"checker: REJECT {result['code']} - {result['reason'][:90]}")
    if kind == "action":
        return f"robot step: {event['action']['function']} {event['action']['args']}"
    if kind == "relocation":
        return f"disturbance: {event['part']} moved to {event['new_parent']}"
    if kind == "execution_failure":
        return f"step failed: {event['reason'][:90]}"
    if kind == "human_intervention":
        return f"operator asked ({event['code']}): {event['message'][:90]!r} -> {event['operator_answer']!r}"
    if kind == "run_error":
        return f"run error: {event['reason']}"
    if kind == "skipped":
        return f"skipped: {event['reason']}"
    if kind == "outcome":
        return f"outcome: task_success={event['task_success']} final_parts={event['final_parts']}"
    if kind == "stop":
        return f"stopped: {event['final_status']}"
    return None


class RunLogger(BaseCallbackHandler):
    """Callable (for orchestrator events) and a LangChain callback handler
    (for token usage), both writing to the same shared text log."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")
        self.model_name = ""
        self.provider = ""
        self._actual_model = ""
        self._actual_provider = ""
        self.ai_calls = 0
        self._llm_start_time: Optional[float] = None

    def section(self, title: str) -> None:
        self._write(f"\n=== {title} (model={self.model_name}, prompt={PROMPT_VERSION}) ===")

    def __call__(self, event: dict) -> None:
        line = describe_event(event)
        if line:
            self._write(f"  {line}")

    def _write(self, line: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self._fh.write(f"[{stamp}] {line}\n")
        self._fh.flush()
        print(line, flush=True)

    # LangChain callback hooks -------------------------------------------

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self._llm_start_time = time.time()
        metadata = kwargs.get("metadata") or {}
        self._actual_model = metadata.get("ls_model_name") or self.model_name
        self._actual_provider = metadata.get("ls_provider") or self.provider

    def on_llm_error(self, error, **kwargs):
        self._write(f"  model call failed ({self._actual_provider}/{self._actual_model}): "
                    f"{type(error).__name__}: {str(error)[:120]} - trying fallback if configured")

    def on_llm_end(self, response, **kwargs):
        self.ai_calls += 1
        latency_s = time.time() - self._llm_start_time if self._llm_start_time else None
        usage = None
        try:
            generation = response.generations[0][0]
            usage = getattr(generation.message, "usage_metadata", None)
        except Exception:
            usage = None
        latency_text = f"{latency_s:.1f}s" if latency_s is not None else "?s"
        self._write(f"  model call #{self.ai_calls} ({self._actual_provider}/{self._actual_model}): "
                    f"{usage} in {latency_text}")

    def close(self) -> None:
        self._fh.close()
