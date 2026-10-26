from __future__ import annotations

import asyncio

import pytest

from app.errors import CallRefusal
from app.inbound import incoming_number
from app.models import CallState, PreparePhoneCallInput
from tests import test_android_calls as phone_tests

android = phone_tests.android


def event(identifier="1730000000.42", number="+34614000000", presentation="allowed_not_screened"):
    return {
        "type": "StasisStart",
        "args": ["inbound", presentation],
        "channel": {
            "id": identifier,
            "name": "PJSIP/android-0000000a",
            "state": "Ring",
            "caller": {"number": number},
        },
    }


async def configure(h, enabled=True):
    c = h.service.control
    business = (await c.current("business"))["value"]
    await c.save(
        "business",
        {
            **business,
            "name": "Example Ltd",
            "description": "Helps with reception",
            "approved_facts": ["We discuss customer needs"],
        },
        1,
        "owner",
    )
    await h.service.db.execute(
        "INSERT INTO reception_state VALUES ('handset_acceptance','{\"passed\":true}')"
    )
    inbound = (await c.current("inbound"))["value"]
    await c.save("inbound", {**inbound, "enabled": enabled, "max_call_seconds": 90}, 1, "owner")
    h.phone.inbound_problem = lambda: None
    h.asterisk.incoming = {}
    h.asterisk.answers, h.asterisk.released = [], []

    def adopt(call_id, channel_id):
        h.asterisk.incoming[call_id] = channel_id

    async def release(channel_id):
        h.asterisk.released.append(channel_id)

    async def answer(call_id, seconds):
        h.asterisk.answers.append(call_id)

    h.asterisk.adopt_incoming, h.asterisk.release_incoming, h.asterisk.answer_incoming = (
        adopt,
        release,
        answer,
    )


async def test_answering_off_releases_native_call_and_does_not_preempt_practice(android):
    h = android
    await configure(h, enabled=False)
    practice = await h.service.control.practice.create("text", h.packet)
    await h.service._handle_event(event())
    await phone_tests.wait_until(lambda: bool(h.asterisk.released))
    assert h.service.control.practice.active.id == practice["id"]
    assert not h.asterisk.answers and not h.asterisk.dials and not h.service._runtime


async def test_inbound_never_originates_and_waits_for_confirmed_handset_audio(android):
    h = android
    await configure(h)
    practice = await h.service.control.practice.create("text", h.packet)
    await h.service._handle_event(event(number="", presentation="prohib_not_screened"))
    await phone_tests.wait_until(lambda: bool(h.asterisk.media))
    runtime = h.service._runtime
    assert runtime.direction == "inbound" and runtime.packet.target.phone == "withheld"
    assert h.service.control.practice.active is None
    assert "telephone" in h.service.control.practice.get(practice["id"]).error
    assert not h.asterisk.answers and not h.asterisk.dials
    await phone_tests.attach(h, expect_dial=False)
    await phone_tests.wait_until(lambda: bool(h.asterisk.answers))
    await h.service._handle_event(
        {"type": "ChannelStateChange", "channel": {"id": "1730000000.42", "state": "Up"}}
    )
    assert not runtime.voice.started
    await h.service._handle_event(
        {"type": "ChannelDtmfReceived", "channel": {"id": "foreign"}, "digit": "D"}
    )
    assert not runtime.voice.started
    await h.service._handle_event(
        {"type": "ChannelDtmfReceived", "channel": {"id": "1730000000.42"}, "digit": "D"}
    )
    await phone_tests.wait_state(h.service, runtime.call_id, CallState.ACTIVE)
    assert runtime.voice.started and not h.asterisk.dials
    assert h.asterisk.incoming[runtime.call_id] == "1730000000.42"
    link = await h.service.db.fetch_one(
        "SELECT * FROM call_contacts WHERE call_id=?", (runtime.call_id,)
    )
    assert link["phone"] is None and link["contact_id"] and link["answer_requested"] == 1
    with pytest.raises(CallRefusal, match="outbound dialing"):
        await h.service.prepare(
            PreparePhoneCallInput(
                context=runtime.packet, requested_by_owner=True, authority_basis="test"
            )
        )
    inbound = await h.service.control.current("inbound")
    await h.service.control.save(
        "inbound", {**inbound["value"], "enabled": False}, inbound["revision"], "owner"
    )
    assert (await h.service.get_snapshot(runtime.call_id)).state == CallState.ACTIVE


