"""Call outcomes and follow-ups assessed by the owner or Hermes, without another model."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.db.workflows import workflow_row
from app.models import TERMINAL_STATES, CallState, ContextPacket
from app.workflow_models import FollowupUpdate, OutcomeWrite

if TYPE_CHECKING:
    from app.control import ControlPlane


class CallWorkflows:
    def __init__(self, control: ControlPlane) -> None:
        self.control = control
        self.db = control.db

    async def _call(self, call_id: str) -> dict[str, Any]:
        row = await self.db.get_call(call_id)
        if row is None:
            raise LookupError("Call not found")
        return row

    async def save_outcome(self, call_id: str, payload: OutcomeWrite, actor: str) -> dict[str, Any]:
        call = await self._call(call_id)
        if CallState(call["state"]) not in TERMINAL_STATES:
            raise ValueError("Wait until the call ends before saving its outcome")
        evidence = set(payload.evidence_turn_ids)
        for fact in payload.facts:
            evidence.update(fact.evidence_turn_ids)
        for callback in payload.callbacks:
            evidence.update(callback.evidence_turn_ids)
        known = {
            r["turn_id"]
            for r in await self.db.fetch_all(
                "SELECT turn_id FROM transcripts WHERE call_id=?", (call_id,)
            )
        }
        if evidence - known:
            raise ValueError("Evidence must use exact turn_id values from this call's transcript")
        if (
            actor == "hermes"
            and payload.status in {"achieved", "declined"}
            and not payload.evidence_turn_ids
        ):
            raise ValueError(
                "Hermes must cite transcript evidence for an achieved or declined outcome"
            )
        return await self.db.workflow_save_outcome(call_id, payload.model_dump(mode="json"), actor)

    async def assessment(self, call_id: str) -> dict[str, Any] | None:
        return await self.db.workflow_assessment(call_id)

    async def followup(self, followup_id: str) -> dict[str, Any]:
        row = workflow_row(
            await self.db.fetch_one(
                "SELECT * FROM call_followups WHERE followup_id=?", (followup_id,)
            )
        )
        if row is None:
            raise LookupError("Follow-up not found")
        call = await self._call(row["source_call_id"])
        plan = await self.db.get_plan_record(call["plan_id"])
        assert plan is not None
        row["target"] = plan.context.target.model_dump(mode="json")
        row["recipient_opted_out"] = await self.db.is_destination_suppressed(
            plan.context.target.phone
        )
        row["attempts"] = await self.db.fetch_all(
            "SELECT p.plan_id,c.call_id,c.state,c.created_at,c.ended_at,a.value_json assessment_json "
            "FROM call_followup_plans p LEFT JOIN calls c USING(plan_id) "
            "LEFT JOIN call_assessments a ON a.call_id=c.call_id AND a.revision=(SELECT MAX(revision) FROM call_assessments WHERE call_id=c.call_id) "
            "WHERE p.followup_id=? ORDER BY p.created_at DESC LIMIT 20",
            (followup_id,),
        )
        for attempt in row["attempts"]:
            attempt["outcome"] = json.loads(attempt.pop("assessment_json") or "{}").get("status")
        return row

    async def list_followups(
        self, *, status: str = "open", call_id: str | None = None, offset: int = 0, limit: int = 40
    ) -> dict[str, Any]:
        clauses, values = [], []
        if status == "open":
            clauses.append("status IN ('needs_information','ready')")
        elif status != "all":
            if status not in {"needs_information", "ready", "completed", "cancelled"}:
                raise ValueError("Unknown follow-up status")
            clauses.append("status=?")
            values.append(status)
        if call_id:
            clauses.append("source_call_id=?")
            values.append(call_id)
        sql = "SELECT followup_id FROM call_followups" + (
            " WHERE " + " AND ".join(clauses) if clauses else ""
        )
        rows = await self.db.fetch_all(
            sql
            + " ORDER BY COALESCE(julianday(json_extract(value_json,'$.window_start')),5373484),created_at,followup_id LIMIT ? OFFSET ?",
            (*values, limit + 1, offset),
        )
        return {
            "followups": [await self.followup(r["followup_id"]) for r in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None,
            "automatic_dialing": False,
        }

    async def update_followup(
        self, followup_id: str, payload: FollowupUpdate, actor: str
    ) -> dict[str, Any]:
        row = await self.followup(followup_id)
        payload = FollowupUpdate.model_validate(
            {
                **{k: v for k, v in row["value"].items() if k in FollowupUpdate.model_fields},
                **payload.model_dump(exclude_unset=True),
            }
        )
        if any(
            a["call_id"] and a["state"] not in {s.value for s in TERMINAL_STATES}
            for a in row["attempts"]
        ):
            raise ValueError("Wait until the linked call ends before changing this follow-up")
        if payload.completion_call_id:
            call = await self._call(payload.completion_call_id)
            link = await self.db.fetch_one(
                "SELECT 1 FROM call_followup_plans WHERE followup_id=? AND plan_id=?",
                (followup_id, call["plan_id"]),
            )
            if not link or CallState(call["state"]) not in TERMINAL_STATES:
                raise ValueError(
                    "The completion call must be a finished attempt for this follow-up"
                )
        if payload.status == "completed" and actor == "hermes":
            assessment = (
                await self.assessment(payload.completion_call_id)
                if payload.completion_call_id
                else None
            )
            if not assessment or assessment["value"]["status"] not in {"achieved", "declined"}:
                raise ValueError(
                    "Record an evidence-backed achieved or declined outcome for the linked call before completing its follow-up"
                )
        await self.db.workflow_update_followup(followup_id, payload.model_dump(mode="json"), actor)
        return await self.followup(followup_id)

    async def validate_followup_plan(self, followup_id: str, packet: ContextPacket) -> None:
        row = await self.followup(followup_id)
        if row["status"] in {"completed", "cancelled"}:
            raise ValueError("Reopen this follow-up before preparing another attempt")
        if row["status"] != "ready" or row["value"].get("needed_information"):
            raise ValueError(
                "Resolve the missing information and mark this callback ready before preparing it"
            )
        if row["target"]["phone"] != packet.target.phone:
            raise ValueError("A linked callback must use its original recipient")
        if row["recipient_opted_out"]:
            raise ValueError("The recipient requested no further calls")

    async def callback_brief(self, followup_id: str) -> dict[str, Any]:
        row = await self.followup(followup_id)
        call = await self._call(row["source_call_id"])
        plan = await self.db.get_plan_record(call["plan_id"])
        assert plan is not None
        packet = plan.context.model_copy(
            update={
                "objective": row["value"]["objective"],
                "completion_criteria": None,
                "intention": "task",
            }
        )
        await self.validate_followup_plan(followup_id, packet)
        context = packet.model_dump(mode="json")
        context["relevant_facts"] = [*context["relevant_facts"], *row["value"].get("new_facts", [])]
        context = ContextPacket.model_validate(context).model_dump(mode="json")
        context["owner"].pop("callback_number", None)
        context["escalation"].pop("owner_phone", None)
        return {
            "followup_id": followup_id,
            "context": context,
            "needed_information": row["value"]["needed_information"],
            "dialed": False,
            "next_action": "Review the task and resolve missing facts. Use prepare_phone_call with this followup_id and the normal owner authorization. This lookup does not prepare or dial a call.",
        }
