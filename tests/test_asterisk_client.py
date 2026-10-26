from __future__ import annotations

import json

import httpx
import pytest

from app.asterisk_client import AsteriskClient, AsteriskError


def client(handler):
    return AsteriskClient(
        base_url="http://asterisk:8088/ari",
        username="agent",
        password="test-secret",
        transport=httpx.MockTransport(handler),
    )


async def test_origination_uses_owned_id_bounded_duration_and_json_body():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "call_test-cell"})

    ast = client(handle)
    try:
        await ast.originate("call_test", "+12025550123", 60)
    finally:
        await ast.close()
    assert len(calls) == 1
    request = calls[0]
    assert request.url.path == "/ari/channels/call_test-cell"
    assert "+12025550123" not in str(request.url)
    body = json.loads(request.content)
    assert body["endpoint"] == "PJSIP/+12025550123@android"
    assert body["variables"]["TIMEOUT(absolute)"] == "60"
    assert body["timeout"] == 45


async def test_unknown_create_outcome_is_not_retried():
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("private provider detail", request=request)

    ast = client(handle)
    try:
        with pytest.raises(AsteriskError, match="outcome may be unknown"):
            await ast.originate("call_test", "+12025550123", 60)
        assert calls == 1
    finally:
        await ast.close()


async def test_hangup_attempts_all_owned_resources_after_one_failure():
    paths = []

    def handle(request):
        paths.append(request.url.path)
        return httpx.Response(503 if request.url.path.endswith("-cell") else 404)

    ast = client(handle)
    try:
        assert not await ast.hangup("call_test")
    finally:
        await ast.close()
    assert paths == [
        "/ari/channels/call_test-cell",
        "/ari/channels/call_test-media",
        "/ari/bridges/call_test-bridge",
    ]


async def test_termination_requires_both_channels_to_be_absent():
    ast = client(
        lambda r: (
            httpx.Response(200, json={"state": "Up"})
            if r.url.path.endswith("-media")
            else httpx.Response(404)
        )
    )
    try:
        assert not await ast.media_absent("call_test")
    finally:
        await ast.close()


async def test_media_is_not_bridged_before_answer():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"state": "Ringing"})

    ast = client(handle)
    try:
        with pytest.raises(AsteriskError, match="has not answered"):
            await ast.bridge_answered_call("call_test")
    finally:
        await ast.close()
    assert len(calls) == 1 and calls[0].method == "GET"


@pytest.mark.parametrize(
    "identifier,number,duration",
    [
        ("../other", "+12025550123", 60),
        ("call_test", "+12025550123@evil", 60),
        ("call_test", "+12025550123", 7201),
    ],
)
async def test_invalid_requests_never_reach_asterisk(identifier, number, duration):
    def handle(request):
        raise AssertionError("Invalid input reached the telephone server")

    ast = client(handle)
    try:
        with pytest.raises(ValueError):
            await ast.originate(identifier, number, duration)
    finally:
        await ast.close()


async def test_external_media_creation_waits_for_separate_bridge_step():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={})

    ast = client(handle)
    try:
        await ast.create_media("call_test", 90)
        assert [request.url.path for request in calls] == [
            "/ari/bridges/call_test-bridge",
            "/ari/channels/externalMedia",
        ]
        body = json.loads(calls[-1].content)
        assert body["channelId"] == "call_test-media"
        assert body["format"] == "slin"
        assert body["transport"] == "websocket"
        await ast.bridge_media("call_test")
        assert json.loads(calls[-1].content) == {"channel": "call_test-media"}
    finally:
        await ast.close()


async def test_recording_uses_owned_bridge_and_a_bounded_wav(tmp_path):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, content=b"wav-data" if request.method == "GET" else b"{}")

    ast = client(handle)
    try:
        await ast.start_recording("call_test", 180)
        await ast.stop_recording("call_test")
        assert await ast.download_recording("call_test", tmp_path / "recording.wav")
        assert (tmp_path / "recording.wav").read_bytes() == b"wav-data"
        await ast.delete_recording("call_test")
        with pytest.raises(ValueError):
            await ast.download_recording("../outside", tmp_path / "recording.wav")
    finally:
        await ast.close()
    assert requests[0].url.path == "/ari/bridges/call_test-bridge/record"
    body = json.loads(requests[0].content)
    assert body == {
        "name": "call_test",
        "format": "wav",
        "maxDurationSeconds": 180,
        "maxSilenceSeconds": 0,
        "ifExists": "fail",
        "beep": False,
        "terminateOn": "none",
    }
    assert requests[1].url.path == "/ari/recordings/live/call_test/stop"
    assert requests[2].url.path == "/ari/recordings/stored/call_test/file"
    assert requests[3].url.path == "/ari/recordings/stored/call_test"


async def test_short_valid_international_destination_reaches_the_dial_adapter():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={})

    ast = client(handle)
    try:
        await ast.originate("call_short_number", "+6903101", 1800)
    finally:
        await ast.close()
    assert json.loads(requests[0].content)["endpoint"] == "PJSIP/+6903101@android"
