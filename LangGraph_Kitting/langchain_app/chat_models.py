"""Chat model factory: Gemini (Google AI) as the primary model, Ollama for
the local fallback model. Same interface for both, so switching model is one setting in
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
        model_name = os.environ.get("OLLAMA_MODEL") or model_name
        # Thinking models add reasoning text around the JSON answer (watch-out
        # in the assignment) - switch it off, same as Ollama's `think: false`.
        return ChatOllama(
            model=model_name, temperature=temperature, base_url=base_url,
            reasoning=False,
        )

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Add it to .env to use the Gemini model."
            )
        return ChatGoogleGenerativeAI(
            model=model_name, temperature=temperature, google_api_key=api_key,
        )

    raise ValueError(f"Unknown model provider: {provider!r}")
