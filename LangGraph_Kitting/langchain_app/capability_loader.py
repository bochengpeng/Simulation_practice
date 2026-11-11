"""Loads config/capability.yaml - the single source of truth for names,
rules and limits (NFR2). Both the prompt builder and the checker import
this, so a name is never typed twice.
"""

from __future__ import annotations

from pathlib import Path

import yaml

CAPABILITY_PATH = Path(__file__).resolve().parent.parent / "config" / "capability.yaml"


def load_capability(path: Path = CAPABILITY_PATH) -> dict:
    with Path(path).open("r", encoding="utf-8") as stream:
        return yaml.safe_load(stream)
