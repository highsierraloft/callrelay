from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import httpx2
import pytest
import respx
from openai import DefaultAsyncHttpxClient
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.processors.aggregators.llm_context import LLMContext

from app.call_ai_inputs import (
    ModelRequestTrace,
    initial_messages,
    model_options,
    model_tools,
    time_notice,
)
from app.openrouter_client import OpenRouterVoiceLLMService
from app.voice_prompts import voice_instructions, voice_prompt_sections
from tests import test_android_calls as phone_tests
from tests import test_control_panel as control_tests

android = phone_tests.android
panel = control_tests.panel


def test_prompt_sections_match_the_new_executed_contract(packet):
    packet = packet.model_copy(update={"language": "en"})
    prompt = voice_instructions(packet, conversation_languages=["en", "es", "ru", "uk"])
    parts = voice_prompt_sections(packet, conversation_languages=["en", "es", "ru", "uk"])
    assert "".join(part["text"] for part in parts) == prompt
    assert {part["id"] for part in parts} >= {"recipient", "task", "personality", "authority"}
    assert {part["source"] for part in parts} >= {
        "Global Defaults",
        "Runtime rules",
        "Hermes or New Call form",
    }


async def test_preview_includes_tools_and_model_parameters_without_the_hermes_guide(panel):
    guide = await panel.h.service.control.current("guide")
    await panel.h.service.control.save(
        "guide", {"text": "HERMES_ONLY_GUIDE_MARKER"}, guide["revision"], "test"
    )
    response = await panel.client.post(
        "/admin/api/prompt-preview", json={"context": panel.h.packet.model_dump(mode="json")}
    )
    assert response.status_code == 200
    data = response.json()["configured"]
    assert "HERMES_ONLY_GUIDE_MARKER" not in json.dumps(data)
    assert data["model_options"]["max_tokens"] == 512
    assert data["model_options"]["model"] == panel.h.service.config.dialogue_model
    assert data["tools"][0]["function"]["name"] == "finish_call"
    assert data["runtime_messages"]["connection_event"] in json.dumps(
        data["initial_request_example"]
    )
    assert "remaining_seconds" in data["runtime_messages"]["closing_timer_template"]
    assert data["system_prompt"] == "".join(part["text"] for part in data["prompt_sections"])
    assert "mcp_bearer_token" not in json.dumps(data)
    assert "openrouter_key_file" not in json.dumps(data)


@pytest.mark.parametrize(
    "model,temperature",
    [
        ("google/gemini-3.1-flash-lite", 0.4),
        ("openai/gpt-6-luna", 0.4),
        ("anthropic/claude-haiku-5.5", None),
        ("openai/gpt-4.1-mini", None),
    ],
)
async def test_request_capture_matches_the_actual_sdk_body_and_has_no_headers(
    android, model, temperature
):
    trace = ModelRequestTrace()
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=b"data: [DONE]\n\n"
        )

    client = DefaultAsyncHttpxClient(
        transport=httpx2.MockTransport(respond), event_hooks={"request": [trace.capture]}
    )
    config = android.service.config.model_copy(
        update={"dialogue_model": model, "dialogue_temperature": temperature}
    )
    options = model_options(config)
    tool = model_tools()[0]["function"]
    service = OpenRouterVoiceLLMService(
        api_key="secret-header-never-in-inspector",
        http_client=client,
        settings=OpenRouterVoiceLLMService.Settings(
            model=options["model"],
            max_tokens=options["max_tokens"],
            **({"temperature": options["temperature"]} if "temperature" in options else {}),
            provider=OpenRouterVoiceLLMService.ProviderPreferences(**options["provider"]),
            extra={"extra_body": {"reasoning": options["reasoning"]}}
            if "reasoning" in options
            else {},
        ),
    )
    messages = initial_messages("A test system prompt\n" + time_notice(120))
    tools = ToolsSchema(
        standard_tools=[
            FunctionSchema(
                name=tool["name"],
                description=tool["description"],
                properties=tool["parameters"]["properties"],
                required=tool["parameters"]["required"],
            )
        ]
    )
    try:
        stream = await service.get_chat_completions(LLMContext(messages=messages, tools=tools))
        async for _ in stream:
            pass
        actual = trace.view()
        assert actual is not None
        assert actual["body"] == json.loads(requests[0].content)
        for key, value in options.items():
            assert actual["body"][key] == value
        if "temperature" not in options:
            assert "temperature" not in actual["body"]
        if "reasoning" not in options:
            assert "reasoning" not in actual["body"]
        assert actual["body"]["tools"] == model_tools()
        assert actual["body"]["messages"] == messages
        messages[0]["content"] = "Later history changed"
        assert actual["body"]["messages"][0]["content"].startswith("A test system prompt")
        assert "secret-header-never-in-inspector" not in json.dumps(actual)
    finally:
        await client.aclose()


