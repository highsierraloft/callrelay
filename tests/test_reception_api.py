from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from app.reception_mcp import REVIEW_TOOL_NAMES, register_reception_tools
from tests import test_android_calls as phone_tests
from tests import test_control_panel as panel_tests
from tests.test_callback_scheduling import proposal
from tests.test_reception_memory import completed

android = phone_tests.android
panel = panel_tests.panel


async def test_scoped_settings_share_atomic_revision_without_changing_other_values(panel):
    client = panel.client
    before = (await client.get("/admin/api/configuration/global")).json()
    outbound = (await client.get("/admin/api/configuration/outbound")).json()
    voice = (await client.get("/admin/api/configuration/voice_defaults")).json()
    changed = {**outbound["value"], "conversation": "Keep the approved task clear."}
    saved = await client.put(
        "/admin/api/configuration/outbound",
        json={"expected_revision": outbound["revision"], "value": changed},
    )
    assert saved.status_code == 200
    assert (await client.get("/admin/api/configuration/global")).json()["value"]["voice"] == before[
        "value"
    ]["voice"]
    stale = await client.put(
        "/admin/api/configuration/voice_defaults",
        json={"expected_revision": voice["revision"], "value": voice["value"]},
    )
    assert stale.status_code == 409
    history = (await client.get("/admin/api/configuration/outbound/history")).json()
    assert history[0]["value"] == changed and "voice" not in history[0]["value"]


async def test_contacts_api_history_memory_conflicts_and_private_boundaries(panel):
    client = panel.client
    body = {
        "expected_revision": 0,
        "name": "Maria",
        "background": "Runs a cafe",
        "private_note": "Owner confidential note",
        "numbers": ["+1 415 555 0120"],
    }
    response = await client.post("/admin/api/contacts", json=body)
    assert response.status_code == 200
    contact = response.json()
    cid = contact["contact_id"]
    assert contact["numbers"][0]["phone"] == "+14155550120"
    assert contact["private_note"] == body["private_note"]
    assert "Owner confidential note" not in json.dumps(
        (await client.get("/admin/api/contacts?search=Maria")).json()
    )
    save = {"expected_revision": 0, "facts": [{"key": "preference", "text": "Prefers afternoons"}]}
    assert (await client.put(f"/admin/api/contacts/{cid}/memory", json=save)).status_code == 200
    assert (await client.put(f"/admin/api/contacts/{cid}/memory", json=save)).status_code == 409
    assert (await client.put(f"/admin/api/contacts/{cid}", json=body)).status_code == 409
    assert (
        await client.get(f"/admin/api/contacts/{cid}", headers={"x-control-bridge": "wrong"})
    ).status_code == 401
    assert (
        await client.post("/admin/api/contacts", json=body, headers={"x-csrf-token": "wrong"})
    ).status_code == 403
    context = panel.h.packet.model_dump(mode="json")
    context["direction"] = "inbound"
    preview = await client.post(
        "/admin/api/prompt-preview",
        json={"mode": "text", "context": context, "overrides": {"simulation_contact_id": cid}},
    )
    assert preview.status_code == 200
    assert "Prefers afternoons" in preview.text and "Owner confidential note" not in preview.text
    assert "propose_callback" in preview.text
    assert not await panel.h.service.db.list_calls()
    assert not await panel.h.service.db.fetch_all("SELECT * FROM callback_schedules")


async def test_unknown_shared_and_different_recipient_snapshots_do_not_expose_wrong_memory(android):
    contacts = android.service.control.contacts
    from app.reception_models import ContactWrite, MemoryWrite

    a = await contacts.save(
        None, ContactWrite(expected_revision=0, name="Alice", numbers=[android.packet.target.phone])
    )
    await contacts.save_memory(
        a["contact_id"],
        MemoryWrite(
            expected_revision=0, facts=[{"key": "preference", "text": "Alice's preferred topic"}]
        ),
    )
    assert (await contacts.snapshot(android.packet.target.phone, "Bob"))["facts"] == []
    assert (await contacts.snapshot(None))["recognition"] == "withheld"
    assert (await contacts.snapshot("+14155550999"))["recognition"] == "new"
    await contacts.save(
        None, ContactWrite(expected_revision=0, name="Bob", numbers=[android.packet.target.phone])
    )
    shared = await contacts.snapshot(android.packet.target.phone)
    assert shared["recognition"] == "shared" and shared["facts"] == []


