"""Shared interface for the RoboDK cell and the plain-Python mirror cell.

Both `RoboDKCell` and `PythonCell` implement this Protocol, so the checker,
the orchestrator and the tests run unchanged against either one (NFR5: keep
a state-based Python simulation alongside the RoboDK view).
"""

from __future__ import annotations

from typing import Protocol


class Cell(Protocol):
    def reset(self) -> None:
        """Reset to the start state of the starter orders (Table 7 shapes)."""

    def read_state(self) -> dict:
        """Return the cell state as the JSON of Table 7: every part's
        parent, the last target the arm reached, and what the tool holds."""

    def move_to(self, position: str) -> None:
        ...

    def pick(self, part: str, source: str) -> None:
        ...

    def place(self, part: str, slot: str) -> None:
        ...

    def locate(self, part: str) -> str:
        """Return the name of the item that currently parents `part`."""

    def run_function(self, name: str, args: dict) -> dict:
        """Dispatch to move_to/pick/place/locate/read_cell_state by name,
        used by the orchestrator so it never calls cell methods directly
        by Python attribute (only allowlisted function calls run, FR5)."""

    def inject_failure(self, part: str, new_parent: str) -> None:
        """Move `part` to another parent while a plan waits (test T6)."""
