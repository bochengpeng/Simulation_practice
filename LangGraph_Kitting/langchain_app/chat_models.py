"""Chat model factory: Groq for GPT-OSS 120B, Ollama for the local backup
model. Same interface for both, so switching model is one setting in
config/capability.yaml - never a code change (NFR1: fixed model name,
temperature 0).
"""

from __future__ import annotations

import os


def get_chat_model(provider: str, capability: dict):
    settings = capability["models"][provider]
    temperature = settings["temperature"]
    model_name = settings["model"]

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        base_url = os.environ.get("OLLAMA_HOST") or settings.get("base_url")
        # Thinking models add reasoning text around the JSON answer (watch-out
        # in the assignment) - switch it off, same as Ollama's `think: false`.
        return ChatOllama(
            model=model_name, temperature=temperature, base_url=base_url,
            reasoning=False,
        )

    if provider == "groq":
        from langchain_groq import ChatGroq

        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Add it to .env to use the Groq model."
            )
        return ChatGroq(model=model_name, temperature=temperature, api_key=api_key)

    raise ValueError(f"Unknown model provider: {provider!r}")