async def test_latest_request_is_bounded_and_unrelated_requests_are_ignored():
    trace = ModelRequestTrace()
    await trace.capture(
        httpx.Request("POST", "https://another.example/api", json={"private": "unused"})
    )
    assert trace.view() is None
    await trace.capture(
        httpx.Request(
            "POST",
            "https://openrouter.ai/api/v1/chat/completions",
            content=b"x" * (4 * 1024 * 1024 + 1),
        )
    )
    latest = trace.view()
    assert latest and latest["omitted"] and latest["body"] is None


async def test_text_practice_inspector_uses_the_frozen_settings_and_real_request(panel):
    body = {"mode": "text", "context": panel.h.packet.model_dump(mode="json")}
    result = await panel.client.post("/admin/api/practice", json=body)
    session_id = result.json()["id"]
    try:
        with respx.mock as mock:
            mock.post("https://openrouter.ai/api/v1/chat/completions").respond(
                200, json={"choices": [{"message": {"content": "A short answer"}}]}
            )
            await panel.client.post(
                f"/admin/api/practice/{session_id}/text",
                json={"text": "Hello from the inspector test"},
            )
        data = (await panel.client.get(f"/admin/api/practice/{session_id}/ai-inputs")).json()
        assert data["configured"]["mode"] == "text"
        assert data["configured"]["runtime_messages"]["connection_event"] is None
        assert (
            data["last_request"]["body"]["messages"][-1]["content"]
            == "Hello from the inspector test"
        )
        assert data["last_request"]["body"]["tools"] == model_tools("text", callbacks_enabled=True)
        assert not panel.h.asterisk.dials
    finally:
        await panel.client.delete(f"/admin/api/practice/{session_id}")


async def test_call_inspection_retains_a_recorded_request_and_marks_legacy_values_unknown(panel):
    h = panel.h
    plan = await control_tests.seed_call(h)
    trace = ModelRequestTrace()
    await trace.capture(
        httpx.Request(
            "POST",
            "https://openrouter.ai/api/v1/chat/completions",
            json={
                "model": "recorded-model",
                "messages": [{"role": "user", "content": "Actual turn"}],
            },
        )
    )
    await h.service._save_model_request(
        SimpleNamespace(call_id="call_control_test", voice=SimpleNamespace(request_trace=trace))
    )
    result = (await panel.client.get("/admin/api/calls/call_control_test/ai-inputs")).json()
    assert result["configured"]["model_options"]["model"] == h.service.config.dialogue_model
    assert result["last_request"]["body"]["model"] == "recorded-model"
    snapshot = await h.service.db.control_plan(plan.plan_id)
    snapshot.pop("ai_inputs")
    await h.service.db.execute(
        "UPDATE plan_configurations SET snapshot_json=? WHERE plan_id=?",
        (json.dumps(snapshot), plan.plan_id),
    )
    result = (await panel.client.get("/admin/api/calls/call_control_test/ai-inputs")).json()
    assert result["configured"] is None
    assert result["saved_system_prompt"] == snapshot["effective_prompt"]
    assert result["last_request"]["body"]["model"] == "recorded-model"
    panel.client.headers.pop("x-control-bridge")
    assert (
        await panel.client.get("/admin/api/calls/call_control_test/ai-inputs")
    ).status_code == 401
