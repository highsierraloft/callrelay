from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.client import Client

from app.db.control import RevisionConflict
from app.mcp_tools import register_tools
from app.routes import control, reception
from app.voice_prompts import voice_instructions
from tests import test_android_calls as phone_tests

android = phone_tests.android
prepare = phone_tests.prepare


@pytest.fixture
async def panel(android, tmp_path):
    h = android
    access = tmp_path / "control.json"
    access.write_text(
        json.dumps(
            {
                "bridge_token": "a" * 48,
                "owner_login": "owner@example.test",
                "public_origin": "https://panel.test.ts.net:8444",
                "hermes_url": "https://hermes.test.ts.net:8443",
                "timezone": "Europe/London",
            }
        )
    )
    h.service.config.dashboard_config_file = access
    app = FastAPI()
    app.state.call_service = h.service
    mcp = FastMCP("test-control")
    register_tools(mcp, lambda: h.service, android_backend=True)
    app.state.mcp = mcp
    app.include_router(control.router)
    app.include_router(reception.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="https://panel.test.ts.net:8444",
        headers={"x-control-bridge": "a" * 48, "tailscale-user-login": "owner@example.test"},
    ) as client:
        response = await client.get("/admin/api/session")
        assert response.status_code == 200
        client.headers.update(
            {"origin": "https://panel.test.ts.net:8444", "x-csrf-token": response.json()["csrf"]}
        )
        yield SimpleNamespace(client=client, h=h, app=app, session=response.json())


async def seed_call(h, call_id="call_control_test", turns=1):
    plan = await prepare(h)
    await h.service.db.claim_plan_and_create_call(
        plan_id=plan.plan_id,
        call_id=call_id,
        conference_name="control-test-" + call_id,
        confirmation_text=plan.confirmation_summary,
    )
    now = datetime.now(UTC).isoformat()
    await h.service.db.execute(
        "UPDATE calls SET state='completed',answered_at=?,ended_at=?,termination_reason='callee_hangup' WHERE call_id=?",
        (now, now, call_id),
    )
    for n in range(turns):
        await h.service.db.add_transcript_turn(
            call_id=call_id,
            turn_id=f"t{n}",
            speaker="callee",
            text=f"Привіт друже. La reunión es mañana. {n}",
            source_event_type="test.transcript",
            source_event_id=f"e{n}",
        )
    return plan


async def test_seeded_configuration_preserves_the_existing_prompt(android):
    from app.phone_menu import MENU_INSTRUCTIONS

    h = android
    snapshot = await h.service.control.resolve(h.packet)
    previous_prompt = voice_instructions(
        h.packet, conversation_languages=h.service.config.conversation_languages
    )
    assert h.service.control.prompt(h.packet, snapshot) == previous_prompt + MENU_INSTRUCTIONS
    snapshot.pop("phone_menu_version")
    assert h.service.control.prompt(h.packet, snapshot) == previous_prompt
    assert set((await h.service.control.setup())["defaults"])
    assert len(await h.service.control.profiles()) == 4
    await h.service.control.initialize()
    assert len(await h.service.control.history("global")) == 1


async def test_save_changes_only_future_plans_and_snapshot_survives_reload(android):
    h = android
    before = await prepare(h)
    first = await h.service.db.control_plan(before.plan_id)
    current = await h.service.control.current("profile:warm")
    current["value"]["prompt"] = "Use short, practical answers and one useful question."
    await h.service.control.save("profile:warm", current["value"], current["revision"], "owner")
    after = await prepare(h)
    second = await h.service.db.control_plan(after.plan_id)
    assert first["personality_prompt"] != second["personality_prompt"]
    assert first == await h.service.db.control_plan(before.plan_id)
    assert "short, practical answers" not in first["effective_prompt"]
    assert "short, practical answers" in second["effective_prompt"]
    raw = await h.service.db.fetch_one(
        "SELECT context_json FROM plans WHERE plan_id=?", (after.plan_id,)
    )
    assert "profile_id" not in json.loads(raw["context_json"])
    assert (await h.service.db.get_plan_record(after.plan_id)).context.profile_id == "warm"


