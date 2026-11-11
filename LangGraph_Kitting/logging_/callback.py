"""Logging callback (Step 6): one JSON record per event.

NOTE: the Design Document's Table 11 (the canonical log field list) does
not exist anywhere in this repository yet. The fields below (model,
prompt version, plan, check result, step result, timestamps, token use)
are an assumption to reconcile once the real Table 11 exists. One run
gives one log file (JSONL - one line per record), and the measures
`run_experiment.py` reports (task success, plan validity, blocked unsafe
actions, human interventions, recovery, latency) can all be computed from
it without reading anything else.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler

PROMPT_VERSION = "ssm3-kitting-v1"


class JsonlLogger(BaseCallbackHandler):
    def __init__(self, path, model_name: str, provider: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.model_name = model_name
        self.provider = provider
        self._fh = self.path.open("a", encoding="utf-8")
        self._llm_start_time: float | None = None

    def __call__(self, event: dict) -> None:
        self._write(event)

    def _write(self, record: dict) -> None:
        record.setdefault("timestamp", time.time())
        record.setdefault("model", self.model_name)
        record.setdefault("provider", self.provider)
        record.setdefault("prompt_version", PROMPT_VERSION)
        self._fh.write(json.dumps(record, default=str) + "\n")
        self._fh.flush()

    # LangChain callback hooks -------------------------------------------

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self._llm_start_time = time.time()

    def on_llm_end(self, response, **kwargs):
        latency_s = (
            time.time() - self._llm_start_time if self._llm_start_time else None
        )
        usage = None
        try:
            generation = response.generations[0][0]
            usage = getattr(generation.message, "usage_metadata", None)
        except Exception:
            usage = None
        self._write({
            "event": "model_usage",
            "usage": usage,
            "latency_s": latency_s,
        })

    def close(self) -> None:
        self._fh.close()
