"""Keep spoken text and history aligned around identity corrections and interruptions."""

from typing import Any

from pipecat.frames.frames import AggregatedTextFrame
from pipecat.services.inworld.tts import InworldTTSService

from app.provider_usage import UsageLedger
from app.spoken_identity import SpeakerIdentityFilter


class InworldVoiceTTSService(InworldTTSService):
    def __init__(
        self,
        *,
        identity_filter: SpeakerIdentityFilter | None = None,
        usage: UsageLedger | None = None,
        **kwargs: Any,
    ) -> None:
        self.identity_filter = identity_filter
        self.usage = usage
        super().__init__(**kwargs)

    async def start_tts_usage_metrics(self, text: str) -> None:
        # The pinned adapter calls this only after sending the text successfully.
        if self.usage:
            self.usage.tts_characters += len(text)
        await super().start_tts_usage_metrics(text)

    async def _handle_aggregated_text(
        self, src_frame: AggregatedTextFrame, context_id: str | None = None
    ) -> None:
        if self.identity_filter is not None:
            corrected = await self.identity_filter.filter(src_frame.text)
            if corrected != src_frame.text:
                # Ordinary TTS filters map word timing back to the original text.
                # Correct the source too so the transcript reflects the spoken words.
                src_frame.text = corrected
                src_frame.raw_text = corrected
        await super()._handle_aggregated_text(src_frame, context_id)

    async def on_audio_context_interrupted(self, context_id: str) -> None:
        # The pinned adapter otherwise commits the complete generated text when
        # an interruption arrives before asynchronous word timestamps. Only the
        # timed words already played belong in the transcript and model history.
        self._context_texts.pop(context_id, None)
        await super().on_audio_context_interrupted(context_id)
