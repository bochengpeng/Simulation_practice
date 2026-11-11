"""The planner chain (Steps 1-3): prompt | model | structured parser.

LangChain replaces only the plumbing (prompt, model call, parser) - the
checker and the cell stay plain Python, imported and called elsewhere.
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage

from langchain_app.chat_models import get_chat_model
from langchain_app.prompt_builder import build_prompt_template, build_system_text
from langchain_app.schemas import PlannerOutput


def build_planner_chain(capability: dict, provider: str):
    model = get_chat_model(provider, capability)
    structured_model = model.with_structured_output(PlannerOutput)
    prompt = build_prompt_template()
    return prompt | structured_model


def invoke_planner(
    chain,
    capability: dict,
    order_text: str,
    cell_state: dict,
    history: list | None = None,
    callbacks: list | None = None,
):
    config = {"callbacks": callbacks} if callbacks else None
    result = chain.invoke({
        "system_text": build_system_text(capability),
        "order_text": order_text,
        "cell_state_json": json.dumps(cell_state),
        "history": history or [],
    }, config=config)
    return result.response


def rejection_history_messages(order_text: str, rejections: list[dict]) -> list:
    """Turn checker rejections into the MessagesPlaceholder("history")
    content for the next planner call (Step 4's feedback loop)."""

    messages = []
    for rejection in rejections:
        messages.append(AIMessage(content=json.dumps(rejection.get("plan", {}))))
        messages.append(
            HumanMessage(
                content=(
                    "That plan was rejected. Code: "
                    f"{rejection['code']}. Step: {rejection.get('step_index')}. "
                    f"Reason: {rejection['reason']}. Send a corrected plan, a "
                    "question, or a refusal."
                )
            )
        )
    return messages