async def test_second_incoming_call_does_not_disturb_owned_call(android):
    h = android
    await configure(h)
    existing = await phone_tests.start(h)
    await h.service._handle_event(event())
    await phone_tests.wait_until(lambda: bool(h.asterisk.released))
    assert h.service._runtime.call_id == existing.call_id
    assert not h.asterisk.ended


async def test_unanswered_setup_failure_finishes_without_rejecting_native_call(android):
    h = android
    await configure(h)

    async def fail(*args):
        raise RuntimeError("provider offline")

    h.asterisk.create_media = fail
    await h.service._handle_event(event())
    await phone_tests.wait_until(lambda: bool(h.asterisk.ended))
    call_id = h.asterisk.ended[0]
    h.phone.idle = False  # The unowned native handset is still ringing.
    await h.service._settle(call_id)
    assert (await h.service.get_snapshot(call_id)).state == CallState.FAILED
    assert not h.asterisk.answers and not h.asterisk.dials
    assert await h.service.db.reception_review_pending() == 1


async def test_business_setup_and_acceptance_are_required_before_enable(android):
    c = android.service.control
    inbound = await c.current("inbound")
    with pytest.raises(ValueError, match="business name"):
        await c.save("inbound", {**inbound["value"], "enabled": True}, 1, "owner")
    business = (await c.current("business"))["value"]
    await c.save(
        "business",
        {
            **business,
            "name": "Business",
            "description": "Approved description",
            "approved_facts": ["Fact"],
        },
        1,
        "owner",
    )
    with pytest.raises(ValueError, match="handset acceptance"):
        await c.save("inbound", {**inbound["value"], "enabled": True}, 1, "owner")


def test_caller_presentation_normalization_and_unknown_numbers():
    assert incoming_number(event(number="614000000")) == ("+34614000000", "presented")
    assert incoming_number(event(number="android")) == (None, "unavailable")
    assert incoming_number(event(number="+34614000000", presentation="prohib")) == (
        None,
        "withheld",
    )


async def active_incoming(h):
    await configure(h)
    await h.service._handle_event(event())
    await phone_tests.wait_until(lambda: bool(h.asterisk.media))
    await phone_tests.attach(h, expect_dial=False)
    await phone_tests.wait_until(lambda: bool(h.asterisk.answers))
    await h.service._handle_event(
        {"type": "ChannelDtmfReceived", "channel": {"id": "1730000000.42"}, "digit": "D"}
    )
    runtime = h.service._runtime
    await phone_tests.wait_state(h.service, runtime.call_id, CallState.ACTIVE)
    return runtime


async def test_evaluation_profile_releases_incoming_calls_without_answering(android):
    h = android
    await configure(h)
    h.service.settings.agent_call_profile = "evaluation"
    await h.service._handle_event(event())
    await phone_tests.wait_until(lambda: bool(h.asterisk.released))
    assert not h.asterisk.answers and not h.asterisk.media and not h.asterisk.dials


