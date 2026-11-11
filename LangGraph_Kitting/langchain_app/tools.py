"""A @tool-shaped description per function (Step 3/Design table), built
from config/capability.yaml so descriptions are never typed twice.

Watch-out from the assignment: these are for *display* (binding into a
prompt, or the Step 2 tool-calling comparison) only. No agent executor ever
calls `.invoke()` on these - the orchestrator is the only thing allowed to
run a function, and only after the checker says run (FR5).
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import create_model


def _not_executable(name: str):
    def _raise(**_kwargs):
        raise RuntimeError(
            f"{name} is descriptive only. Only the orchestrator may run "
            "functions on the cell, after the checker approves a plan."
        )

    return _raise


def build_function_tools(capability: dict) -> list[StructuredTool]:
    tools = []
    for name, info in capability["functions"].items():
        args_schema = create_model(
            f"{name}_args",
            **{arg: (str, ...) for arg in info["args"]},
        )
        tools.append(
            StructuredTool.from_function(
                func=_not_executable(name),
                name=name,
                description=info["description"],
                args_schema=args_schema,
            )
        )
    return tools
