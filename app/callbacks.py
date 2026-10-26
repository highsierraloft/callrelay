"""Confirmed callback scheduling. Only the existing call service can dial."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.db.callback_schedules import schedule_row
from app.models import TERMINAL_STATES, ContextPacket, PreparePhoneCallInput
from app.policy import validate_destination
from app.reception_models import (
    CallbackConfirmation,
    CallbackPolicy,
    CallbackProposal,
    ScheduleUpdate,
)

if TYPE_CHECKING:
    from app.control import ControlPlane

logger = logging.getLogger(__name__)


def normalized_speech(text: str) -> str:
    folded = "".join(
        c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)
    )
    return re.sub(r"[^\w]", "", folded)


CONFIRMATIONS = {
    normalized_speech(text)
    for text in (
        "yes",
        "yes please",
        "yeah",
        "yep",
        "sure",
        "okay",
        "ok",
        "that's right",
        "sounds good to me",
        "yes that's correct",
        "yes that is correct",
        "yes that is right",
        "yes please do",
        "yes I confirm",
        "that is correct",
        "that is right",
        "that's correct",
        "correct",
        "confirmed",
        "yes that works",
        "yes that would be great",
        "yes exactly",
        "absolutely",
        "sounds good",
        "sí",
        "sí por favor",
        "correcto",
        "sí correcto",
        "sí es correcto",
        "sí está bien",
        "de acuerdo",
        "perfecto",
        "vale",
        "así es",
        "sí claro",
        "да",
        "да пожалуйста",
        "да всё верно",
        "да всё правильно",
        "верно",
        "подтверждаю",
        "да хорошо",
        "хорошо",
        "так",
        "так будь ласка",
        "так правильно",
        "так усе правильно",
        "підтверджую",
        "так добре",
        "гаразд",
        "домовилися",
    )
}


def callback_readback(value: CallbackProposal, language: str) -> str:
    language = value.language or language
    objective = value.objective.rstrip(".!? ")
    start, end = value.window_start, value.window_end
    first = start.strftime("%H:%M:%S" if start.second else "%H:%M")
    last = end.strftime("%H:%M:%S" if end.second else "%H:%M")
    offsets = (
        f"UTC{start:%z}" if start.utcoffset() == end.utcoffset() else f"UTC{start:%z} → UTC{end:%z}"
    )
    details = f"{' '.join(value.destination)}, {start:%Y-%m-%d}, {first}–{last}, {value.timezone} ({offsets})"
    if value.needed_information:
        missing = "; ".join(value.needed_information)
        if language == "es":
            return f"Anotaré una solicitud de llamada sobre {objective}: {details}. Quedará pendiente hasta confirmar: {missing}. ¿Es correcto?"
        if language == "ru":
            return f"Я запишу просьбу перезвонить по вопросу {objective}: {details}. Звонок будет ожидать уточнения: {missing}. Всё верно?"
        if language == "uk":
            return f"Я запишу прохання передзвонити щодо {objective}: {details}. Дзвінок чекатиме уточнення: {missing}. Усе правильно?"
        return f"I'll record a callback request about {objective}: {details}. It will wait until we confirm: {missing}. Is that correct?"
    if language == "es":
        return f"Para confirmar: le llamaremos para {objective}. {details}. ¿Es correcto?"
    if language == "ru":
        return (
            f"Подтвердите, пожалуйста: мы перезвоним по вопросу {objective}. {details}. Всё верно?"
        )
    if language == "uk":
        return (
            f"Підтвердьте, будь ласка: ми зателефонуємо щодо {objective}. {details}. Усе правильно?"
        )
    return f"To confirm: we'll call you about {objective}. {details}. Is that correct?"


class Callbacks:
    def __init__(self, control: ControlPlane) -> None:
        self.control, self.db = control, control.db
        self.task: asyncio.Task[None] | None = None

    async def policy(self) -> tuple[CallbackPolicy, int]:
        record = await self.control.current("callback_policy")
        return CallbackPolicy.model_validate(record["value"]), record["revision"]

    async def validate(
        self, proposal: CallbackProposal, source_call_id: str
    ) -> tuple[dict[str, Any], int]:
        policy, revision = await self.policy()
        if proposal.window_start <= datetime.now(UTC):
            raise ValueError("Agree a future callback window")
        if not policy.permits(proposal.window_start, proposal.window_end):
            raise ValueError(f"Automatic callbacks must fit the saved hours in {policy.timezone}")
        decision = validate_destination(proposal.destination, self.control.service.settings)
        if not decision.allowed:
            raise ValueError(decision.message)
        if await self.db.is_destination_suppressed(proposal.destination):
            raise ValueError("The callback destination requested no further calls")
        if await self.db.call_recorded_opt_out(source_call_id):
            raise ValueError("This caller requested no further calls")
        call = await self.db.get_call(source_call_id)
        if call is None:
            raise LookupError("Source call not found")
        plan = await self.db.get_plan_record(call["plan_id"])
        assert plan is not None
        link = await self.db.fetch_one(
            "SELECT phone FROM call_contacts WHERE call_id=?", (source_call_id,)
        )
        original = link["phone"] if link else plan.context.target.phone
        if original and await self.db.is_destination_suppressed(original):
            raise ValueError("This caller requested no further calls")
        if proposal.destination != original and not policy.allow_alternate_number:
            raise ValueError("The callback policy does not permit another number")
        snapshot = await self.db.control_plan(call["plan_id"])
        return {
            **proposal.model_dump(mode="json"),
            "window_start_utc": proposal.window_start.astimezone(UTC).isoformat(),
            "window_end_utc": proposal.window_end.astimezone(UTC).isoformat(),
            "source_brief": plan.context.model_dump(mode="json"),
            "source_direction": (snapshot or {}).get("direction", plan.context.direction),
            "source_business": (snapshot or {}).get("business"),
            "original_number": original,
            "alternate_number": proposal.destination != original,
        }, revision

    async def propose(
        self, call_id: str, proposal: CallbackProposal, language: str = "en"
    ) -> dict[str, Any]:
        value, _ = await self.validate(proposal, call_id)
        result = await self.db.callback_propose(
            call_id, value, callback_readback(proposal, language)
        )
        if result["schedule_id"]:
            return {**result, **self.receipt(await self.get(result["schedule_id"]))}
        return {
            **result,
            "scheduled": bool(result["schedule_id"]),
            "next_action": "The tool speaks this readback. Wait for a clear yes without corrections, then confirm this proposal. Only a saved receipt with scheduled=true permits saying it is scheduled.",
        }

    async def confirm(
        self, call_id: str, confirmation: CallbackConfirmation, *, owner: bool = False
    ) -> dict[str, Any]:
        proposal = await self.db.fetch_one(
            "SELECT * FROM callback_proposals WHERE proposal_id=? AND call_id=?",
            (confirmation.proposal_id, call_id),
        )
        if proposal is None:
            raise LookupError("Callback proposal not found for this call")
        value = json.loads(proposal["value_json"])
        # Return the existing receipt after a lost response, even if the time has since passed.
        saved = await self.db.fetch_one(
            "SELECT s.* FROM callback_schedules s JOIN callback_confirmation_receipts r USING(schedule_id) WHERE r.proposal_id=?",
            (confirmation.proposal_id,),
        )
        if saved:
            if proposal["revision"] != confirmation.proposal_revision:
                raise ValueError("The confirmed proposal revision does not match")
            return self.receipt(schedule_row(saved))
        validated, revision = await self.validate(
            CallbackProposal.model_validate(
                {k: v for k, v in value.items() if k in CallbackProposal.model_fields}
            ),
            call_id,
        )
        if validated != value:
            raise ValueError("The approved source brief changed; propose the callback again")
        if not owner:
            turns = await self.db.fetch_all(
                "SELECT * FROM transcripts WHERE call_id=? AND turn_id IN (?,?) ORDER BY sequence_number",
                (call_id, confirmation.readback_turn_id, confirmation.confirmation_turn_id),
            )
            if len(turns) != 2:
                raise ValueError(
                    "The complete readback and clear caller confirmation must be in the transcript"
                )
            readback, answer = turns
            if (
                readback["turn_id"] != confirmation.readback_turn_id
                or readback["speaker"] != "assistant"
                or answer["speaker"] != "callee"
                or readback["sequence_number"] <= proposal["after_sequence"]
                or normalized_speech(readback["text"]) != normalized_speech(proposal["readback"])
                or ".interrupted" in readback["source_event_type"]
                or normalized_speech(answer["text"]) not in CONFIRMATIONS
            ):
                raise ValueError(
                    "Read back the complete current proposal, then obtain a clear confirmation without corrections"
                )
            # A correction or extra reply between readback and confirmation invalidates this exchange.
            if answer["sequence_number"] != readback["sequence_number"] + 1:
                raise ValueError("Read back the latest details again before confirming")
        result = await self.db.callback_confirm(
            confirmation.proposal_id,
            confirmation.proposal_revision,
            {"actor": "owner" if owner else "caller", **confirmation.model_dump()},
            expected_value=value,
            policy_revision=revision,
        )
        return self.receipt(result)

    @staticmethod
    def receipt(row: dict[str, Any] | None) -> dict[str, Any]:
        assert row is not None
        return {
            "schedule_id": row["schedule_id"],
            "revision": row["revision"],
            "state": row["state"],
            "destination": row["destination"],
            "window_start": row["value"]["window_start"],
            "window_end": row["value"]["window_end"],
            "timezone": row["value"]["timezone"],
            "saved": True,
            "scheduled": row["state"] in {"scheduled", "preparing", "dial_claimed"},
            "attempts_allowed": 1,
            "needed_information": row["value"].get("needed_information", []),
            "next_action": "Saved as pending information; do not promise a scheduled call until the required facts are resolved."
            if row["state"] == "needs_information"
            else "Use the saved state to describe this request accurately.",
        }

    async def get(self, schedule_id: str) -> dict[str, Any]:
        row = schedule_row(
            await self.db.fetch_one(
                "SELECT * FROM callback_schedules WHERE schedule_id=?", (schedule_id,)
            )
        )
        if row is None:
            raise LookupError("Callback not found")
        return row

    async def list(self, status: str = "open", offset: int = 0) -> dict[str, Any]:
        if (
            status
            not in {
                "open",
                "all",
                "scheduled",
                "needs_information",
                "missed",
                "cancelled",
                "attempted",
                "completed",
                "uncertain",
            }
            or offset < 0
        ):
            raise ValueError("Invalid callback filter")
        clause = (
            "state IN ('scheduled','preparing','dial_claimed','needs_information')"
            if status == "open"
            else "1=1"
            if status == "all"
            else "state=?"
        )
        args: tuple[Any, ...] = () if status in {"open", "all"} else (status,)
        rows = await self.db.fetch_all(
            f"SELECT * FROM callback_schedules WHERE {clause} ORDER BY window_start,schedule_id LIMIT 41 OFFSET ?",
            (*args, offset),
        )  # noqa: S608
        return {
            "callbacks": [schedule_row(row) for row in rows[:40]],
            "next_offset": offset + 40 if len(rows) > 40 else None,
        }

    async def update(
        self, schedule_id: str, update: ScheduleUpdate, actor: str = "owner"
    ) -> dict[str, Any]:
        row = await self.get(schedule_id)
        value = row["value"]
        if update.action == "cancel":
            return await self.db.callback_update(
                schedule_id, update.expected_revision, "cancelled", value, update.reason, actor
            )
        if update.action == "resolve_information":
            if row["state"] not in {"needs_information", "scheduled"}:
                raise ValueError(
                    "Reschedule this inactive request before changing required information"
                )
            if (
                value.get("needed_information")
                and not update.needed_information
                and not update.new_facts
            ):
                raise ValueError("Supply the verified answers before clearing required information")
            value = {
                **value,
                "needed_information": update.needed_information,
                "new_facts": [*value.get("new_facts", []), *update.new_facts],
            }
            value["new_facts"] = list(dict.fromkeys(value["new_facts"]))
            if len(value["new_facts"]) > 20:
                raise ValueError("Keep at most 20 verified callback facts")
            ContextPacket.model_validate(
                {
                    **value["source_brief"],
                    "relevant_facts": [
                        *value["source_brief"].get("relevant_facts", []),
                        *value["new_facts"],
                    ],
                }
            )
            state = "needs_information" if update.needed_information else "scheduled"
            return await self.db.callback_update(
                schedule_id, update.expected_revision, state, value, update.reason, actor
            )
        if update.action == "reschedule":
            if update.proposal is None:
                raise ValueError("Supply the newly approved callback details")
            value, revision = await self.validate(update.proposal, row["source_call_id"])
        else:
            policy, revision = await self.policy()
            from zoneinfo import ZoneInfo

            start = datetime.now(UTC) + timedelta(seconds=1)
            zone = ZoneInfo(policy.timezone)
            proposal = CallbackProposal.model_validate(
                {
                    **{k: v for k, v in value.items() if k in CallbackProposal.model_fields},
                    "timezone": policy.timezone,
                    "window_start": start.astimezone(zone),
                    "window_end": (
                        start + timedelta(minutes=policy.default_window_minutes)
                    ).astimezone(zone),
                }
            )
            value, revision = await self.validate(proposal, row["source_call_id"])
        value["new_facts"] = row["value"].get("new_facts", [])
        # Rescheduling is not an answer to an outstanding question.
        value["needed_information"] = list(
            dict.fromkeys(
                [*row["value"].get("needed_information", []), *value.get("needed_information", [])]
            )
        )
        value["policy_revision"] = revision
        value["confirmation"] = {"actor": actor, "reason": update.reason, "owner_requested": True}
        state = "needs_information" if value.get("needed_information") else "scheduled"
        return await self.db.callback_update(
            schedule_id, update.expected_revision, state, value, update.reason, actor
        )

    async def final_dial_claim(self, call_id: str, plan_id: str, schedule_id: str) -> bool:
        row = await self.get(schedule_id)
        policy, revision = await self.policy()
        now = datetime.now(UTC)
        start, end = (
            datetime.fromisoformat(row["window_start"]),
            datetime.fromisoformat(row["window_end"]),
        )
        if not (start <= now < end and policy.permits(start, end)):
            return False
        original = row["value"].get("original_number")
        if await self.db.call_recorded_opt_out(row["source_call_id"]):
            return False
        if original and await self.db.is_destination_suppressed(original):
            return False
        if getattr(self.control.service, "_incoming_pending", None):
            return False
        return await self.db.callback_claim_dial(call_id, plan_id, revision)

    async def _mark_current(self, row: dict[str, Any], state: str, reason: str) -> bool:
        changed = await self.db.execute(
            "UPDATE callback_schedules SET state=?,reason=?,updated_at=? WHERE schedule_id=? "
            "AND revision=? AND state=? AND plan_id IS ? AND dial_claimed=?",
            (
                state,
                reason,
                datetime.now(UTC).isoformat(),
                row["schedule_id"],
                row["revision"],
                row["state"],
                row["plan_id"],
                row["dial_claimed"],
            ),
        )
        return changed == 1

    async def tick(self) -> None:
        service = self.control.service
        now = datetime.now(UTC)
        # Finalize attempt state without ever replacing a started call after a restart.
        rows = await self.db.fetch_all(
            "SELECT s.*,p.call_id started_call FROM callback_schedules s LEFT JOIN plans p ON p.plan_id=s.plan_id WHERE s.state IN ('preparing','dial_claimed')"
        )
        for raw in rows:
            row = schedule_row(raw)
            assert row is not None
            if row["started_call"]:
                call = await self.db.get_call(row["started_call"])
                if call and call["state"] in {s.value for s in TERMINAL_STATES}:
                    state = "attempted" if row["dial_claimed"] else "uncertain"
                    await self.db.execute(
                        "UPDATE callback_schedules SET state=?,call_id=?,reason=?,updated_at=? WHERE schedule_id=? AND state IN ('preparing','dial_claimed') AND revision=? AND plan_id IS ?",
                        (
                            state,
                            call["call_id"],
                            "One attempt finished"
                            if row["dial_claimed"]
                            else "The prepared call ended before a confirmed dial; owner review is required",
                            now.isoformat(),
                            row["schedule_id"],
                            row["revision"],
                            row["plan_id"],
                        ),
                    )
        await self.db.execute(
            "UPDATE callback_schedules SET state='missed',reason='The agreed window ended before a dialing attempt',updated_at=? WHERE state IN ('scheduled','preparing','needs_information') AND dial_claimed=0 AND window_end<=? AND NOT EXISTS(SELECT 1 FROM plans p WHERE p.plan_id=callback_schedules.plan_id AND p.call_id IS NOT NULL)",
            (now.isoformat(), now.isoformat()),
        )
        if (
            service._runtime
            or getattr(service, "_incoming_pending", None)
            or not service.supervision_ready()
            or service.phone.admission_problem()
        ):
            return
        due = await self.db.fetch_one(
            "SELECT * FROM callback_schedules WHERE state IN ('scheduled','preparing') AND dial_claimed=0 AND window_start<=? AND window_end>? ORDER BY window_start,schedule_id LIMIT 1",
            (now.isoformat(), now.isoformat()),
        )
        row = schedule_row(due)
        if row is None:
            return
        policy, _ = await self.policy()
        if not policy.permits(
            datetime.fromisoformat(row["window_start"]), datetime.fromisoformat(row["window_end"])
        ):
            await self._mark_current(
                row, "cancelled", "Current callback policy no longer permits this window"
            )
            return
        value = row["value"]
        if await self.db.call_recorded_opt_out(row["source_call_id"]):
            await self._mark_current(row, "cancelled", "Caller opted out")
            return
        for number in {row["destination"], value.get("original_number")} - {None}:
            if await self.db.is_destination_suppressed(number):
                await self._mark_current(row, "cancelled", "Recipient opted out")
                return
        if row["plan_id"]:
            plan = await self.db.get_plan_record(row["plan_id"])
            if plan is None or plan.call_id:
                return
            if datetime.fromisoformat(plan.expires_at) <= now:
                await self.db.execute(
                    "UPDATE callback_schedules SET plan_id=NULL,state='scheduled' WHERE schedule_id=? AND plan_id=? AND NOT EXISTS(SELECT 1 FROM plans p WHERE p.plan_id=callback_schedules.plan_id AND p.call_id IS NOT NULL)",
                    (row["schedule_id"], row["plan_id"]),
                )
                return
            snapshot = await self.db.control_plan(row["plan_id"])
            assert snapshot is not None
            plan_id, confirmation = row["plan_id"], snapshot["confirmation_summary"]
        else:
            brief = dict(value["source_brief"])
            brief["relevant_facts"] = [
                *brief.get("relevant_facts", []),
                *value.get("new_facts", []),
            ]
            brief["target"] = {**brief["target"], "phone": row["destination"]}
            brief.update(
                objective=value["objective"],
                completion_criteria=None,
                intention="task",
                direction="outbound",
                language=value.get("language") or brief["language"],
            )
            request = PreparePhoneCallInput(
                context=ContextPacket.model_validate(brief),
                requested_by_owner=True,
                authority_basis="Owner's saved callback policy and the confirmed callback receipt "
                + row["schedule_id"],
            )
            prepared = await service.prepare(request, callback_schedule=row)
            if not prepared.plan_id:
                await self._mark_current(row, "missed", "Required execution information is missing")
                return
            if not await self.db.callback_bind_plan(
                row["schedule_id"], row["revision"], prepared.plan_id
            ):
                return
            plan_id, confirmation = prepared.plan_id, prepared.confirmation_summary
        await service.start(plan_id, explicit_confirmation=True, confirmation_text=confirmation)

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                # A readiness or provider failure leaves the same plan for recovery, never a redial.
                logger.warning("Callback dispatcher pass deferred")
            await asyncio.sleep(2)
