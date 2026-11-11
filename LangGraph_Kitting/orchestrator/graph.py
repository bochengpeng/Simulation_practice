"""LangGraph orchestrator (Step 5).

NOTE: the Design Document's Table 10 (the canonical edge table) does not
exist anywhere in this repository yet, so the edges below are inferred
from the T1-T6 expected-behaviour column of the assignment and documented
here as an assumption to reconcile once the real Table 10 exists:

  plan -> check            (planner returned a plan)
  plan -> ask_operator      (planner returned a question or a refusal -
                             the operator is told either way; a refusal
                             still stops the run, FR3/FR4)
  check -> execute_step     (checker approved the plan)
  check -> plan             (checker rejected, retries < max_retries:
                             the rejection code/reason go into the prompt
                             history for Step 4's feedback loop)
  check -> ask_operator      (checker rejected and retries are exhausted)
  execute_step -> execute_step (more steps remain, no failure)
  execute_step -> stop        (all steps done: SUCCESS)
  execute_step -> plan        (closed loop only: a step failed, e.g. T6's
                                injected failure, and retries remain)
  execute_step -> stop        (open loop, or retries exhausted: stop
                                safely, logged as not recovered)
  ask_operator -> stop
"""

from __future__ import annotations

import dataclasses

from langgraph.graph import END, StateGraph

from checker.checker import check
from langchain_app.planner_chain import invoke_planner, rejection_history_messages
from langchain_app.schemas import Plan
from orchestrator.state import GraphState


def build_graph(cell, capability: dict, planner_chain, logger=None, operator=None,
                 callback_handler=None):
    max_retries = capability["rules"]["max_retries"]
    logger = logger or (lambda event: None)
    operator = operator or (lambda message, code, order_id=None: "")
    callbacks = [callback_handler] if callback_handler else None

    def plan_node(state: GraphState) -> dict:
        history = rejection_history_messages(state["order_text"], state.get("rejections", []))
        response = invoke_planner(
            planner_chain, capability, state["order_text"], state["cell_state"],
            history=history, callbacks=callbacks,
        )
        plan_dict = response.model_dump()
        logger({"event": "plan", "order_id": state["order_id"], "plan": plan_dict})
        return {
            "plan": plan_dict,
            "step_index": 0,
            "step_failure": None,
        }

    def route_after_plan(state: GraphState) -> str:
        plan_type = state["plan"]["type"]
        return "check" if plan_type == "plan" else "ask_operator"

    def check_node(state: GraphState) -> dict:
        plan = Plan(**state["plan"])
        result = check(plan, state["cell_state"], capability)
        result_dict = dataclasses.asdict(result)
        logger({"event": "check", "order_id": state["order_id"], "check_result": result_dict})
        if result.status == "OK":
            return {"check_result": result_dict}
        rejections = state.get("rejections", []) + [{
            "plan": state["plan"],
            "code": result.code,
            "reason": result.reason,
            "step_index": result.step_index,
        }]
        return {
            "check_result": result_dict,
            "rejections": rejections,
            "retries": state.get("retries", 0) + 1,
        }

    def route_after_check(state: GraphState) -> str:
        if state["check_result"]["status"] == "OK":
            return "execute_step"
        if state.get("retries", 0) >= max_retries:
            return "ask_operator"
        return "plan"

    def execute_step_node(state: GraphState) -> dict:
        steps = state["plan"]["steps"]
        index = state["step_index"]
        action = steps[index]
        try:
            result = cell.run_function(action["function"], action["args"])
            logger({"event": "action", "order_id": state["order_id"], "action": action, "result": result})
            new_cell_state = cell.read_state()
            new_index = index + 1
            if new_index >= len(steps):
                return {
                    "step_index": new_index,
                    "cell_state": new_cell_state,
                    "final_status": "SUCCESS",
                }
            return {"step_index": new_index, "cell_state": new_cell_state}
        except Exception as exc:  # noqa: BLE001 - cell execution errors only
            logger({
                "event": "execution_failure", "order_id": state["order_id"],
                "action": action, "reason": str(exc),
            })
            rejections = state.get("rejections", []) + [{
                "plan": state["plan"],
                "code": "EXECUTION_FAILURE",
                "reason": str(exc),
                "step_index": index,
            }]
            return {
                "step_failure": str(exc),
                "cell_state": cell.read_state(),
                "rejections": rejections,
                "retries": state.get("retries", 0) + 1,
            }

    def route_after_execute(state: GraphState) -> str:
        if state.get("final_status") == "SUCCESS":
            return "stop"
        if state.get("step_failure"):
            if state["loop_mode"] == "closed" and state.get("retries", 0) < max_retries:
                return "plan"
            return "stop"
        return "execute_step"

    def ask_operator_node(state: GraphState) -> dict:
        plan = state["plan"]
        if plan["type"] == "question":
            code, message = "AMBIGUOUS", plan["message"]
        elif plan["type"] == "refusal":
            code, message = "REFUSAL", plan["reason"]
        else:
            check_result = state.get("check_result") or {}
            code = check_result.get("code", "UNKNOWN")
            message = check_result.get("reason", "The plan was rejected.")
        answer = operator(message, code, state.get("order_id"))
        logger({
            "event": "human_intervention", "order_id": state["order_id"],
            "code": code, "message": message, "operator_answer": answer,
        })
        return {"operator_answer": answer, "final_status": f"ASKED_OPERATOR:{code}"}

    def stop_node(state: GraphState) -> dict:
        final_status = state.get("final_status")
        if final_status:
            logger({"event": "stop", "order_id": state["order_id"], "final_status": final_status})
            return {}
        final_status = "FAILED_NOT_RECOVERED" if state.get("step_failure") else "STOPPED"
        logger({"event": "stop", "order_id": state["order_id"], "final_status": final_status})
        return {"final_status": final_status}

    graph = StateGraph(GraphState)
    graph.add_node("plan", plan_node)
    graph.add_node("check", check_node)
    graph.add_node("execute_step", execute_step_node)
    graph.add_node("ask_operator", ask_operator_node)
    graph.add_node("stop", stop_node)

    graph.set_entry_point("plan")
    graph.add_conditional_edges("plan", route_after_plan, {
        "check": "check", "ask_operator": "ask_operator",
    })
    graph.add_conditional_edges("check", route_after_check, {
        "execute_step": "execute_step", "plan": "plan", "ask_operator": "ask_operator",
    })
    graph.add_conditional_edges("execute_step", route_after_execute, {
        "execute_step": "execute_step", "plan": "plan", "stop": "stop",
    })
    graph.add_edge("ask_operator", "stop")
    graph.add_edge("stop", END)

    return graph.compile()


def initial_state(order_id: str, order_text: str, cell_state: dict, loop_mode: str) -> GraphState:
    return GraphState(
        order_id=order_id,
        order_text=order_text,
        cell_state=cell_state,
        loop_mode=loop_mode,
        plan=None,
        check_result=None,
        step_index=0,
        step_failure=None,
        retries=0,
        rejections=[],
        operator_answer=None,
        final_status=None,
        events=[],
    )
