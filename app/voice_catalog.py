"""Read provider catalogs without sending provider credentials to the browser."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.android_config import AndroidConfig
from app.model_capabilities import reviewed_model

TTS_MODELS = [
    {
        "id": "inworld-tts-2",
        "name": "Inworld TTS-2 · expressive",
        "supports_delivery": True,
    },
    {
        "id": "inworld-tts-2-flash",
        "name": "Inworld TTS-2 Flash · faster",
        "supports_delivery": False,
    },
]


class VoiceCatalog:
    def __init__(self, config: AndroidConfig, openrouter_key: str) -> None:
        self.config, self._key = config, openrouter_key
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    async def _get(
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        async with client.stream("GET", url, headers=headers, params=params) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 4 * 1024 * 1024:
                    raise ValueError("Catalog response is too large")
        parsed = await asyncio.to_thread(json.loads, data)
        if not isinstance(parsed, dict):
            raise ValueError("Invalid catalog")
        return parsed

    async def _load(
        self, name: str, fetch: Callable[[], Awaitable[list[dict[str, Any]]]]
    ) -> tuple[list[dict[str, Any]], bool]:
        async with self._locks.setdefault(name, asyncio.Lock()):
            cached = self._cache.get(name)
            if cached and time.monotonic() - cached[0] < 3600:
                return cached[1], True
            try:
                values = await fetch()
                if not values:
                    raise ValueError("Empty catalog")
                self._cache[name] = (time.monotonic(), values)
                return values, True
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                return (cached[1] if cached else []), False

    async def _models(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            data = await self._get(
                client,
                "https://openrouter.ai/api/v1/models",
                {"Authorization": "Bearer " + self._key},
                {"supported_parameters": "tools,tool_choice", "output_modalities": "text"},
            )
        models = []
        for model in data.get("data", []):
            if (
                not isinstance(model, dict)
                or not isinstance(model.get("id"), str)
                or not {"tools", "tool_choice"} <= set(model.get("supported_parameters", []))
                or ":batch" in model["id"]
            ):
                continue
            reasoning = model.get("reasoning") or (reviewed_model(model["id"]) or {}).get(
                "reasoning", {}
            )
            pricing = model.get("pricing") or {}
            models.append(
                {
                    "id": model["id"],
                    "name": model.get("name") or model["id"],
                    "supported_parameters": model["supported_parameters"],
                    "reasoning": {
                        key: reasoning[key]
                        for key in ["mandatory", "supported_efforts", "default_effort"]
                        if key in reasoning
                    },
                    "pricing": {k: pricing[k] for k in ["prompt", "completion"] if k in pricing},
                    "source": "OpenRouter model catalog",
                }
            )
        return sorted(models, key=lambda m: str(m["name"]).casefold())

    async def _voices(self) -> list[dict[str, Any]]:
        headers = {"Authorization": "Basic " + self.config.inworld_key().get_secret_value()}
        cursor, seen, voices = "", set(), {}
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            for _ in range(10):
                page = await self._get(
                    client,
                    "https://api.inworld.ai/voices/v1/voices",
                    headers,
                    {"pageSize": "200", "pageToken": cursor},
                )
                for voice in page.get("voices", []):
                    if not isinstance(voice, dict) or not isinstance(voice.get("voiceId"), str):
                        continue
                    voices[voice["voiceId"]] = {
                        "id": voice["voiceId"],
                        "name": str(voice.get("displayName") or voice["voiceId"]),
                        "description": str(voice.get("description", ""))[:500],
                        "language": voice.get("languageCode") or voice.get("langCode") or "",
                    }
                cursor = page.get("nextPageToken") or ""
                if not cursor:
                    return sorted(voices.values(), key=lambda v: v["name"].casefold())
                if cursor in seen:
                    raise ValueError("Repeated catalog cursor")
                seen.add(cursor)
        raise ValueError("Too many catalog pages")

    async def _stt_models(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            data = await self._get(
                client,
                "https://api.soniox.com/v1/models",
                {"Authorization": "Bearer " + self.config.soniox_key().get_secret_value()},
            )
        # This transport sends these three endpoint controls. Offline transcription models
        # and real-time models without those controls cannot run this voice pipeline.
        required = [
            "supports_max_endpoint_delay",
            "supports_endpoint_sensitivity",
            "supports_endpoint_latency_adjustment",
        ]
        return [
            {
                "id": model["id"],
                "name": model.get("name") or model["id"],
                "aliased_model_id": model.get("aliased_model_id"),
                "max_latency_level": model.get("endpoint_latency_adjustment_max_level", 0),
            }
            for model in data.get("models", [])
            if isinstance(model, dict)
            and isinstance(model.get("id"), str)
            and model.get("transcription_mode") == "real_time"
            and all(model.get(key) for key in required)
        ]

    async def model(self, model_id: str) -> dict[str, Any]:
        cached = self._cache.get("models")
        if cached and time.monotonic() - cached[0] < 3600:
            models = cached[1]
        else:
            fallback = reviewed_model(model_id)
            if fallback:
                return fallback
            models, _ = await self._load("models", self._models)
        for model in models:
            if model["id"] == model_id:
                return model
        raise ValueError("Model unavailable or missing tool support. Refresh the model choices.")

    async def validate_stt(self, model_id: str, latency_level: int) -> None:
        if model_id == "stt-rt-v5" and "stt_models" not in self._cache:
            return  # Reviewed fallback for the unchanged deployment during a catalog outage.
        models, _ = await self._load("stt_models", self._stt_models)
        model = next((m for m in models if m["id"] == model_id), None)
        if not model:
            raise ValueError("Select a compatible Soniox real-time model from the speech list.")
        if latency_level > model["max_latency_level"]:
            raise ValueError("The selected Soniox model does not support this latency level.")

    async def get(self) -> dict[str, Any]:
        groups = [
            ("voices", self._voices, "Inworld voices"),
            ("models", self._models, "OpenRouter models"),
            ("stt_models", self._stt_models, "Soniox real-time models"),
        ]
        values = await asyncio.gather(*(self._load(name, fetch) for name, fetch, _ in groups))
        result: dict[str, Any] = {"tts_models": TTS_MODELS, "errors": []}
        for (name, _, label), (items, fresh) in zip(groups, values, strict=True):
            result[name] = items
            if not fresh:
                result["errors"].append(
                    f"{label} could not be refreshed. Kept the last available choices and your selection."
                )
        return result