async def test_owner_callback_schedule_and_cancel_api_has_no_immediate_dial(panel):
    h = panel.h
    _, body = await proposal(h)
    response = await panel.client.post(
        "/admin/api/calls/call_memory/callbacks", json=body.model_dump(mode="json")
    )
    assert response.status_code == 200
    receipt = response.json()
    assert receipt["saved"] and receipt["scheduled"]
    assert not h.asterisk.dials
    assert len((await panel.client.get("/admin/api/callbacks")).json()["callbacks"]) == 1
    cancelled = await panel.client.put(
        "/admin/api/callbacks/" + receipt["schedule_id"],
        json={"expected_revision": 1, "action": "cancel"},
    )
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
    assert not (await panel.client.get("/admin/api/callbacks")).json()["callbacks"]
    assert (await panel.client.get("/admin/api/callbacks/missing")).status_code == 404


async def test_hermes_review_tools_are_scoped_and_guide_edits_are_immediate(android):
    h = android
    mcp = FastMCP("reception-tools")
    register_reception_tools(mcp, lambda: h.service, None)
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    assert set(REVIEW_TOOL_NAMES) <= set(tools)
    assert not any("start" in name or "schedule" in name for name in REVIEW_TOOL_NAMES)
    queue = await tools["calling_review_queue"].fn()
    assert queue == {"pending": 0, "wakeAgent": False}
    link = await completed(h.service.db, h.packet)
    await h.service.control.contacts.request_review(link["call_id"])
    assert (await tools["calling_review_queue"].fn())["wakeAgent"] is True
    job = (await tools["claim_call_review"].fn())["job"]
    page = await tools["read_call_review"].fn(job["job_id"], job["lease_token"])
    assert len(page["transcript"]) == 3 and page["next_offset"] is None
    assert "private_note" not in json.dumps(page)
    assert (
        await tools["release_call_review"].fn(
            job["job_id"], job["lease_token"], "provider unavailable"
        )
    )["released"]
    with pytest.raises(ToolError):
        await tools["lookup_call_contact"].fn()
    guide = await h.service.control.current("guide")
    await h.service.control.save(
        "guide", {"text": "Owner's revised workflow"}, guide["revision"], "owner"
    )
    setup = await h.service.get_calling_setup()
    assert (
        setup["guide"] == "Owner's revised workflow"
        and setup["guide_revision"] == guide["revision"] + 1
    )


def precheck_module():
    path = Path(__file__).resolve().parents[1] / "deploy/hermes/review_precheck.py"
    spec = importlib.util.spec_from_file_location("review_precheck", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("pending", [0, 3])
def test_native_precheck_reads_only_queue_and_reuses_existing_auth(monkeypatch, pending):
    module = precheck_module()
    requests = []

    class Response:
        headers = {"Mcp-Session-Id": "test-session"}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self, _):
            if len(requests) == 1:
                return b'{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-03-26"}}'
            if len(requests) == 2:
                return b""
            return (
                "data: "
                + json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "result": {"structuredContent": {"pending": pending}},
                    }
                )
                + "\n\n"
            ).encode()

    def request(req, timeout):
        if req.get_method() == "DELETE":
            assert timeout == 3 and req.headers["Mcp-session-id"] == "test-session"
            requests.append({"method": "DELETE"})
            return Response()
        requests.append(json.loads(req.data))
        assert req.headers["Authorization"] == "Bearer private-test-value" and timeout == 8
        return Response()

    monkeypatch.setattr(module, "urlopen", request)
    assert (
        module.queue_available(
            {
                "url": "http://127.0.0.1/mcp/",
                "headers": {"Authorization": "Bearer private-test-value"},
            }
        )
        == pending
    )
    assert [r["method"] for r in requests] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
        "DELETE",
    ]
    assert requests[-2]["params"]["name"] == "calling_review_queue"