async def test_concurrent_saves_have_one_winner_and_restore_creates_a_revision(android):
    c = android.service.control
    current = await c.current("guide")
    results = await asyncio.gather(
        c.save("guide", {"text": "First guide"}, 1, "one"),
        c.save("guide", {"text": "Second guide"}, 1, "two"),
        return_exceptions=True,
    )
    assert sum(isinstance(v, RevisionConflict) for v in results) == 1
    restored = await c.save("guide", current["value"], 2, "owner")
    assert restored["revision"] == 3 and restored["value"] == current["value"]
    assert len(await c.history("guide")) == 3


async def test_custom_profile_archive_and_voice_override_validation(android):
    c = android.service.control
    base = copy.deepcopy((await c.current("profile:warm"))["value"])
    base.update(name="Gaming friend", voice_overrides={"dialogue_temperature": 0.2})
    saved = await c.save("profile:gaming", base, 0, "owner")
    packet = android.packet.model_copy(update={"profile_id": "gaming"})
    resolved = await c.resolve(packet)
    assert resolved["voice"]["dialogue_temperature"] == 0.2
    assert resolved["profile_name"] == "Gaming friend"
    saved["value"]["archived"] = True
    await c.save("profile:gaming", saved["value"], 1, "owner")
    assert "profile:gaming" not in {p["entity"] for p in await c.profiles()}
    with pytest.raises(ValueError, match="archived"):
        await c.resolve(packet)
    with pytest.raises(ValueError):
        await c.save("profile:warm", {**base, "archived": True}, 1, "owner")
    with pytest.raises(ValueError):
        await c.save(
            "profile:bad",
            {**base, "voice_overrides": {"caller_number": "+12025550123"}},
            0,
            "owner",
        )
    with pytest.raises(ValueError):
        await c.save(
            "profile:bad", {**base, "voice_overrides": {"dialogue_temperature": 99}}, 0, "owner"
        )
    with pytest.raises(ValueError):
        await c.save("secrets", {"key": "secret"}, 0, "owner")


@pytest.mark.parametrize(
    "header,value,status",
    [("x-control-bridge", "wrong", 401), ("tailscale-user-login", "other@example.test", 403)],
)
async def test_admin_auth_rejects_spoofed_identity(panel, header, value, status):
    assert (
        await panel.client.get("/admin/api/profiles", headers={header: value})
    ).status_code == status


@pytest.mark.parametrize("headers", [{"origin": "https://evil.test"}, {"x-csrf-token": "wrong"}])
async def test_configuration_mutation_requires_same_origin_and_csrf(panel, headers):
    response = await panel.client.put(
        "/admin/api/configuration/guide",
        headers=headers,
        json={"expected_revision": 1, "value": {"text": "Unwanted change"}},
    )
    assert response.status_code == 403
    assert (await panel.h.service.control.current("guide"))["revision"] == 1


async def test_control_is_disabled_without_explicit_private_access(panel):
    panel.h.service.config.dashboard_config_file = None
    assert (await panel.client.get("/admin/api/session")).status_code == 404


async def test_optional_client_link_and_configured_line_label(panel):
    path = panel.h.service.config.dashboard_config_file
    values = json.loads(path.read_text())
    values.pop("hermes_url")
    path.write_text(json.dumps(values))
    panel.h.service.config.line_label = "Reception line"
    response = (await panel.client.get("/admin/api/session")).json()
    assert response["hermes_url"] is None
    assert response["setup"]["source_line"] == "Reception line"
    assert response["setup"]["destination_policy"] == "configured_allowlist"


async def test_admin_configuration_history_preview_and_hermes_tools(panel):
    client = panel.client
    assert (await client.get("/admin/api/session")).headers["cache-control"] == "no-store"
    assert len((await client.get("/admin/api/profiles")).json()) == 4
    row = (await client.get("/admin/api/configuration/global")).json()
    row["value"]["voice"]["dialogue_temperature"] = 0.7
    # Refreshing the session above rotates the browser's CSRF cookie.
    response = await client.get("/admin/api/session")
    client.headers["x-csrf-token"] = response.json()["csrf"]
    saved = await client.put(
        "/admin/api/configuration/global", json={"expected_revision": 1, "value": row["value"]}
    )
    assert saved.status_code == 200 and saved.json()["revision"] == 2
    conflict = await client.put(
        "/admin/api/configuration/global", json={"expected_revision": 1, "value": row["value"]}
    )
    assert conflict.status_code == 409
    assert len((await client.get("/admin/api/configuration/global/history")).json()) == 2
    assert (await client.get("/admin/api/configuration/unknown")).status_code == 404
    preview = await client.post(
        "/admin/api/prompt-preview", json={"context": panel.h.packet.model_dump(mode="json")}
    )
    assert preview.status_code == 200
    assert preview.json()["configuration"]["voice"]["dialogue_temperature"] == 0.7
    assert panel.h.packet.target.phone not in preview.json()["prompt"]
    integration = (await client.get("/admin/api/integration")).json()
    assert "get_calling_setup" in [t["name"] for t in integration["tools"]]
    async with Client(panel.app.state.mcp) as mcp:
        setup = await mcp.call_tool("get_calling_setup", {})
        assert not setup.is_error
    assert (await client.get("/admin/api/status")).json()["last_hermes_request"]
    assert not panel.h.asterisk.dials


