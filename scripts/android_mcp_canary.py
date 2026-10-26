"""Run the MCP call lifecycle against local Asterisk channels, with synthetic speech."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import struct
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket
from fastmcp import Client, FastMCP
from websockets.asyncio.client import connect

import app.android_calls as calls_module
from app.android_calls import AndroidCallService
from app.android_config import AndroidConfig
from app.asterisk_client import AsteriskClient
from app.db import Database
from app.mcp_tools import register_tools
from app.settings import Settings


def tone(hz: int) -> bytes:
    return b"".join(
        struct.pack("<h", int(3000 * math.sin(i * hz * 2 * math.pi / 8000))) for i in range(160)
    )


class SyntheticPhone:
    def admission_problem(self):
        return None

    def read(self):
        return {"control_ready": True}

    def carrier_idle_after(self, _requested_at):
        return True


class SyntheticVoice:
    def __init__(self, websocket, *, call_id, callbacks, **_kwargs):
        self.websocket, self.callbacks, self.call_id = websocket, callbacks, call_id
        self.ready = asyncio.Event()
        self.connected = asyncio.Event()
        self.sending = None

    async def run(self):
        try:
            while True:
                message = await self.websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("text"):
                    event = json.loads(message["text"])
                    if event.get("event") == "MEDIA_START":
                        assert event["channel_id"] == self.call_id + "-media"
                        self.ready.set()
        finally:
            if self.sending:
                self.sending.cancel()
                await asyncio.gather(self.sending, return_exceptions=True)

    async def start_conversation(self):
        self.connected.set()
        self.callbacks.transcript(
            "assistant", "Synthetic hello. Привіт. Hola.", "offline.canary", False
        )
        self.callbacks.transcript(
            "callee", "Synthetic recipient response.", "offline.canary", False
        )

        async def send():
            while True:
                await self.websocket.send_bytes(tone(500))
                await asyncio.sleep(0.02)

        self.sending = asyncio.create_task(send())

    async def stop(self):
        if self.sending:
            self.sending.cancel()
            await asyncio.gather(self.sending, return_exceptions=True)


class LocalAsterisk(AsteriskClient):
    """Replace only the cellular leg; this class has no telephone origination path."""

    socket = None
    sender = None

    async def gateway_registered(self):
        return True

    async def originate(self, call_id, number, max_seconds):
        assert number == "+442079460123"
        cell, _, _ = self.resource_ids(call_id)
        response = await self.request(
            "POST",
            "channels/externalMedia",
            body={
                "channelId": cell,
                "app": self.application,
                "external_host": "INCOMING",
                "transport": "websocket",
                "encapsulation": "none",
                "connection_type": "server",
                "format": "slin",
                "direction": "both",
                "transport_data": "f(json)",
                "variables": {"TIMEOUT(absolute)": str(max_seconds)},
            },
        )
        connection = response.json()["channelvars"]["MEDIA_WEBSOCKET_CONNECTION_ID"]
        self.socket = await connect(
            "ws://127.0.0.1:8088/media/" + connection, subprotocols=["media"]
        )
        first = json.loads(await self.socket.recv())
        assert first["event"] == "MEDIA_START"

        async def send():
            while True:
                await self.socket.send(tone(750))
                await asyncio.sleep(0.02)

        self.sender = asyncio.create_task(send())

    async def close(self):
        if self.sender:
            self.sender.cancel()
            await asyncio.gather(self.sender, return_exceptions=True)
        if self.socket:
            await self.socket.close()
        await super().close()


async def main():
    credentials = json.loads(await asyncio.to_thread(Path("/run/secrets/asterisk.json").read_text))
    settings = Settings.from_values(
        call_backend="android_openrouter",
        post_call_extraction_enabled=False,
        live_calls_enabled=True,
        allow_all_country_codes=True,
        owner_display_name="Alex",
        owner_phone_e164="+12025550123",
        database_url="sqlite:////evidence/calls.db",
    )
    config = AndroidConfig(
        selected_sim_slot=1,
        caller_number="+12025550199",
        allow_any_destination=True,
        recording_enabled=True,
    )
    db = Database(settings.database_path)
    await db.initialize()
    ari = LocalAsterisk(
        base_url="http://127.0.0.1:8088/ari",
        username="agent-call",
        password=credentials["ari_password"],
    )
    calls_module.AndroidVoiceSession = SyntheticVoice
    service = AndroidCallService(
        settings, db, config=config, asterisk=ari, phone=SyntheticPhone(), api_key="unused"
    )
    app = FastAPI()

    @app.websocket("/phone-media")
    async def media(websocket: WebSocket):
        await service.media_session(websocket)

    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=8000, access_log=False, log_level="error")
    )
    server_task = asyncio.create_task(server.serve())
    mcp = FastMCP("offline-calling-canary")
    register_tools(mcp, lambda: service, android_backend=True)
    try:
        await service.start_watchdog()
        async with asyncio.timeout(10):
            while not service.supervision_ready() or not server.started:  # noqa: ASYNC110 - bounded external readiness check
                await asyncio.sleep(0.05)
        packet = {
            "owner": {"display_name": "Alex", "timezone": "UTC", "callback_number": "+12025550123"},
            "target": {"name": "Synthetic recipient", "phone": "+442079460123"},
            "objective": "Exercise the isolated MCP lifecycle.",
            "language": "en",
            "intention": "conversation",
            "personality": "warm",
            "max_duration_seconds": 1800,
            "escalation": {"mode": "end_call", "owner_phone": "+12025550123"},
        }
        async with Client(mcp) as client:
            prepared = (
                await client.call_tool(
                    "prepare_phone_call",
                    {
                        "context": packet,
                        "requested_by_owner": True,
                        "authority_basis": "Isolated synthetic validation without telephone dialing",
                    },
                )
            ).data
            started = (
                await client.call_tool(
                    "start_phone_call",
                    {
                        "plan_id": prepared["plan_id"],
                        "explicit_confirmation": True,
                        "confirmation_text": prepared["confirmation_summary"],
                    },
                )
            ).data
            call_id = started["call_id"]
            async with asyncio.timeout(20):
                while (await service.get_snapshot(call_id)).state != "active":  # noqa: ASYNC110 - bounded call-state observation
                    await asyncio.sleep(0.05)
            await asyncio.sleep(8)
            await client.call_tool("end_phone_call", {"call_id": call_id})
            cursor = 0
            async with asyncio.timeout(20):
                while True:
                    state = (
                        await client.call_tool(
                            "phone_call_poll",
                            {"call_id": call_id, "after_sequence": cursor, "timeout_seconds": 1},
                        )
                    ).data
                    cursor = state["next_after_sequence"]
                    if state["terminal"]:
                        break
            result = await client.call_tool("get_call_result", {"call_id": call_id})
            assert result.data["state"] == "completed", result.data["state"]
            assert result.data["artifacts"]["audio_status"] == "available"
            assert [block.type for block in result.content] == ["text", "audio", "resource"]
            markdown = base64.b64decode(result.content[2].resource.blob).decode()
            assert "Synthetic hello" in markdown and "Synthetic recipient response" in markdown
            payload = {
                "content": [block.model_dump(mode="json") for block in result.content],
                "structuredContent": result.data,
            }
            await asyncio.to_thread(
                service.artifacts._write,
                Path("/evidence/mcp-payload.json"),
                json.dumps(payload).encode(),
            )
            assert not (await ari.request("GET", "channels")).json()
            print(
                json.dumps(
                    {
                        "mcp_lifecycle": "prepare/start/wait/end/result",
                        "state": result.data["state"],
                        "transcript_turns": len(result.data["transcript"]),
                        "attachments": [f["mime_type"] for f in result.data["artifacts"]["files"]],
                        "audio_status": "available",
                        "call_budget_seconds": 1800,
                        "cellular_calls": 0,
                        "remaining_channels": 0,
                    }
                )
            )
    finally:
        await service.stop()
        server.should_exit = True
        await asyncio.wait_for(server_task, 10)
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
