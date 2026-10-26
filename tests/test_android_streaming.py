from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocket

pytest.importorskip("pipecat")

from pipecat.frames.frames import (
    LLMTextFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from websockets.asyncio.server import serve

from app.android_calls import AndroidCallService
from app.android_config import AndroidConfig
from app.android_voice import AndroidVoiceSession, VoiceCallbacks


@pytest.mark.parametrize("language", ["en", "es", "ru", "uk"])
@pytest.mark.parametrize("turn_detection", ["semantic", "vad"])
@pytest.mark.parametrize(
    "provider_error",
    [
        None,
        "long_reply",
        "identity",
        "sales_opt_out",
        "soniox",
        "inworld",
        "interruption",
        "finish",
        "finish_interruption",
        "finish_after_text",
    ],
)
async def test_streaming_audio_transcripts_and_provider_failure(
    packet, tmp_path, monkeypatch, caplog, language, turn_detection, provider_error, browser=False
):
    """Exercise the pinned adapters over real local sockets, without provider requests."""
    from pipecat.frames.frames import OutputAudioRawFrame, OutputTransportMessageFrame
    from pipecat.frames.protobufs import frames_pb2
    from pipecat.serializers.protobuf import ProtobufFrameSerializer

    import app.android_voice as module

    caplog.set_level(logging.INFO, logger="app.android_voice")

    input_received = asyncio.Event()
    finalize_allowed = asyncio.Event()
    output_received = asyncio.Event()
    assistant_recorded = asyncio.Event()
    failed = asyncio.Event()
    model_done = asyncio.Event()
    finished = asyncio.Event()
    assistant_stopped_at = []
    interrupt_allowed = asyncio.Event()
    speech_canceled = asyncio.Event()
    media_flushed = asyncio.Queue()
    stt_configs, tts_configs, transcripts, contexts, model_inputs = [], [], [], [], []
    spoken_texts = []
    synthesis_seconds = 1.2 if provider_error == "long_reply" else 0.2
    output_audio = b"\x00\x04" * int(8000 * synthesis_seconds)
    input_audio = b"\x00\x08" * 160
    phrases = {
        "en": ("We open at noon.", "Thank you. ", "Anything else?"),
        "es": ("Abrimos al mediodía.", "Muchas gracias. ", "¿Algo más?"),
        "ru": ("Мы открываемся в полдень.", "Спасибо. ", "Что-нибудь ещё?"),
        "uk": ("Ми відкриваємося опівдні.", "Дякую. ", "Щось іще?"),
    }
    utterance, acknowledgement, unheard_tail = phrases[language]
    reply = acknowledgement + unheard_tail + " "
    expected_reply = reply.strip()
    if provider_error == "identity":
        reply = f"Hey Alex, it's {packet.owner.display_name}. Let's start. "
        expected_reply = f"Hey Alex, it's {packet.owner.display_name}'s AI assistant. Let's start."
    suppressed = AsyncMock()
    if provider_error == "sales_opt_out":
        utterance = {
            "en": "Do not call me again.",
            "es": "No me vuelvas a llamar.",
            "ru": "Не звоните мне больше.",
            "uk": "Більше не телефонуйте мені.",
        }[language]
        reply = acknowledgement
        expected_reply = reply.strip()

    async def soniox_provider(ws):
        assert ws.request.headers["Authorization"] == "Bearer test-soniox-private-credential"
        stt_configs.append(json.loads(await ws.recv()))
        async for message in ws:
            if not isinstance(message, bytes):
                continue
            assert input_audio in message
            input_received.set()
            await ws.send(json.dumps({"tokens": [{"text": utterance, "is_final": False}]}))
            await finalize_allowed.wait()
            if turn_detection == "vad":
                assert json.loads(await ws.recv()) == {"type": "finalize"}
            if provider_error == "soniox":
                await ws.send(json.dumps({"error_code": 401, "error_message": "Test rejection"}))
            else:
                await ws.send(
                    json.dumps(
                        {
                            "tokens": [
                                {"text": utterance, "is_final": True, "language": language},
                                {
                                    "text": "<end>" if turn_detection == "semantic" else "<fin>",
                                    "is_final": True,
                                },
                            ]
                        }
                    )
                )
            break
        if provider_error in {"interruption", "finish_interruption"}:
            await interrupt_allowed.wait()
            await ws.send(json.dumps({"tokens": [{"text": "Wait", "is_final": False}]}))
        await ws.wait_closed()

    async def inworld_provider(ws):
        assert ws.request.headers["Authorization"] == "Basic test-inworld-private-credential"
        async for raw in ws:
            message = json.loads(raw)
            context_id = message.get("contextId")
            if "create" in message:
                contexts.append(context_id)
                tts_configs.append(message["create"])
                await ws.send(
                    json.dumps({"result": {"contextId": context_id, "contextCreated": {}}})
                )
            if "send_text" in message:
                if provider_error == "inworld":
                    await ws.send(
                        json.dumps(
                            {
                                "result": {
                                    "contextId": context_id,
                                    "status": {"code": 16, "message": "Test rejection"},
                                }
                            }
                        )
                    )
                    continue
                text = message["send_text"]["text"].strip()
                spoken_texts.append(text)
                words = text.split()
                await ws.send(
                    json.dumps(
                        {
                            "result": {
                                "contextId": context_id,
                                "audioChunk": {
                                    "audioContent": base64.b64encode(output_audio).decode(),
                                    "timestampInfo": {
                                        "wordAlignment": {
                                            "words": words,
                                            "wordStartTimeSeconds": [
                                                i * synthesis_seconds / len(words)
                                                for i in range(len(words))
                                            ],
                                            "wordEndTimeSeconds": [
                                                (i + 1) * synthesis_seconds / len(words)
                                                for i in range(len(words))
                                            ],
                                        }
                                    },
                                },
                            }
                        }
                    )
                )
                # Each synthetic send_text is one auto-mode synthesis batch.
                # Inworld resets word timestamps only after this boundary.
                await ws.send(
                    json.dumps({"result": {"contextId": context_id, "flushCompleted": {}}})
                )
            if "flush_context" in message:
                await ws.send(
                    json.dumps({"result": {"contextId": context_id, "flushCompleted": {}}})
                )
            if "close_context" in message:
                speech_canceled.set()
                await ws.send(
                    json.dumps({"result": {"contextId": context_id, "contextClosed": {}}})
                )

    async with (
        serve(soniox_provider, "127.0.0.1", 0) as stt_server,
        serve(inworld_provider, "127.0.0.1", 0) as tts_server,
    ):

        class LocalSoniox(module.SonioxSTTService):
            def __init__(self, **kwargs):
                super().__init__(
                    url=f"ws://127.0.0.1:{stt_server.sockets[0].getsockname()[1]}", **kwargs
                )

        class LocalInworld(module.InworldTTSService):
            def __init__(self, **kwargs):
                super().__init__(
                    url=f"ws://127.0.0.1:{tts_server.sockets[0].getsockname()[1]}", **kwargs
                )

        class LocalDialogue(module.OpenRouterVoiceLLMService):
            async def _process_context(self, context):
                model_inputs.append(context.get_messages())
                params = self.build_chat_completion_params({"messages": context.get_messages()})
                assert params["extra_body"]["provider"]["only"] == ["groq"]
                assert params["extra_body"]["provider"]["allow_fallbacks"] is False
                assert "reasoning" not in params["extra_body"]
                assert params["temperature"] == 0.4
                if provider_error in {
                    "finish",
                    "finish_interruption",
                    "finish_after_text",
                    "sales_opt_out",
                }:
                    if provider_error == "sales_opt_out":
                        assert context.tool_choice == {
                            "type": "function",
                            "function": {"name": "finish_call"},
                        }
                    if provider_error == "finish_after_text":
                        await self.push_frame(LLMTextFrame(reply))
                        await output_received.wait()
                    await session._finish_call(
                        SimpleNamespace(
                            arguments={
                                "farewell": reply,
                                "do_not_call": provider_error == "sales_opt_out",
                            },
                            result_callback=AsyncMock(),
                            llm=self,
                        )
                    )
                    model_done.set()
                    return
                await self.push_frame(LLMTextFrame(reply))
                # Require speech playback to begin before model completion.
                await output_received.wait()
                if provider_error in {"interruption", "finish_interruption"}:
                    await asyncio.Event().wait()
                model_done.set()

        monkeypatch.setattr(module, "SonioxSTTService", LocalSoniox)
        monkeypatch.setattr(module, "InworldTTSService", LocalInworld)
        monkeypatch.setattr(module, "OpenRouterVoiceLLMService", LocalDialogue)
        incoming = asyncio.Queue()
        outgoing = []

        async def send(message):
            outgoing.append(message)
            if "FLUSH_MEDIA" in message.get("text", ""):
                media_flushed.put_nowait(True)
            audio = bool(message.get("bytes"))
            if browser and audio:
                frame = frames_pb2.Frame.FromString(message["bytes"])
                if frame.HasField("interruption"):
                    media_flushed.put_nowait(True)
                audio = frame.HasField("audio")
                if audio:
                    assert frame.audio.sample_rate == 24000
            if audio and not output_received.is_set():
                if provider_error not in {"finish", "finish_interruption", "sales_opt_out"}:
                    assert not model_done.is_set()
                output_received.set()

        def record(*args):
            transcripts.append(args)
            if args[0] == "assistant":
                assistant_recorded.set()
                assistant_stopped_at.append(time.monotonic())

        async def finish(guard):
            assert provider_error in {"finish", "finish_after_text", "sales_opt_out"}
            assert assistant_stopped_at and guard()
            assert time.monotonic() - assistant_stopped_at[-1] >= 2.9
            finished.set()
            return True

        websocket = WebSocket(
            {"type": "websocket", "path": "/phone-media", "headers": []}, incoming.get, send
        )
        await incoming.put({"type": "websocket.connect"})
        await websocket.accept(subprotocol="media")
        soniox_key, inworld_key = tmp_path / "soniox", tmp_path / "inworld"
        soniox_key.write_text("test-soniox-private-credential")
        inworld_key.write_text("test-inworld-private-credential")
        config = AndroidConfig(
            selected_sim_slot=1,
            caller_number="+12025550199",
            allowed_destinations=["+12025550123"],
            conversation_languages=["en", "es", "ru", "uk"],
            stt_transport="soniox",
            soniox_key_file=soniox_key,
            soniox_turn_detection=turn_detection,
            tts_transport="inworld",
            inworld_key_file=inworld_key,
            inworld_model="inworld-tts-2",
            inworld_voice="Wade",
            inworld_delivery_mode="CREATIVE",
            dialogue_model="meta-llama/llama-3.3-70b-instruct",
            dialogue_provider="groq",
            reasoning_effort=None,
        )
        session = AndroidVoiceSession(
            websocket,
            call_id="call_streaming",
            packet=packet.model_copy(
                update={
                    "language": language,
                    "intention": "sales" if provider_error == "sales_opt_out" else "task",
                }
            ),
            config=config,
            api_key="unused-openrouter-test-key",
            callbacks=VoiceCallbacks(record, failed.set, finish, suppressed),
            browser=browser,
        )
        if browser:
            session.start_conversation = AsyncMock()
        task = asyncio.create_task(session.run())
        try:
            if browser:
                serializer = ProtobufFrameSerializer()
                ready_message = await serializer.serialize(
                    OutputTransportMessageFrame(
                        {
                            "label": "rtvi-ai",
                            "type": "client-ready",
                            "id": "browser-test",
                            "data": {"version": "2.0.0"},
                        }
                    )
                )
                await incoming.put({"type": "websocket.receive", "bytes": ready_message})
            else:
                await incoming.put(
                    {
                        "type": "websocket.receive",
                        "text": json.dumps(
                            {
                                "event": "MEDIA_START",
                                "connection_id": "test",
                                "channel": "WebSocket/test",
                                "channel_id": "call_streaming-media",
                                "format": "slin",
                                "optimal_frame_size": 320,
                                "ptime": 20,
                            }
                        ),
                    }
                )
            await asyncio.wait_for(session.ready.wait(), 15)
            encoded_input = input_audio
            if browser:
                encoded_input = await serializer.serialize(
                    OutputAudioRawFrame(input_audio, sample_rate=16000, num_channels=1)
                )
                session.start_conversation.assert_awaited_once()
            await incoming.put({"type": "websocket.receive", "bytes": encoded_input})
            await asyncio.wait_for(input_received.wait(), 5)
            assert not model_inputs and not transcripts and not tts_configs
            assert stt_configs[0]["sample_rate"] == (16000 if browser else 8000)
            assert stt_configs[0]["model"] == "stt-rt-v5"
            assert stt_configs[0]["enable_endpoint_detection"] == (turn_detection == "semantic")
            assert stt_configs[0]["max_endpoint_delay_ms"] == (
                500 if turn_detection == "semantic" else None
            )
            assert stt_configs[0]["endpoint_latency_adjustment_level"] == (
                2 if turn_detection == "semantic" else None
            )
            assert stt_configs[0]["endpoint_sensitivity"] == (
                0.3 if turn_detection == "semantic" else None
            )
            assert set(stt_configs[0]["language_hints"]) == {"en", "es", "ru", "uk"}
            finalize_allowed.set()
            if turn_detection == "vad":
                await session._worker.queue_frame(VADUserStartedSpeakingFrame())
                await session._worker.queue_frame(VADUserStoppedSpeakingFrame(stop_secs=0.3))
            if provider_error in {"interruption", "finish_interruption"}:
                await asyncio.wait_for(output_received.wait(), 5)
                while not media_flushed.empty():
                    media_flushed.get_nowait()
                if provider_error == "interruption":
                    session.closing.request("Goodbye.")
                    session.closing.played("Goodbye.", interrupted=False)
                    guard = session.closing.guard()
                    assert guard and guard()
                else:
                    assert session.closing.farewell is not None

                    def guard():
                        return session.closing.farewell is not None

                interrupt_allowed.set()
                if turn_detection == "vad":
                    await session._worker.queue_frame(VADUserStartedSpeakingFrame())
                await asyncio.wait_for(media_flushed.get(), 5)
                await asyncio.wait_for(speech_canceled.wait(), 5)
                assert not guard()
                assert len(model_inputs) == 1
                assert model_done.is_set() == (provider_error == "finish_interruption")
                assert len(contexts) == (
                    2 if provider_error in {"finish", "finish_interruption", "sales_opt_out"} else 1
                )
                if turn_detection == "vad":
                    timings = [
                        json.loads(record.getMessage().removeprefix("voice_latency "))
                        for record in caplog.records
                        if record.getMessage().startswith("voice_latency ")
                    ]
                    assert timings and timings[0]["seconds"] >= 0
                    assert timings[0]["call_id"] == "call_streaming"
                    assert utterance not in json.dumps(timings)
                    assert reply.strip() not in json.dumps(timings)
                assert all(
                    unheard_tail not in row[1] for row in transcripts if row[0] == "assistant"
                )
                assert not failed.is_set()
                await asyncio.sleep(3.1)
                assert not finished.is_set()
            elif provider_error in {"soniox", "inworld"}:
                await asyncio.wait_for(failed.wait(), 5)
                assert not output_received.is_set()
                errors = [
                    json.loads(record.getMessage().removeprefix("voice_error "))
                    for record in caplog.records
                    if record.getMessage().startswith("voice_error ")
                ]
                assert errors and errors[0]["call_id"] == "call_streaming"
                assert "Test rejection" not in json.dumps(errors)
                assert "private-credential" not in json.dumps(errors)
            else:
                await asyncio.wait_for(assistant_recorded.wait(), 10)
                await asyncio.wait_for(session._idle.wait(), 10)
                assert output_received.is_set() and model_done.is_set()
                assert not failed.is_set()
                assert [row[0] for row in transcripts] == ["callee", "assistant"]
                assert transcripts[0][1] == utterance
                assert transcripts[1][1].strip() == expected_reply
                assert not transcripts[1][3]
                assert " ".join(spoken_texts) == expected_reply
                if browser:
                    frames = [
                        frames_pb2.Frame.FromString(item["bytes"])
                        for item in outgoing
                        if item.get("bytes")
                    ]
                    pcm = b"".join(frame.audio.audio for frame in frames if frame.HasField("audio"))
                    assert any(pcm)
                    assert abs(len(pcm) - len(output_audio) * len(spoken_texts) * 3) <= 960
                else:
                    assert output_audio * len(spoken_texts) == b"".join(
                        item.get("bytes", b"") for item in outgoing
                    )
                assert len(contexts) == (
                    2 if provider_error in {"finish", "finish_interruption", "sales_opt_out"} else 1
                )
                if provider_error in {"finish", "finish_after_text", "sales_opt_out"}:
                    await asyncio.wait_for(finished.wait(), 5)
                if provider_error == "sales_opt_out":
                    suppressed.assert_awaited_once()
            if tts_configs:
                assert tts_configs[0]["modelId"] == "inworld-tts-2"
                assert tts_configs[0]["voiceId"] == "Wade"
                assert tts_configs[0]["deliveryMode"] == "CREATIVE"
                assert tts_configs[0]["audioConfig"] == {
                    "audioEncoding": "PCM",
                    "sampleRateHertz": 8000,
                }
                assert "language" not in tts_configs[0]
                assert tts_configs[0]["autoMode"]
        finally:
            finalize_allowed.set()
            interrupt_allowed.set()
            await incoming.put({"type": "websocket.disconnect", "code": 1000})
            await asyncio.wait_for(task, 10)
        assert not any(
            "FlowController.flow_control" in str(pending.get_coro())
            for pending in asyncio.all_tasks()
            if not pending.done()
        )


@pytest.mark.parametrize("missing", ["soniox", "inworld"])
async def test_missing_provider_key_refuses_service_start(settings, database, tmp_path, missing):
    settings.post_call_extraction_enabled = False
    soniox, inworld = tmp_path / "soniox", tmp_path / "inworld"
    (inworld if missing == "soniox" else soniox).write_text("test-private-credential-for-loopback")
    config = AndroidConfig(
        selected_sim_slot=1,
        caller_number="+12025550199",
        allowed_destinations=["+12025550123"],
        stt_transport="soniox",
        soniox_key_file=soniox,
        tts_transport="inworld",
        inworld_key_file=inworld,
    )
    with pytest.raises(ValueError, match=missing.title() + " credential is unavailable"):
        AndroidCallService(
            settings, database, config=config, asterisk=None, phone=None, api_key="unused"
        )


@pytest.mark.parametrize("provider", ["soniox", "inworld"])
def test_invalid_provider_key_is_not_in_the_error(tmp_path, provider):
    key = tmp_path / "key"
    secret = "invalid private credential with whitespace"
    key.write_text(secret)
    config = AndroidConfig(
        selected_sim_slot=1,
        caller_number="+12025550199",
        allowed_destinations=["+12025550123"],
        **{provider + "_key_file": key},
    )
    with pytest.raises(ValueError) as error:
        getattr(config, provider + "_key")()
    assert secret not in str(error.value)
    assert "credential is invalid" in str(error.value)


@pytest.mark.parametrize("language", ["en", "es", "ru", "uk"])
@pytest.mark.parametrize("provider_error", [None, "interruption"])
async def test_browser_protobuf_audio_and_interruptions(
    packet, tmp_path, monkeypatch, caplog, language, provider_error
):
    await test_streaming_audio_transcripts_and_provider_failure(
        packet, tmp_path, monkeypatch, caplog, language, "semantic", provider_error, browser=True
    )
