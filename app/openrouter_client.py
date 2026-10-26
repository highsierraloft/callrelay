"""Keep Pipecat's OpenRouter behavior while applying the call's HTTP timeouts."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

from openai import AsyncOpenAI, AsyncStream
from openai.types.chat import ChatCompletionChunk
from openai.types.completion_usage import CompletionUsage
from pipecat.frames.frames import (
    Frame,
    FunctionCallsStartedFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.metrics.metrics import LLMTokenUsage
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.openrouter.llm import OpenRouterLLMService

from app.phone_menu import is_hold_prompt, is_phone_menu_prompt
from app.provider_usage import UsageLedger


class OpenRouterVoiceLLMService(OpenRouterLLMService):
    generated_response_text = ""
    response_serial = 0

    def __init__(
        self, *, usage: UsageLedger | None = None, phone_menu_enabled: bool = False, **kwargs: Any
    ) -> None:
        self.usage = usage
        self._phone_menu_enabled = phone_menu_enabled
        self._buffer_menu_speech = False
        self._silent_hold_turn = False
        self._menu_text: list[LLMTextFrame] = []
        self._menu_tool_started = False
        self._usage_request: ContextVar[int | None] = ContextVar("usage_request", default=None)
        super().__init__(**kwargs)

    async def get_chat_completions(self, context: LLMContext) -> AsyncStream[ChatCompletionChunk]:
        latest = next(
            (
                m.get("content")
                for m in reversed(context.messages)
                if isinstance(m, dict) and m.get("role") == "user"
            ),
            None,
        )
        self._buffer_menu_speech = (
            self._phone_menu_enabled and isinstance(latest, str) and is_phone_menu_prompt(latest)
        )
        self._silent_hold_turn = (
            self._buffer_menu_speech and isinstance(latest, str) and is_hold_prompt(latest)
        )
        if self.usage:
            self._usage_request.set(self.usage.start_llm())
        return await super().get_chat_completions(context)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, InterruptionFrame):
            self._menu_text.clear()
            self._menu_tool_started = True
        await super().process_frame(frame, direction)

    def _token_usage(self, usage: CompletionUsage) -> LLMTokenUsage:
        request_id = self._usage_request.get()
        if self.usage and request_id is not None:
            self.usage.record_llm(request_id, usage.model_dump())
        return super()._token_usage(usage)

    async def push_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, FunctionCallsStartedFrame) and self._buffer_menu_speech:
                # Event handlers run in background tasks. Use the ordered frame so a
                # fast TTS service cannot receive a preamble before the handler runs.
                self._menu_tool_started = True
                self._menu_text.clear()
            elif isinstance(frame, LLMFullResponseStartFrame):
                self.generated_response_text = ""
                self.response_serial += 1
                self._menu_text.clear()
                self._menu_tool_started = False
            elif isinstance(frame, LLMTextFrame):
                if self._buffer_menu_speech:
                    if not self._menu_tool_started:
                        self._menu_text.append(frame)
                    return
                self.generated_response_text += frame.text
            elif isinstance(frame, LLMFullResponseEndFrame):
                # Tool preambles are not speech. A normal answer to a phrase containing
                # a menu cue is still heard, after the model has completed that turn.
                if not self._menu_tool_started and not self._silent_hold_turn:
                    for part in self._menu_text:
                        self.generated_response_text += part.text
                        await super().push_frame(part, direction)
                self._menu_text.clear()
        await super().push_frame(frame, direction)

    def create_client(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        organization: str | None = None,
        project: str | None = None,
        default_headers: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> AsyncOpenAI:
        # Keep request capture, timeouts, retry policy, and cleanup on the session-owned client.
        client = kwargs.get("http_client")
        if client is None or not api_key or not base_url:
            raise ValueError("The voice model requires an explicit client and credentials")
        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            organization=organization,
            project=project,
            default_headers=default_headers,
            http_client=client,
            max_retries=0,
        )
