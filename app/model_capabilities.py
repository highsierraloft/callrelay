"""Reviewed catalog fallbacks; live metadata is frozen before a conversation starts."""

from __future__ import annotations

from typing import Any

# Keep this small: these are outage fallbacks, not a replacement for the provider catalog.
# Source: OpenRouter /api/v1/models, verified 2026-10-24.
_BASE = ["tools", "tool_choice", "max_tokens"]
_REVIEWED = {
    "google/gemini-3.1-flash-lite": (True, False, ["none", "minimal", "low", "medium", "high"]),
    "google/gemini-3.5-flash-lite": (True, True, ["minimal", "low", "medium", "high"]),
    "google/gemini-3.8-flash": (True, True, ["low", "medium", "high"]),
    "openai/gpt-6-luna": (False, False, ["none", "low", "medium", "high", "xhigh", "max"]),
    "openai/gpt-6-sol": (False, False, ["none", "low", "medium", "high", "xhigh", "max"]),
    "anthropic/claude-haiku-5.5": (False, False, ["none", "low", "medium", "high", "xhigh", "max"]),
    "openai/gpt-4.1-mini": (True, False, []),
}


def reviewed_model(model_id: str) -> dict[str, Any] | None:
    if model_id not in _REVIEWED:
        return None
    temperature, mandatory, efforts = _REVIEWED[model_id]
    return {
        "id": model_id,
        "supported_parameters": [
            *_BASE,
            *(["temperature"] if temperature else []),
            *(["reasoning"] if efforts else []),
        ],
        "reasoning": {"mandatory": mandatory, "supported_efforts": efforts},
        "source": "Reviewed provider metadata (2026-10-24)",
    }


def validate_reasoning(model: dict[str, Any], effort: str | None) -> None:
    reasoning = model.get("reasoning") or {}
    efforts = reasoning.get("supported_efforts") or []
    if effort is None:
        return  # The owner explicitly selected the provider's default.
    if (
        effort == "none"
        and not reasoning.get("mandatory")
        and ("reasoning" not in model["supported_parameters"] or "none" in efforts)
    ):
        return  # Non-reasoning models simply omit the reasoning parameter.
    if effort not in efforts:
        raise ValueError(
            f"{model['id']} does not support reasoning '{effort}'. "
            "Choose a supported reasoning level or a model that supports Off."
        )
