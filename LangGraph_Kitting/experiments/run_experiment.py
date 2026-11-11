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
from logging_.callback import JsonlLogger
from orchestrator.graph import build_graph, initial_state
from orchestrator.operator_console import make_scripted_operator
from station.robodk_cell import RoboDKCell

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
PROVIDERS = ("ollama", "groq")
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
    "elapsed_s", "ai_calls", "log_path", "reason",
]


def run_one(order, provider: str, loop_mode: str, capability: dict, cell) -> dict:
    run_id = f"{order.order_id}_{provider}_{loop_mode}_{int(time.time())}"
    log_path = LOGS_DIR / f"{run_id}.jsonl"
    model_name = capability["models"][provider]["model"]
    logger = JsonlLogger(log_path, model_name=model_name, provider=provider)

    try:
        planner_chain = build_planner_chain(capability, provider)
    except RuntimeError as exc:
        logger({"event": "skipped", "order_id": order.order_id, "reason": str(exc)})
        logger.close()
        return {
            "order_id": order.order_id, "provider": provider, "loop_mode": loop_mode,
            "status": "SKIPPED", "reason": str(exc), "log_path": str(log_path),
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
    elapsed_s = time.time() - start

    final_status = final_state.get("final_status") if final_state else "UNKNOWN"
    retries = final_state.get("retries", 0) if final_state else None
    final_parts = cell.read_state()["parts"]
    task_success = task_succeeded(order.order_id, start_parts, final_parts)
    logger.close()
    ai_calls = sum(
        1 for line in log_path.read_text(encoding="utf-8").splitlines()
        if '"event": "plan"' in line
    )
    return {
        "order_id": order.order_id, "provider": provider, "loop_mode": loop_mode,
        "status": final_status, "task_success": task_success, "retries": retries,
        "elapsed_s": round(elapsed_s, 2), "ai_calls": ai_calls,
        "log_path": str(log_path), "reason": "",
    }


def main() -> None:
    load_dotenv()
    capability = load_capability()
    cell = RoboDKCell()

    results = []
    for order in STARTER_ORDERS:
        for provider in PROVIDERS:
            for loop_mode in LOOP_MODES:
                print(f"=== {order.order_id} {provider} {loop_mode} ===")
                result = run_one(order, provider, loop_mode, capability, cell)
                print(result)
                results.append(result)

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    results_path = LOGS_DIR / "results_table.csv"
    with results_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        for row in results:
            writer.writerow({field: row.get(field, "") for field in RESULT_FIELDS})
    print(f"\nWrote {results_path}")


if __name__ == "__main__":
    main()
