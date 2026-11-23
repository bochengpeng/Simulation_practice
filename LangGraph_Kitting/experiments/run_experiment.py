"""Mini experiment (Step 7): run the starter orders with two models and
both loop modes. Same orders, same cell, same checker for every variant -
only the planner and the loop change.

Usage (from LangGraph_Kitting/):
    python -m experiments.run_experiment
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

from dotenv import load_dotenv

from experiments.starter_orders import STARTER_ORDERS
from experiments.success_criteria import task_succeeded
from langchain_app.capability_loader import load_capability
from langchain_app.planner_chain import build_planner_chain
from logging_.callback import RunLogger
from orchestrator.graph import build_graph, initial_state
from orchestrator.operator_console import make_scripted_operator
from station.robodk_cell import RoboDKCell

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
PROVIDERS = ("ollama", "gemini")
LOOP_MODES = ("open", "closed")

# Scripted operator answers so a batch run needs no live person. The rule
# for repeating a run: always re-run the full order list fresh from
# cell.reset() - a repeat never depends on whether a previous run
# succeeded (assignment instruction under "Run each order with").
SCRIPTED_ANSWERS = {
    "T2": "SPACER_LONG",
    "T3": "acknowledged",
    "T4": "acknowledged",
    "T5": "acknowledged",
}

RESULT_FIELDS = [
    "order_id", "provider", "loop_mode", "status", "task_success", "retries",
    "elapsed_s", "ai_calls", "reason",
]


def run_one(order, provider: str, loop_mode: str, capability: dict, cell, logger: RunLogger) -> dict:
    logger.model_name = capability["models"][provider]["model"]
    logger.provider = provider
    logger.ai_calls = 0
    logger.section(f"{order.order_id} {provider} {loop_mode}")

    try:
        planner_chain = build_planner_chain(capability, provider)
    except RuntimeError as exc:
        logger({"event": "skipped", "order_id": order.order_id, "reason": str(exc)})
        return {
            "order_id": order.order_id, "provider": provider, "loop_mode": loop_mode,
            "status": "SKIPPED", "task_success": "", "retries": "",
            "elapsed_s": "", "ai_calls": 0, "reason": str(exc),
        }

    operator = make_scripted_operator(SCRIPTED_ANSWERS)
    graph = build_graph(
        cell, capability, planner_chain,
        logger=logger, operator=operator, callback_handler=logger,
    )

    cell.reset()
    start_parts = dict(cell.read_state()["parts"])
    state = initial_state(order.order_id, order.order_text, cell.read_state(), loop_mode)

    start = time.time()
    config = {"recursion_limit": 100}
    final_state = None
    injected = False
    error_reason = ""
    try:
        for update in graph.stream(state, config=config, stream_mode="values"):
            final_state = update
            if (
                order.inject_failure and not injected
                and (update.get("check_result") or {}).get("status") == "OK"
                and update.get("step_index", 0) == 0
            ):
                part, new_parent = order.inject_failure
                cell.inject_failure(part, new_parent)
                injected = True
                logger({
                    "event": "relocation", "order_id": order.order_id,
                    "part": part, "new_parent": new_parent,
                })
    except Exception as exc:  # noqa: BLE001 - one bad run must not abort the batch
        error_reason = f"{type(exc).__name__}: {str(exc)[:300]}"
        logger({"event": "run_error", "order_id": order.order_id, "reason": error_reason})
    elapsed_s = time.time() - start

    final_status = "ERROR" if error_reason else (
        final_state.get("final_status") if final_state else "UNKNOWN"
    )
    retries = final_state.get("retries", 0) if final_state else None
    final_parts = cell.read_state()["parts"]
    task_success = task_succeeded(order.order_id, start_parts, final_parts)
    logger({
        "event": "outcome", "order_id": order.order_id,
        "start_parts": start_parts, "final_parts": final_parts,
        "task_success": task_success,
    })
    return {
        "order_id": order.order_id, "provider": provider, "loop_mode": loop_mode,
        "status": final_status, "task_success": task_success, "retries": retries,
        "elapsed_s": round(elapsed_s, 2), "ai_calls": logger.ai_calls, "reason": error_reason,
    }


def run_matrix(
    orders=STARTER_ORDERS,
    providers=PROVIDERS,
    loop_modes=LOOP_MODES,
    results_filename: str = "results_table.csv",
    log_filename: str = "experiment.log",
) -> list[dict]:
    """Run every (order, provider, loop_mode) combination, writing one
    shared text log and one results CSV. Used by `main()` for the full
    matrix and by `experiments/cli.py` for a user-chosen subset (same
    cell, same checker, same orders for every variant - only the
    planner/loop change)."""
    load_dotenv()
    capability = load_capability()
    cell = RoboDKCell()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = RunLogger(LOGS_DIR / log_filename)

    results = []
    try:
        for order in orders:
            for provider in providers:
                for loop_mode in loop_modes:
                    result = run_one(order, provider, loop_mode, capability, cell, logger)
                    print(result)
                    results.append(result)
    finally:
        logger.close()

    results_path = LOGS_DIR / results_filename
    with results_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        for row in results:
            writer.writerow({field: row.get(field, "") for field in RESULT_FIELDS})
    print(f"\nWrote {results_path}")
    return results


def main() -> None:
    run_matrix()


if __name__ == "__main__":
    main()
