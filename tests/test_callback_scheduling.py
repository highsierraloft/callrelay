from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.db.control import RevisionConflict
from app.models import CallState
from app.reception_models import CallbackConfirmation, CallbackProposal, ScheduleUpdate
from tests import test_android_calls as phone_tests
from tests.test_reception_memory import completed

android = phone_tests.android


async def proposal(h, **changes):
    await completed(h.service.db, h.packet)
    policy = await h.service.control.current("callback_policy")
    await h.service.control.save(
        "callback_policy",
        {**policy["value"], "weekdays": list(range(7)), "opens": "00:00", "closes": "23:59"},
        policy["revision"],
        "owner",
    )
    start = datetime.now(UTC) + timedelta(minutes=2)
    # UTC is an explicit agreed timezone; policy hours are still evaluated in the workspace timezone.
    body = CallbackProposal(
        request_key="request",
        objective="Discuss the approved request",
        destination=h.packet.target.phone,
        window_start=start,
        window_end=start + timedelta(minutes=15),
        timezone="UTC",
        **changes,
    )
    row = await h.service.control.callbacks.propose("call_memory", body)
    return row, body


async def say(h, identifier, speaker, text, interrupted=False):
    await h.service.db.add_transcript_turn(
        call_id="call_memory",
        turn_id=identifier,
        speaker=speaker,
        text=text,
        source_event_type="test.interrupted" if interrupted else "test",
        source_event_id=identifier,
    )


async def confirmed(h, owner=False):
    row, body = await proposal(h)
    await say(h, "readback", "assistant", row["readback"])
    await say(h, "yes", "callee", "Yes please")
    confirmation = CallbackConfirmation(
        proposal_id=row["proposal_id"],
        proposal_revision=row["revision"],
        readback_turn_id="readback",
        confirmation_turn_id="yes",
    )
    receipt = await h.service.control.callbacks.confirm("call_memory", confirmation, owner=owner)
    return row, body, confirmation, receipt


async def test_confirmed_callback_receipt_is_idempotent_and_preserves_alternate_number(android):
    h = android
    row, body = await proposal(h)
    different = body.model_copy(update={"destination": "+14155550188"})
    row = await h.service.control.callbacks.propose("call_memory", different)
    assert row["revision"] == 2
    await say(h, "readback", "assistant", row["readback"])
    await say(h, "yes", "callee", "yes")
    confirmation = CallbackConfirmation(
        proposal_id=row["proposal_id"],
        proposal_revision=2,
        readback_turn_id="readback",
        confirmation_turn_id="yes",
    )
    first = await h.service.control.callbacks.confirm("call_memory", confirmation)
    assert first == await h.service.control.callbacks.confirm("call_memory", confirmation)
    assert first["destination"] == "+14155550188" and first["saved"]
    assert not await h.service.db.contact_lookup("+14155550188")
    assert not h.asterisk.dials


@pytest.mark.parametrize(
    "answer,interrupted", [("Yes but tomorrow instead", False), ("yes", True), ("no", False)]
)
async def test_corrections_interruptions_and_refusal_cannot_schedule(android, answer, interrupted):
    row, _ = await proposal(android)
    await say(android, "readback", "assistant", row["readback"], interrupted)
    await say(android, "yes", "callee", answer)
    with pytest.raises(ValueError, match="Read back"):
        await android.service.control.callbacks.confirm(
            "call_memory",
            CallbackConfirmation(
                proposal_id=row["proposal_id"],
                proposal_revision=1,
                readback_turn_id="readback",
                confirmation_turn_id="yes",
            ),
        )
    assert not (await android.service.control.callbacks.list())["callbacks"]


async def test_cancelled_before_final_claim_never_dials(android):
    h = android
    _, _, _, receipt = await confirmed(h)
    callbacks = h.service.control.callbacks
    await callbacks.update(
        receipt["schedule_id"], ScheduleUpdate(expected_revision=1, action="cancel")
    )
    with pytest.raises(RevisionConflict):
        await callbacks.update(
            receipt["schedule_id"], ScheduleUpdate(expected_revision=1, action="cancel")
        )
    await callbacks.tick()
    assert not h.asterisk.dials


