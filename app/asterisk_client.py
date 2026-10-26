"""Control owned Asterisk channels. Asterisk supplies SIP and audio transport."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import connect


class AsteriskError(RuntimeError):
    """A sanitized failure; request bodies and authorization never enter its message."""


class AsteriskClient:
    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        application: str = "agent-call",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.query
        ):
            raise ValueError("Invalid Asterisk service URL")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", application):
            raise ValueError("Invalid Asterisk application name")
        self.application = application
        self._incoming_channels: dict[str, str] = {}
        self.base_url = base_url.rstrip("/")
        self._authorization = (
            "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        )
        self._http = httpx.AsyncClient(
            base_url=self.base_url + "/",
            auth=httpx.BasicAuth(username, password),
            timeout=httpx.Timeout(8, connect=3),
            follow_redirects=False,
            transport=transport,
        )

    async def close(self) -> None:
        await self._http.aclose()

    @staticmethod
    def resource_ids(call_id: str) -> tuple[str, str, str]:
        if not re.fullmatch(r"call_[A-Za-z0-9_-]{1,128}", call_id):
            raise ValueError("Invalid call identifier")
        return call_id + "-cell", call_id + "-media", call_id + "-bridge"

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        missing_ok: bool = False,
    ) -> httpx.Response:
        try:
            response = await self._http.request(method, path, json=body)
        except httpx.RequestError as exc:
            raise AsteriskError("Asterisk request failed; remote outcome may be unknown") from exc
        if missing_ok and response.status_code == 404:
            return response
        if not 200 <= response.status_code < 300:
            raise AsteriskError(f"Asterisk rejected the operation: HTTP {response.status_code}")
        return response

    async def healthy(self) -> bool:
        try:
            await self.request("GET", "asterisk/info")
            return True
        except AsteriskError:
            return False

    async def gateway_registered(self) -> bool:
        response = await self.request("GET", "endpoints/PJSIP/android", missing_ok=True)
        return response.status_code == 200 and response.json().get("state") == "online"

    async def create_media(self, call_id: str, max_seconds: int) -> None:
        _, media, bridge = self.resource_ids(call_id)
        self._validate_duration(max_seconds)
        await self.request("POST", "bridges/" + bridge, body={"type": "mixing,proxy_media"})
        await self.request(
            "POST",
            "channels/externalMedia",
            body={
                "channelId": media,
                "app": self.application,
                "external_host": "agent-call",
                "transport": "websocket",
                "encapsulation": "none",
                "connection_type": "client",
                "format": "slin",
                "direction": "both",
                "transport_data": "f(json)",
                "variables": {"AGENT_CALL_ID": call_id, "TIMEOUT(absolute)": str(max_seconds)},
            },
        )

    async def bridge_media(self, call_id: str) -> None:
        _, media, bridge = self.resource_ids(call_id)
        await self.request("POST", "bridges/" + bridge + "/addChannel", body={"channel": media})

    async def start_recording(self, call_id: str, max_seconds: int) -> None:
        _, _, bridge = self.resource_ids(call_id)
        self._validate_duration(max_seconds)
        await self.request(
            "POST",
            "bridges/" + bridge + "/record",
            body={
                "name": call_id,
                "format": "wav",
                "maxDurationSeconds": max_seconds,
                "maxSilenceSeconds": 0,
                "ifExists": "fail",
                "beep": False,
                "terminateOn": "none",
            },
        )

    async def stop_recording(self, call_id: str) -> None:
        self.resource_ids(call_id)
        await self.request("POST", f"recordings/live/{call_id}/stop", missing_ok=True)

    async def download_recording(self, call_id: str, destination: Path) -> bool:
        self.resource_ids(call_id)
        temporary = destination.with_suffix(".pending.wav")
        try:
            async with self._http.stream("GET", f"recordings/stored/{call_id}/file") as response:
                if response.status_code == 404:
                    return False
                if response.status_code != 200:
                    raise AsteriskError("The stored recording is unavailable")
                total = 0
                with temporary.open("xb") as output:
                    temporary.chmod(0o600)
                    async for chunk in response.aiter_bytes(64 * 1024):
                        total += len(chunk)
                        if total > 128 * 1024 * 1024:
                            raise AsteriskError("The stored recording exceeds the size limit")
                        output.write(chunk)
                temporary.replace(destination)
                return True
        except httpx.RequestError as exc:
            raise AsteriskError("The stored recording download failed") from exc
        finally:
            temporary.unlink(missing_ok=True)

    async def delete_recording(self, call_id: str) -> None:
        self.resource_ids(call_id)
        await self.request("DELETE", f"recordings/stored/{call_id}", missing_ok=True)

    @staticmethod
    def _validate_duration(seconds: int) -> None:
        if not 1 <= seconds <= 7200:
            raise ValueError("Call duration must be between 1 and 7200 seconds")

    async def originate(self, call_id: str, number: str, max_seconds: int) -> None:
        cell, _, _ = self.resource_ids(call_id)
        if not re.fullmatch(r"\+[1-9][0-9]{1,14}", number):
            raise ValueError("Destination must be a complete international number")
        self._validate_duration(max_seconds)
        await self.request(
            "POST",
            "channels/" + cell,
            body={
                "endpoint": f"PJSIP/{number}@android",
                "app": self.application,
                "appArgs": "cell," + call_id,
                "timeout": min(45, max_seconds),
                "formats": "alaw,ulaw",
                "variables": {"AGENT_CALL_ID": call_id, "TIMEOUT(absolute)": str(max_seconds)},
            },
        )

    async def bridge_answered_call(self, call_id: str) -> None:
        cell, _, bridge = self.owned_ids(call_id)
        response = await self.request("GET", "channels/" + cell)
        if response.json().get("state") != "Up":
            raise AsteriskError("The telephone channel has not answered")
        await self.request("POST", "bridges/" + bridge + "/addChannel", body={"channel": cell})

    async def send_dtmf(
        self, call_id: str, digits: str, *, guard: Callable[[], bool] | None = None
    ) -> None:
        from app.phone_menu import validate_digits

        digits = validate_digits(digits)
        cell, _, _ = self.owned_ids(call_id)
        response = await self.request("GET", "channels/" + cell)
        if response.json().get("state") != "Up" or (guard is not None and not guard()):
            raise ValueError("The owned telephone channel is no longer active")
        # RFC 4733 reaches the guarded SIP leg, which invokes Android's cellular keypad.
        # No media-channel tones, new originate, redirects, or retries are involved.
        await self.request(
            "POST",
            "channels/" + cell + "/dtmf",
            body={"dtmf": digits, "before": 0, "between": 100, "duration": 160, "after": 0},
        )

    async def hangup(self, call_id: str) -> bool:
        cell, media, bridge = self.owned_ids(call_id)
        succeeded = True
        for path in ["channels/" + cell, "channels/" + media, "bridges/" + bridge]:
            try:
                await self.request("DELETE", path, missing_ok=True)
            except AsteriskError:
                succeeded = False
        return succeeded

    async def media_absent(self, call_id: str) -> bool:
        cell, media, _ = self.owned_ids(call_id)
        for identifier in [cell, media]:
            response = await self.request("GET", "channels/" + identifier, missing_ok=True)
            if response.status_code != 404:
                return False
        return True

    def adopt_incoming(self, call_id: str, channel_id: str) -> None:
        self.resource_ids(call_id)
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", channel_id):
            raise ValueError("Invalid incoming channel identifier")
        if (
            channel_id in self._incoming_channels.values()
            and self._incoming_channels.get(call_id) != channel_id
        ):
            raise ValueError("Incoming channel already belongs to another call")
        self._incoming_channels[call_id] = channel_id

    def owned_ids(self, call_id: str) -> tuple[str, str, str]:
        cell, media, bridge = self.resource_ids(call_id)
        return self._incoming_channels.get(call_id, cell), media, bridge

    def forget_incoming(self, call_id: str) -> None:
        self._incoming_channels.pop(call_id, None)

    async def answer_incoming(self, call_id: str, max_seconds: int) -> None:
        if call_id not in self._incoming_channels:
            raise ValueError("This call does not own an incoming channel")
        self._validate_duration(max_seconds)
        cell, _, _ = self.owned_ids(call_id)
        await self.request(
            "POST",
            "channels/" + cell + "/variable",
            body={"variable": "TIMEOUT(absolute)", "value": str(max_seconds)},
        )
        await self.request("POST", "channels/" + cell + "/answer")

    async def release_incoming(self, channel_id: str) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", channel_id):
            raise ValueError("Invalid incoming channel identifier")
        # A failed SIP leg causes the guarded gateway to withdraw ownership without rejecting the handset.
        await self.request("DELETE", "channels/" + channel_id, missing_ok=True)

    async def play_local_exit(self, call_id: str, kind: str, language: str) -> str:
        if kind not in {"failure", "silence"} or language not in {"en", "es", "ru", "uk"}:
            raise ValueError("Unknown reception exit message")
        _, _, bridge = self.resource_ids(call_id)
        playback_id = call_id + "-exit"
        await self.request(
            "POST",
            "bridges/" + bridge + "/play/" + playback_id,
            body={"media": f"sound:reception/reception-{kind}-{language}"},
        )
        return playback_id

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        parsed = urlsplit(self.base_url)
        url = urlunsplit(
            (
                "wss" if parsed.scheme == "https" else "ws",
                parsed.netloc,
                parsed.path.rstrip("/") + "/events",
                urlencode({"app": self.application}),
                "",
            )
        )
        async with connect(
            url,
            additional_headers={"Authorization": self._authorization},
            open_timeout=5,
            ping_interval=15,
            ping_timeout=15,
            max_size=262144,
        ) as websocket:
            yield {"type": "ConnectionReady"}
            async for raw in websocket:
                event = json.loads(raw)
                if isinstance(event, dict):
                    yield event
