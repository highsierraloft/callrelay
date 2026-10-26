"""Compose Pipecat's existing Asterisk, transcription, and conversation components."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx2
from fastapi import WebSocket
from loguru import logger as pipecat_logger
from openai import DefaultAsyncHttpxClient
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    Frame,
    FunctionCallResultProperties,
    InputTransportMessageFrame,
    LLMRunFrame,
    TTSSpeakFrame,
)
from pipecat.observers.user_bot_latency_observer import LatencyBreakdown, UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.stt import OpenAISTTService
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService
from pipecat.transcriptions.language import Language
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.turns.user_start import ExternalUserTurnStartStrategy, VADUserTurnStartStrategy
from pipecat.turns.user_stop import ExternalUserTurnStopStrategy, SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner
from pipecat_asterisk import AsteriskFrameSerializer

from app.android_config import AndroidConfig
from app.call_ai_inputs import (
    CLOSING_RESERVE_SECONDS,
    FAREWELL_REPLY_SECONDS,
    ModelRequestTrace,
    call_state_notice,
    initial_messages,
    model_options,
    model_tools,
    scheduling_clock,
    time_notice,
)
from app.contact_preferences import is_direct_opt_out
from app.inworld_client import InworldVoiceTTSService as InworldTTSService
from app.models import ContextPacket
from app.openrouter_client import OpenRouterVoiceLLMService
from app.openrouter_tts import OPENROUTER_API_URL, OpenRouterTTSService
from app.phone_menu import MENU_LISTEN_SECONDS, PhoneMenu
from app.provider_usage import UsageLedger
from app.soniox_client import SonioxVoiceSTTService as SonioxSTTService
from app.spoken_identity import SpeakerIdentityFilter
from app.voice_prompts import voice_instructions

logger = logging.getLogger(__name__)


@dataclass
class VoiceCallbacks:
    transcript: Callable[[Literal["callee", "assistant"], str, str, bool], None]
    failed: Callable[[], None]
    finish: Callable[[Callable[[], bool]], Awaitable[bool]]
    suppress_destination: Callable[[], Awaitable[None]] | None = None
    metrics: Callable[[dict[str, Any]], Any] | None = None
    callback_tool: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None
    silence_timeout: Callable[[], None] | None = None
    send_dtmf: Callable[[str], Awaitable[None]] | None = None


class ClosingWindow:
    """Require a played farewell and preserve the recipient's reply window."""

    def __init__(self) -> None:
        self.user_revision = 0
        self.last_played_text = ""
        self.played_revision = -1
        self.farewell: str | None = None
        self.cancelled = False

    def user_started(self) -> None:
        self.user_revision += 1
        self.cancelled = self.cancelled or self.farewell is not None
        self.farewell = None

    def played(self, text: str, interrupted: bool) -> None:
        self.last_played_text = "" if interrupted else text
        self.played_revision = self.user_revision

    def request(self, farewell: str) -> None:
        self.farewell = farewell.strip()
        self.cancelled = False
        self.played_revision = -1

    def guard(self) -> Callable[[], bool] | None:
        def normalize(value: str) -> str:
            return " ".join(value.casefold().split())

        if not self.farewell or self.played_revision != self.user_revision:
            return None
        if normalize(self.farewell) not in normalize(self.last_played_text):
            return None
        revision, farewell = self.user_revision, self.farewell
        return lambda: self.user_revision == revision and self.farewell == farewell


class _MediaIdentity(FrameProcessor):
    def __init__(
        self,
        channel_id: str,
        ready: asyncio.Event,
        failed: Callable[[], None],
        playback: Callable[[bool], None],
    ) -> None:
        super().__init__()
        self.channel_id = channel_id
        self.ready = ready
        self.failed = failed
        self.playback = playback

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            self.playback(True)
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self.playback(False)
        if (
            isinstance(frame, InputTransportMessageFrame)
            and frame.message.get("event") == "MEDIA_START"
        ):
            if (
                frame.message.get("channel_id") != self.channel_id
                or frame.message.get("format") != "slin"
            ):
                self.failed()
                return
            self.ready.set()
        await self.push_frame(frame, direction)


