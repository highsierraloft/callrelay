"""Small in-memory usage counters. No network or database work in the audio path."""

from __future__ import annotations

import math
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PricingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    soniox_usd_per_hour: float = Field(default=0.12, ge=0, le=1000)
    inworld_tts2_usd_per_million_characters: float = Field(default=25, ge=0, le=10000)
    inworld_flash_usd_per_million_characters: float = Field(default=15, ge=0, le=10000)
    note: str = Field(
        default="On-demand list rates checked 25 October 2026. Soniox uses token billing; its hourly equivalent is an approximation. Adjust these assumptions for your plan. Carrier charges, taxes, subscriptions, and free credits are excluded.",
        max_length=2000,
    )


def nonnegative(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


class UsageLedger:
    def __init__(self, *, stt: str = "soniox", tts: str = "inworld") -> None:
        self.stt, self.tts = stt, tts
        self.requests: dict[int, dict[str, Any] | None] = {}
        self.request_count = 0
        self.stt_seconds = 0.0
        self._stt_start: float | None = None
        self.tts_characters = 0
        self.incomplete = False
        self.finalized = False

    def start_llm(self) -> int:
        self.request_count += 1
        request_id = self.request_count
        if len(self.requests) < 10000:
            self.requests[request_id] = None
        else:
            self.incomplete = True
        return request_id

    def record_llm(self, request_id: int, usage: dict[str, Any]) -> None:
        if request_id not in self.requests:
            return
        self.requests[request_id] = {
            "prompt_tokens": nonnegative(usage.get("prompt_tokens")),
            "completion_tokens": nonnegative(usage.get("completion_tokens")),
            "cached_tokens": nonnegative(
                (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            ),
            "reported_usd": nonnegative(usage.get("cost")),
        }

    def start_stt(self) -> None:
        if self._stt_start is None:
            self._stt_start = time.monotonic()

    def stop_stt(self) -> None:
        if self._stt_start is not None:
            self.stt_seconds += max(0, time.monotonic() - self._stt_start)
            self._stt_start = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": 1,
            "stt": self.stt,
            "tts": self.tts,
            "llm_requests": list(self.requests.values()),
            "stt_session_seconds": round(
                self.stt_seconds
                + (
                    max(0, time.monotonic() - self._stt_start) if self._stt_start is not None else 0
                ),
                3,
            ),
            "tts_input_characters": self.tts_characters,
            "incomplete": self.incomplete,
            "finalized": self.finalized,
        }


def estimate_cost(usage: dict[str, Any] | None, pricing: dict[str, Any] | None) -> dict[str, Any]:
    if usage is None:
        return {
            "currency": "USD",
            "coverage": "unavailable",
            "total_usd": None,
            "known_subtotal_usd": None,
            "providers": [],
            "note": "Usage was not recorded. Historical costs are not reconstructed.",
        }
    rates = (pricing or {}).get("rates", {})
    model_rates = (pricing or {}).get("openrouter", {})
    llm_total, reported_count, estimated_count, missing = 0.0, 0, 0, 0
    prompt_tokens, completion_tokens = 0.0, 0.0
    for request in usage.get("llm_requests", []):
        if not request:
            missing += 1
            continue
        prompt_tokens += request.get("prompt_tokens") or 0
        completion_tokens += request.get("completion_tokens") or 0
        reported = nonnegative(request.get("reported_usd"))
        if reported is not None:
            llm_total += reported
            reported_count += 1
        else:
            input_rate, output_rate = (
                nonnegative(model_rates.get("prompt")),
                nonnegative(model_rates.get("completion")),
            )
            if (
                input_rate is not None
                and output_rate is not None
                and request.get("prompt_tokens") is not None
                and request.get("completion_tokens") is not None
            ):
                # List rates deliberately do not assume unreported cache discounts.
                llm_total += (
                    request["prompt_tokens"] * input_rate
                    + request["completion_tokens"] * output_rate
                )
                estimated_count += 1
            else:
                missing += 1
    stt_rate = nonnegative(rates.get("soniox_usd_per_hour"))
    tts_rate_field = {
        "inworld-tts-2": "inworld_tts2_usd_per_million_characters",
        "inworld-tts-2-flash": "inworld_flash_usd_per_million_characters",
    }.get((pricing or {}).get("tts_model", ""))
    tts_rate = nonnegative(rates.get(tts_rate_field)) if tts_rate_field else None
    stt_cost = (
        usage.get("stt_session_seconds", 0) / 3600 * stt_rate
        if stt_rate is not None and usage.get("stt") == "soniox"
        else None
    )
    tts_cost = (
        usage.get("tts_input_characters", 0) / 1e6 * tts_rate
        if tts_rate is not None and usage.get("tts") == "inworld"
        else None
    )
    llm_cost = (
        llm_total if reported_count + estimated_count or not usage.get("llm_requests") else None
    )
    providers = [
        {
            "provider": "OpenRouter",
            "usd": round(llm_cost, 8) if llm_cost is not None else None,
            "basis": "reported"
            if not estimated_count and not missing
            else "partial"
            if missing
            else "catalog_estimate",
            "prompt_tokens": int(prompt_tokens),
            "completion_tokens": int(completion_tokens),
            "requests_reported": reported_count,
            "requests_estimated": estimated_count,
            "requests_missing": missing,
        },
        {
            "provider": "Soniox",
            "usd": round(stt_cost, 8) if stt_cost is not None else None,
            "basis": "hourly_equivalent_estimate",
            "session_seconds": usage.get("stt_session_seconds", 0),
        },
        {
            "provider": "Inworld",
            "usd": round(tts_cost, 8) if tts_cost is not None else None,
            "basis": "character_estimate",
            "input_characters": usage.get("tts_input_characters", 0),
        },
    ]
    partial = (
        missing > 0
        or usage.get("incomplete")
        or not usage.get("finalized")
        or any(p["usd"] is None for p in providers)
    )
    total = round(sum(p["usd"] for p in providers if p["usd"] is not None), 8)
    return {
        "currency": "USD",
        "coverage": "partial" if partial else "complete",
        "total_usd": None if partial else total,
        "known_subtotal_usd": total,
        "providers": providers,
        "pricing": pricing,
        "note": "Estimated provider cost; not an invoice. Interrupted responses may omit usage. Synthesized text can be billed even when interrupted or unheard. Carrier charges, taxes, plan fees, and free credits are excluded.",
    }