async def make_due(h, identifier):
    now = datetime.now(UTC)
    await h.service.db.execute(
        "UPDATE callback_schedules SET window_start=?,window_end=? WHERE schedule_id=?",
        (
            (now - timedelta(seconds=1)).isoformat(),
            (now + timedelta(minutes=15)).isoformat(),
            identifier,
        ),
    )


async def test_dispatch_one_attempt_and_recovery_never_replaces_started_call(android):
    h = android
    # The fixture's normal outbound allowlist already contains this destination.
    _, _, _, receipt = await confirmed(h)
    callbacks = h.service.control.callbacks
    await make_due(h, receipt["schedule_id"])
    await callbacks.tick()
    await phone_tests.attach(h)
    assert len(h.asterisk.dials) == 1
    row = await callbacks.get(receipt["schedule_id"])
    assert row["dial_claimed"] == 1 and row["state"] == "dial_claimed"
    with pytest.raises(ValueError, match="underway"):
        await callbacks.update(
            row["schedule_id"], ScheduleUpdate(expected_revision=1, action="cancel")
        )
    await callbacks.tick()
    assert len(h.asterisk.dials) == 1
    h.phone.idle = True
    await h.service.terminate_call(row["call_id"], "callee_hangup")
    await h.service._settle(row["call_id"])
    await phone_tests.wait_state(h.service, row["call_id"], CallState.FAILED)
    await callbacks.tick()
    assert (await callbacks.get(row["schedule_id"]))["state"] == "attempted"
    await callbacks.tick()
    assert len(h.asterisk.dials) == 1


async def test_cancellation_during_prewarm_and_ringing_priority(android):
    h = android
    _, _, _, receipt = await confirmed(h)
    callbacks = h.service.control.callbacks
    await make_due(h, receipt["schedule_id"])
    h.service._incoming_pending.add("ringing")
    await callbacks.tick()
    assert h.service._runtime is None
    h.service._incoming_pending.clear()
    await callbacks.tick()
    await callbacks.update(
        receipt["schedule_id"], ScheduleUpdate(expected_revision=1, action="cancel")
    )
    await phone_tests.attach(h, expect_dial=False)
    await phone_tests.wait_until(lambda: bool(h.asterisk.ended))
    assert not h.asterisk.dials


async def test_expired_window_and_opt_out_do_not_dial(android):
    h = android
    _, _, _, receipt = await confirmed(h)
    await h.service.db.execute(
        "UPDATE callback_schedules SET window_start='2000-01-01T00:00:00+00:00',window_end='2000-01-01T00:15:00+00:00' WHERE schedule_id=?",
        (receipt["schedule_id"],),
    )
    await h.service.control.callbacks.tick()
    assert (await h.service.control.callbacks.get(receipt["schedule_id"]))["state"] == "missed"
    assert not h.asterisk.dials


async def test_opt_out_is_atomic_even_if_number_suppression_write_fails(android):
    import aiosqlite

    h = android
    _, _, _, receipt = await confirmed(h)
    await h.service.db.execute(
        "CREATE TRIGGER reject_test_opt_out BEFORE INSERT ON destination_suppressions BEGIN SELECT RAISE(ABORT,'simulated write failure'); END"
    )
    with pytest.raises(aiosqlite.IntegrityError):
        await h.service.db.opt_out_call("call_memory", h.packet.target.phone)
    assert not await h.service.db.call_recorded_opt_out("call_memory")
    assert (await h.service.control.callbacks.get(receipt["schedule_id"]))["state"] == "scheduled"
    await h.service.db.execute("DROP TRIGGER reject_test_opt_out")
    await h.service.db.opt_out_call("call_memory", h.packet.target.phone)
    assert await h.service.db.is_destination_suppressed(h.packet.target.phone)
    assert await h.service.db.call_recorded_opt_out("call_memory")
    assert (await h.service.control.callbacks.get(receipt["schedule_id"]))["state"] == "cancelled"


