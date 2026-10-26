"""Bound live callback tools and an isolated in-memory practice implementation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.callbacks import CONFIRMATIONS, callback_readback, normalized_speech
from app.reception_models import (
    CallbackConfirmation,
    CallbackPolicy,
    CallbackProposal,
    ScheduleUpdate,
)

if TYPE_CHECKING:
    from app.android_calls import AndroidCallService, _Call


def callback_tools() -> list[dict[str, Any]]:
    proposal_schema = CallbackProposal.model_json_schema()
    proposal_schema["properties"]["language"] = {
        "type": "string",
        "enum": ["en", "es", "ru", "uk"],
        "description": "The current conversation language, including an explicit language-switch request.",
    }
    proposal_schema["required"].append("language")
    return [
        {"type": "function", "function": function}
        for function in [
            {
                "name": "propose_callback",
                "description": "Propose a callback on this same approved task. Supply a complete destination number, exact date, timezone, and short window. This does not schedule anything. The tool speaks the readback automatically. Do not say it separately. Wait for a clear yes. Corrections require proposing the corrected details again with the same request_key before confirmation.",
                "parameters": proposal_schema,
            },
            {
                "name": "confirm_callback",
                "description": "Save the latest proposal only after its full readback and a clear caller confirmation without corrections. The server checks actual heard transcript turns. Only a saved receipt with scheduled=true permits saying the callback is scheduled. Requests needing information remain pending. Do not invent transcript or confirmation details.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "proposal_id": {"type": "string"},
                        "proposal_revision": {"type": "integer"},
                    },
                    "required": ["proposal_id", "proposal_revision"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "cancel_callback",
                "description": "Cancel a callback saved during this conversation when the caller withdraws it. Use its receipt ID and revision. A dialing attempt already underway cannot be cancelled by this tool.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "schedule_id": {"type": "string"},
                        "revision": {"type": "integer"},
                    },
                    "required": ["schedule_id", "revision"],
                    "additionalProperties": False,
                },
            },
        ]
    ]


async def live_callback_tool(
    service: AndroidCallService, runtime: _Call, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    await runtime.transcript.join()
    if runtime.ending_after is not None:
        raise ValueError("The call is ending; no new callback can be scheduled")
    callbacks = service.control.callbacks
    if name == "propose_callback":
        if arguments.get("language") not in (runtime.configuration or {}).get("voice", {}).get(
            "conversation_languages", ["en", "es", "ru", "uk"]
        ):
            raise ValueError("Choose an enabled conversation language for the readback")
        return await callbacks.propose(
            runtime.call_id,
            CallbackProposal.model_validate(arguments),
            runtime.packet.language or "en",
        )
    if name == "confirm_callback":
        turns = await service.db.fetch_all(
            "SELECT turn_id FROM transcripts WHERE call_id=? ORDER BY sequence_number DESC LIMIT 2",
            (runtime.call_id,),
        )
        if len(turns) != 2:
            raise ValueError("Read the proposal back and obtain a clear confirmation first")
        return await callbacks.confirm(
            runtime.call_id,
            CallbackConfirmation(
                **arguments,
                confirmation_turn_id=turns[0]["turn_id"],
                readback_turn_id=turns[1]["turn_id"],
            ),
        )
    if name == "cancel_callback":
        row = await callbacks.get(str(arguments.get("schedule_id", "")))
        if row["source_call_id"] != runtime.call_id:
            raise ValueError("This conversation does not own that callback")
        changed = await callbacks.update(
            row["schedule_id"],
            ScheduleUpdate.model_validate(
                {
                    "expected_revision": arguments.get("revision"),
                    "action": "cancel",
                    "reason": "Caller withdrew the request",
                }
            ),
            "caller",
        )
        return callbacks.receipt(changed)
    raise ValueError("Unknown callback action")


class PracticeCallbacks:
    """Uses no database, telephone transport, or real contact identifier."""

    def __init__(self, policy: dict[str, Any], language: str) -> None:
        self.policy, self.language = CallbackPolicy.model_validate(policy), language
        self.proposals: dict[str, dict[str, Any]] = {}
        self.receipts: dict[str, dict[str, Any]] = {}
        self.messages: list[tuple[str, str, bool]] = []

    def observe(self, speaker: str, text: str, interrupted: bool = False) -> None:
        self.messages.append((speaker, text, interrupted))
        self.messages = self.messages[-4:]

    async def tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "propose_callback":
            proposal = CallbackProposal.model_validate(arguments)
            if proposal.window_start <= datetime.now(UTC) or not self.policy.permits(
                proposal.window_start, proposal.window_end
            ):
                raise ValueError("Choose a future window within the saved callback hours")
            key = "practice_proposal_" + proposal.request_key
            old = self.proposals.get(key)
            revision = (
                old["revision"] + 1
                if old and old["value"] != proposal.model_dump(mode="json")
                else old["revision"]
                if old
                else 1
            )
            row: dict[str, Any] = {
                "proposal_id": key,
                "revision": revision,
                "readback": callback_readback(proposal, self.language),
                "value": proposal.model_dump(mode="json"),
            }
            self.proposals[key] = row
            return {**row, "practice_only": True, "scheduled": False}
        if name == "confirm_callback":
            proposed = self.proposals.get(str(arguments.get("proposal_id")))
            if not proposed or proposed["revision"] != arguments.get("proposal_revision"):
                raise ValueError("Read the current proposal back first")
            if len(self.messages) < 2:
                raise ValueError("Wait for caller confirmation")
            a, b = self.messages[-2:]
            if (
                a[0] != "assistant"
                or a[2]
                or normalized_speech(a[1]) != normalized_speech(proposed["readback"])
                or b[0] != "callee"
                or normalized_speech(b[1]) not in CONFIRMATIONS
            ):
                raise ValueError("Read back the full proposal and wait for a clear yes")
            key = proposed["proposal_id"].replace("proposal", "schedule")
            self.receipts.setdefault(
                key,
                {
                    "schedule_id": key,
                    "revision": 1,
                    "state": "needs_information"
                    if proposed["value"].get("needed_information")
                    else "scheduled",
                    "scheduled": not bool(proposed["value"].get("needed_information")),
                    "practice_only": True,
                    "saved": True,
                    **proposed["value"],
                },
            )
            return self.receipts[key]
        if name == "cancel_callback":
            receipt = self.receipts.get(str(arguments.get("schedule_id")))
            if not receipt or receipt["revision"] != arguments.get("revision"):
                raise ValueError("Use the latest practice receipt")
            receipt.update(state="cancelled", scheduled=False, revision=receipt["revision"] + 1)
            return receipt
        raise ValueError("Unknown callback action")
