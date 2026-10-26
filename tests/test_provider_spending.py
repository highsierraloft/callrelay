from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.provider_usage import PricingSettings, UsageLedger, estimate_cost
from tests import test_android_calls as phone_tests
from tests import test_control_panel as panel_tests

android = phone_tests.android
panel = panel_tests.panel


def pricing():
    return {
        "revision": 1,
        "rates": PricingSettings().model_dump(),
        "tts_model": "inworld-tts-2",
        "openrouter": {"prompt": "0.00000025", "completion": "0.0000015"},
    }


def usage():
    ledger = UsageLedger()
    request = ledger.start_llm()
    ledger.record_llm(request, {"prompt_tokens": 1000, "completion_tokens": 200, "cost": 0.0004})
    ledger.tts_characters = 1000
    ledger.stt_seconds = 60
    ledger.finalized = True
    return ledger


def test_reported_model_cost_and_speech_units_produce_a_frozen_estimate():
    result = estimate_cost(usage().snapshot(), pricing())
    assert result["coverage"] == "complete"
    assert result["total_usd"] == pytest.approx(0.0004 + 0.002 + 0.025)
    assert result["providers"][0]["basis"] == "reported"
    assert result["providers"][1]["basis"] == "hourly_equivalent_estimate"
    assert result["providers"][2]["input_characters"] == 1000


def test_interrupted_model_request_is_partial_and_missing_data_is_not_free():
    ledger = usage()
    ledger.start_llm()
    result = estimate_cost(ledger.snapshot(), pricing())
    assert result["total_usd"] is None and result["coverage"] == "partial"
    assert result["providers"][0]["requests_missing"] == 1
    assert result["known_subtotal_usd"] > 0
    assert estimate_cost(None, pricing())["known_subtotal_usd"] is None


def test_cumulative_usage_replaces_the_same_request_and_free_usage_is_valid():
    ledger = UsageLedger()
    key = ledger.start_llm()
    ledger.record_llm(key, {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.01})
    ledger.record_llm(key, {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.02})
    ledger.finalized = True
    result = estimate_cost(ledger.snapshot(), pricing())
    assert result["total_usd"] == 0.02 and result["providers"][0]["completion_tokens"] == 20
    ledger.record_llm(key, {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0})
    assert estimate_cost(ledger.snapshot(), pricing())["total_usd"] == 0


def test_fallback_uses_catalog_rates_and_missing_or_invalid_prices_stay_unknown():
    ledger = usage()
    ledger.record_llm(1, {"prompt_tokens": 1000, "completion_tokens": 200})
    report = estimate_cost(ledger.snapshot(), pricing())
    assert report["providers"][0]["basis"] == "catalog_estimate"
    assert report["providers"][0]["usd"] == pytest.approx(0.00055)
    report = estimate_cost(ledger.snapshot(), {"openrouter": {"prompt": -1, "completion": "NaN"}})
    assert report["coverage"] == "partial" and report["total_usd"] is None
    assert all(p["usd"] is None for p in report["providers"])
    with pytest.raises(ValidationError):
        PricingSettings(soniox_usd_per_hour=float("inf"))


async def test_call_cost_keeps_its_prepared_rates_after_owner_changes_pricing(android):
    h = android
    plan = await panel_tests.seed_call(h)
    snapshot = await h.service.db.control_plan(plan.plan_id)
    await h.service.db.control_usage("call_control_test", usage().snapshot(), snapshot["pricing"])
    before = await h.service.control.spending.for_call("call_control_test")
    record = await h.service.control.current("pricing")
    record["value"]["inworld_tts2_usd_per_million_characters"] = 100
    await h.service.control.save("pricing", record["value"], record["revision"], "owner")
    assert await h.service.control.spending.for_call("call_control_test") == before
    next_plan = await phone_tests.prepare(h)
    assert (await h.service.db.control_plan(next_plan.plan_id))["pricing"]["rates"][
        "inworld_tts2_usd_per_million_characters"
    ] == 100


