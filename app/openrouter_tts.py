"""Use Pipecat speech processing with OpenRouter's PCM speech endpoint."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from email.message import Message
from typing import Any

import httpx
from pipecat.frames.frames import CancelWorkerFrame, ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService

OPENROUTER_API_URL = "https://openrouter.ai/api/v1"
PCM_RATE = 24_000


def validate_pcm_content_type(value: str) -> None:
    message = Message()
    message["content-type"] = value
    if (
        message.get_content_type() != "audio/pcm"
        or message.get_param("rate") != str(PCM_RATE)
        or message.get_param("channels") != "1"
    ):
        raise ValueError("The speech response must explicitly identify 24 kHz mono PCM")


class OpenRouterTTSService(TTSService):
    """Keep provider voice names intact; OpenAI's adapter restricts them to its own voices."""

    Settings = TTSSettings

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        voice: str,
        http_client: httpx.AsyncClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            sample_rate=PCM_RATE,
            push_start_frame=True,
            push_stop_frames=True,
            settings=TTSSettings(model=model, voice=voice, language=None),
            **kwargs,
        )
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(20, connect=5), follow_redirects=False
        )

    async def cleanup(self) -> None:
        try:
            await super().cleanup()  # type: ignore[no-untyped-call]
        finally:
            if self._owns_client:
                await self._http.aclose()

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        if not text.strip():
            return
        await self.start_ttfb_metrics()
        try:
            async with self._http.stream(
                "POST",
                OPENROUTER_API_URL + "/audio/speech",
                headers={"Authorization": "Bearer " + self._api_key},
                json={
                    "model": self._model,
                    "voice": self._voice,
                    "input": text,
                    "response_format": "pcm",
                },
            ) as response:
                response.raise_for_status()
                validate_pcm_content_type(response.headers.get("content-type", ""))
                await self.start_tts_usage_metrics(text)
                pending = b""
                total = 0
                async for chunk in response.aiter_bytes(4096):
                    pending += chunk
                    total += len(chunk)
                    if total > PCM_RATE * 2 * 120:
                        raise ValueError("Speech response exceeded the two-minute segment limit")
                    size = len(pending) - len(pending) % 2
                    if size:
                        await self.stop_ttfb_metrics()
                        yield TTSAudioRawFrame(pending[:size], PCM_RATE, 1, context_id=context_id)
                        pending = pending[size:]
                if pending or total == 0:
                    raise ValueError("Speech response was empty or ended inside a PCM sample")
        except httpx.HTTPStatusError as exc:
            yield ErrorFrame(
                error=f"OpenRouter speech request failed: HTTP {exc.response.status_code}",
            )
            yield CancelWorkerFrame(reason="speech_provider_failed")
        except (httpx.RequestError, ValueError):
            yield ErrorFrame(error="OpenRouter speech transport or audio format failed")
            yield CancelWorkerFrame(reason="speech_transport_failed")