async def test_call_search_pages_notes_metrics_and_downloads(panel):
    h = panel.h
    await seed_call(h, turns=105)
    client = panel.client
    for query in ["ПРИВІТ", "reunión", h.packet.target.name, h.packet.target.phone]:
        result = (await client.get("/admin/api/calls", params={"q": query})).json()
        assert [c["call_id"] for c in result["calls"]] == ["call_control_test"]
        assert "context" not in result["calls"][0]
    assert not (await client.get("/admin/api/calls", params={"q": "' OR 1=1 --"})).json()["calls"]
    assert not (await client.get("/admin/api/calls", params={"state": "failed"})).json()["calls"]
    assert (
        await client.get(
            "/admin/api/calls",
            params={
                "language": "en",
                "profile": "warm",
                "start": "2020-01-01",
                "end": "2099-12-31",
            },
        )
    ).json()["calls"]
    first = (await client.get("/admin/api/calls/call_control_test")).json()
    second = (
        await client.get(
            "/admin/api/calls/call_control_test", params={"after": first["next_transcript_cursor"]}
        )
    ).json()
    assert len(first["transcript"]) == 100 and len(second["transcript"]) == 5
    assert first["configuration"]["profile_id"] == "warm"
    assert first["prompt"] == first["configuration"]["effective_prompt"]
    assert (
        await client.get("/admin/api/calls/call_control_test/files/recording.mp3")
    ).status_code == 404
    markdown = await client.get("/admin/api/calls/call_control_test/files/transcript.md")
    assert markdown.status_code == 200 and "Привіт" in markdown.text and "105." in markdown.text
    assert (await client.get("/admin/api/calls/missing")).status_code == 404
    assert (
        await client.get("/admin/api/calls/call_control_test/files/secrets.json")
    ).status_code == 422
    before = await h.service.db.get_transcript("call_control_test")
    note = await client.put(
        "/admin/api/calls/call_control_test/annotation",
        json={"note": "Clear voice", "tags": ["Natural", "Natural"]},
    )
    assert note.status_code == 204
    await h.service.db.control_measure(
        "call_control_test", {"seconds": 1.1, "measured_from": "server", "parts": []}
    )
    result = (await client.get("/admin/api/calls/call_control_test")).json()
    assert result["annotation"] == {"note": "Clear voice", "tags": ["Natural"]}
    assert result["latency"][0]["seconds"] == 1.1
    assert await h.service.db.get_transcript("call_control_test") == before


async def test_legacy_calls_have_no_invented_snapshot(panel):
    plan = await seed_call(panel.h)
    await panel.h.service.db.execute(
        "DELETE FROM plan_configurations WHERE plan_id=?", (plan.plan_id,)
    )
    result = (await panel.client.get("/admin/api/calls/call_control_test")).json()
    assert result["configuration"] is None and result["prompt"] is None


