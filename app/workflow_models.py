"""Owner and Hermes assessments stay separate from the original call record."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Outcome = Literal["achieved", "declined", "incomplete", "uncertain"]
FollowupState = Literal["needs_information", "ready", "completed", "cancelled"]


class EvidenceFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1500)
    evidence_turn_ids: list[str] = Field(min_length=1, max_length=20)


class CallbackDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_key: str = Field(
        pattern=r"^[a-zA-Z0-9_-]{1,80}$",
        description="Stable key for this promise within its source call; reuse it on retries.",
    )
    objective: str = Field(min_length=1, max_length=4000)
    source: Literal["agent_promise", "recipient_request", "owner_request"]
    needed_information: list[str] = Field(default_factory=list, max_length=30)
    evidence_turn_ids: list[str] = Field(default_factory=list, max_length=20)
    window_start: datetime | None = None
    window_end: datetime | None = None
    timezone: str | None = Field(default=None, max_length=80)

    @field_validator("needed_information")
    @classmethod
    def bounded_information(cls, value: list[str]) -> list[str]:
        if any(not x.strip() or len(x) > 1500 for x in value):
            raise ValueError("Each missing fact must contain 1–1500 characters")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def valid_window(self) -> CallbackDraft:
        if self.window_end and not self.window_start:
            raise ValueError("An end time needs a start time")
        if self.window_start:
            if self.window_start.utcoffset() is None or (
                self.window_end and self.window_end.utcoffset() is None
            ):
                raise ValueError("Callback times need an explicit UTC offset")
            if not self.timezone:
                raise ValueError("Include the agreed timezone for a callback window")
            if self.window_end and self.window_end < self.window_start:
                raise ValueError("Callback window ends before it starts")
        if self.timezone:
            try:
                ZoneInfo(self.timezone)
            except ZoneInfoNotFoundError as exc:
                raise ValueError("Use a valid IANA timezone") from exc
        if self.source != "owner_request" and not self.evidence_turn_ids:
            raise ValueError("A call promise or recipient request needs transcript evidence")
        return self


class OutcomeWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    request_id: str = Field(
        pattern=r"^[a-zA-Z0-9_-]{1,100}$",
        description="Keep this ID unchanged when retrying the same save after a lost response.",
    )
    status: Outcome
    explanation: str = Field(min_length=1, max_length=4000)
    evidence_turn_ids: list[str] = Field(default_factory=list, max_length=30)
    facts: list[EvidenceFact] = Field(default_factory=list, max_length=30)
    callbacks: list[CallbackDraft] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def unique_callbacks(self) -> OutcomeWrite:
        keys = [v.source_key for v in self.callbacks]
        if len(keys) != len(set(keys)):
            raise ValueError("Callback keys must be unique within the call")
        return self


class FollowupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    status: FollowupState
    objective: str = Field(min_length=1, max_length=4000)
    needed_information: list[str] = Field(default_factory=list, max_length=30)
    window_start: datetime | None = None
    window_end: datetime | None = None
    timezone: str | None = Field(default=None, max_length=80)
    new_facts: list[str] = Field(default_factory=list, max_length=30)
    note: str = Field(default="", max_length=3000)
    completion_call_id: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def valid_update(self) -> FollowupUpdate:
        CallbackDraft(
            source_key="validation",
            source="owner_request",
            **self.model_dump(
                exclude={"expected_revision", "status", "note", "completion_call_id", "new_facts"}
            ),
        )
        CallbackDraft.bounded_information(self.new_facts)
        if self.status == "completed" and not self.note.strip():
            raise ValueError("Explain how this follow-up was completed")
        if self.status == "ready" and self.needed_information:
            raise ValueError("Resolve the missing information before marking a callback ready")
        return self
