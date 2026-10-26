"""Reuse Agent Call's plans and transcripts with an owned Android/Asterisk call."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import WebSocket

from app.android_config import AndroidConfig
from app.android_voice import AndroidVoiceSession, VoiceCallbacks
from app.asterisk_client import AsteriskClient, AsteriskError
from app.call_artifacts import ArtifactContent, CallArtifacts
from app.call_planning import claim_approved_call, prepare_call
from app.control import ControlPlane
from app.db import Database
from app.errors import CallRefusal, ErrorCode
from app.finalizer import Finalizer
from app.models import (
    TERMINAL_STATES,
    CallSnapshot,
    CallState,
    ContextPacket,
    PreparePhoneCallInput,
    PreparePhoneCallOutput,
    StartPhoneCallOutput,
)
from app.phone_status import PhoneStatus
from app.records import AnswerCommand
from app.settings import Settings

logger = logging.getLogger(__name__)
TranscriptItem = tuple[Literal["callee", "assistant"], str, str, bool]


@dataclass
class _Call:
    call_id: str
    packet: ContextPacket
    duration_seconds: int
    created: float = field(default_factory=time.monotonic)
    media_attached: asyncio.Event = field(default_factory=asyncio.Event)
    media_in_application: asyncio.Event = field(default_factory=asyncio.Event)
    config: AndroidConfig | None = None
    configuration: dict[str, Any] | None = None
    voice: AndroidVoiceSession | None = None
    voice_task: asyncio.Task[Any] | None = None
    transcript: asyncio.Queue[TranscriptItem] = field(default_factory=asyncio.Queue)
    writer: asyncio.Task[None] | None = None
    ending_after: float | None = None
    direction: Literal["inbound", "outbound"] = "outbound"
    incoming_channel: str | None = None
    incoming_audio_ready: asyncio.Event = field(default_factory=asyncio.Event)
    answer_requested: bool = False
    local_exit_started: bool = False
    local_exit_done: asyncio.Event = field(default_factory=asyncio.Event)
    exit_playback_finished: asyncio.Event = field(default_factory=asyncio.Event)
    exit_playback_completed: bool = False


class AndroidCallService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        *,
        config: AndroidConfig,
        asterisk: AsteriskClient,
        phone: PhoneStatus,
        api_key: str,
    ) -> None:
        if settings.post_call_extraction_enabled:
            raise ValueError("The Android backend requires transcript-only finalization")
        config.validate_voice_credentials()
        self.settings, self.db, self.config = settings, db, config
        self.asterisk, self.phone, self._api_key = asterisk, phone, api_key
        self.finalizer = Finalizer(settings, db, None)
        self.artifacts = CallArtifacts(
            settings.database_path.parent / "call-artifacts",
            recording_enabled=config.recording_enabled,
            export_root=config.artifact_export_directory,
            client_root=config.artifact_client_directory,
            export_group=config.artifact_export_group,
        )
        self._runtime: _Call | None = None
        self._incoming_pending: set[str] = set()
        self._operations = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._events_connected = False
        self._watchdog_at = 0.0
        self._watchdog: asyncio.Task[None] | None = None
        self._events: asyncio.Task[None] | None = None
        self._stopping = False
        self._ending_after: dict[str, float] = {}
        self.control = ControlPlane(self, db)
        from app.inbound import InboundReception

        self.inbound = InboundReception(self)

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)

        def done(finished: asyncio.Task[None]) -> None:
            self._tasks.discard(finished)
            if not finished.cancelled() and finished.exception() is not None:
                # Provider exceptions can contain call content or authorization details.
                logger.error("Android call background operation failed")

        task.add_done_callback(done)
        return task

    def _validate_packet(self, packet: ContextPacket, config: AndroidConfig | None = None) -> None:
        if packet.direction != "outbound":
            raise CallRefusal(
                "inbound_not_dialable", "Incoming calls cannot enter the outbound dialing path"
            )
        config = config or self.config
        if (
            not self.config.allow_any_destination
            and packet.target.phone not in self.config.allowed_destinations
        ):
            raise CallRefusal(
                "destination_not_enabled", "The destination is not enabled by this deployment"
            )
        try:
            config.call_duration(packet.max_duration_seconds)
        except ValueError as exc:
            raise CallRefusal("duration_not_allowed", str(exc)) from exc
        if packet.target.phone == self.config.caller_number:
            raise CallRefusal("service_number", "The phone cannot call its own line")
        if packet.escalation.mode != "end_call":
            raise CallRefusal(
                "unsupported_transfer", "This backend requires escalation.mode=end_call"
            )

    async def prepare(
        self, request: PreparePhoneCallInput, *, callback_schedule: dict[str, Any] | None = None
    ) -> PreparePhoneCallOutput:
        self._validate_packet(request.context)
        if await self.db.is_destination_suppressed(request.context.target.phone):
            raise CallRefusal("recipient_opted_out", "The recipient requested no further calls")
        try:
            snapshot = await self.control.resolve(request.context)
        except LookupError as exc:
            raise CallRefusal(
                "profile_not_found", "Select a profile from get_calling_setup"
            ) from exc
        if request.followup_id:
            await self.control.workflows.validate_followup_plan(
                request.followup_id, request.context
            )
            snapshot["followup_id"] = request.followup_id
        snapshot["reception_contract_version"] = 1
        snapshot["direction"] = "outbound"
        snapshot["contact_memory"] = await self.control.contacts.snapshot(
            request.context.target.phone, request.context.target.name
        )
        snapshot["callback_policy"] = (await self.control.current("callback_policy"))["value"]
        if callback_schedule:
            snapshot["callback_source_call_id"] = callback_schedule["source_call_id"]
            snapshot["contact_memory"] = await self.control.contacts.callback_snapshot(
                callback_schedule["source_call_id"]
            )
            target = request.context.target
            memory = snapshot["contact_memory"]
            if memory.get("name"):
                target = target.model_copy(
                    update={
                        "name": memory["name"],
                        "background": memory.get("background") or target.background,
                    }
                )
            elif callback_schedule["value"].get("source_direction") == "inbound":
                target = target.model_copy(update={"name": "Callback recipient"})
            request = request.model_copy(
                update={"context": request.context.model_copy(update={"target": target})}
            )
            snapshot["callback_schedule_id"] = callback_schedule["schedule_id"]
            snapshot["callback_schedule_revision"] = callback_schedule["revision"]
            if callback_schedule["value"].get("source_business"):
                snapshot["business"] = callback_schedule["value"]["source_business"]
                snapshot["reception_callback"] = True
                snapshot["prompts"]["opening"] = (
                    "Briefly identify the business you represent and explain that this is the agreed callback about the supplied task. Ask whether now is still a good time."
                )
        packet = self.control.normalize_packet(request.context, snapshot)
        self.control.freeze_inputs(packet, snapshot)
        return await prepare_call(
            self.settings,
            self.db,
            request.model_copy(update={"context": packet}),
            configuration_snapshot=snapshot,
        )

    async def get_calling_setup(self) -> dict[str, Any]:
        return await self.control.setup(from_mcp=True)

    async def start(
        self, plan_id: str, *, explicit_confirmation: bool, confirmation_text: str
    ) -> StartPhoneCallOutput:
        if not self.settings.live_calls_enabled:
            raise CallRefusal(ErrorCode.LIVE_CALLS_DISABLED)
        async with self._operations:
            if self.control.practice.active is not None or self.control.checks.active is not None:
                await self.preempt_practice()
            if not self.supervision_ready():
                raise CallRefusal(ErrorCode.SUPERVISION_UNAVAILABLE)
            problem = self.phone.admission_problem()
            if problem:
                raise CallRefusal(ErrorCode.SUPERVISION_UNAVAILABLE, problem)
            plan = await self.db.get_plan_record(plan_id)
            if plan is None:
                raise CallRefusal(ErrorCode.PLAN_NOT_FOUND)
            configuration = await self.db.control_plan(plan_id)
            config = self.control.runtime_config(configuration)
            if configuration and configuration.get("followup_id"):
                await self.control.workflows.validate_followup_plan(
                    configuration["followup_id"], plan.context
                )
            self._validate_packet(plan.context, config)
            if await self.db.is_destination_suppressed(plan.context.target.phone):
                raise CallRefusal("recipient_opted_out", "The recipient requested no further calls")
            if plan.context.language not in config.conversation_languages:
                raise CallRefusal("language_required", "Specify an enabled initial call language")
            try:
                registered = await self.asterisk.gateway_registered()
            except AsteriskError:
                registered = False
            if not registered:
                raise CallRefusal(
                    ErrorCode.SUPERVISION_UNAVAILABLE, "The SIP phone is not registered"
                )
            approved = await claim_approved_call(
                self.db,
                plan_id=plan_id,
                explicit_confirmation=explicit_confirmation,
                confirmation_text=confirmation_text,
            )
            runtime = _Call(
                approved.call_id,
                approved.context,
                config.call_duration(approved.context.max_duration_seconds),
                config=config,
                configuration=configuration,
            )
            self._runtime = runtime
            await self.db.contact_link_call(
                call_id=approved.call_id,
                phone=approved.context.target.phone,
                name=approved.context.target.name,
                direction="outbound",
                frozen_memory=(configuration or {}).get("contact_memory"),
                callback_source_call_id=(configuration or {}).get("callback_source_call_id"),
            )
            runtime.writer = self._spawn(self._write_transcript(runtime))
            self._spawn(self._setup(runtime))
        return StartPhoneCallOutput(
            call_id=approved.call_id,
            state=CallState.PREWARMING,
            next_action="Use phone_call_poll with timeout_seconds=20 until terminal, then retrieve every transcript page and both attachments with get_call_result. Hermes interprets the transcript. This backend has no owner transfer or mid-call question tool.",
        )

    async def _setup(self, runtime: _Call) -> None:
        try:
            async with self._operations:
                row = await self.db.get_call(runtime.call_id)
                if not row or row["state"] != CallState.PREWARMING:
                    return
                await self.asterisk.create_media(runtime.call_id, runtime.duration_seconds)
            await asyncio.wait_for(runtime.media_attached.wait(), 15)
            assert runtime.voice is not None
            await asyncio.wait_for(runtime.voice.ready.wait(), 15)
            await asyncio.wait_for(runtime.media_in_application.wait(), 15)
            async with self._operations:
                if self.phone.admission_problem() or not self.supervision_ready():
                    raise AsteriskError("Phone readiness changed during setup")
                if not await self.asterisk.gateway_registered():
                    raise AsteriskError("Phone registration changed during setup")
                await self.asterisk.bridge_media(runtime.call_id)
                if not await self.db.promote_to_ready_to_activate(runtime.call_id):
                    return
                schedule_id = (runtime.configuration or {}).get("callback_schedule_id")
                claimed = (
                    await self.control.callbacks.final_dial_claim(
                        runtime.call_id, row["plan_id"], schedule_id
                    )
                    if schedule_id
                    else await self.db.claim_callee_dial(runtime.call_id)
                )
                if not claimed:
                    raise AsteriskError(
                        "Callback was cancelled or became unavailable before dialing"
                    )
                if not await self.db.promote_to_activating(runtime.call_id):
                    return
                if schedule_id and self._incoming_pending:
                    raise AsteriskError(
                        "An incoming call took priority before the originate request"
                    )
                # Never retry an originate whose remote outcome may be unknown.
                await self.asterisk.originate(
                    runtime.call_id, runtime.packet.target.phone, runtime.duration_seconds
                )
        except Exception:
            await self.terminate_call(runtime.call_id, "android_setup_failed")

    async def media_session(self, websocket: WebSocket) -> None:
        runtime = self._runtime
        if runtime is None or runtime.voice is not None or runtime.ending_after is not None:
            await websocket.close(code=1008)
            return
        row = await self.db.get_call(runtime.call_id)
        if not row or row["state"] != CallState.PREWARMING:
            await websocket.close(code=1008)
            return

        async def finish(guard: Callable[[], bool]) -> bool:
            return await self.terminate_call(runtime.call_id, "assistant_finished", guard=guard)

        def failed() -> None:
            self._spawn(self._fail_voice(runtime.call_id))

        def silence_timeout() -> None:
            self._spawn(self._local_exit(runtime.call_id, "silence"))

        def transcript(
            speaker: Literal["callee", "assistant"], text: str, source: str, interrupted: bool
        ) -> None:
            runtime.transcript.put_nowait((speaker, text, source, interrupted))

        async def suppress_destination() -> None:
            phone = runtime.packet.target.phone
            await self.db.opt_out_call(
                runtime.call_id, phone if phone not in {"withheld", "unavailable"} else None
            )

        async def callback_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            from app.callback_voice import live_callback_tool

            return await live_callback_tool(self, runtime, name, arguments)

        async def send_dtmf(digits: str) -> None:
            await self.send_dtmf(runtime.call_id, digits)

        await websocket.accept(subprotocol="media")
        runtime.voice = AndroidVoiceSession(
            websocket,
            call_id=runtime.call_id,
            packet=runtime.packet,
            config=runtime.config or self.config,
            api_key=self._api_key,
            configuration=runtime.configuration,
            callbacks=VoiceCallbacks(
                transcript,
                failed,
                finish,
                suppress_destination,
                lambda data: self._spawn(self.db.control_measure(runtime.call_id, data)),
                callback_tool,
                silence_timeout,
                send_dtmf,
            ),
        )
        runtime.voice_task = asyncio.current_task()
        runtime.media_attached.set()
        try:
            await runtime.voice.run()
        except Exception:
            failed()
        finally:
            if runtime.direction == "inbound" and runtime.ending_after is None:
                await self._fail_voice(runtime.call_id)
            else:
                await self.terminate_call(runtime.call_id, "android_media_disconnected")
            self._spawn(self._save_model_request(runtime))

    async def send_dtmf(self, call_id: str, digits: str) -> None:
        runtime = self._runtime

        def ready() -> bool:
            return bool(
                runtime is not None
                and self._runtime is runtime
                and runtime.call_id == call_id
                and runtime.ending_after is None
                and runtime.voice
                and runtime.voice.connected.is_set()
            )

        if not ready():
            raise ValueError("The telephone call is no longer active")
        row = await self.db.get_call(call_id)
        if not row or row["state"] != CallState.ACTIVE or not ready():
            raise ValueError("The telephone call is no longer active")
        # Do not hold the lifecycle lock during network I/O: End Call stays responsive.
        await self.asterisk.send_dtmf(call_id, digits, guard=ready)

    async def _save_model_request(self, runtime: _Call) -> None:
        if runtime.voice is None:
            return
        usage = getattr(runtime.voice, "usage", None)
        if usage is not None:
            await self.db.control_usage(
                runtime.call_id, usage.snapshot(), (runtime.configuration or {}).get("pricing")
            )
        latest = await asyncio.to_thread(runtime.voice.request_trace.view)
        if latest is not None:
            await self.db.control_model_request(runtime.call_id, latest)

    async def _fail_voice(self, call_id: str) -> None:
        await self._local_exit(call_id, "failure")

    async def _local_exit(self, call_id: str, kind: str) -> None:
        from app.reception_audio import EXIT_MESSAGES

        runtime = self._runtime
        if runtime and runtime.call_id == call_id:
            if runtime.local_exit_started:
                await runtime.local_exit_done.wait()
                return
            runtime.local_exit_started = True
            try:
                row = await self.db.get_call(call_id)
                if (
                    runtime.direction == "inbound"
                    and row
                    and row["state"] == CallState.ACTIVE
                    and runtime.ending_after is None
                ):
                    language = (
                        getattr(runtime.voice, "language", None) or runtime.packet.language or "en"
                    )
                    try:
                        if runtime.voice:
                            try:
                                await asyncio.wait_for(runtime.voice.stop(), 2)
                            except (TimeoutError, RuntimeError):
                                pass
                        await self.asterisk.play_local_exit(call_id, kind, language)
                        await asyncio.wait_for(runtime.exit_playback_finished.wait(), 10)
                        if runtime.exit_playback_completed:
                            runtime.transcript.put_nowait(
                                (
                                    "assistant",
                                    EXIT_MESSAGES[kind][language],
                                    "local_reception_exit",
                                    False,
                                )
                            )
                    except (AsteriskError, TimeoutError, AttributeError):
                        logger.warning("Reception exit playback could not be confirmed")
                await self.terminate_call(
                    call_id,
                    "inbound_silence_timeout" if kind == "silence" else "android_voice_failed",
                )
            finally:
                runtime.local_exit_done.set()
        else:
            await self.terminate_call(call_id, "android_voice_failed")

    async def _write_transcript(self, runtime: _Call) -> None:
        while True:
            speaker, text, source, interrupted = await runtime.transcript.get()
            try:
                identifier = secrets.token_hex(12)
                await self.db.add_transcript_turn(
                    call_id=runtime.call_id,
                    turn_id=identifier,
                    speaker=speaker,
                    text=text,
                    source_event_type=source + (".interrupted" if interrupted else ""),
                    source_event_id=identifier,
                    end_ms=(time.monotonic() - runtime.created) * 1000,
                )
            except Exception:
                self._spawn(self._transcript_failed(runtime.call_id))
                raise
            finally:
                runtime.transcript.task_done()

    async def _transcript_failed(self, call_id: str) -> None:
        await self.terminate_call(call_id, "android_transcript_failed")

    async def _handle_event(self, event: dict[str, Any]) -> None:
        runtime = self._runtime
        if (
            runtime
            and event.get("type") == "PlaybackFinished"
            and event.get("playback", {}).get("id") == runtime.call_id + "-exit"
        ):
            runtime.exit_playback_completed = (
                event.get("playback", {}).get("state", "done") == "done"
            )
            runtime.exit_playback_finished.set()
            return
        channel = event.get("channel", {})
        identifier = channel.get("id")
        if (
            event.get("type") == "StasisStart"
            and (event.get("args") or [None])[0] == "inbound"
            and isinstance(identifier, str)
            and channel.get("name", "").startswith("PJSIP/android-")
            and (runtime is None or runtime.incoming_channel != identifier)
        ):
            if identifier not in self._incoming_pending:
                self._incoming_pending.add(identifier)
                self._spawn(self.inbound.admit(event))
            return
        if runtime is None:
            return
        cell, media, _ = self.asterisk.resource_ids(runtime.call_id)
        cell = runtime.incoming_channel or cell
        if identifier not in {cell, media}:
            return
        kind = event.get("type")
        if runtime.direction == "inbound" and identifier == cell and kind == "ChannelDtmfReceived":
            if event.get("digit") == "D" and runtime.answer_requested:
                runtime.incoming_audio_ready.set()
            return
        if identifier == media and kind == "StasisStart":
            runtime.media_in_application.set()
            return
        if kind in {"StasisEnd", "ChannelDestroyed"}:
            if (
                identifier == media
                and runtime.direction == "inbound"
                and runtime.local_exit_started
                and not runtime.local_exit_done.is_set()
            ):
                return
            if identifier == cell:
                runtime.exit_playback_finished.set()
            reason = "callee_hangup" if identifier == cell else "android_media_disconnected"
            await self.terminate_call(runtime.call_id, reason)
        elif identifier == cell and kind in {"StasisStart", "ChannelStateChange"}:
            if runtime.direction == "inbound":
                return
            if event.get("channel", {}).get("state") != "Up":
                return
            await self.activate_answered(runtime)

    async def activate_answered(self, runtime: _Call) -> None:
        async with self._operations:
            row = await self.db.get_call(runtime.call_id)
            if not row or row["state"] != CallState.ACTIVATING:
                return
            try:
                await self.asterisk.bridge_answered_call(runtime.call_id)
                if self.config.recording_enabled:
                    await self.asterisk.start_recording(runtime.call_id, runtime.duration_seconds)
                    self.artifacts.mark_recording_start(runtime.call_id)
                if not await self.db.promote_to_active(runtime.call_id):
                    return
                await self.db.update_call(
                    runtime.call_id, answered_at=datetime.now(UTC).isoformat(), callee_joined=1
                )
                assert runtime.voice is not None
                await runtime.voice.start_conversation()
            except Exception:
                self._spawn(self._fail_voice(runtime.call_id))

    async def preempt_practice(self) -> None:
        if self.control.practice.active:
            self.control.practice.active.error = (
                "Practice stopped because a telephone call needs the line."
            )
            await self.control.practice.close()
        if self.control.checks.active:
            self.control.checks.active.error = (
                "The check stopped because a telephone call needs the line."
            )
            await self.control.checks.cancel()
        if self.control.practice.active or self.control.checks.active:
            raise CallRefusal(
                ErrorCode.CALL_BUSY, "Practice is still releasing its provider connections"
            )

    async def terminate_call(
        self, call_id: str, reason: str, *, guard: Callable[[], bool] | None = None
    ) -> bool:
        async with self._operations:
            claimed = await self.db.claim_termination(call_id, reason, claim_guard=guard)
            if claimed is None:
                return False
            self._ending_after[call_id] = time.time()
            if self._runtime and self._runtime.call_id == call_id:
                self._runtime.ending_after = self._ending_after[call_id]
        self._spawn(self._settle(call_id))
        return True

    async def _settle(self, call_id: str) -> None:
        async with self._operations:
            row = await self.db.get_call(call_id)
            if not row or row["state"] != CallState.TERMINATING:
                return
            runtime = self._runtime if self._runtime and self._runtime.call_id == call_id else None
            # Preserve this observation boundary across retries and startup recovery.
            requested_at = self._ending_after.setdefault(call_id, time.time())
            if self.config.recording_enabled:
                try:
                    await asyncio.wait_for(self.asterisk.stop_recording(call_id), timeout=1)
                    self.artifacts.mark_recording_stop(call_id)
                except (AsteriskError, TimeoutError, OSError, ValueError):
                    logger.warning("Call recording stop was not confirmed")
            await self.asterisk.hangup(call_id)
            if runtime and runtime.voice:
                await runtime.voice.stop()
            if not await self.asterisk.media_absent(call_id):
                return
            # Asterisk alone cannot prove that the cellular modem hung up.
            incoming = await self.db.fetch_one(
                "SELECT direction,answer_requested FROM call_contacts WHERE call_id=?", (call_id,)
            )
            released_unanswered = (
                incoming and incoming["direction"] == "inbound" and not incoming["answer_requested"]
            ) or (
                not row["callee_dialed"] and (not incoming or incoming["direction"] == "outbound")
            )
            if not released_unanswered and not self.phone.carrier_idle_after(requested_at):
                return
            if runtime and runtime.voice_task and not runtime.voice_task.done():
                return
            if runtime:
                if runtime.writer and runtime.writer.done() and runtime.transcript.qsize():
                    raise RuntimeError("Transcript storage is unavailable")
                await runtime.transcript.join()
            now = datetime.now(UTC)
            started = row.get("answered_at") or row.get("started_at") or row["created_at"]
            duration = max(0, int((now - datetime.fromisoformat(started)).total_seconds()))
            reason = row["termination_reason"]
            terminal = CallState.FAILED
            if reason == "duration_limit":
                terminal = CallState.TIMED_OUT
            elif reason in {"owner_request", "assistant_finished", "callee_hangup"}:
                terminal = CallState.COMPLETED if row.get("answered_at") else CallState.FAILED
            if not await self.db.finish_claimed_termination(
                call_id,
                expected_reason=reason,
                terminal_state=terminal,
                ended_at=now.isoformat(),
                duration_seconds=duration,
            ):
                return
            await self.finalizer.finalize(call_id)
            await self.db.reception_enqueue_review(call_id)
            if incoming and incoming["direction"] == "inbound":
                forget = getattr(self.asterisk, "forget_incoming", None)
                if forget:
                    forget(call_id)
            if runtime:
                if runtime.writer:
                    runtime.writer.cancel()
                if self._runtime is runtime:
                    self._runtime = None

    async def recover_startup(self) -> None:
        # Never adopt the other backend's provider identities or recover by redialling.
        for row in await self.db.list_nonterminal_calls():
            incoming = await self.db.fetch_one(
                "SELECT channel_id FROM call_contacts WHERE call_id=? AND direction='inbound'",
                (row["call_id"],),
            )
            if not incoming:
                configuration = await self.db.control_plan(row["plan_id"])
                if (
                    configuration
                    and configuration.get("direction") == "inbound"
                    and configuration.get("incoming_channel")
                ):
                    plan = await self.db.get_plan_record(row["plan_id"])
                    assert plan is not None
                    phone = plan.context.target.phone
                    await self.db.contact_link_call(
                        call_id=row["call_id"],
                        phone=phone if phone.startswith("+") else None,
                        name="",
                        direction="inbound",
                        channel_id=configuration["incoming_channel"],
                        frozen_memory=configuration.get("contact_memory"),
                    )
                    incoming = {"channel_id": configuration["incoming_channel"]}
            if incoming and incoming["channel_id"]:
                self.asterisk.adopt_incoming(row["call_id"], incoming["channel_id"])
            if any(
                row.get(key)
                for key in ("openai_call_id", "twilio_ai_call_sid", "twilio_callee_call_sid")
            ):
                raise RuntimeError("Use a separate database for the Android backend")
            await self.db.claim_startup_recovery(
                row["call_id"], expected_transfer_outcome=None, completed_transfer=False
            )
        for row in await self.db.list_terminal_calls_needing_finalization():
            await self.finalizer.finalize(row["call_id"])

    async def _event_loop(self) -> None:
        while not self._stopping:
            try:
                async for event in self.asterisk.events():
                    if event.get("type") == "ConnectionReady":
                        self._events_connected = True
                    else:
                        await self._handle_event(event)
            except Exception:
                logger.warning("Asterisk event connection is unavailable")
            finally:
                self._events_connected = False
            if self._runtime:
                await self.terminate_call(self._runtime.call_id, "android_control_disconnected")
            await asyncio.sleep(2)

    async def _watch(self) -> None:
        while not self._stopping:
            try:
                for row in await self.db.list_nonterminal_calls():
                    call_id = row["call_id"]
                    if row["state"] == CallState.TERMINATING:
                        await self._settle(call_id)
                        continue
                    age = (
                        datetime.now(UTC) - datetime.fromisoformat(row["created_at"])
                    ).total_seconds()
                    budget = (
                        self._runtime.duration_seconds
                        if self._runtime and self._runtime.call_id == call_id
                        else self.config.max_call_seconds
                    )
                    if age > budget:
                        await self.terminate_call(call_id, "duration_limit")
                    elif self.phone.read() is None:
                        await self.terminate_call(call_id, "android_supervision_lost")
                self._watchdog_at = time.monotonic()
            except Exception:
                logger.error("Android call supervision pass failed")
            await asyncio.sleep(1)

    async def start_watchdog(self) -> None:
        await self.control.initialize()
        await self.control.contacts.recover_pending_reviews()
        self._events = asyncio.create_task(self._event_loop())
        self._watchdog = asyncio.create_task(self._watch())
        self.control.callbacks.task = asyncio.create_task(self.control.callbacks.run())

    def supervision_ready(self) -> bool:
        return bool(
            not self._stopping
            and self._events_connected
            and self._watchdog
            and not self._watchdog.done()
            and time.monotonic() - self._watchdog_at < 10
        )

    async def stop(self) -> None:
        self._stopping = True
        await self.control.checks.cancel()
        await self.control.practice.close()
        for row in await self.db.list_nonterminal_calls():
            await self.terminate_call(row["call_id"], "service_shutdown")
            await self._settle(row["call_id"])
        for task in [self._events, self._watchdog, self.control.callbacks.task, *self._tasks]:
            if task:
                task.cancel()
        await asyncio.gather(
            *(
                task
                for task in [
                    self._events,
                    self._watchdog,
                    self.control.callbacks.task,
                    *self._tasks,
                ]
                if task
            ),
            return_exceptions=True,
        )
        await self.asterisk.close()

    async def get_plan_status(self, plan_id: str) -> dict[str, Any]:
        from app.call_lookup import lookup_plan

        status = await lookup_plan(self.db, plan_id)
        if status["call_id"]:
            snapshot = await self.get_snapshot(status["call_id"])
            return {**snapshot.model_dump(mode="json", exclude={"result"}), "plan_id": plan_id}
        return status

    async def get_snapshot(self, call_id: str) -> CallSnapshot:
        row = await self.db.get_call(call_id)
        if row is None:
            raise LookupError(call_id)
        return CallSnapshot(
            call_id=call_id,
            state=CallState(row["state"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row.get("started_at") else None,
            answered_at=datetime.fromisoformat(row["answered_at"])
            if row.get("answered_at")
            else None,
            ended_at=datetime.fromisoformat(row["ended_at"]) if row.get("ended_at") else None,
            answered_by=row.get("answered_by"),
            answer_handling=row.get("answer_handling"),
            duration_seconds=row.get("duration_seconds"),
            result=await self.db.get_result(call_id),
            cost=None,
        )

    async def get_result(self, call_id: str, *, after_sequence: int = 0) -> dict[str, Any]:
        snapshot = await self.get_snapshot(call_id)
        result = snapshot.result
        if snapshot.state in TERMINAL_STATES and (
            not result or result.finalization_status == "telephony_only"
        ):
            result = await self.finalizer.finalize(call_id)
        turns = await self.db.get_transcript_page(call_id, after_sequence=after_sequence, limit=101)
        artifacts: dict[str, Any] = {"audio_status": "pending", "files": []}
        if snapshot.state in TERMINAL_STATES:
            row = await self.db.get_call(call_id)
            assert row is not None
            artifacts = await self.artifacts.ensure(
                row, await self.db.get_transcript(call_id), self.asterisk
            )
        call_row = await self.db.get_call(call_id)
        configuration = await self.db.control_plan(call_row["plan_id"]) if call_row else None
        return {
            **await self.control.contacts.call_context(call_id),
            "configuration": self.control.reference(configuration),
            "followup_id": (configuration or {}).get("followup_id"),
            "call_id": call_id,
            "state": snapshot.state,
            "result": result.model_dump(mode="json") if result else None,
            "cost": await self.control.spending.for_call(call_id),
            "cost_note": "Provider estimates exclude carrier charges, taxes, plan fees, and credits. Missing usage is not zero.",
            "artifacts": artifacts,
            "next_transcript_cursor": turns[99].sequence_number if len(turns) > 100 else None,
            "transcript_is_call_content": True,
            "recipient_opted_out": await self.db.call_recorded_opt_out(call_id),
            "assessment": await self.control.workflows.assessment(call_id),
            "followups": (
                await self.control.workflows.list_followups(call_id=call_id, status="all")
            )["followups"],
            "next_action": "Return every transcript page and both attachments. New finished calls enter the review queue: use claim_call_review, read_call_review through every page, then commit_call_review for the summary, outcome, and eligible memory together. If another reviewer owns the lease, analysis is pending. Do not bypass this with save_call_outcome. Historical calls enter memory only on an explicit owner review request. Ended calls do not imply successful tasks.",
            "transcript": [turn.model_dump(mode="json") for turn in turns[:100]],
        }

    def artifact_content(self, call_id: str) -> list[ArtifactContent]:
        return self.artifacts.content(call_id)

    async def wait_for_call_event(
        self, call_id: str, after_sequence: int = 0, timeout_seconds: float = 20.0
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(
            0, min(timeout_seconds, self.settings.wait_for_call_event_max_seconds)
        )
        while True:
            snapshot = await self.get_snapshot(call_id)
            terminal = snapshot.state in TERMINAL_STATES
            if terminal or time.monotonic() >= deadline:
                return {
                    "call_id": call_id,
                    "state": snapshot.state,
                    "terminal": terminal,
                    "events": [],
                    "next_after_sequence": max(0, after_sequence),
                    "next_action": "Retrieve get_call_result and all transcript pages."
                    if terminal
                    else "Continue phone_call_poll with timeout_seconds=20; the call is still in progress.",
                }
            await asyncio.sleep(min(0.25, max(0, deadline - time.monotonic())))

    async def answer_call_question(self, command: AnswerCommand) -> dict[str, Any]:
        raise LookupError(command.question_id)

    async def acquire_deployment_lock(self) -> int:
        async with self._operations:
            if self.control.practice.active is not None or self.control.checks.active is not None:
                return 1
            return await self.db.acquire_deployment_lock()

    async def release_deployment_lock(self) -> None:
        await self.db.release_deployment_lock()