async def test_unsaved_practice_is_isolated_and_telephone_preempts_it_cleanly(panel):
    h = panel.h
    before = await h.service.control.current("profile:warm")
    custom = copy.deepcopy(before["value"])
    custom["prompt"] = "Ask about a fictional moon garden."
    result = await panel.client.post(
        "/admin/api/practice",
        json={
            "mode": "text",
            "context": h.packet.model_dump(mode="json"),
            "overrides": {"profile": custom},
        },
    )
    assert result.status_code == 200
    practice = result.json()
    assert (
        practice["configuration"]["unsaved"]
        and "moon garden" in practice["configuration"]["personality_prompt"]
    )
    assert (await h.service.control.current("profile:warm")) == before
    with respx.mock as mock:
        mock.post("https://openrouter.ai/api/v1/chat/completions").respond(
            200, json={"choices": [{"message": {"content": "Tell me about the garden."}}]}
        )
        reply = await panel.client.post(
            "/admin/api/practice/" + practice["id"] + "/text", json={"text": "Hello"}
        )
    assert reply.json()["messages"][-1]["text"] == "Tell me about the garden."
    assert (await panel.client.get("/admin/api/practice/" + practice["id"])).json()[
        "state"
    ] == "ready"
    assert not await h.service.db.list_calls()
    assert not h.asterisk.dials
    plan = await prepare(h)
    started = await panel.client.post(
        "/admin/api/calls/start",
        json={
            "plan_id": plan.plan_id,
            "explicit_confirmation": True,
            "confirmation_text": plan.confirmation_summary,
        },
    )
    assert started.status_code == 200 and h.service.control.practice.active is None
    assert "telephone" in h.service.control.practice.get(practice["id"]).error
    snapshot = await h.service.db.control_plan(plan.plan_id)
    assert "moon garden" not in snapshot["personality_prompt"]


async def test_busy_practice_refuses_deployment_without_leaving_a_lease(android):
    h = android
    practice = await h.service.control.practice.create("text", h.packet)
    assert await h.service.acquire_deployment_lock() == 1
    assert not await h.service.db.deployment_lock_is_active()
    await h.service.control.practice.close(practice["id"])
    again = await h.service.control.practice.create("text", h.packet)
    await h.service.control.practice.close(again["id"])
    assert await h.service.acquire_deployment_lock() == 0
    assert await h.service.db.deployment_lock_is_active()
    await h.service.release_deployment_lock()
    assert not await h.service.db.list_calls()
    assert not h.asterisk.dials


async def test_practice_provider_failure_releases_capacity(panel):
    result = await panel.client.post(
        "/admin/api/practice",
        json={"mode": "text", "context": panel.h.packet.model_dump(mode="json")},
    )
    session_id = result.json()["id"]
    with respx.mock as mock:
        mock.post("https://openrouter.ai/api/v1/chat/completions").respond(503)
        reply = await panel.client.post(
            "/admin/api/practice/" + session_id + "/text", json={"text": "Hello"}
        )
    assert reply.json()["state"] == "failed"
    assert panel.h.service.control.practice.active is None
    assert not panel.h.asterisk.dials


async def test_session_refresh_keeps_other_tabs_valid(panel):
    first = panel.session["csrf"]
    second = (await panel.client.get("/admin/api/session")).json()["csrf"]
    assert first == second
    assert (
        await panel.client.put(
            "/admin/api/configuration/guide",
            json={"expected_revision": 1, "value": {"text": "Updated from the first tab"}},
        )
    ).status_code == 200


async def test_call_pages_handle_equal_timestamps_and_reject_bad_cursor(panel):
    await seed_call(panel.h, "call_equal_a")
    # A terminal call does not consume capacity for this independent historical fixture.
    plan = await prepare(panel.h)
    await panel.h.service.db.claim_plan_and_create_call(
        plan_id=plan.plan_id,
        call_id="call_equal_b",
        conference_name="second-history",
        confirmation_text=plan.confirmation_summary,
    )
    await panel.h.service.db.execute(
        "UPDATE calls SET state='completed',created_at='2026-10-24T08:00:00+00:00'"
    )
    first = (await panel.client.get("/admin/api/calls?limit=1")).json()
    second = (
        await panel.client.get(
            "/admin/api/calls", params={"limit": 1, "cursor": first["next_cursor"]}
        )
    ).json()
    assert first["calls"][0]["call_id"] == "call_equal_b"
    assert second["calls"][0]["call_id"] == "call_equal_a"
    assert second["next_cursor"] is None
    assert (await panel.client.get("/admin/api/calls?cursor=not-a-cursor")).status_code == 422


