from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from app import control_legacy
from app.call_planning import claim_approved_call, confirmation_summary
from app.control_migration import migrate_contract
from app.control_models import DEFAULT_CALLING_GUIDE
from app.mcp_tools import register_tools
from app.models import BusinessContext
from app.voice_prompts import DEFAULT_CONVERSATION, voice_prompt_sections
from tests import test_android_calls as phone_tests
from tests import test_control_panel as control_tests

android = phone_tests.android
panel = control_tests.panel


async def test_migration_is_revisioned_preserves_history_and_custom_values(android):
    c, db = android.service.control, android.service.db
    current = await c.current("global")
    old = copy.deepcopy(current["value"])
    old.pop("prompt_contract_version")
    old["prompts"].update(
        conversation=control_legacy.DEFAULT_CONVERSATION,
        opening="Owner's custom opening",
        intentions={"sales": "RETIRED PURPOSE"},
    )
    await db.control_save("global", old, 1, "old release")
    await db.control_save("guide", {"text": control_legacy.DEFAULT_CALLING_GUIDE}, 1, "old release")
    old_profile = (await c.current("profile:salesperson"))["value"]
    old_profile["prompt"] = control_legacy.PERSONALITIES["salesperson"]
    await db.control_save("profile:salesperson", old_profile, 1, "old release")
    before = copy.deepcopy(await db.control_history("global"))
    await migrate_contract(db)
    current = await c.current("global")
    assert current["revision"] == 3
    assert current["value"]["prompts"] == {
        "conversation": DEFAULT_CONVERSATION,
        "opening": "Owner's custom opening",
    }
    assert (await db.control_history("global"))[1:] == before
    assert (await c.current("guide"))["value"]["text"] == DEFAULT_CALLING_GUIDE
    await migrate_contract(db)
    assert (await c.current("global"))["revision"] == 3
    restored = await c.save("global", old, 3, "owner")
    assert restored["revision"] == 4 and "intentions" not in restored["value"]["prompts"]
    assert "RETIRED PURPOSE" not in c.prompt(android.packet, await c.resolve(android.packet))


async def test_migration_preserves_custom_global_guide_and_style(android):
    c, db = android.service.control, android.service.db
    current = await c.current("global")
    old = current["value"]
    old.pop("prompt_contract_version")
    old["prompts"]["conversation"] = "CUSTOM GLOBAL"
    await db.control_save("global", old, 1, "owner")
    await db.control_save("guide", {"text": "CUSTOM GUIDE"}, 1, "owner")
    profile = (await c.current("profile:warm"))["value"]
    profile["prompt"] = "CUSTOM STYLE"
    await db.control_save("profile:warm", profile, 1, "owner")
    await migrate_contract(db)
    assert (await c.current("global"))["value"]["prompts"]["conversation"] == "CUSTOM GLOBAL"
    assert (await c.current("guide"))["value"]["text"] == "CUSTOM GUIDE"
    assert (await c.current("profile:warm"))["value"]["prompt"] == "CUSTOM STYLE"


async def test_legacy_plan_confirmation_and_snapshot_survive_migration(android):
    h = android
    snapshot = {
        "effective_prompt": "EXACT LEGACY PURPOSE AND PROMPT",
        "prompts": {"intentions": {"sales": "old purpose"}},
    }
    await h.service.db.create_plan(
        "plan_legacy",
        h.packet.model_dump(mode="json"),
        "owner",
        datetime.now(UTC) + timedelta(minutes=5),
        configuration_snapshot=snapshot,
    )
    await h.service.control.initialize()
    assert await h.service.db.control_plan("plan_legacy") == snapshot
    assert h.service.control.prompt(h.packet, snapshot) == snapshot["effective_prompt"]
    approved = await claim_approved_call(
        h.service.db,
        plan_id="plan_legacy",
        explicit_confirmation=True,
        confirmation_text=confirmation_summary(h.packet),
    )
    assert approved.plan_id == "plan_legacy"
    assert not h.asterisk.dials


async def test_new_plan_freezes_recipient_task_criteria_prompt_and_exact_confirmation(android):
    h = android
    h.packet.target.background = "Sam manages Example Cafe; met the owner last week."
    h.packet.completion_criteria = "Confirm the opening time, including day and timezone."
    plan = await phone_tests.prepare(h)
    snapshot = await h.service.db.control_plan(plan.plan_id)
    assert snapshot["prompt_contract_version"] == 2
    assert snapshot["phone_menu_version"] == 1
    assert "Automated phone menus:" in snapshot["effective_prompt"]
    assert {t["function"]["name"] for t in snapshot["ai_inputs"]["tools"]} >= {
        "send_dtmf",
        "wait_for_phone_menu",
    }
    assert snapshot["confirmation_summary"] == plan.confirmation_summary
    assert "Intention:" not in plan.confirmation_summary
    assert h.packet.target.background in snapshot["effective_prompt"]
    assert h.packet.completion_criteria in snapshot["effective_prompt"]
    assert snapshot["approved_brief"]["target"]["background"] == h.packet.target.background
    h.packet.target.background = "Changed after preparation"
    h.packet.objective = "Another task"
    h.packet.completion_criteria = None
    assert await h.service.db.control_plan(plan.plan_id) == snapshot
    with pytest.raises(ValueError, match="confirmation_mismatch"):
        await claim_approved_call(
            h.service.db,
            plan_id=plan.plan_id,
            explicit_confirmation=True,
            confirmation_text="Different summary",
        )
    approved = await claim_approved_call(
        h.service.db,
        plan_id=plan.plan_id,
        explicit_confirmation=True,
        confirmation_text=plan.confirmation_summary,
    )
    assert approved.context.target.background == snapshot["approved_brief"]["target"]["background"]


