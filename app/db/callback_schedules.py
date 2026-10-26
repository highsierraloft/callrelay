"""Callback proposals, receipts, and durable execution claims."""

from __future__ import annotations

import json
import secrets
from typing import TYPE_CHECKING, Any

from app.db.control import RevisionConflict
from app.db.engine import _iso_now

if TYPE_CHECKING:
    from app.db.protocols import DatabaseAccess


def schedule_row(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    value = dict(row)
    value["value"] = json.loads(value.pop("value_json"))
    return value


class CallbackSchedulesMixin:
    async def callback_propose(
        self: DatabaseAccess, call_id: str, value: dict[str, Any], readback: str
    ) -> dict[str, Any]:
        now = _iso_now()
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False)
        async with self._immediate_transaction() as conn:
            cursor = await conn.execute(
                "SELECT p.*,r.schedule_id FROM callback_proposals p LEFT JOIN callback_confirmation_receipts r USING(proposal_id) WHERE p.call_id=? AND p.request_key=?",
                (call_id, value["request_key"]),
            )
            old = await cursor.fetchone()
            if old and old["value_json"] == encoded:
                return {
                    "proposal_id": old["proposal_id"],
                    "revision": old["revision"],
                    "readback": old["readback"],
                    "schedule_id": old["schedule_id"],
                }
            if old and old["schedule_id"]:
                raise ValueError(
                    "This proposal has a saved receipt. Use owner rescheduling, or cancel the saved callback before proposing revised details with a new request_key."
                )
            cursor = await conn.execute(
                "SELECT COALESCE(MAX(sequence_number),0) FROM transcripts WHERE call_id=?",
                (call_id,),
            )
            sequence = await cursor.fetchone()
            assert sequence is not None
            identifier = old["proposal_id"] if old else "proposal_" + secrets.token_urlsafe(16)
            revision = old["revision"] + 1 if old else 1
            await conn.execute(
                "INSERT INTO callback_proposals VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(proposal_id) DO UPDATE SET revision=excluded.revision,value_json=excluded.value_json,readback=excluded.readback,updated_at=excluded.updated_at,after_sequence=excluded.after_sequence",
                (
                    identifier,
                    call_id,
                    value["request_key"],
                    revision,
                    encoded,
                    readback,
                    now,
                    now,
                    sequence[0],
                ),
            )
            return {
                "proposal_id": identifier,
                "revision": revision,
                "readback": readback,
                "schedule_id": None,
            }

    async def callback_confirm(
        self: DatabaseAccess,
        proposal_id: str,
        revision: int,
        evidence: dict[str, Any],
        *,
        expected_value: dict[str, Any],
        policy_revision: int,
    ) -> dict[str, Any]:
        now = _iso_now()
        async with self._immediate_transaction() as conn:
            cursor = await conn.execute(
                "SELECT * FROM callback_proposals WHERE proposal_id=?", (proposal_id,)
            )
            proposal = await cursor.fetchone()
            if proposal is None:
                raise LookupError("Callback proposal not found")
            if (
                proposal["revision"] != revision
                or json.loads(proposal["value_json"]) != expected_value
            ):
                raise RevisionConflict(
                    "The proposal changed; read back the new details and confirm again"
                )
            cursor = await conn.execute(
                "SELECT s.* FROM callback_schedules s JOIN callback_confirmation_receipts r USING(schedule_id) WHERE r.proposal_id=?",
                (proposal_id,),
            )
            existing = schedule_row(await cursor.fetchone())
            if existing:
                return existing
            cursor = await conn.execute(
                "SELECT MAX(revision) FROM control_revisions WHERE entity='callback_policy'"
            )
            current_policy = await cursor.fetchone()
            if current_policy is None or current_policy[0] != policy_revision:
                raise RevisionConflict("Callback policy changed; validate the proposal again")
            cursor = await conn.execute(
                "SELECT 1 FROM destination_suppressions WHERE phone IN (?,?)",
                (expected_value["destination"], expected_value.get("original_number")),
            )
            if await cursor.fetchone():
                raise ValueError("The callback destination requested no further calls")
            cursor = await conn.execute(
                "SELECT 1 FROM call_opt_outs WHERE call_id=?", (proposal["call_id"],)
            )
            if await cursor.fetchone():
                raise ValueError("This caller requested no further calls")
            cursor = await conn.execute(
                "SELECT contact_id FROM call_contacts WHERE call_id=?", (proposal["call_id"],)
            )
            link = await cursor.fetchone()
            cursor = await conn.execute(
                "SELECT * FROM callback_schedules WHERE source_call_id=? AND destination=? AND window_start=? AND window_end=? "
                "AND lower(trim(json_extract(value_json,'$.objective')))=lower(trim(?)) AND state!='cancelled' LIMIT 1",
                (
                    proposal["call_id"],
                    expected_value["destination"],
                    expected_value["window_start_utc"],
                    expected_value["window_end_utc"],
                    expected_value["objective"],
                ),
            )
            duplicate = schedule_row(await cursor.fetchone())
            if duplicate:
                if duplicate["value"].get("needed_information", []) != expected_value.get(
                    "needed_information", []
                ) or duplicate["value"].get("language") != expected_value.get("language"):
                    raise ValueError(
                        "An existing callback has different required information or language. Update that request instead of creating another one."
                    )
                await conn.execute(
                    "INSERT INTO callback_confirmation_receipts VALUES (?,?,?)",
                    (proposal_id, duplicate["schedule_id"], now),
                )
                return duplicate
            identifier = "schedule_" + secrets.token_urlsafe(16)
            value = {**expected_value, "confirmation": evidence, "policy_revision": policy_revision}
            state = "needs_information" if value.get("needed_information") else "scheduled"
            await conn.execute(
                "INSERT INTO callback_schedules(schedule_id,proposal_id,source_call_id,contact_id,revision,state,value_json,window_start,window_end,destination,created_at,updated_at) VALUES (?,?,?,?,1,?,?,?,?,?,?,?)",
                (
                    identifier,
                    proposal_id,
                    proposal["call_id"],
                    link["contact_id"] if link else None,
                    state,
                    json.dumps(value, ensure_ascii=False),
                    value["window_start_utc"],
                    value["window_end_utc"],
                    value["destination"],
                    now,
                    now,
                ),
            )
            await conn.execute(
                "INSERT INTO callback_schedule_history VALUES (?,1,?,?,?)",
                (
                    identifier,
                    json.dumps({"state": state, **value}, ensure_ascii=False),
                    evidence["actor"],
                    now,
                ),
            )
            await conn.execute(
                "INSERT INTO callback_confirmation_receipts VALUES (?,?,?)",
                (proposal_id, identifier, now),
            )
            cursor = await conn.execute(
                "SELECT * FROM callback_schedules WHERE schedule_id=?", (identifier,)
            )
            result = schedule_row(await cursor.fetchone())
            assert result is not None
            return result

    async def callback_update(
        self: DatabaseAccess,
        identifier: str,
        expected_revision: int,
        state: str,
        value: dict[str, Any],
        reason: str,
        actor: str,
    ) -> dict[str, Any]:
        now = _iso_now()
        async with self._immediate_transaction() as conn:
            cursor = await conn.execute(
                "SELECT * FROM callback_schedules WHERE schedule_id=?", (identifier,)
            )
            old = await cursor.fetchone()
            if old is None:
                raise LookupError("Callback not found")
            if old["revision"] != expected_revision:
                raise RevisionConflict("This callback changed. Read it again before editing.")
            if old["dial_claimed"]:
                raise ValueError(
                    "The dialing attempt may already be underway; use End Call if needed"
                )
            if old["state"] in {"completed", "attempted", "uncertain"}:
                raise ValueError("This callback already used its automatic attempt")
            revision = old["revision"] + 1
            # A prepared plan is invalidated. Final dialing checks the current plan binding.
            await conn.execute(
                "UPDATE callback_schedules SET revision=?,state=?,value_json=?,window_start=?,window_end=?,destination=?,plan_id=NULL,call_id=NULL,reason=?,updated_at=? WHERE schedule_id=?",
                (
                    revision,
                    state,
                    json.dumps(value, ensure_ascii=False),
                    value["window_start_utc"],
                    value["window_end_utc"],
                    value["destination"],
                    reason,
                    now,
                    identifier,
                ),
            )
            await conn.execute(
                "INSERT INTO callback_schedule_history VALUES (?,?,?,?,?)",
                (
                    identifier,
                    revision,
                    json.dumps({"state": state, "reason": reason, **value}, ensure_ascii=False),
                    actor,
                    now,
                ),
            )
            cursor = await conn.execute(
                "SELECT * FROM callback_schedules WHERE schedule_id=?", (identifier,)
            )
            result = schedule_row(await cursor.fetchone())
            assert result is not None
            return result

    async def callback_bind_plan(
        self: DatabaseAccess, identifier: str, revision: int, plan_id: str
    ) -> bool:
        return await self._execute_cas(
            "UPDATE callback_schedules SET plan_id=?,state='preparing',updated_at=?,contact_id=(SELECT contact_id FROM call_contacts WHERE call_id=callback_schedules.source_call_id) WHERE schedule_id=? AND revision=? "
            "AND state='scheduled' AND dial_claimed=0 AND plan_id IS NULL AND window_end>?",
            (plan_id, _iso_now(), identifier, revision, _iso_now()),
        )

    async def callback_claim_dial(
        self: DatabaseAccess, call_id: str, plan_id: str, policy_revision: int
    ) -> bool:
        """Claim the existing one-shot dial and schedule in the same SQLite transaction."""
        now = _iso_now()
        async with self._immediate_transaction() as conn:
            cursor = await conn.execute(
                "SELECT * FROM callback_schedules WHERE plan_id=? AND state='preparing' AND dial_claimed=0 "
                "AND window_start<=? AND window_end>?",
                (plan_id, now, now),
            )
            row = await cursor.fetchone()
            if row is None:
                return False
            if json.loads(row["value_json"]).get("needed_information"):
                return False
            cursor = await conn.execute(
                "SELECT MAX(revision) FROM control_revisions WHERE entity='callback_policy'"
            )
            policy = await cursor.fetchone()
            if policy is None or policy[0] != policy_revision:
                return False
            cursor = await conn.execute(
                "SELECT 1 FROM destination_suppressions WHERE phone IN (?,?)",
                (row["destination"], json.loads(row["value_json"]).get("original_number")),
            )
            if await cursor.fetchone():
                return False
            cursor = await conn.execute(
                "SELECT 1 FROM call_opt_outs WHERE call_id=?", (row["source_call_id"],)
            )
            if await cursor.fetchone():
                return False
            # Admission's deployment lease check is repeated at the last reversible boundary.
            cursor = await conn.execute(
                "SELECT locked,locked_at FROM deployment_control WHERE singleton=1"
            )
            lock = await cursor.fetchone()
            if lock and lock["locked"]:
                from app.db.deployment import _lock_is_active

                if _lock_is_active(lock["locked_at"]):
                    return False
            cursor = await conn.execute(
                "UPDATE calls SET callee_dialed=1,last_event_at=? WHERE call_id=? AND plan_id=? AND state='ready_to_activate' AND callee_dialed=0 AND termination_claimed=0",
                (now, call_id, plan_id),
            )
            if cursor.rowcount != 1:
                return False
            await conn.execute(
                "UPDATE callback_schedules SET state='dial_claimed',dial_claimed=1,call_id=?,updated_at=? WHERE schedule_id=?",
                (call_id, now, row["schedule_id"]),
            )
            return True