async def test_authenticated_recording_supports_byte_ranges(panel):
    from unittest.mock import AsyncMock

    from tests.test_call_artifacts import sample_wav

    await seed_call(panel.h)
    panel.h.service.artifacts.recording_enabled = True
    panel.h.service.artifacts.mark_recording_start("call_control_test")

    async def download(call_id, path):
        path.write_bytes(sample_wav())
        return True

    panel.h.asterisk.download_recording = AsyncMock(side_effect=download)
    result = await panel.client.get(
        "/admin/api/calls/call_control_test/files/recording.mp3", headers={"Range": "bytes=0-127"}
    )
    assert result.status_code == 206
    assert result.headers["content-range"].startswith("bytes 0-127/")
    assert len(result.content) == 128 and result.content.startswith(b"ID3")
    assert "no-store" in result.headers["cache-control"]
    assert (
        await panel.client.get(
            "/admin/api/calls/call_control_test/files/recording.mp3",
            headers={"x-control-bridge": "wrong"},
        )
    ).status_code == 401


async def test_practice_finish_call_never_sets_a_real_opt_out(panel):
    response = await panel.client.post(
        "/admin/api/practice",
        json={"mode": "text", "context": panel.h.packet.model_dump(mode="json")},
    )
    session_id = response.json()["id"]
    with respx.mock as mock:
        mock.post("https://openrouter.ai/api/v1/chat/completions").respond(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "finish_call",
                                        "arguments": json.dumps(
                                            {"farewell": "Goodbye.", "do_not_call": True}
                                        ),
                                    }
                                }
                            ],
                        }
                    }
                ]
            },
        )
        result = await panel.client.post(
            "/admin/api/practice/" + session_id + "/text", json={"text": "Stop calling me."}
        )
    assert result.json()["state"] == "stopped"
    assert not await panel.h.service.db.is_destination_suppressed(panel.h.packet.target.phone)
    assert not panel.h.asterisk.dials


@pytest.mark.parametrize("end_from_dashboard", [False, True])
async def test_voice_practice_disconnect_closes_provider_and_releases_capacity(
    android, monkeypatch, end_from_dashboard
):
    from fastapi import WebSocket

    import app.android_voice as voice_module

    h = android
    stopped = asyncio.Event()

    class PracticeVoice:
        def __init__(self, websocket, **kwargs):
            assert kwargs["browser"] is True
            self.websocket = websocket
            self.callbacks = kwargs["callbacks"]

        async def run(self):
            self.callbacks.transcript("assistant", "Hello.", "practice", False)
            await self.websocket.receive()

        async def stop(self):
            stopped.set()

    monkeypatch.setattr(voice_module, "AndroidVoiceSession", PracticeVoice)
    practice = await h.service.control.practice.create("voice", h.packet)
    incoming = asyncio.Queue()
    sent = []

    async def receive():
        return await incoming.get()

    async def send(value):
        sent.append(value)

    ws = WebSocket({"type": "websocket", "path": "/practice", "headers": []}, receive, send)
    await incoming.put({"type": "websocket.connect"})
    task = asyncio.create_task(h.service.control.practice.voice(practice["id"], ws))
    for _ in range(100):
        if sent:
            break
        await asyncio.sleep(0.01)
    assert sent[0]["type"] == "websocket.accept"
    if end_from_dashboard:
        await asyncio.wait_for(h.service.control.practice.close(practice["id"]), 3)
    else:
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 3)
    assert stopped.is_set() and h.service.control.practice.active is None
    assert h.service.control.practice.get(practice["id"]).state == "stopped"
    assert not h.asterisk.dials


async def test_practice_respects_deployment_and_budget_before_reserving_slot(android):
    h = android
    await h.service.acquire_deployment_lock()
    with pytest.raises(ValueError, match="updated"):
        await h.service.control.practice.create("text", h.packet)
    await h.service.release_deployment_lock()
    with pytest.raises(ValueError, match="duration"):
        await h.service.control.practice.create(
            "text", h.packet.model_copy(update={"max_duration_seconds": 7200})
        )
    assert h.service.control.practice.active is None


