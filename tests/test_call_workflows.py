from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.call_planning import claim_approved_call
from app.db.control import RevisionConflict
from app.models import PreparePhoneCallInput
from app.workflow_models import CallbackDraft, FollowupUpdate, OutcomeWrite
from tests import test_android_calls as phone_tests
from tests import test_control_panel as panel_tests

android = phone_tests.android
panel = panel_tests.panel


def outcome(**changes):
    return OutcomeWrite.model_validate(
        {
            "expected_revision": 0,
            "request_id": "result-one",
            "status": "incomplete",
            "explanation": "The price needs checking.",
            "evidence_turn_ids": ["t0"],
            "facts": [{"text": "The meeting is tomorrow.", "evidence_turn_ids": ["t0"]}],
            "callbacks": [
                {
                    "source_key": "price_callback",
                    "source": "agent_promise",
                    "objective": "Call back with the approved price.",
                    "needed_information": ["Ask the owner for the price"],
                    "evidence_turn_ids": ["t0"],
                }
            ],
            **changes,
        }
    )


def update(row, **changes):
    values = {
        k: row["value"].get(k)
        for k in [
            "objective",
            "needed_information",
            "window_start",
            "window_end",
            "timezone",
            "note",
            "completion_call_id",
        ]
    }
    return FollowupUpdate.model_validate(
        {**values, "expected_revision": row["revision"], "status": row["status"], **changes}
    )


async def test_outcome_and_callback_save_is_atomic_idempotent_and_keeps_transcript(android):
    h = android
    await panel_tests.seed_call(h)
    before = await h.service.db.get_transcript("call_control_test")
    w = h.service.control.workflows
    first = await w.save_outcome("call_control_test", outcome(), "hermes")
    again = await w.save_outcome("call_control_test", outcome(), "hermes")
    assert first == again and first["revision"] == 1
    rows = (await w.list_followups())["followups"]
    assert len(rows) == 1 and rows[0]["status"] == "needs_information"
    assert rows[0]["target"]["phone"] == h.packet.target.phone
    assert (await w.assessment("call_control_test"))["value"]["status"] == "incomplete"
    assert await h.service.db.get_transcript("call_control_test") == before
    assert not h.asterisk.dials
    with pytest.raises(ValueError, match="different content"):
        await w.save_outcome("call_control_test", outcome(explanation="Changed"), "hermes")


async def test_outcome_evidence_belongs_to_call_and_success_needs_evidence(android):
    h = android
    await panel_tests.seed_call(h)
    w = h.service.control.workflows
    for body in [
        outcome(evidence_turn_ids=["foreign"]),
        outcome(status="achieved", evidence_turn_ids=[]),
    ]:
        with pytest.raises(ValueError, match="evidence|Evidence"):
            await w.save_outcome("call_control_test", body, "hermes")
    assert await w.assessment("call_control_test") is None
    assert (await w.list_followups())["followups"] == []
    saved = await w.save_outcome(
        "call_control_test",
        outcome(status="achieved", evidence_turn_ids=[], facts=[], callbacks=[]),
        "owner",
    )
    assert saved["revision"] == 1


async def test_new_assessment_does_not_reset_existing_callback_and_conflicting_save_fails(android):
    h = android
    await panel_tests.seed_call(h)
    w = h.service.control.workflows
    await w.save_outcome("call_control_test", outcome(), "hermes")
    row = (await w.list_followups())["followups"][0]
    changed = await w.update_followup(
        row["followup_id"], update(row, status="cancelled", note="Owner withdrew it"), "owner"
    )
    await w.save_outcome(
        "call_control_test", outcome(expected_revision=1, request_id="result-two"), "hermes"
    )
    assert (await w.followup(row["followup_id"]))["status"] == "cancelled"
    assert len((await w.list_followups(status="all"))["followups"]) == 1
    with pytest.raises(RevisionConflict):
        await w.save_outcome(
            "call_control_test", outcome(expected_revision=1, request_id="result-three"), "hermes"
        )
    with pytest.raises(RevisionConflict):
        await w.update_followup(
            row["followup_id"], update(row, status="ready", needed_information=[]), "owner"
        )
    assert changed["revision"] == 2


