"""GraphState (Step 5): the retry counter, step budget and time budget all
live here, so a run always ends.
"""

from __future__ import annotations

from typing import Literal, Optional, TypedDict


class GraphState(TypedDict, total=False):
    order_id: str
    order_text: str
    cell_state: dict
    loop_mode: Literal["open", "closed"]

    plan: Optional[dict]
    check_result: Optional[dict]
    step_index: int
    step_failure: Optional[str]

    retries: int
    max_retries: int
    rejections: list

    operator_answer: Optional[str]
    final_status: Optional[str]
    events: list
