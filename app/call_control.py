"""The existing MCP surface shared by independently implemented telephone backends."""

from __future__ import annotations

from typing import Any, Protocol

from app.models import (
    CallSnapshot,
    PreparePhoneCallInput,
    PreparePhoneCallOutput,
    StartPhoneCallOutput,
)
from app.records import AnswerCommand
from app.settings import Settings


class CallControl(Protocol):
    settings: Settings

    async def prepare(self, request: PreparePhoneCallInput) -> PreparePhoneCallOutput: ...

    async def start(
        self, plan_id: str, *, explicit_confirmation: bool, confirmation_text: str
    ) -> StartPhoneCallOutput: ...

    async def get_result(self, call_id: str, *, after_sequence: int = 0) -> dict[str, Any]: ...

    async def get_plan_status(self, plan_id: str) -> dict[str, Any]: ...

    async def get_snapshot(self, call_id: str) -> CallSnapshot: ...

    async def terminate_call(self, call_id: str, reason: str) -> bool: ...

    async def wait_for_call_event(
        self, call_id: str, after_sequence: int = 0, timeout_seconds: float = 20.0
    ) -> dict[str, Any]: ...

    async def answer_call_question(self, command: AnswerCommand) -> dict[str, Any]: ...

    async def recover_startup(self) -> None: ...

    async def start_watchdog(self) -> None: ...

    async def stop(self) -> None: ...

    def supervision_ready(self) -> bool: ...
