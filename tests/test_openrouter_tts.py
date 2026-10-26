from __future__ import annotations

import httpx
import pytest
import respx

pytest.importorskip("pipecat")

from pipecat.frames.frames import CancelWorkerFrame, ErrorFrame, TTSAudioRawFrame

from app.openrouter_tts import OPENROUTER_API_URL, OpenRouterTTSService, validate_pcm_content_type


def test_pcm_rate_is_explicit_and_wrong_formats_are_rejected():
    validate_pcm_content_type("audio/pcm;rate=24000;channels=1")
    for value in [
        "audio/pcm",
        "audio/mpeg",
        "audio/pcm;rate=48000;channels=1",
        "audio/pcm;rate=24000;channels=2",
    ]:
        with pytest.raises(ValueError):
            validate_pcm_content_type(value)


@respx.mock
async def test_openrouter_voice_and_audio_are_preserved_without_openai_endpoint():
    payload = b"\x01\x02" * 3000
    route = respx.post(OPENROUTER_API_URL + "/audio/speech").mock(
        return_value=httpx.Response(
            200, content=payload, headers={"content-type": "audio/pcm;rate=24000;channels=1"}
        )
    )
    async with httpx.AsyncClient() as http:
        service = OpenRouterTTSService(
            api_key="test-openrouter",
            model="elevenlabs/eleven-flash-v2.5",
            voice="sarah",
            http_client=http,
        )
        frames = [frame async for frame in service.run_tts("A synthetic test.", "segment-1")]
        await service.cleanup()
    assert route.call_count == 1
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer test-openrouter"
    assert b'"voice":"sarah"' in request.content
    assert not any(isinstance(frame, ErrorFrame) for frame in frames)
    assert (
        b"".join(frame.audio for frame in frames if isinstance(frame, TTSAudioRawFrame)) == payload
    )
    assert all(frame.sample_rate == 24000 and frame.context_id == "segment-1" for frame in frames)


@respx.mock
@pytest.mark.parametrize(
    "status,content_type,payload",
    [
        (401, "application/json", b'{"error":"provider private details"}'),
        (200, "audio/pcm;rate=16000;channels=1", b"abcd"),
        (200, "audio/pcm;rate=24000;channels=1", b""),
    ],
)
async def test_bad_provider_response_fails_without_exposing_body(status, content_type, payload):
    respx.post(OPENROUTER_API_URL + "/audio/speech").mock(
        return_value=httpx.Response(status, content=payload, headers={"content-type": content_type})
    )
    async with httpx.AsyncClient() as http:
        service = OpenRouterTTSService(
            api_key="test-openrouter", model="test/model", voice="test", http_client=http
        )
        frames = [frame async for frame in service.run_tts("Test.", "segment-1")]
        await service.cleanup()
    assert len(frames) == 2 and isinstance(frames[0], ErrorFrame)
    assert isinstance(frames[1], CancelWorkerFrame)
    assert "provider private details" not in frames[0].error
    assert "test-openrouter" not in frames[0].error
