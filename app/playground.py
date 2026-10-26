"""Private practice sessions have no telephone transport or dialing capability."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import httpx
from fastapi import WebSocket

from app.call_ai_inputs import ModelRequestTrace, model_options, model_tools, scheduling_clock
from app.callback_voice import PracticeCallbacks
from app.db.deployment import _lock_is_active
from app.models import ContextPacket

if TYPE_CHECKING:
    from app.android_voice import AndroidVoiceSession
    from app.control import ControlPlane


@dataclass
class PracticeSession:
    id: str
    mode: Literal["voice", "text"]
    packet: ContextPacket
    configuration: dict[str, Any]
    state: str = "waiting"
    messages: list[dict[str, Any]] = field(default_factory=list)
    metrics: list[dict[str, Any]] = field(default_factory=list)
    created: float = field(default_factory=time.monotonic)
    touched: float = field(default_factory=time.monotonic)
    voice: AndroidVoiceSession | None = None
    task: asyncio.Task[Any] | None = None
    timer: asyncio.Task[None] | None = None
    text_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    error: str | None = None
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    closer: asyncio.Task[Any] | None = None
    request_trace: ModelRequestTrace = field(default_factory=ModelRequestTrace)
    callbacks: PracticeCallbacks | None = None
    model_turns: list[list[dict[str, Any]]] = field(default_factory=list)

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mode": self.mode,
            "state": self.state,
            "messages": self.messages,
            "metrics": self.metrics,
            "error": self.error,
            "configuration": self.configuration,
            "callback_previews": list(self.callbacks.receipts.values()) if self.callbacks else [],
        }


class Playground:
    def __init__(self, control: ControlPlane) -> None:
        self.control = control
        self.active: PracticeSession | None = None
        self.recent: dict[str, PracticeSession] = {}

    async def create(
        self,
        mode: Literal["voice", "text"],
        packet: ContextPacket,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        service = self.control.service
        async with service._operations:
            if (
                self.active
                or self.control.checks.active
                or await self.control.db.list_nonterminal_calls()
            ):
                raise ValueError(
                    "A call or practice session is already active. End it before starting another."
                )
            lock = await self.control.db.fetch_one(
                "SELECT locked,locked_at FROM deployment_control WHERE singleton=1"
            )
            if lock and lock["locked"] and _lock_is_active(lock["locked_at"]):
                raise ValueError("The calling service is being updated. Try again shortly.")
            packet, configuration = await self.control.practice_snapshot(packet, overrides, mode)
            session = PracticeSession(secrets.token_urlsafe(24), mode, packet, configuration)
            session.callbacks = PracticeCallbacks(
                configuration["callback_policy"], packet.language or "en"
            )
            self.active = session
            session.timer = asyncio.create_task(self._expire(session))
            return session.view()

    def get(self, session_id: str) -> PracticeSession:
        session = (
            self.active
            if self.active and self.active.id == session_id
            else self.recent.get(session_id)
        )
        if session is None:
            raise LookupError("Practice session not found")
        return session

    async def inputs(self, session_id: str) -> dict[str, Any]:
        session = self.get(session_id)
        trace = session.voice.request_trace if session.voice else session.request_trace
        return {
            "configured": session.configuration.get("ai_inputs"),
            "last_request": await asyncio.to_thread(trace.view),
            "saved_system_prompt": session.configuration.get("effective_prompt"),
        }

    async def _expire(self, session: PracticeSession) -> None:
        while self.active is session:
            await asyncio.sleep(1)
            age = time.monotonic() - session.created
            idle = time.monotonic() - session.touched
            budget = self.control.runtime_config(session.configuration).call_duration(
                session.packet.max_duration_seconds
            )
            if (
                age > budget
                or (session.state == "waiting" and age > 60)
                or (session.mode == "text" and idle > 300)
            ):
                await self.close(session.id)
                return

    async def close(self, session_id: str | None = None) -> None:
        session = self.active
        if session is None or (session_id is not None and session.id != session_id):
            return
        if session.state == "stopping":
            if asyncio.current_task() not in {session.task, session.closer}:
                await session.closed.wait()
            return
        session.state = "stopping"
        session.closer = asyncio.current_task()
        session.closed.clear()
        task = session.task
        cleanup_complete = True
        try:
            if session.voice:
                try:
                    async with asyncio.timeout(10):
                        await session.voice.stop()
                except (TimeoutError, RuntimeError):
                    session.error = "The voice connection needed forced cleanup."
        finally:
            if task and task is not asyncio.current_task() and not task.done():
                task.cancel()
                _, pending = await asyncio.wait({task}, timeout=5)
                cleanup_complete = not pending
            if cleanup_complete:
                session.state = "failed" if session.error else "stopped"
                self.recent[session.id] = session
                for key in list(self.recent)[:-8]:
                    del self.recent[key]
                if self.active is session:
                    self.active = None
            else:
                session.state = "failed_cleanup"
                session.error = "The provider session is still stopping. End practice again before starting another session."
            session.closed.set()
            if session.timer and session.timer is not asyncio.current_task():
                session.timer.cancel()

    async def voice(self, session_id: str, websocket: WebSocket) -> None:
        from app.android_voice import AndroidVoiceSession, VoiceCallbacks

        session = self.get(session_id)
        if self.active is not session or session.mode != "voice" or session.state != "waiting":
            await websocket.close(code=1008)
            return
        session.state = "connected"
        session.task = asyncio.current_task()

        def transcript(speaker: str, text: str, source: str, interrupted: bool) -> None:
            if session.callbacks:
                session.callbacks.observe(speaker, text, interrupted)
            session.messages.append(
                {"speaker": speaker, "text": text, "interrupted": interrupted, "source": source}
            )

        def failed() -> None:
            if session.error is None:
                session.error = "The voice provider or audio connection failed. Check your settings and try again."
                self.control.service._spawn(self.close(session.id))

        async def finish(guard: Any) -> bool:
            if not guard():
                return False
            self.control.service._spawn(self.close(session.id))
            return True

        async def suppress() -> None:
            # An imaginary recipient in a practice session never changes the real opt-out list.
            return None

        try:
            await websocket.accept()
            session.voice = AndroidVoiceSession(
                websocket,
                call_id="practice_" + session.id,
                packet=session.packet,
                config=self.control.runtime_config(session.configuration),
                configuration=session.configuration,
                browser=True,
                api_key=self.control.service._api_key,
                callbacks=VoiceCallbacks(
                    transcript,
                    failed,
                    finish,
                    suppress,
                    session.metrics.append,
                    session.callbacks.tool if session.callbacks else None,
                ),
            )
            await session.voice.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            failed()
        finally:
            await self.close(session.id)
            session.task = None

    async def text(self, session_id: str, text: str) -> dict[str, Any]:
        session = self.get(session_id)
        if self.active is not session or session.mode != "text":
            raise ValueError("Start a text practice session first")
        if session.text_lock.locked():
            raise ValueError("Wait for the current reply")
        async with session.text_lock:
            session.task = asyncio.current_task()
            session.state = "thinking"
            session.touched = time.monotonic()
            config = self.control.runtime_config(session.configuration)
            messages = [
                {
                    "role": "system",
                    "content": self.control.prompt(session.packet, session.configuration),
                }
            ]
            messages[0]["content"] += "\n" + scheduling_clock(
                session.configuration["callback_policy"]["timezone"]
            )
            messages += [message for turn in session.model_turns[-20:] for message in turn]
            turn_start = len(messages)
            messages.append({"role": "user", "content": text})
            body: dict[str, Any] = {
                **model_options(config, "text"),
                "messages": messages,
                "tools": model_tools("text", callbacks_enabled=True),
            }
            try:
                assert session.callbacks is not None
                session.callbacks.observe("callee", text)
                started = time.monotonic()
                async with httpx.AsyncClient(
                    timeout=30, event_hooks={"request": [session.request_trace.capture]}
                ) as client:
                    for _ in range(4):
                        response = await client.post(
                            "https://openrouter.ai/api/v1/chat/completions",
                            json=body,
                            headers={"Authorization": "Bearer " + self.control.service._api_key},
                        )
                        response.raise_for_status()
                        message = response.json()["choices"][0]["message"]
                        tool_calls = message.get("tool_calls", [])
                        if not tool_calls or any(
                            tool.get("function", {}).get("name") == "finish_call"
                            for tool in tool_calls
                        ):
                            break
                        body["messages"].append(message)
                        automatic_readback = None
                        for tool in tool_calls:
                            try:
                                result = await session.callbacks.tool(
                                    tool["function"]["name"],
                                    json.loads(tool["function"]["arguments"]),
                                )
                            except (ValueError, LookupError) as exc:
                                result = {"saved": False, "error": str(exc)}
                            body["messages"].append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tool["id"],
                                    "content": json.dumps(result),
                                }
                            )
                            if tool["function"]["name"] == "propose_callback" and result.get(
                                "readback"
                            ):
                                automatic_readback = result["readback"]
                        if automatic_readback:
                            message = {"role": "assistant", "content": automatic_readback}
                            break
                    else:
                        raise ValueError("Practice tool limit reached")
                output = message.get("content") or ""
                finished = False
                for tool in message.get("tool_calls", []):
                    if tool.get("function", {}).get("name") == "finish_call":
                        output = json.loads(tool["function"]["arguments"]).get(
                            "farewell", "Goodbye."
                        )
                        finished = True
                session.messages.extend(
                    [{"speaker": "callee", "text": text}, {"speaker": "assistant", "text": output}]
                )
                session.callbacks.observe("assistant", output)
                session.model_turns.append([*body["messages"][turn_start:], message])
                session.model_turns = session.model_turns[-20:]
                session.metrics.append(
                    {
                        "seconds": round(time.monotonic() - started, 3),
                        "measured_from": "Text request; no audio",
                    }
                )
                session.state = "ready"
                session.touched = time.monotonic()
                if finished:
                    session.task = None
                    await self.close(session.id)
                return session.view()
            except (httpx.HTTPError, KeyError, ValueError):
                session.error = (
                    "The model could not complete this reply. Check its provider settings."
                )
                session.task = None
                await self.close(session.id)
                return session.view()
            finally:
                session.task = None