@pytest.mark.parametrize("next_status", ["cancelled", "needs_information"])
async def test_followup_preparation_binds_original_recipient_and_cancellation_stops_old_plan(
    android,
    next_status,
):
    h = android
    await panel_tests.seed_call(h)
    w = h.service.control.workflows
    saved = await w.save_outcome("call_control_test", outcome(), "hermes")
    fid = saved["followup_ids"][0]
    pending = await w.followup(fid)
    with pytest.raises(ValueError, match="missing information"):
        await w.callback_brief(fid)
    await w.update_followup(
        fid,
        update(
            pending,
            status="ready",
            needed_information=[],
            new_facts=["Approved price: twenty euros"],
        ),
        "owner",
    )
    suggested = await w.callback_brief(fid)
    assert "Approved price: twenty euros" in suggested["context"]["relevant_facts"]
    assert suggested["dialed"] is False and "callback_number" not in suggested["context"]["owner"]
    request = PreparePhoneCallInput(
        context=suggested["context"],
        followup_id=fid,
        requested_by_owner=True,
        authority_basis="Owner approved this callback",
    )
    plan = await h.service.prepare(request)
    assert (await h.service.db.control_plan(plan.plan_id))["followup_id"] == fid
    assert (await w.followup(fid))["attempts"][0]["call_id"] is None
    changed = request.model_copy(deep=True)
    changed.context.target.phone = "+12025550188"
    with pytest.raises(ValueError):
        await w.validate_followup_plan(fid, changed.context)
    row = await w.followup(fid)
    await w.update_followup(fid, update(row, status=next_status), "owner")
    with pytest.raises(ValueError, match="plan_unavailable"):
        await claim_approved_call(
            h.service.db,
            plan_id=plan.plan_id,
            explicit_confirmation=True,
            confirmation_text=plan.confirmation_summary,
        )
    assert not h.asterisk.dials


async def test_hermes_completion_requires_a_linked_call_with_evidence_assessment(android):
    h = android
    await panel_tests.seed_call(h)
    w = h.service.control.workflows
    fid = (await w.save_outcome("call_control_test", outcome(), "hermes"))["followup_ids"][0]
    row = await w.followup(fid)
    with pytest.raises(ValueError, match="finished attempt"):
        await w.update_followup(
            fid,
            update(row, status="completed", note="Done", completion_call_id="call_control_test"),
            "hermes",
        )
    manual = await w.update_followup(
        fid, update(row, status="completed", note="Resolved directly with the owner"), "owner"
    )
    assert manual["status"] == "completed"


def test_callback_windows_never_invent_timezone_or_allow_unordered_ranges():
    base = {"source_key": "one", "objective": "Call back", "source": "owner_request"}
    with pytest.raises(ValidationError):
        CallbackDraft(**base, window_start=datetime.now())
    with pytest.raises(ValidationError):
        CallbackDraft(**base, window_start=datetime.now(UTC))
    with pytest.raises(ValidationError):
        CallbackDraft(
            **base,
            window_start=datetime.now(UTC),
            window_end=datetime.now(UTC) - timedelta(hours=1),
            timezone="Europe/Madrid",
        )
    assert CallbackDraft(**base).window_start is None


