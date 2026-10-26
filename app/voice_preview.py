"""Click-driven, unbilled provider samples. No synthesis fallback."""

from __future__ import annotations

import asyncio
import base64
import binascii
import time
from collections import OrderedDict

import httpx

from app.voice_catalog import TTS_MODELS, VoiceCatalog


class PreviewUnavailable(ValueError):
    def __init__(self, message: str, status: int = 503, retry_after: int = 0) -> None:
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


class VoicePreviews:
    ttl = 24 * 60 * 60
    max_bytes = 32 * 1024 * 1024

    def __init__(self, catalog: VoiceCatalog) -> None:
        self.catalog = catalog
        self._lock = asyncio.Lock()
        self._cache: OrderedDict[tuple[str, str], tuple[float, bytes]] = OrderedDict()
        self._size = 0
        self._retry_at = 0.0

    async def get(self, voice: str, model: str) -> bytes:
        if model not in {m["id"] for m in TTS_MODELS}:
            raise PreviewUnavailable("Choose a supported speech model.", 422)
        async with self._lock:
            now = time.monotonic()
            for key, (created, data) in list(self._cache.items()):
                if now - created >= self.ttl:
                    self._size -= len(data)
                    del self._cache[key]
            key = (voice, model)
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key][1]
            if now < self._retry_at:
                raise PreviewUnavailable(
                    "Provider samples are rate limited. Try again shortly.",
                    429,
                    max(1, int(self._retry_at - now)),
                )
            voices, _ = await self.catalog._load("voices", self.catalog._voices)
            if not any(v["id"] == voice for v in voices):
                raise PreviewUnavailable(
                    "A sample is unavailable for this voice. Reload voice choices and try again.",
                    404,
                )
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                    payload = await self.catalog._get(
                        client,
                        "https://api.inworld.ai/tts/v1/voice:preview",
                        {
                            "Authorization": "Basic "
                            + self.catalog.config.inworld_key().get_secret_value()
                        },
                        {"voice_id": voice, "model_id": model},
                    )
                data = await asyncio.to_thread(
                    base64.b64decode, payload["audioContent"], validate=True
                )
                if not data or not (
                    data.startswith(b"ID3")
                    or (len(data) > 1 and data[0] == 255 and data[1] & 224 == 224)
                ):
                    raise ValueError("Invalid MP3")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    retry = exc.response.headers.get("Retry-After", "30")
                    seconds = min(300, max(1, int(retry))) if retry.isdigit() else 30
                    self._retry_at = time.monotonic() + seconds
                    raise PreviewUnavailable(
                        "Provider samples are rate limited. Try again shortly.", 429, seconds
                    ) from None
                raise PreviewUnavailable(
                    "This provider sample is unavailable. Try again later.",
                    404 if exc.response.status_code == 404 else 503,
                ) from None
            except (httpx.HTTPError, ValueError, KeyError, TypeError, binascii.Error):
                raise PreviewUnavailable(
                    "Could not load this provider sample. Try again later."
                ) from None
            if len(data) > self.max_bytes:
                raise PreviewUnavailable("This provider sample is too large.")
            while self._size + len(data) > self.max_bytes:
                _, (_, removed) = self._cache.popitem(last=False)
                self._size -= len(removed)
            self._cache[key] = (time.monotonic(), data)
            self._size += len(data)
            return data
