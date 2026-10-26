"""Hermes writes assessments and manages callbacks without another summary model."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from app.call_control import CallControl
from app.workflow_models import FollowupUpdate, OutcomeWrite
from app.workflows import CallWorkflows


def register_workflow_tools(
    mcp: FastMCP, get_service: Callable[[], CallControl], auth_meta: dict[str, Any] | None
) -> None:
    def workflows() -> CallWorkflows:
        from app.android_calls import AndroidCallService

        service = get_service()
        if not isinstance(service, AndroidCallService):
            raise ToolError("Call workflow storage is unavailable")
        return service.control.workflows

    @mcp.tool(
        name="save_call_outcome",
        meta=auth_meta,
        annotations={"readOnlyHint": False, "openWorldHint": False, "idempotentHint": True},
        description="After reading the entire finished-call transcript, store Hermes's task assessment, confirmed facts, and any callback promises. Cite exact turn_id values from that call; achieved/declined outcomes require evidence. This never changes the transcript, schedules a call, or adds an analysis model. Use expected_revision from get_call_result (zero if assessment is absent). Reuse request_id for an identical retry; use stable callback source_key values to avoid duplicate work. A callback needs an agreed timezone and offset only when a time was actually agreed; do not invent a date. Callback records are work to do, not owner authorization to dial.",
    )
    async def save_call_outcome(call_id: str, outcome: OutcomeWrite) -> dict[str, Any]:
        try:
            return await workflows().save_outcome(call_id, outcome, "hermes")
        except (ValueError, LookupError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name="get_call_followups",
        meta=auth_meta,
        annotations={"readOnlyHint": True, "openWorldHint": False},
        description="Read callback work and its linked call attempts. Defaults to open follow-ups; pass a followup_id for its editable details and suggested brief. Nothing is prepared, scheduled, or dialed. Respect missing information, existing owner authority, and recipient opt-outs. Prepare an authorized callback through prepare_phone_call with the returned followup_id, then use the usual confirmation/start/poll/result workflow. Never blindly redial after an uncertain start; recover by plan_id.",
    )
    async def get_call_followups(
        followup_id: str | None = None, status: str = "open", offset: int = 0
    ) -> dict[str, Any]:
        try:
            if followup_id:
                row = await workflows().followup(followup_id)
                if row["status"] == "ready" and not row["recipient_opted_out"]:
                    row["suggested_call"] = await workflows().callback_brief(followup_id)
                return row
            if not 0 <= offset <= 100000:
                raise ValueError("Invalid follow-up offset")
            return await workflows().list_followups(status=status, offset=offset)
        except (ValueError, LookupError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name="update_call_followup",
        meta=auth_meta,
        annotations={"readOnlyHint": False, "openWorldHint": False},
        description="Update tracked callback work with its expected revision. Resolve missing facts before marking ready. This does not dial or schedule anything. For completed, provide a linked terminal completion_call_id whose saved assessment is achieved or declined, plus an explanatory note. Use cancelled when the owner withdraws the work. A technical call disconnect alone never completes a follow-up. A stale revision is a conflict; read get_call_followups before retrying.",
    )
    async def update_call_followup(followup_id: str, update: FollowupUpdate) -> dict[str, Any]:
        try:
            return await workflows().update_followup(followup_id, update, "hermes")
        except (ValueError, LookupError) as exc:
            raise ToolError(str(exc)) from exc