async def test_phone_routes_require_confirmation_and_surface_missing_details(panel):
    request = {"context": panel.h.packet.model_dump(mode="json"), "requested_by_owner": False}
    result = await panel.client.post("/admin/api/calls/prepare", json=request)
    assert result.status_code == 200 and result.json()["plan_id"] is None
    request.update(
        requested_by_owner=True, authority_basis="Owner requested this isolated unit test"
    )
    plan = (await panel.client.post("/admin/api/calls/prepare", json=request)).json()
    refused = await panel.client.post(
        "/admin/api/calls/start",
        json={
            "plan_id": plan["plan_id"],
            "confirmation_text": plan["confirmation_summary"],
            "explicit_confirmation": False,
        },
    )
    assert refused.status_code == 409 and not panel.h.asterisk.dials
    assert (await panel.client.get("/admin/api/configuration/unknown/history")).status_code == 404
    assert (
        await panel.client.post(
            "/admin/api/prompt-preview",
            json={"context": panel.h.packet.model_dump(mode="json"), "overrides": {"global": {}}},
        )
    ).status_code == 422
    assert (
        await panel.client.put("/admin/api/calls/unknown/annotation", json={"note": "Test"})
    ).status_code == 404


async def test_provider_catalogs_are_filtered_cached_and_keep_keys_private(panel, tmp_path):
    key = tmp_path / "inworld-key"
    key.write_text("private-test-provider-value-long-enough")
    panel.h.service.config.inworld_key_file = key
    panel.h.service.config.soniox_key_file = key
    with respx.mock as mock:
        voices = mock.get("https://api.inworld.ai/voices/v1/voices").respond(
            200,
            json={
                "voices": [
                    {
                        "voiceId": "Wade",
                        "displayName": "Wade",
                        "description": "Conversational",
                        "languageCode": "en-US",
                    }
                ]
            },
        )
        models = mock.get("https://openrouter.ai/api/v1/models").respond(
            200,
            json={
                "data": [
                    {
                        "id": "example/tool-model",
                        "name": "Tool model",
                        "supported_parameters": ["tools", "tool_choice", "temperature"],
                    },
                    {
                        "id": "openai/gpt-6-luna",
                        "name": "GPT-6 Luna",
                        "supported_parameters": ["tools", "tool_choice", "reasoning"],
                        "reasoning": {"mandatory": False, "supported_efforts": ["none", "low"]},
                    },
                    {"id": "example/unsupported", "supported_parameters": []},
                ]
            },
        )
        speech = mock.get("https://api.soniox.com/v1/models").respond(
            200,
            json={
                "models": [
                    {
                        "id": "stt-rt-v5",
                        "name": "Real-time v5",
                        "transcription_mode": "real_time",
                        "supports_max_endpoint_delay": True,
                        "supports_endpoint_sensitivity": True,
                        "supports_endpoint_latency_adjustment": True,
                        "endpoint_latency_adjustment_max_level": 3,
                    },
                    {"id": "stt-async-v5", "transcription_mode": "async"},
                ]
            },
        )
        first = await panel.client.get("/admin/api/catalog")
        second = await panel.client.get("/admin/api/catalog")
    assert first.status_code == 200 and first.json() == second.json()
    assert first.json()["voices"][0]["id"] == "Wade"
    assert {m["id"] for m in first.json()["models"]} == {"example/tool-model", "openai/gpt-6-luna"}
    assert [m["id"] for m in first.json()["stt_models"]] == ["stt-rt-v5"]
    assert speech.call_count == 1
    assert len(first.json()["tts_models"]) == 2
    assert voices.call_count == 1 and models.call_count == 1
    assert key.read_text() not in first.text and "Authorization" not in first.text


async def test_catalog_failure_does_not_change_saved_settings(panel):
    before = await panel.h.service.control.current("global")
    with respx.mock as mock:
        mock.get("https://openrouter.ai/api/v1/models").respond(503)
        response = await panel.client.get("/admin/api/catalog")
    assert response.status_code == 200 and response.json()["errors"]
    assert await panel.h.service.control.current("global") == before