async def test_stale_dispatch_pass_cannot_cancel_a_rescheduled_window(android, monkeypatch):
    import asyncio

    from app.reception_models import CallbackPolicy

    h = android
    _, body, _, receipt = await confirmed(h)
    callbacks = h.service.control.callbacks
    await make_due(h, receipt["schedule_id"])
    original_policy = callbacks.policy
    read, resume = asyncio.Event(), asyncio.Event()

    async def stale_policy():
        read.set()
        await resume.wait()
        return CallbackPolicy(enabled=False), 1

    monkeypatch.setattr(callbacks, "policy", stale_policy)
    task = asyncio.create_task(callbacks.tick())
    await asyncio.wait_for(read.wait(), 2)
    monkeypatch.setattr(callbacks, "policy", original_policy)
    replacement = body.model_copy(
        update={
            "window_start": body.window_start + timedelta(hours=1),
            "window_end": body.window_end + timedelta(hours=1),
        }
    )
    changed = await callbacks.update(
        receipt["schedule_id"],
        ScheduleUpdate(expected_revision=1, action="reschedule", proposal=replacement),
    )
    resume.set()
    await task
    current = await callbacks.get(receipt["schedule_id"])
    assert current["state"] == "scheduled" and current["revision"] == changed["revision"] == 2
    assert current["window_start"] == changed["window_start"]
    assert not h.asterisk.dials


async def test_duplicate_requests_with_new_keys_share_one_receipt(android):
    h = android
    _, body, _, first = await confirmed(h)
    proposed = await h.service.control.callbacks.propose(
        "call_memory", body.model_copy(update={"request_key": "retried_with_new_key"})
    )
    confirmation = CallbackConfirmation(
        proposal_id=proposed["proposal_id"],
        proposal_revision=1,
        readback_turn_id="owner",
        confirmation_turn_id="owner",
    )
    second = await h.service.control.callbacks.confirm("call_memory", confirmation, owner=True)
    assert first["schedule_id"] == second["schedule_id"]
    assert second == await h.service.control.callbacks.confirm(
        "call_memory", confirmation, owner=True
    )
    assert len((await h.service.control.callbacks.list())["callbacks"]) == 1


async def test_missing_facts_wait_for_owner_resolution_and_survive_reschedule(android):
    h = android
    row, body = await proposal(h, needed_information=["Confirm the approved price"])
    assert "wait until" in row["readback"]
    confirmation = CallbackConfirmation(
        proposal_id=row["proposal_id"],
        proposal_revision=1,
        readback_turn_id="owner",
        confirmation_turn_id="owner",
    )
    callbacks = h.service.control.callbacks
    receipt = await callbacks.confirm("call_memory", confirmation, owner=True)
    assert receipt["saved"] and not receipt["scheduled"]
    assert receipt["state"] == "needs_information"
    await make_due(h, receipt["schedule_id"])
    await callbacks.tick()
    assert not h.asterisk.dials
    with pytest.raises(ValueError, match="verified answers"):
        await callbacks.update(
            receipt["schedule_id"],
            ScheduleUpdate(expected_revision=1, action="resolve_information"),
        )
    updated = await callbacks.update(
        receipt["schedule_id"],
        ScheduleUpdate(
            expected_revision=1,
            action="resolve_information",
            new_facts=["The approved price is EUR 20 per month."],
        ),
    )
    assert updated["state"] == "scheduled"
    updated = await callbacks.update(
        receipt["schedule_id"],
        ScheduleUpdate(
            expected_revision=2,
            action="reschedule",
            proposal=body.model_copy(update={"needed_information": []}),
        ),
    )
    assert updated["value"]["new_facts"] == ["The approved price is EUR 20 per month."]
    await make_due(h, receipt["schedule_id"])
    await callbacks.tick()
    await phone_tests.attach(h)
    assert len(h.asterisk.dials) == 1
    saved = await callbacks.get(receipt["schedule_id"])
    plan = await h.service.db.get_plan_record(saved["plan_id"])
    assert "The approved price is EUR 20 per month." in plan.context.relevant_facts


@pytest.mark.parametrize(
    "text",
    ["Yes, that is correct.", "Sí, es correcto.", "Да, всё правильно.", "Так, усе правильно."],
)
def test_clear_confirmation_variations(text):
    from app.callbacks import CONFIRMATIONS, normalized_speech

    assert normalized_speech(text) in CONFIRMATIONS
    assert normalized_speech(text + " But change the time") not in CONFIRMATIONS


