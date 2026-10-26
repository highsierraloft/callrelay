"""Exercise two-way Asterisk media and recordings without dialing a telephone."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import struct
import time
import wave
from datetime import UTC, datetime
from pathlib import Path

from websockets.asyncio.client import connect

from app.asterisk_client import AsteriskClient
from app.call_artifacts import CallArtifacts


async def run(base_url: str, credentials_file: Path, output: Path, seconds: int) -> None:
    credentials = json.loads(await asyncio.to_thread(credentials_file.read_text))
    ari = AsteriskClient(
        base_url=base_url,
        username="agent-call",
        password=credentials["ari_password"],
        application="agent-call-offline-canary",
    )
    call_id = "call_offline_canary_" + str(time.time_ns())
    _, _, bridge = ari.resource_ids(call_id)
    sockets = []
    channels = []
    ready = asyncio.Event()
    workers = []
    received = [0, 0]

    async def events():
        async for event in ari.events():
            if event.get("type") == "ConnectionReady":
                ready.set()

    event_task = asyncio.create_task(events())
    try:
        await asyncio.wait_for(ready.wait(), 10)
        await ari.request("POST", "bridges/" + bridge, body={"type": "mixing,proxy_media"})
        for side in range(2):
            channel = call_id + "-" + str(side)
            channels.append(channel)
            response = await ari.request(
                "POST",
                "channels/externalMedia",
                body={
                    "channelId": channel,
                    "app": ari.application,
                    "external_host": "INCOMING",
                    "transport": "websocket",
                    "encapsulation": "none",
                    "connection_type": "server",
                    "format": "slin",
                    "direction": "both",
                    "transport_data": "f(json)",
                    "variables": {"TIMEOUT(absolute)": str(seconds + 30)},
                },
            )
            connection = response.json()["channelvars"]["MEDIA_WEBSOCKET_CONNECTION_ID"]
            socket = await connect(
                base_url.removesuffix("/ari").replace("http", "ws", 1) + "/media/" + connection,
                subprotocols=["media"],
            )
            sockets.append(socket)
            first = json.loads(await asyncio.wait_for(socket.recv(), 5))
            assert first["event"] == "MEDIA_START"
            await ari.request(
                "POST", "bridges/" + bridge + "/addChannel", body={"channel": channel}
            )

        # Reproduce an answer delay without asking the recorder to capture nonexistent audio.
        await asyncio.sleep(4)

        async def send(side: int):
            hz = (500, 750)[side]
            chunk = b"".join(
                struct.pack("<h", int(3000 * math.sin(i * hz * 2 * math.pi / 8000)))
                for i in range(160)
            )
            while True:
                await sockets[side].send(chunk)
                await asyncio.sleep(0.02)

        async def receive(side: int):
            async for message in sockets[side]:
                if isinstance(message, bytes) and any(message):
                    received[side] += len(message)

        for side in range(2):
            workers.extend([asyncio.create_task(send(side)), asyncio.create_task(receive(side))])
        await asyncio.sleep(0.15)
        await ari.start_recording(call_id, seconds + 10)
        store = CallArtifacts(output, recording_enabled=True)
        store.mark_recording_start(call_id)
        answered = datetime.now(UTC).isoformat()
        await asyncio.sleep(seconds)
        await ari.stop_recording(call_id)
        store.mark_recording_stop(call_id)
        raw = store.directory(call_id) / "recording.wav"
        assert await ari.download_recording(call_id, raw)
        with wave.open(str(raw)) as wav:
            pcm = wav.readframes(wav.getnframes())
            duration = wav.getnframes() / wav.getframerate()
            samples = struct.unpack("<" + "h" * (len(pcm) // 2), pcm)
            # Both injected frequencies must be present in the mixed recording.
            sample = samples[8000:16000]
            power = {}
            for hz in (500, 750):
                real = sum(v * math.cos(i * hz * 2 * math.pi / 8000) for i, v in enumerate(sample))
                imag = sum(v * math.sin(i * hz * 2 * math.pi / 8000) for i, v in enumerate(sample))
                power[hz] = math.hypot(real, imag) / len(sample)
        assert duration >= seconds - 1, (duration, seconds)
        assert min(power.values()) > 100, power
        artifacts = await store.ensure(
            {
                "call_id": call_id,
                "state": "completed",
                "answered_at": answered,
                "ended_at": datetime.now(UTC).isoformat(),
            },
            [],
            ari,
        )
        assert artifacts["audio_status"] == "available", artifacts
        encoded_duration = await store.audio_duration(store.directory(call_id) / "recording.mp3")
        assert encoded_duration is not None and encoded_duration >= seconds - 1
        assert all(count > seconds * 8000 for count in received)
        print(
            json.dumps(
                {
                    "no_telephone_calls": True,
                    "two_way_pcm_bytes": received,
                    "recording_seconds": round(duration, 2),
                    "mp3_seconds": round(encoded_duration, 2),
                    "both_sides_present": True,
                    "artifacts": artifacts,
                }
            )
        )
    finally:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        for channel in channels:
            await ari.request("DELETE", "channels/" + channel, missing_ok=True)
        await ari.request("DELETE", "bridges/" + bridge, missing_ok=True)
        for socket in sockets:
            await socket.close()
        event_task.cancel()
        await asyncio.gather(event_task, return_exceptions=True)
        assert not (await ari.request("GET", "channels")).json()
        await ari.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8088/ari")
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=8, choices=range(5, 601))
    args = parser.parse_args()
    asyncio.run(run(args.base_url, args.credentials, args.output, args.seconds))