@pytest.mark.parametrize("caller_hangs_up", [False, True])
async def test_local_exit_survives_deliberate_media_close_but_respects_caller_hangup(
    android, caller_hangs_up
):
    h = android
    runtime = await active_incoming(h)
    playing = asyncio.Event()

    async def play(call_id, kind, language):
        await h.service._handle_event({"type": "StasisEnd", "channel": {"id": call_id + "-media"}})
        assert (await h.service.get_snapshot(call_id)).state == CallState.ACTIVE
        playing.set()
        return call_id + "-exit"

    h.asterisk.play_local_exit = play
    task = asyncio.create_task(h.service._fail_voice(runtime.call_id))
    await asyncio.wait_for(playing.wait(), 2)
    assert not h.asterisk.ended
    if caller_hangs_up:
        await h.service._handle_event(
            {"type": "StasisEnd", "channel": {"id": runtime.incoming_channel}}
        )
    else:
        await h.service._handle_event(
            {
                "type": "PlaybackFinished",
                "playback": {"id": runtime.call_id + "-exit", "state": "done"},
            }
        )
    await asyncio.wait_for(task, 2)
    await runtime.transcript.join()
    transcript = await h.service.db.get_transcript(runtime.call_id)
    assert (
        any(turn.source_event_type == "local_reception_exit" for turn in transcript)
        is not caller_hangs_up
    )


async def test_restart_recovers_incoming_channel_from_plan_before_contact_link(
    android, monkeypatch
):
    h = android
    await configure(h)

    class ProcessStopped(BaseException):
        pass

    async def crash(**kwargs):
        raise ProcessStopped()

    with monkeypatch.context() as scoped:
        scoped.setattr(h.service.db, "contact_link_call", crash)
        with pytest.raises(ProcessStopped):
            await h.service.inbound.admit(event())
    call_id = h.service._runtime.call_id
    h.service._runtime = None
    h.asterisk.incoming.clear()
    await h.service.recover_startup()
    assert h.asterisk.incoming[call_id] == "1730000000.42"
    link = await h.service.db.fetch_one("SELECT * FROM call_contacts WHERE call_id=?", (call_id,))
    assert link["answer_requested"] == 0
    h.phone.idle = False
    await h.service._settle(call_id)
    assert (await h.service.get_snapshot(call_id)).state == CallState.FAILED
    assert not h.asterisk.answers and not h.asterisk.dials


async def test_handset_window_and_acceptance_need_actual_completed_evidence(android, monkeypatch):
    from datetime import UTC, datetime

    from app.reception_models import HandsetAcceptance
    from tests.test_callback_scheduling import confirmed
    from tests.test_reception_memory import completed

    h = android
    await configure(h, enabled=False)
    await h.service.db.execute("DELETE FROM reception_state")
    reception = h.service.inbound
    window = await reception.test_window(True)
    assert window["window_until"] and not window["passed"]
    assert await reception.allowed()
    assert not (await reception.test_window(False))["window_until"]
    assert not await reception.allowed()
    _, _, _, receipt = await confirmed(h)
    evidence = HandsetAcceptance(
        inbound_call_id="call_memory",
        callback_schedule_id=receipt["schedule_id"],
        caller_number=h.packet.target.phone,
        greeting_heard=True,
        recording_checked=True,
        native_fallback_checked=True,
    )
    with pytest.raises(ValueError, match="completed inbound"):
        await reception.accept_handset(evidence)
    await h.service.db.execute(
        "UPDATE call_contacts SET direction='inbound' WHERE call_id='call_memory'"
    )
    await h.service.db.update_call("call_memory", answered_at=datetime.now(UTC).isoformat())

    async def result(_):
        return {"artifacts": {"audio_status": "available"}}

    monkeypatch.setattr(h.service, "get_result", result)
    with pytest.raises(ValueError, match="scheduled callback"):
        await reception.accept_handset(evidence)
    await completed(h.service.db, h.packet, "call_callback_fixture")
    await h.service.db.update_call(
        "call_callback_fixture", answered_at=datetime.now(UTC).isoformat()
    )
    await h.service.db.execute(
        "UPDATE callback_schedules SET call_id='call_callback_fixture',dial_claimed=1,state='attempted' WHERE schedule_id=?",
        (receipt["schedule_id"],),
    )
    accepted = await reception.accept_handset(evidence)
    assert accepted["passed"] and accepted["evidence"]["gateway_contract"] == 2
    assert not (await reception.availability())["answering_on"]
    h.phone.inbound_problem = lambda: "wrong SIM contract"
    with pytest.raises(ValueError, match="gateway contract"):
        await reception.accept_handset(evidence)