@pytest.mark.parametrize("alternate", [False, True])
async def test_inbound_callback_reuses_learned_identity_without_associating_another_number(
    android, alternate
):
    from app.reception_models import ReviewCommit

    h = android
    row, body = await proposal(h)
    db = h.service.db
    source = await db.fetch_one("SELECT contact_id FROM call_contacts WHERE call_id='call_memory'")
    cid = source["contact_id"]
    # Match an actual new inbound caller: a nameless provisional contact and placeholder brief.
    record = await db.get_plan_record("plan_call_memory")
    packet = record.context.model_copy(
        update={
            "direction": "inbound",
            "target": record.context.target.model_copy(update={"name": "Incoming caller"}),
        }
    )
    await db.execute(
        "UPDATE plans SET context_json=? WHERE plan_id='plan_call_memory'",
        (json.dumps(packet.model_dump(mode="json")),),
    )
    await db.execute("UPDATE call_contacts SET direction='inbound' WHERE call_id='call_memory'")
    await db.execute("UPDATE contacts SET name='',actor='call' WHERE contact_id=?", (cid,))
    await db.reception_enqueue_review("call_memory")
    job = await db.reception_claim_review()
    await db.reception_read_review(job["job_id"], job["lease_token"], 0)
    await h.service.control.contacts.commit_review(
        ReviewCommit(
            job_id=job["job_id"],
            lease_token=job["lease_token"],
            request_id="learned-name",
            transcript_digest=job["transcript_digest"],
            expected_memory_revision=0,
            expected_summary_revision=0,
            summary="Sam requested help with bookings.",
            outcome="incomplete",
            recipient_name="Sam",
            identity_evidence_turn_ids=["turn0"],
            memory_facts=[
                {
                    "key": "need",
                    "text": "Runs a cafe and needs booking help",
                    "evidence_turn_ids": ["turn1"],
                }
            ],
        )
    )
    assert (await db.contact_get(cid))["name"] == "Sam"
    if alternate:
        body = body.model_copy(update={"destination": "+14155550188"})
        h.service.config.allow_any_destination = True
    row = await h.service.control.callbacks.propose("call_memory", body)
    receipt = await h.service.control.callbacks.confirm(
        "call_memory",
        CallbackConfirmation(
            proposal_id=row["proposal_id"],
            proposal_revision=row["revision"],
            readback_turn_id="owner",
            confirmation_turn_id="owner",
        ),
        owner=True,
    )
    await make_due(h, receipt["schedule_id"])
    await h.service.control.callbacks.tick()
    runtime = h.service._runtime
    assert runtime.packet.target.name == "Sam"
    assert (
        runtime.configuration["contact_memory"]["facts"][0]["text"]
        == "Runs a cafe and needs booking help"
    )
    link = await db.fetch_one("SELECT * FROM call_contacts WHERE call_id=?", (runtime.call_id,))
    assert link["contact_id"] == cid and link["recognition"] == "confirmed_callback_source"
    assert len(await db.fetch_all("SELECT * FROM contacts")) == 1
    if alternate:
        assert await db.contact_lookup("+14155550188") == []
    assert not h.asterisk.dials


async def test_unresolved_callback_does_not_create_a_new_identity(android):
    h = android
    row, body = await proposal(h)
    await h.service.db.execute(
        "UPDATE call_contacts SET recognition='identity_mismatch' WHERE call_id='call_memory'"
    )
    receipt = await h.service.control.callbacks.confirm(
        "call_memory",
        CallbackConfirmation(
            proposal_id=row["proposal_id"],
            proposal_revision=1,
            readback_turn_id="owner",
            confirmation_turn_id="owner",
        ),
        owner=True,
    )
    await make_due(h, receipt["schedule_id"])
    await h.service.control.callbacks.tick()
    runtime = h.service._runtime
    assert runtime.configuration["contact_memory"]["facts"] == []
    link = await h.service.db.fetch_one(
        "SELECT * FROM call_contacts WHERE call_id=?", (runtime.call_id,)
    )
    assert link["contact_id"] is None and link["recognition"] == "unresolved_callback"
    assert len(await h.service.db.fetch_all("SELECT * FROM contacts")) == 1
