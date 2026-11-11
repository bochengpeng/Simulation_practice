"""Operator console (Step 5): a plain terminal prompt. A scripted-answer
mode for batch experiment runs (so a run needs no live person) is a stretch
goal (System Design draft open decision) - `scripted_answers` below is the
minimal version of that: a dict of order_id -> canned answer used instead
of blocking on input() when running the mini experiment unattended.
"""

from __future__ import annotations


def ask_terminal(message: str, code: str, order_id: str | None = None) -> str:
    print(f"\n[OPERATOR] ({code}) {message}")
    try:
        return input("Your answer (blank = acknowledged): ")
    except EOFError:
        return ""


def make_scripted_operator(scripted_answers: dict[str, str]):
    def _ask(message: str, code: str, order_id: str | None = None) -> str:
        answer = scripted_answers.get(order_id, "")
        print(f"[OPERATOR:scripted] ({code}) {message} -> {answer!r}")
        return answer

    return _ask
