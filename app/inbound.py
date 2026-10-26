"""Own incoming SIP channels without ever entering the outbound originate path."""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import phonenumbers

from app.call_planning import claim_approved_call
from app.models import ContextPacket
from app.reception_models import BusinessSettings, HandsetAcceptance, InboundSettings

if TYPE_CHECKING:
    from app.android_calls import AndroidCallService, _Call


def incoming_number(event: dict[str, Any]) -> tuple[str | None, str]:
    presentation = str((event.get("args") or ["", ""])[-1]).lower()
    if "prohib" in presentation or "unavailable" in presentation:
        return None, "withheld" if "prohib" in presentation else "unavailable"
    raw = str(event.get("channel", {}).get("caller", {}).get("number", ""))
    if not re.fullmatch(r"[+0-9 ()-]{3,40}", raw):
        return None, "unavailable"
    try:
        parsed = phonenumbers.parse(raw, "ES")
        if phonenumbers.is_possible_number(parsed):
            return str(
                phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
            ), "presented"
    except phonenumbers.NumberParseException:
        pass
    return None, "unavailable"


class InboundReception:
    def __init__(self, service: AndroidCallService) -> None:
        self.service, self.control, self.db = service, service.control, service.db

    async def acceptance_status(self) -> dict[str, Any]:
        rows = await self.db.fetch_all(
            "SELECT name,value_json FROM reception_state WHERE name IN ('handset_acceptance','acceptance_window')"
        )
        values = {row["name"]: json.loads(row["value_json"]) for row in rows}
        until = values.get("acceptance_window", {}).get("until")
        return {
            "passed": bool(values.get("handset_acceptance", {}).get("passed")),
            "evidence": values.get("handset_acceptance"),
            "window_until": until
            if until and datetime.fromisoformat(until) > datetime.now(UTC)
            else None,
        }

    async def test_window(self, open_window: bool) -> dict[str, Any]:
        if open_window:
            if not self.service.settings.live_calls_enabled:
                raise ValueError("Handset checks require live calling mode")
            if (await self.availability())["availability"] != "Ready":
                raise ValueError(
                    "Finish business setup and wait for the phone to be ready before testing"
                )
            value = {"until": (datetime.now(UTC) + timedelta(minutes=5)).isoformat()}
            await self.db.execute(
                "INSERT INTO reception_state VALUES ('acceptance_window',?) ON CONFLICT(name) DO UPDATE SET value_json=excluded.value_json",
                (json.dumps(value),),
            )
        else:
            await self.db.execute("DELETE FROM reception_state WHERE name='acceptance_window'")
        return await self.acceptance_status()

    async def accept_handset(self, evidence: HandsetAcceptance) -> dict[str, Any]:
        incoming = await self.db.fetch_one(
            "SELECT c.*,l.phone,l.direction FROM calls c JOIN call_contacts l USING(call_id) WHERE c.call_id=?",
            (evidence.inbound_call_id,),
        )
        if (
            not incoming
            or incoming["direction"] != "inbound"
            or not incoming["answered_at"]
            or incoming["state"] != "completed"
            or incoming["phone"] != evidence.caller_number
        ):
            raise ValueError("Choose a completed inbound test with the correct caller number")
        result = await self.service.get_result(evidence.inbound_call_id)
        if result["artifacts"]["audio_status"] != "available":
            raise ValueError("The inbound recording must be available before accepting the handset")
        callback = await self.control.callbacks.get(evidence.callback_schedule_id)
        attempt = await self.db.get_call(callback["call_id"]) if callback["call_id"] else None
        if (
            callback["source_call_id"] != evidence.inbound_call_id
            or not callback["dial_claimed"]
            or not attempt
            or not attempt["answered_at"]
            or attempt["state"] != "completed"
        ):
            raise ValueError(
                "Complete an answered, scheduled callback from this test conversation first"
            )
        if getattr(self.service.phone, "inbound_problem", lambda: "Unavailable")():
            raise ValueError("The selected-SIM gateway contract is not ready")
        saved = {
            "passed": True,
            "gateway_contract": 2,
            "checked_at": datetime.now(UTC).isoformat(),
            **evidence.model_dump(mode="json"),
        }
        await self.db.execute(
            "INSERT INTO reception_state VALUES ('handset_acceptance',?) ON CONFLICT(name) DO UPDATE SET value_json=excluded.value_json",
            (json.dumps(saved),),
        )
        await self.db.execute("DELETE FROM reception_state WHERE name='acceptance_window'")
        return await self.acceptance_status()

    async def availability(self) -> dict[str, Any]:
        settings = InboundSettings.model_validate((await self.control.current("inbound"))["value"])
        business = BusinessSettings.model_validate(
            (await self.control.current("business"))["value"]
        )
        reason = None
        from app.db.deployment import _lock_is_active

        lease = await self.db.fetch_one(
            "SELECT locked,locked_at FROM deployment_control WHERE singleton=1"
        )
        phone_check = getattr(self.service.phone, "inbound_problem", None)
        if not self.service.settings.live_calls_enabled:
            reason = "Live calling is disabled"
        elif lease and lease["locked"] and _lock_is_active(lease["locked_at"]):
            reason = "The calling service is being updated"
        elif not business.configured:
            reason = "Business setup is incomplete"
        elif phone_check is None or (problem := phone_check()):
            reason = problem if phone_check else "The inbound phone contract is unavailable"
        elif not self.service.supervision_ready():
            reason = "Call supervision is unavailable"
        else:
            try:
                if not await self.service.asterisk.gateway_registered():
                    reason = "The phone is not registered with Asterisk"
            except Exception:
                reason = "Asterisk is unavailable"
        busy = bool(await self.db.list_nonterminal_calls())
        return {
            "answering_on": settings.enabled,
            "availability": "Busy" if busy else "Unavailable" if reason else "Ready",
            "reason": reason,
            "business_configured": business.configured,
        }

    async def allowed(self) -> bool:
        if not self.service.settings.live_calls_enabled:
            return False
        settings = InboundSettings.model_validate((await self.control.current("inbound"))["value"])
        if settings.enabled:
            return True
        window = await self.db.fetch_one(
            "SELECT value_json FROM reception_state WHERE name='acceptance_window'"
        )
        if not window:
            return False
        return datetime.fromisoformat(json.loads(window["value_json"])["until"]) > datetime.now(UTC)

    async def packet(self, phone: str | None, recognition: str) -> ContextPacket:
        inbound = InboundSettings.model_validate((await self.control.current("inbound"))["value"])
        business = BusinessSettings.model_validate(
            (await self.control.current("business"))["value"]
        )
        return ContextPacket.model_validate(
            {
                "direction": "inbound",
                "owner": {
                    "display_name": self.service.settings.owner_display_name,
                    "timezone": business.timezone,
                    "callback_number": self.service.settings.owner_phone_e164,
                },
                "target": {"name": "Incoming caller", "phone": phone or recognition},
                "objective": "Receive this incoming business call, understand today's need, answer from approved information, and agree on a useful next step.",
                "language": inbound.language,
                "profile_id": inbound.profile_id,
                "max_duration_seconds": inbound.max_call_seconds,
                # Business facts are frozen once in the business prompt layer, not duplicated in the brief.
                "relevant_facts": [],
                "hard_constraints": [
                    "Caller ID is recognition, not authentication. Do not disclose sensitive history.",
                    "Do not invent prices, staff availability, capabilities, bookings, or completed actions.",
                ],
                "allowed_commitments": [
                    "A routine callback on this caller's request within the saved callback policy"
                ],
                "escalation": {
                    "mode": "end_call",
                    "owner_phone": self.service.settings.owner_phone_e164,
                },
            }
        )

    async def admit(self, event: dict[str, Any]) -> None:
        from app.android_calls import _Call

        service = self.service
        channel = event["channel"]["id"]
        runtime = None
        try:
            async with asyncio.timeout(30):
                pending = service._runtime
                if pending and (pending.configuration or {}).get("callback_schedule_id"):
                    row = await self.db.get_call(pending.call_id)
                    if row and not row["callee_dialed"]:
                        await service.terminate_call(pending.call_id, "incoming_call_priority")
                        for _ in range(50):
                            await service._settle(pending.call_id)
                            if service._runtime is None:
                                break
                            await asyncio.sleep(0.1)
                async with service._operations:
                    if (
                        service._runtime
                        or await self.db.list_nonterminal_calls()
                        or not await self.allowed()
                    ):
                        return
                    availability = await self.availability()
                    if availability["availability"] != "Ready":
                        return
                    await service.preempt_practice()
                    phone, recognition = incoming_number(event)
                    packet = await self.packet(phone, recognition)
                    snapshot = await self.control.resolve(packet)
                    snapshot["incoming_channel"] = channel
                    snapshot["contact_memory"] = await self.control.contacts.snapshot(phone)
                    snapshot["callback_policy"] = (await self.control.current("callback_policy"))[
                        "value"
                    ]
                    packet = self.control.normalize_packet(packet, snapshot)
                    self.control.freeze_inputs(packet, snapshot)
                    plan_id = "plan_" + secrets.token_urlsafe(18)
                    snapshot["confirmation_summary"] = (
                        "Receive incoming Selected SIM call under the saved inbound policy"
                    )
                    await self.db.create_plan(
                        plan_id,
                        packet.model_dump(mode="json"),
                        "Saved inbound reception policy",
                        datetime.now(UTC) + timedelta(seconds=40),
                        snapshot,
                    )
                    approved = await claim_approved_call(
                        self.db,
                        plan_id=plan_id,
                        explicit_confirmation=True,
                        confirmation_text=snapshot["confirmation_summary"],
                    )
                    runtime = _Call(
                        approved.call_id,
                        packet,
                        packet.max_duration_seconds or 1800,
                        configuration=snapshot,
                        config=self.control.runtime_config(snapshot),
                        direction="inbound",
                        incoming_channel=channel,
                    )
                    service._runtime = runtime
                    service.asterisk.adopt_incoming(runtime.call_id, channel)
                    await self.db.contact_link_call(
                        call_id=runtime.call_id,
                        phone=phone,
                        name="",
                        direction="inbound",
                        recognition=recognition if phone is None else None,
                        channel_id=channel,
                        frozen_memory=snapshot["contact_memory"],
                    )
                    runtime.writer = service._spawn(service._write_transcript(runtime))
                    service._spawn(self.prewarm(runtime))
        except Exception:
            if runtime:
                await service.terminate_call(runtime.call_id, "inbound_admission_failed")
        finally:
            service._incoming_pending.discard(channel)
            if runtime is None:
                await service.asterisk.release_incoming(channel)

    async def prewarm(self, runtime: _Call) -> None:
        service = self.service
        try:
            async with asyncio.timeout(30):
                await service.asterisk.create_media(runtime.call_id, runtime.duration_seconds)
                await runtime.media_attached.wait()
                assert runtime.voice is not None
                await runtime.voice.ready.wait()
                await runtime.media_in_application.wait()
                async with service._operations:
                    if (
                        not await self.allowed()
                        or service.phone.inbound_problem()
                        or not service.supervision_ready()
                    ):
                        raise ValueError("Inbound admission changed during setup")
                    await service.asterisk.bridge_media(runtime.call_id)
                    if not await self.db.promote_to_ready_to_activate(runtime.call_id):
                        return
                    if not await self.db.promote_to_activating(runtime.call_id):
                        return
                    # Persist before the remote answer request; an uncertain answer is owned until cleanup.
                    await self.db.execute(
                        "UPDATE call_contacts SET answer_requested=1 WHERE call_id=?",
                        (runtime.call_id,),
                    )
                    runtime.answer_requested = True
                    await service.asterisk.answer_incoming(
                        runtime.call_id, runtime.duration_seconds
                    )
                # SIP 200 alone is not proof of cellular audio. The guarded bridge emits D after both ports connect.
                await asyncio.wait_for(runtime.incoming_audio_ready.wait(), 10)
                await service.activate_answered(runtime)
        except Exception:
            await service.terminate_call(runtime.call_id, "inbound_setup_failed")