@pytest.mark.parametrize("model", ["openai/gpt-6-luna", "anthropic/claude-haiku-5.5"])
async def test_model_capabilities_and_nullable_temperature_are_frozen_for_future_calls(
    android, model
):
    from app.call_ai_inputs import model_options

    c = android.service.control
    old_plan = await prepare(android)
    old_snapshot = await android.service.db.control_plan(old_plan.plan_id)
    row = await c.current("global")
    row["value"]["voice"].update(
        dialogue_model=model,
        dialogue_provider=None,
        dialogue_fallback_providers=[],
        dialogue_temperature=None,
    )
    await c.save("global", row["value"], row["revision"], "owner")
    new_plan = await prepare(android)
    frozen = await android.service.db.control_plan(new_plan.plan_id)
    assert frozen["model_capabilities"]["id"] == model
    assert "temperature" not in model_options(c.runtime_config(frozen))
    assert model_options(c.runtime_config(frozen))["reasoning"] == {"effort": "none"}
    assert await android.service.db.control_plan(old_plan.plan_id) == old_snapshot
    row = await c.current("global")
    row["value"]["voice"]["dialogue_max_tokens"] = 1024
    await c.save("global", row["value"], row["revision"], "owner")
    assert model_options(c.runtime_config(frozen))["max_tokens"] == 512


async def test_mandatory_reasoning_is_rejected_until_owner_selects_supported_level(panel):
    row = (await panel.client.get("/admin/api/configuration/global")).json()
    row["value"]["voice"].update(dialogue_model="google/gemini-3.8-flash", reasoning_effort="none")
    response = await panel.client.put(
        "/admin/api/configuration/global",
        json={"expected_revision": row["revision"], "value": row["value"]},
    )
    assert response.status_code == 422
    assert "does not support reasoning" in response.text
    row["value"]["voice"].update(reasoning_effort="low", dialogue_max_tokens=4096)
    response = await panel.client.put(
        "/admin/api/configuration/global",
        json={"expected_revision": row["revision"], "value": row["value"]},
    )
    assert response.status_code == 200
    snapshot = await panel.h.service.control.resolve(panel.h.packet)
    panel.h.service.control.freeze_inputs(panel.h.packet, snapshot)
    assert snapshot["ai_inputs"]["model_options"]["reasoning"] == {"effort": "low"}
    assert snapshot["ai_inputs"]["model_options"]["max_tokens"] == 4096


async def test_practice_choices_override_profile_without_saving_or_dialing(panel):
    panel.h.service.config.tts_transport = "inworld"
    before = await panel.h.service.control.current("global")
    response = await panel.client.post(
        "/admin/api/prompt-preview",
        json={
            "context": panel.h.packet.model_dump(mode="json"),
            "overrides": {
                "voice": {
                    "dialogue_model": "openai/gpt-6-luna",
                    "dialogue_temperature": None,
                    "dialogue_provider": None,
                    "dialogue_fallback_providers": [],
                    "inworld_voice": "Ashley",
                    "inworld_model": "inworld-tts-2-flash",
                }
            },
        },
    )
    assert response.status_code == 200
    inputs = response.json()["configured"]
    assert inputs["model_options"]["model"] == "openai/gpt-6-luna"
    assert "temperature" not in inputs["model_options"]
    assert inputs["speech_settings"]["voice"] == "Ashley"
    assert inputs["speech_settings"]["delivery_mode"] is None
    assert await panel.h.service.control.current("global") == before
    assert not panel.h.asterisk.dials


async def test_unknown_models_fail_closed_and_cached_capabilities_survive_provider_failure(android):
    catalog = android.service.control.catalog
    with respx.mock as mock:
        route = mock.get("https://openrouter.ai/api/v1/models").respond(
            200,
            json={
                "data": [
                    {
                        "id": "example/no-temperature",
                        "name": "Test",
                        "supported_parameters": ["tools", "tool_choice"],
                        "reasoning": {},
                    }
                ]
            },
        )
        model = await catalog.model("example/no-temperature")
        assert "temperature" not in model["supported_parameters"]
        route.respond(503)
        catalog._cache["models"] = (0, catalog._cache["models"][1])
        assert await catalog.model("example/no-temperature") == model
        with pytest.raises(ValueError, match="Model unavailable"):
            await catalog.model("example/missing")


async def test_non_reasoning_model_does_not_accept_thinking_and_speech_models_are_validated(
    android,
):
    c = android.service.control
    row = await c.current("global")
    row["value"]["voice"].update(dialogue_model="openai/gpt-4.1-mini", reasoning_effort="low")
    with pytest.raises(ValueError, match="does not support reasoning"):
        await c.save("global", row["value"], row["revision"], "owner")
    row["value"]["voice"].update(reasoning_effort="none", inworld_model="invented-model")
    with pytest.raises(ValueError, match="Select Inworld"):
        await c.save("global", row["value"], row["revision"], "owner")