async def test_workflow_api_requires_owner_auth_and_rejects_stale_revision(panel):
    await panel_tests.seed_call(panel.h)
    path = "/admin/api/calls/call_control_test/outcome"
    assert (
        await panel.client.put(
            path, json=outcome().model_dump(mode="json"), headers={"x-csrf-token": "wrong"}
        )
    ).status_code == 403
    response = await panel.client.put(path, json=outcome().model_dump(mode="json"))
    assert response.status_code == 200
    fid = response.json()["followup_ids"][0]
    assert (await panel.client.get("/admin/api/followups/" + fid + "/brief")).status_code == 422
    details = (await panel.client.get("/admin/api/calls/call_control_test")).json()
    assert details["assessment"]["revision"] == 1 and len(details["followups"]) == 1
    stale = outcome(request_id="another").model_dump(mode="json")
    assert (await panel.client.put(path, json=stale)).status_code == 409


async def test_mcp_saves_outcome_and_recovers_same_callback_after_lost_response(android):
    from fastmcp import FastMCP
    from fastmcp.exceptions import ToolError

    from app.mcp_tools import register_tools

    h = android
    await panel_tests.seed_call(h)
    mcp = FastMCP("workflows")
    register_tools(mcp, lambda: h.service, android_backend=True)
    tools = {t.name: t for t in await mcp.list_tools()}
    assert {"save_call_outcome", "get_call_followups", "update_call_followup"} <= tools.keys()
    assert "followup_id" in tools["prepare_phone_call"].parameters["properties"]
    payload = outcome()
    first = await tools["save_call_outcome"].fn("call_control_test", payload)
    repeated = await tools["save_call_outcome"].fn("call_control_test", payload)
    assert first == repeated
    fid = first["followup_ids"][0]
    row = await tools["get_call_followups"].fn(followup_id=fid)
    assert "suggested_call" not in row
    row = await tools["update_call_followup"].fn(
        fid,
        update(
            row, status="ready", needed_information=[], new_facts=["Approved price is twenty euros"]
        ),
    )
    recovered = await tools["get_call_followups"].fn(followup_id=fid)
    assert recovered["suggested_call"]["followup_id"] == fid
    assert not h.asterisk.dials
    with pytest.raises(ToolError, match="not found"):
        await tools["get_call_followups"].fn(followup_id="missing")
    with pytest.raises(ToolError, match="offset"):
        await tools["get_call_followups"].fn(offset=-1)


async def test_hermes_closes_callback_only_after_linked_successful_assessment(android):
    from app.models import CallState

    h = android
    await panel_tests.seed_call(h)
    w = h.service.control.workflows
    fid = (await w.save_outcome("call_control_test", outcome(), "hermes"))["followup_ids"][0]
    row = await w.followup(fid)
    await w.update_followup(fid, update(row, status="ready", needed_information=[]), "owner")
    brief = await w.callback_brief(fid)
    plan = await h.service.prepare(
        PreparePhoneCallInput(
            context=brief["context"],
            followup_id=fid,
            requested_by_owner=True,
            authority_basis="Owner requested callback",
        )
    )
    claimed = await claim_approved_call(
        h.service.db,
        plan_id=plan.plan_id,
        explicit_confirmation=True,
        confirmation_text=plan.confirmation_summary,
    )
    call_id = claimed.call_id
    await h.service.db.update_call(call_id, state=CallState.COMPLETED.value)
    await h.service.db.add_transcript_turn(
        call_id=call_id,
        turn_id="reply",
        speaker="callee",
        text="Yes, that solves it, thanks.",
        source_event_type="test",
        source_event_id="reply",
    )
    row = await w.followup(fid)
    done = update(
        row,
        status="completed",
        completion_call_id=call_id,
        note="The recipient confirmed the answer resolved the question.",
    )
    with pytest.raises(ValueError, match="evidence-backed"):
        await w.update_followup(fid, done, "hermes")
    await w.save_outcome(
        call_id,
        OutcomeWrite(
            expected_revision=0,
            request_id="complete",
            status="achieved",
            explanation="The recipient confirmed resolution.",
            evidence_turn_ids=["reply"],
        ),
        "hermes",
    )
    assert (await w.update_followup(fid, done, "hermes"))["status"] == "completed"