async def test_monthly_totals_report_coverage_and_never_fill_historical_costs(panel):
    h = panel.h
    first = await panel_tests.seed_call(h, call_id="call_costed")
    await panel_tests.seed_call(h, call_id="call_unknown")
    prices = (await h.service.db.control_plan(first.plan_id))["pricing"]
    await h.service.db.control_usage("call_costed", usage().snapshot(), prices)
    month = datetime.now(UTC).strftime("%Y-%m")
    response = await panel.client.get("/admin/api/costs", params={"month": month})
    assert response.status_code == 200
    data = response.json()
    assert data["call_count"] == 2 and data["coverage"] == {
        "complete": 1,
        "partial": 0,
        "unavailable": 1,
    }
    assert data["known_subtotal_usd"] == pytest.approx(0.0174)
    unknown = next(c for c in data["calls"] if c["call_id"] == "call_unknown")
    assert unknown["cost"]["total_usd"] is None
    assert (await panel.client.get("/admin/api/costs?month=2026-99")).status_code == 422
    assert (
        await panel.client.get(
            "/admin/api/costs?month=2026-10", headers={"x-control-bridge": "wrong"}
        )
    ).status_code == 401


async def test_provider_usage_storage_replaces_once_and_keeps_raw_counts(android):
    h = android
    await panel_tests.seed_call(h)
    await h.service.db.control_usage("call_control_test", usage().snapshot(), pricing())
    await h.service.db.control_usage("call_control_test", usage().snapshot(), pricing())
    rows = await h.service.db.fetch_all("SELECT * FROM call_provider_usage")
    assert len(rows) == 1 and json.loads(rows[0]["usage_json"])["tts_input_characters"] == 1000


async def test_model_adapter_keeps_usage_from_the_original_stream_and_marks_lost_responses(
    monkeypatch,
):
    from openai.types.completion_usage import CompletionUsage
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.services.openrouter.llm import OpenRouterLLMService

    from app.openrouter_client import OpenRouterVoiceLLMService

    ledger = UsageLedger()
    stream = object()

    async def completion(self, context):
        return stream

    monkeypatch.setattr(OpenRouterLLMService, "get_chat_completions", completion)
    # A real adapter instance is needed for super(); no provider request is sent.
    import httpx

    async with httpx.AsyncClient() as client:
        instance = OpenRouterVoiceLLMService(
            api_key="test", base_url="https://example.invalid", http_client=client, usage=ledger
        )
        assert await instance.get_chat_completions(LLMContext()) is stream
        instance._token_usage(
            CompletionUsage(prompt_tokens=42, completion_tokens=8, total_tokens=50, cost=0.001)
        )
        assert await instance.get_chat_completions(LLMContext()) is stream
    assert ledger.snapshot()["llm_requests"][0]["reported_usd"] == 0.001
    assert ledger.snapshot()["llm_requests"][1] is None


async def test_speech_adapters_meter_successful_sends_and_separate_reconnections(monkeypatch):
    from pipecat.services.inworld.tts import InworldTTSService
    from pipecat.services.soniox.stt import SonioxSTTService

    from app.inworld_client import InworldVoiceTTSService
    from app.soniox_client import SonioxVoiceSTTService

    times = iter([10, 15, 20, 23])
    from types import SimpleNamespace

    monkeypatch.setattr("app.provider_usage.time", SimpleNamespace(monotonic=lambda: next(times)))
    ledger = UsageLedger()

    async def connect(self):
        self._websocket = object()

    async def disconnect(self):
        self._websocket = None

    async def metric(self, text):
        pass

    monkeypatch.setattr(SonioxSTTService, "_connect_websocket", connect)
    monkeypatch.setattr(SonioxSTTService, "_disconnect_websocket", disconnect)
    monkeypatch.setattr(InworldTTSService, "start_tts_usage_metrics", metric)
    stt = SonioxVoiceSTTService(api_key="test", usage=ledger)
    tts = InworldVoiceTTSService(api_key="test", voice_id="Wade", usage=ledger)
    await stt._connect_websocket()
    await stt._disconnect_websocket()
    await stt._connect_websocket()
    await stt._disconnect_websocket()
    await tts.start_tts_usage_metrics("Hello")
    await tts.start_tts_usage_metrics("Привіт")
    assert ledger.stt_seconds == 8 and ledger.tts_characters == 11


def test_unknown_speech_model_does_not_borrow_a_different_models_price():
    report = estimate_cost(usage().snapshot(), {**pricing(), "tts_model": "future-model"})
    assert report["coverage"] == "partial" and report["total_usd"] is None
    assert report["providers"][2]["usd"] is None