class AndroidVoiceSession:
    def __init__(
        self,
        websocket: WebSocket,
        *,
        call_id: str,
        packet: ContextPacket,
        config: AndroidConfig,
        api_key: str,
        callbacks: VoiceCallbacks,
        configuration: dict[str, Any] | None = None,
        browser: bool = False,
        transport: FastAPIWebsocketTransport | None = None,
    ) -> None:
        if packet.language is None or packet.language not in config.conversation_languages:
            raise ValueError("The initial call language must be enabled")
        config.validate_voice_credentials()
        # Call text belongs in the private transcript store, not dependency debug logs.
        pipecat_logger.disable("pipecat")
        pipecat_logger.disable("pipecat_asterisk")
        self.callbacks = callbacks
        self.language: str = packet.language
        self._allowed_languages = set(config.conversation_languages)
        self.usage = UsageLedger(stt=config.stt_transport, tts=config.tts_transport)
        self.ready = asyncio.Event()
        self.connected = asyncio.Event()
        self.closing = ClosingWindow()
        self._phone_menu = PhoneMenu(callbacks.send_dtmf)
        self._recipient_message_revision = 0
        self._menu_listen_until = 0.0
        self._closing_task: asyncio.Task[None] | None = None
        self._wrapup_task: asyncio.Task[None] | None = None
        self._silence_task: asyncio.Task[None] | None = None
        self._last_activity = time.monotonic()
        self._silence_seconds = int(
            ((configuration or {}).get("inbound") or {}).get("silence_seconds", 0)
        )
        self._started_at = time.monotonic()
        self._max_call_seconds = config.call_duration(packet.max_duration_seconds)
        self._idle = asyncio.Event()
        self._user_speaking = False
        self._assistant_busy = False
        self._playback_active = False
        self._sales_call = packet.is_commercial
        self._respect_all_opt_outs = bool((configuration or {}).get("reception_contract_version"))
        self._callback_timezone = ((configuration or {}).get("callback_policy") or {}).get(
            "timezone"
        )
        self._destination_suppressed = False
        self._identity_filter = SpeakerIdentityFilter(packet.owner.display_name)
        self._played_response_serial: int | None = None
        self._stt_http = DefaultAsyncHttpxClient(timeout=httpx2.Timeout(8, connect=3))
        self.request_trace = ModelRequestTrace()
        self._llm_http = DefaultAsyncHttpxClient(
            timeout=httpx2.Timeout(10, connect=3),
            event_hooks={"request": [self.request_trace.capture]},
        )
        input_sample_rate = 16000 if browser else 8000
        output_sample_rate = 24000 if browser else 8000
        transport = transport or FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                serializer=ProtobufFrameSerializer() if browser else AsteriskFrameSerializer(),
                audio_in_enabled=True,
                audio_out_enabled=True,
                audio_in_sample_rate=input_sample_rate,
                audio_out_sample_rate=output_sample_rate,
            ),
        )
        self._transport = transport
        stt: STTService
        if config.stt_transport == "deepgram_flux":
            flux = DeepgramFluxSTTService(
                api_key=config.deepgram_key().get_secret_value(),
                sample_rate=input_sample_rate,
                enable_eager_end_of_turn=True,
                settings=DeepgramFluxSTTService.Settings(
                    model="flux-general-multi",
                    language_hints=[Language(code) for code in config.conversation_languages],
                    eager_eot_threshold=0.5,
                    eot_threshold=0.7,
                ),
            )
            stt = flux
            user_params = LLMUserAggregatorParams(
                user_turn_strategies=flux.recommended_user_turn_strategies(
                    enable_interruptions=True
                )
            )
        elif config.stt_transport == "soniox":
            semantic = config.soniox_turn_detection == "semantic"
            stt = SonioxSTTService(
                on_language=self._note_language,
                usage=self.usage,
                api_key=config.soniox_key().get_secret_value(),
                sample_rate=input_sample_rate,
                vad_force_turn_endpoint=not semantic,
                settings=SonioxSTTService.Settings(
                    model=config.soniox_model,
                    language_hints=[Language(code) for code in config.conversation_languages],
                    max_endpoint_delay_ms=config.soniox_endpoint_delay_ms if semantic else None,
                    endpoint_latency_adjustment_level=config.soniox_endpoint_latency_level
                    if semantic
                    else None,
                    endpoint_sensitivity=config.soniox_endpoint_sensitivity if semantic else None,
                ),
            )
            if semantic:
                # Local speech detection interrupts promptly; Soniox decides when
                # the utterance is complete instead of cutting at every short pause.
                user_params = LLMUserAggregatorParams(
                    vad_analyzer=SileroVADAnalyzer(),
                    user_turn_strategies=UserTurnStrategies(
                        start=[VADUserTurnStartStrategy(), ExternalUserTurnStartStrategy()],
                        stop=[ExternalUserTurnStopStrategy()],
                    ),
                )
            else:
                # Finalize the live stream after a short silence, then wait for final text.
                user_params = LLMUserAggregatorParams(
                    vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.3)),
                    user_turn_strategies=UserTurnStrategies(
                        stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.0)]
                    ),
                )
        else:
            stt = OpenAISTTService(
                api_key=api_key,
                base_url=OPENROUTER_API_URL,
                http_client=self._stt_http,
                sample_rate=input_sample_rate,
                include_prob_metrics=False,
                settings=OpenAISTTService.Settings(
                    model=config.stt_model, language=Language(packet.language)
                ),
            )
            user_params = LLMUserAggregatorParams(
                vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.3)),
            )
        options = model_options(config)
        llm = OpenRouterVoiceLLMService(
            usage=self.usage,
            phone_menu_enabled=bool((configuration or {}).get("phone_menu_version")),
            api_key=api_key,
            http_client=self._llm_http,
            settings=OpenRouterVoiceLLMService.Settings(
                model=options["model"],
                max_tokens=options["max_tokens"],
                **({"temperature": options["temperature"]} if "temperature" in options else {}),
                extra={
                    "extra_body": {"reasoning": options["reasoning"]}
                    if "reasoning" in options
                    else {}
                },
                provider=OpenRouterVoiceLLMService.ProviderPreferences(**options["provider"]),
            ),
        )
        tts: TTSService
        if config.tts_transport == "inworld":
            tts = InworldTTSService(
                usage=self.usage,
                api_key=config.inworld_key().get_secret_value(),
                sample_rate=8000,
                encoding="PCM",
                auto_mode=True,
                timestamp_transport_strategy="ASYNC",
                identity_filter=self._identity_filter,
                settings=InworldTTSService.Settings(
                    model=config.inworld_model,
                    voice=config.inworld_voice,
                    delivery_mode=config.inworld_delivery_mode,
                    language=None,
                ),
            )
        else:
            tts = OpenRouterTTSService(api_key=api_key, model=config.tts_model, voice=config.voice)
        tools = ToolsSchema(
            standard_tools=[
                FunctionSchema(
                    name=function["name"],
                    description=function["description"],
                    properties=function["parameters"]["properties"],
                    required=function["parameters"]["required"],
                )
                for function in [
                    tool["function"]
                    for tool in model_tools(
                        callbacks_enabled=bool((configuration or {}).get("callback_policy")),
                        phone_menu_enabled=bool((configuration or {}).get("phone_menu_version")),
                    )
                ]
            ]
        )
        self._system_prompt = (
            configuration.get("effective_prompt") if configuration else None
        ) or voice_instructions(packet, conversation_languages=list(config.conversation_languages))
        context = LLMContext(
            messages=initial_messages(self._system_prompt),
            tools=tools,
        )
        self._context = context
        aggregators = LLMContextAggregatorPair(context, user_params=user_params)
        latency = UserBotLatencyObserver()

        async def latency_measured(_observer: Any, breakdown: LatencyBreakdown) -> None:
            if callbacks.metrics:
                callbacks.metrics(
                    {
                        "measured_from": str(breakdown.measured_from),
                        "seconds": round(breakdown.total_secs, 4),
                        "parts": [
                            {"stage": part.key, "seconds": round(part.duration_secs, 4)}
                            for part in breakdown.contributions
                        ],
                    }
                )
            # Keep speech and credentials out of diagnostic logs.
            logger.info(
                "voice_latency %s",
                json.dumps(
                    {
                        "call_id": call_id,
                        "measured_from": str(breakdown.measured_from),
                        "seconds": round(breakdown.total_secs, 4),
                        "parts": [
                            {"stage": part.key, "seconds": round(part.duration_secs, 4)}
                            for part in breakdown.contributions
                        ],
                    }
                ),
            )

        latency.add_event_handler("on_latency_breakdown", latency_measured)
        self._worker = PipelineWorker(
            Pipeline(
                [
                    transport.input(),
                    stt,
                    aggregators.user(),
                    llm,
                    tts,
                    transport.output(),
                    _MediaIdentity(
                        call_id + "-media", self.ready, callbacks.failed, self._playback_changed
                    ),
                    aggregators.assistant(),
                ]
            ),
            params=PipelineParams(
                audio_in_sample_rate=input_sample_rate,
                audio_out_sample_rate=output_sample_rate,
                enable_metrics=True,
            ),
            observers=[latency],
        )
        self._runner = WorkerRunner(handle_sigint=False)

        llm.register_function("finish_call", self._finish_call, cancel_on_interruption=True)
        if (configuration or {}).get("phone_menu_version"):
            # Once dispatched, an interruption must not lose the keypad receipt and retry it.
            llm.register_function("send_dtmf", self._send_dtmf, cancel_on_interruption=False)
            llm.register_function(
                "wait_for_phone_menu", self._wait_for_phone_menu, cancel_on_interruption=True
            )
        if (configuration or {}).get("callback_policy"):
            for name in ("propose_callback", "confirm_callback", "cancel_callback"):
                llm.register_function(name, self._callback_tool, cancel_on_interruption=True)

        async def pipeline_error(_worker: Any, frame: ErrorFrame) -> None:
            self.usage.incomplete = True
            logger.error(
                "voice_error %s",
                json.dumps(
                    {
                        "call_id": call_id,
                        "processor": type(frame.processor).__name__,
                        "exception_type": type(frame.exception).__name__,
                        "category": str(frame.category),
                        "status_code": getattr(frame.exception, "status_code", None),
                    }
                ),
            )
            callbacks.failed()

        async def user_started(*_: Any) -> None:
            self._last_activity = time.monotonic()
            self._user_speaking = True
            self._idle.clear()
            self.closing.user_started()
            if self._closing_task:
                self._closing_task.cancel()
                self._closing_task = None

        async def user_message(_aggregator: Any, message: Any) -> None:
            if message.content:
                self._recipient_message_revision += 1
                self._last_activity = time.monotonic()
                opt_out = (self._sales_call or self._respect_all_opt_outs) and is_direct_opt_out(
                    message.content
                )
                if opt_out:
                    await self._suppress_destination()
                self._context.set_tool_choice(
                    {"type": "function", "function": {"name": "finish_call"}} if opt_out else "auto"
                )
                self._assistant_busy = True
                self._idle.clear()
                self._add_time_notice()
                callbacks.transcript("callee", message.content, "pipecat.transcription", False)

        async def user_stopped(_aggregator: Any, _strategy: Any, message: Any) -> None:
            self._user_speaking = False
            if not message.content and not self._assistant_busy:
                self._idle.set()

        async def assistant_started(*_: Any) -> None:
            self._assistant_busy = True
            self._idle.clear()

        async def assistant_message(_aggregator: Any, message: Any) -> None:
            self._last_activity = time.monotonic()
            self._assistant_busy = False
            if not self._user_speaking and not self._playback_active:
                self._idle.set()
            if message.content:
                callbacks.transcript(
                    "assistant", message.content, "pipecat.assistant_output", message.interrupted
                )
            self.closing.played(message.content or "", message.interrupted)
            self._played_response_serial = None if message.interrupted else llm.response_serial
            self._schedule_closing()

        async def disconnected(*_: Any) -> None:
            await self._runner.cancel(reason="media disconnected")

        self._worker.add_event_handler("on_pipeline_error", pipeline_error)
        aggregators.user().add_event_handler("on_user_turn_started", user_started)
        aggregators.user().add_event_handler("on_user_turn_stopped", user_stopped)
        aggregators.user().add_event_handler("on_user_turn_message_added", user_message)
        aggregators.assistant().add_event_handler("on_assistant_turn_started", assistant_started)
        aggregators.assistant().add_event_handler("on_assistant_turn_stopped", assistant_message)
        transport.add_event_handler("on_client_disconnected", disconnected)
        if browser:

            async def browser_ready(*_: Any) -> None:
                self.ready.set()
                await self.start_conversation()

            self._worker.rtvi.add_event_handler("on_client_ready", browser_ready)

    async def _finish_call(self, params: FunctionCallParams) -> None:
        farewell = params.arguments.get("farewell", "")
        if not isinstance(farewell, str) or not 1 <= len(farewell.strip()) <= 500:
            await params.result_callback(
                {"closing_requested": False, "reason": "Supply a brief farewell to speak"}
            )
            return
        if params.arguments.get("do_not_call") is True and (
            getattr(self, "_sales_call", False) or getattr(self, "_respect_all_opt_outs", False)
        ):
            await self._suppress_destination()
        identity_filter = getattr(self, "_identity_filter", None)
        if identity_filter is not None:
            farewell = await identity_filter.filter(farewell)
        if self.closing.farewell is None:
            generated = getattr(params.llm, "generated_response_text", "")
            if identity_filter is not None and isinstance(generated, str):
                generated = await identity_filter.filter(generated)

            def normalize(text: str) -> str:
                return " ".join(text.casefold().split())

            already_generated = isinstance(generated, str) and normalize(farewell) in normalize(
                generated
            )
            serial = getattr(params.llm, "response_serial", None)
            already_played = (
                already_generated
                and serial is not None
                and serial == self._played_response_serial
                and self.closing.played_revision == self.closing.user_revision
                and normalize(farewell) in normalize(self.closing.last_played_text)
            )
            self.closing.request(farewell)
            if already_played:
                self.closing.played(self.closing.last_played_text, interrupted=False)
                self._schedule_closing()
            elif not already_generated:
                await self._worker.queue_frame(
                    TTSSpeakFrame(farewell.strip(), append_to_context=True)
                )
        await params.result_callback(
            {
                "closing_requested": True,
                "call_ended": False,
                "status": "closing_pending",
                "next_action": "Wait for farewell playback. Recipient speech cancels closing and the conversation must resume.",
            },
            properties=FunctionCallResultProperties(run_llm=False),
        )

    async def _send_dtmf(self, params: FunctionCallParams) -> None:
        try:
            if self.closing.farewell is not None:
                raise ValueError("Closing is pending; do not press keys")
            result = await self._phone_menu.press(
                params.tool_call_id,
                params.arguments.get("digits"),
                self._recipient_message_revision,
            )
        except ValueError as exc:
            result = {"status": "not_sent", "reason": str(exc)}
        self._menu_listen_until = time.monotonic() + MENU_LISTEN_SECONDS
        await params.result_callback(result, properties=FunctionCallResultProperties(run_llm=False))

    async def _wait_for_phone_menu(self, params: FunctionCallParams) -> None:
        self._menu_listen_until = time.monotonic() + MENU_LISTEN_SECONDS
        await params.result_callback(
            {"listening": True, "call_ended": False},
            properties=FunctionCallResultProperties(run_llm=False),
        )

    async def _suppress_destination(self) -> None:
        if self._destination_suppressed:
            return
        if self.callbacks.suppress_destination is None:
            raise RuntimeError("Recipient opt-out storage is unavailable")
        try:
            await self.callbacks.suppress_destination()
        except Exception:
            self.callbacks.failed()
            raise RuntimeError("Recipient opt-out storage failed") from None
        self._destination_suppressed = True

    async def _callback_tool(self, params: FunctionCallParams) -> None:
        try:
            if self.callbacks.callback_tool is None:
                raise ValueError("Callback scheduling is unavailable in this session")
            result = await self.callbacks.callback_tool(
                params.function_name, dict(params.arguments)
            )
        except (ValueError, LookupError) as exc:
            result = {"saved": False, "error": str(exc)}
        readback = (
            result.get("readback")
            if params.function_name == "propose_callback" and not result.get("schedule_id")
            else None
        )
        if isinstance(readback, str):
            await params.result_callback(
                result, properties=FunctionCallResultProperties(run_llm=False)
            )
            await self._worker.queue_frame(TTSSpeakFrame(readback))
        else:
            await params.result_callback(result)

    def _note_language(self, language: str) -> None:
        if language in self._allowed_languages:
            self.language = language

    def _add_time_notice(self, *, closing: bool = False) -> None:
        remaining = max(0, int(self._max_call_seconds - (time.monotonic() - self._started_at)))
        content = time_notice(remaining, closing=closing)
        content += "\n" + call_state_notice(
            closing=self.closing.farewell is not None, cancelled=self.closing.cancelled
        )
        if self._callback_timezone:
            content += "\n" + scheduling_clock(self._callback_timezone)
        # The Gemini adapter converts additional system messages to user messages.
        # Update the leading instruction so internal timing never becomes recipient speech.
        self._context.messages[0] = {
            "role": "system",
            "content": self._system_prompt + "\n" + content,
        }

    async def _request_timed_wrapup(self) -> None:
        delay = max(
            0.0,
            self._max_call_seconds
            - CLOSING_RESERVE_SECONDS
            - (time.monotonic() - self._started_at),
        )
        await asyncio.sleep(delay)
        # Wait for a quiet boundary; never cut off speech just to announce the timer.
        await self._idle.wait()
        if self.closing.farewell is not None:
            return
        self._idle.clear()
        self._assistant_busy = True
        self._add_time_notice(closing=True)
        await self._worker.queue_frame(LLMRunFrame())

    def _playback_changed(self, active: bool) -> None:
        self._playback_active = active
        if active:
            self._idle.clear()
        elif not self._user_speaking and not self._assistant_busy:
            self._idle.set()
        if not active:
            self._schedule_closing()

    def _schedule_closing(self) -> None:
        if self._playback_active:
            return
        guard = self.closing.guard()
        if guard is not None and (self._closing_task is None or self._closing_task.done()):

            async def close_after_reply_window() -> None:
                await asyncio.sleep(FAREWELL_REPLY_SECONDS)
                if guard():
                    await self.callbacks.finish(guard)

            self._closing_task = asyncio.create_task(close_after_reply_window())

    async def start_conversation(self) -> None:
        if not self.ready.is_set():
            raise RuntimeError("The owned media channel has not started")
        if not self.connected.is_set():
            self.connected.set()
            self._assistant_busy = True
            self._add_time_notice()
            self._wrapup_task = asyncio.create_task(self._request_timed_wrapup())
            self._last_activity = time.monotonic()
            if self._silence_seconds and self.callbacks.silence_timeout:
                self._silence_task = asyncio.create_task(self._watch_silence())
            await self._worker.queue_frame(LLMRunFrame())

    async def _watch_silence(self) -> None:
        while True:
            await asyncio.sleep(1)
            if (
                not (self._user_speaking or self._assistant_busy or self._playback_active)
                and time.monotonic() >= self._menu_listen_until
                and time.monotonic() - self._last_activity >= self._silence_seconds
            ):
                assert self.callbacks.silence_timeout is not None
                self.callbacks.silence_timeout()
                return

    async def stop(self) -> None:
        await self._runner.cancel(reason="call ended")

    async def run(self) -> None:
        try:
            await self._runner.add_workers(self._worker)
            await self._runner.run()
        except Exception:
            self.callbacks.failed()
            raise
        finally:
            if self._silence_task:
                self._silence_task.cancel()
                await asyncio.gather(self._silence_task, return_exceptions=True)
            if self._closing_task:
                self._closing_task.cancel()
                await asyncio.gather(self._closing_task, return_exceptions=True)
            if self._wrapup_task:
                self._wrapup_task.cancel()
                await asyncio.gather(self._wrapup_task, return_exceptions=True)
            await self._stt_http.aclose()
            await self._llm_http.aclose()
            self.usage.stop_stt()
            self.usage.finalized = True
