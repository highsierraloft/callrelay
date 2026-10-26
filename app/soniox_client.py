"""Keep Pipecat's Soniox parser while handling error-only provider messages."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.soniox.stt import SonioxSTTService

from app.provider_usage import UsageLedger


class _SonioxMessages:
    def __init__(self, websocket: Any) -> None:
        self.websocket = websocket

    async def __aiter__(self) -> AsyncIterator[str | bytes]:
        async for message in self.websocket:
            try:
                data = json.loads(message)
            except (ValueError, TypeError):
                yield message
                continue
            # Pipecat 1.13 reads tokens before checking Soniox's error fields.
            # Error-only responses must reach its normal pipeline error handling.
            if (
                isinstance(data, dict)
                and "tokens" not in data
                and (data.get("error_code") or data.get("error_message") or data.get("finished"))
            ):
                data["tokens"] = []
                message = json.dumps(data)
            yield message


class SonioxVoiceSTTService(SonioxSTTService):
    def __init__(
        self,
        *,
        usage: UsageLedger | None = None,
        on_language: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        self.usage = usage
        self.on_language = on_language
        super().__init__(**kwargs)

    async def push_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        if self.on_language and isinstance(frame, TranscriptionFrame) and frame.language:
            language = str(getattr(frame.language, "value", frame.language)).split("-")[0]
            self.on_language(language)
        await super().push_frame(frame, direction)

    async def _connect_websocket(self) -> None:
        await super()._connect_websocket()  # type: ignore[no-untyped-call]
        if self.usage and self._websocket is not None:
            self.usage.start_stt()

    async def _disconnect_websocket(self) -> None:
        try:
            await super()._disconnect_websocket()  # type: ignore[no-untyped-call]
        finally:
            if self.usage:
                self.usage.stop_stt()

    def _get_websocket(self) -> _SonioxMessages:
        return _SonioxMessages(super()._get_websocket())  # type: ignore[no-untyped-call]
