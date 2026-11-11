"""PythonCell: a plain-Python mirror of RoboDKCell for fast unit tests that
don't need a running RoboDK station (NFR5). Same interface (cell_protocol.Cell),
same start state and the same five functions.
"""

from __future__ import annotations

from pathlib import Path

import yaml

CAPABILITY_PATH = Path(__file__).resolve().parent.parent / "config" / "capability.yaml"

TOOL_NAME = "TOOL"

ALLOWED_FUNCTIONS = ("move_to", "pick", "place", "locate", "read_cell_state")


class CellExecutionError(RuntimeError):
    pass


class PythonCell:
    def __init__(self, capability: dict | None = None):
        if capability is None:
            with CAPABILITY_PATH.open("r", encoding="utf-8") as stream:
                capability = yaml.safe_load(stream)
        self.capability = capability
        self._positions = set(capability["positions"])
        self._parts: dict[str, str] = {}
        self._last_target: str | None = None
        self.reset()

    def reset(self) -> None:
        self._last_target = "HOME"
        self._parts = {
            "BOLT": "SLOT_1",
            "SPACER_SHORT": "SLOT_2",
            "SPACER_LONG": "BIN_3",
        }

    def _require_position(self, name: str) -> None:
        if name not in self._positions:
            raise CellExecutionError(f"Unknown position: {name!r}")

    def _require_part(self, name: str) -> None:
        if name not in self._parts:
            raise CellExecutionError(f"Unknown part: {name!r}")

    def move_to(self, position: str) -> None:
        self._require_position(position)
        self._last_target = position

    def pick(self, part: str, source: str) -> None:
        self._require_part(part)
        self._require_position(source)
        current = self._parts[part]
        if current != source:
            raise CellExecutionError(
                f"pick({part!r}, {source!r}) but {part} is at {current!r}"
            )
        self.move_to(source)
        self._parts[part] = TOOL_NAME

    def place(self, part: str, slot: str) -> None:
        self._require_part(part)
        self._require_position(slot)
        if self._parts[part] != TOOL_NAME:
            raise CellExecutionError(f"place({part!r}, ...) but the gripper is empty")
        self.move_to(slot)
        self._parts[part] = slot

    def locate(self, part: str) -> str:
        self._require_part(part)
        return self._parts[part]

    def read_cell_state(self) -> dict:
        return self.read_state()

    def read_state(self) -> dict:
        tool_holds = next(
            (part for part, parent in self._parts.items() if parent == TOOL_NAME),
            None,
        )
        return {
            "parts": dict(self._parts),
            "last_target": self._last_target,
            "tool_holds": tool_holds,
        }

    def run_function(self, name: str, args: dict) -> dict:
        if name not in ALLOWED_FUNCTIONS:
            raise CellExecutionError(f"Function not allowlisted: {name!r}")
        result = getattr(self, name)(**(args or {}))
        return {"function": name, "args": args, "result": result}

    def inject_failure(self, part: str, new_parent: str) -> None:
        self._require_part(part)
        self._require_position(new_parent)
        self._parts[part] = new_parent
