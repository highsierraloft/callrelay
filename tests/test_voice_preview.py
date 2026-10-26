from __future__ import annotations

import asyncio
import base64
import time

import pytest
import respx

from app.voice_preview import PreviewUnavailable
from tests import test_android_calls as phone_tests
from tests import test_control_panel as control_tests

android = phone_tests.android
panel = control_tests.panel
URL = "https://api.inworld.ai/tts/v1/voice:preview"
SAMPLE = b"ID3" + b"sample-audio" * 10


@pytest.fixture
async def previews(panel, tmp_path):
    key = tmp_path / "inworld-key"
    key.write_text("dummy-inworld-secret-key")
    panel.h.service.config.inworld_key_file = key
    p = panel.h.service.control.voice_previews
    p.catalog._cache["voices"] = (time.monotonic(), [{"id": "Wade"}, {"id": "Alex"}])
    return p


async def test_preview_click_fetch_is_cached_and_same_voice_requests_coalesce(panel, previews):
    with respx.mock as mock:
        route = mock.get(URL).respond(200, json={"audioContent": base64.b64encode(SAMPLE).decode()})
        responses = await asyncio.gather(
            *(
                panel.client.get("/admin/api/voice-preview?voice=Wade&model=inworld-tts-2")
                for _ in range(4)
            )
        )
        assert route.call_count == 1
        assert all(r.status_code == 200 and r.content == SAMPLE for r in responses)
        assert responses[0].headers["content-type"] == "audio/mpeg"
        assert "private" in responses[0].headers["cache-control"]
        await previews.get("Wade", "inworld-tts-2-flash")
        assert route.call_count == 2
        assert dict(route.calls[0].request.url.params) == {
            "voice_id": "Wade",
            "model_id": "inworld-tts-2",
        }
    assert not panel.h.asterisk.dials


async def test_preview_expiry_and_memory_eviction(previews):
    previews.max_bytes = len(SAMPLE)
    with respx.mock as mock:
        route = mock.get(URL).respond(200, json={"audioContent": base64.b64encode(SAMPLE).decode()})
        await previews.get("Wade", "inworld-tts-2")
        await previews.get("Alex", "inworld-tts-2")
        assert len(previews._cache) == 1 and previews._size == len(SAMPLE)
        await previews.get("Wade", "inworld-tts-2")
        assert route.call_count == 3
        key = ("Wade", "inworld-tts-2")
        previews._cache[key] = (time.monotonic() - 86401, SAMPLE)
        await previews.get(*key)
        assert route.call_count == 4


@pytest.mark.parametrize(
    "payload",
    [{}, {"audioContent": "broken"}, {"audioContent": ""}, {"audioContent": "bm90IGF1ZGlv"}],
)
async def test_invalid_samples_never_fall_back_to_synthesis(previews, payload):
    with respx.mock as mock:
        mock.get(URL).respond(200, json=payload)
        with pytest.raises(PreviewUnavailable):
            await previews.get("Wade", "inworld-tts-2")
        assert len(mock.calls) == 1 and not previews._cache


async def test_rate_limit_cooldown_and_unavailable_response(panel, previews):
    with respx.mock as mock:
        route = mock.get(URL).respond(429, headers={"Retry-After": "20"})
        first = await panel.client.get("/admin/api/voice-preview?voice=Wade&model=inworld-tts-2")
        second = await panel.client.get("/admin/api/voice-preview?voice=Alex&model=inworld-tts-2")
        assert first.status_code == second.status_code == 429
        assert first.headers["retry-after"] == "20" and route.call_count == 1
        previews._retry_at = 0
        route.respond(404)
        assert (
            await panel.client.get("/admin/api/voice-preview?voice=Wade&model=inworld-tts-2")
        ).status_code == 404
        route.respond(500, json={"error": "private upstream detail"})
        response = await panel.client.get("/admin/api/voice-preview?voice=Wade&model=inworld-tts-2")
        assert response.status_code == 503 and "private upstream" not in response.text


async def test_preview_requires_owner_and_known_voice_and_model(panel, previews):
    with respx.mock as mock:
        for query, code in [
            ("voice=missing&model=inworld-tts-2", 404),
            ("voice=Wade&model=other", 422),
        ]:
            assert (await panel.client.get("/admin/api/voice-preview?" + query)).status_code == code
        assert (
            await panel.client.get(
                "/admin/api/voice-preview?voice=Wade&model=inworld-tts-2",
                headers={"x-control-bridge": "wrong"},
            )
        ).status_code == 401
        assert not mock.calls


async def test_different_preview_requests_are_serialized(previews):
    import httpx

    active = 0
    peak = 0

    async def respond(request):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json={"audioContent": base64.b64encode(SAMPLE).decode()})

    with respx.mock as mock:
        mock.get(URL).mock(side_effect=respond)
        await asyncio.gather(
            previews.get("Wade", "inworld-tts-2"), previews.get("Alex", "inworld-tts-2")
        )
    assert peak == 1