def test_personality_and_legacy_category_do_not_change_recipient_or_task(packet):
    packet.target.background = "Known recipient; do not infer authority"
    packet.completion_criteria = "Obtain an agreed time"
    parts = voice_prompt_sections(packet)
    data = {p["id"]: p["text"] for p in parts}
    for style in ["warm", "playful", "professional", "salesperson"]:
        for legacy in ["task", "sales", "prank", "roleplay", "conversation"]:
            variant = packet.model_copy(update={"personality": style, "intention": legacy})
            changed = {p["id"]: p["text"] for p in voice_prompt_sections(variant)}
            assert changed["task"] == data["task"]
            assert changed["recipient"] == data["recipient"]
            assert "Call intention" not in "".join(changed.values())
            assert packet.target.phone not in "".join(changed.values())


async def test_business_requires_permission_without_a_purpose_selector(android):
    android.packet.business = BusinessContext(name="Example")
    result = await phone_tests.prepare(android)
    assert result.plan_id is None and "business.contact_permission" in result.missing_fields


async def test_lost_start_is_recovered_by_plan_without_another_dial(panel):
    h = panel.h
    plan = await phone_tests.prepare(h)
    result = await panel.client.get("/admin/api/plans/" + plan.plan_id)
    assert result.json()["state"] == "prepared" and result.json()["call_id"] is None
    approved = await claim_approved_call(
        h.service.db,
        plan_id=plan.plan_id,
        explicit_confirmation=True,
        confirmation_text=plan.confirmation_summary,
    )
    mcp = FastMCP("recovery-test")
    register_tools(mcp, lambda: h.service, android_backend=True)
    tools = {t.name: t for t in await mcp.list_tools()}
    for _ in range(3):
        recovered = await tools["get_phone_call"].fn(plan_id=plan.plan_id)
        assert recovered["call_id"] == approved.call_id and recovered["state"] == "prewarming"
        assert (await panel.client.get("/admin/api/plans/" + plan.plan_id)).json()[
            "call_id"
        ] == approved.call_id
    assert len(await h.service.db.fetch_all("SELECT call_id FROM calls")) == 1
    assert not h.asterisk.dials
    for args in [{}, {"plan_id": plan.plan_id, "call_id": approved.call_id}]:
        with pytest.raises(ToolError, match="exactly one"):
            await tools["get_phone_call"].fn(**args)
    assert (await panel.client.get("/admin/api/plans/missing")).status_code == 404


async def test_unused_expired_plan_lookup_never_dials(android):
    plan = await phone_tests.prepare(android)
    await android.service.db.execute(
        "UPDATE plans SET expires_at=? WHERE plan_id=?",
        ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), plan.plan_id),
    )
    status = await android.service.get_plan_status(plan.plan_id)
    assert status["state"] == "expired" and status["call_id"] is None
    assert not android.asterisk.dials


async def test_saved_guide_is_seen_by_next_mcp_read_without_restart(panel):
    tools = {t.name: t for t in await panel.app.state.mcp.list_tools()}
    first = await tools["get_calling_setup"].fn()
    result = await panel.client.put(
        "/admin/api/configuration/guide",
        json={
            "expected_revision": first["guide_revision"],
            "value": {"text": "Ask for a preferred callback window."},
        },
    )
    assert result.status_code == 200
    second = await tools["get_calling_setup"].fn()
    assert second["guide_revision"] == first["guide_revision"] + 1
    assert second["guide"] == "Ask for a preferred callback window."
    assert "background" in json.dumps(tools["prepare_phone_call"].parameters)


async def test_contract_migration_conflict_rolls_back_all_revisions(android):
    from app.db.control import RevisionConflict

    c = android.service.control
    global_record, guide = await c.current("global"), await c.current("guide")
    await c.save("guide", {"text": "Concurrent owner edit"}, guide["revision"], "owner")
    with pytest.raises(RevisionConflict):
        await android.service.db.control_migrate(
            [
                (
                    "global",
                    {**global_record["value"], "prompt_contract_version": 2},
                    global_record["revision"],
                ),
                ("guide", {"text": "Stale migration"}, guide["revision"]),
            ]
        )
    assert await c.current("global") == global_record
    assert (await c.current("guide"))["value"]["text"] == "Concurrent owner edit"


def test_callback_default_is_visible_and_explicit_limits_still_apply(packet):
    from app.call_planning import task_confirmation_summary
    from app.voice_prompts import voice_instructions

    packet.prohibited_actions = ["Do not call back"]
    prompt = voice_instructions(packet)
    assert "check and call back unless the brief forbids" in prompt
    assert "Do not call back" in prompt
    assert "Explicit limits override this default" in prompt
    assert "routine callback on the same task unless prohibited" in task_confirmation_summary(
        packet
    )
