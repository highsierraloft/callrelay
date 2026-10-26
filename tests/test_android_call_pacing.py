from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pipecat")

from pipecat.frames.frames import LLMRunFrame, TTSSpeakFrame
from pipecat.processors.aggregators.llm_context import LLMContext

from app.android_voice import AndroidVoiceSession, ClosingWindow
from app.spoken_identity import SpeakerIdentityFilter


@pytest.mark.parametrize("already_closing", [False, True])
async def test_timed_wrapup_waits_for_quiet_and_does_not_duplicate_closing(already_closing):
    session = AndroidVoiceSession.__new__(AndroidVoiceSession)
    session._max_call_seconds = 180
    session._started_at = time.monotonic() - 170
    session._idle = asyncio.Event()
    session._assistant_busy = False
    session._system_prompt = "Follow the approved brief."
    session._callback_timezone = "Europe/London"
    session._context = LLMContext([{"role": "system", "content": session._system_prompt}])
    session._worker = SimpleNamespace(queue_frame=AsyncMock())
    session.closing = ClosingWindow()
    if already_closing:
        session.closing.request("Talk soon. Bye.")
    task = asyncio.create_task(session._request_timed_wrapup())
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        session._worker.queue_frame.assert_not_awaited()
        assert not task.done()
        session._idle.set()
        await asyncio.wait_for(task, 1)
        if already_closing:
            session._worker.queue_frame.assert_not_awaited()
            assert session._context.messages == [
                {"role": "system", "content": session._system_prompt}
            ]
        else:
            session._worker.queue_frame.assert_awaited_once()
            assert isinstance(session._worker.queue_frame.call_args.args[0], LLMRunFrame)
            assert session._assistant_busy and not session._idle.is_set()
            assert session._context.messages[-1]["role"] == "system"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_clock_updates_keep_recipient_last_without_growing_history():
    session = AndroidVoiceSession.__new__(AndroidVoiceSession)
    session.closing = ClosingWindow()
    session._max_call_seconds = 180
    session._started_at = time.monotonic()
    session._system_prompt = "Follow the approved brief."
    session._callback_timezone = "Europe/London"
    goodbye = {"role": "user", "content": "Thanks for the chat and goodbye."}
    session._context = LLMContext([{"role": "system", "content": session._system_prompt}, goodbye])
    session._add_time_notice()
    session._add_time_notice(closing=True)
    assert len(session._context.messages) == 2
    assert session._context.messages[0]["role"] == "system"
    assert session._context.messages[-1] == goodbye
    assert str(session._context.messages[0]["content"]).count("Runtime notice:") == 1


async def test_finish_tool_speaks_once_and_cannot_reuse_old_playback():
    session = AndroidVoiceSession.__new__(AndroidVoiceSession)
    session.closing = ClosingWindow()
    session.closing.played("Goodbye.", interrupted=False)
    session._worker = SimpleNamespace(queue_frame=AsyncMock())
    result = AsyncMock()
    params = SimpleNamespace(
        arguments={"farewell": "Goodbye."}, result_callback=result, llm=SimpleNamespace()
    )
    await session._finish_call(params)
    await session._finish_call(params)
    session._worker.queue_frame.assert_awaited_once()
    speech = session._worker.queue_frame.call_args.args[0]
    assert isinstance(speech, TTSSpeakFrame) and speech.text == "Goodbye."
    assert speech.append_to_context
    assert session.closing.guard() is None
    assert result.call_args.kwargs["properties"].run_llm is False
    assert result.call_args.args[0]["call_ended"] is False
    session.closing.user_started()
    assert session.closing.farewell is None


@pytest.mark.parametrize("farewell", [None, "", " " * 3, "x" * 501])
async def test_invalid_farewell_never_speaks_or_closes(farewell):
    session = AndroidVoiceSession.__new__(AndroidVoiceSession)
    session.closing = ClosingWindow()
    session._worker = SimpleNamespace(queue_frame=AsyncMock())
    result = AsyncMock()
    await session._finish_call(
        SimpleNamespace(
            arguments={"farewell": farewell}, result_callback=result, llm=SimpleNamespace()
        )
    )
    session._worker.queue_frame.assert_not_awaited()
    assert session.closing.farewell is None
    assert not result.call_args.args[0]["closing_requested"]


async def test_already_played_farewell_reuses_only_the_current_model_response():
    session = AndroidVoiceSession.__new__(AndroidVoiceSession)
    session.closing = ClosingWindow()
    session.closing.played("Thanks. Goodbye.", interrupted=False)
    session._played_response_serial = 3
    session._worker = SimpleNamespace(queue_frame=AsyncMock())
    session._schedule_closing = lambda: None
    llm = SimpleNamespace(generated_response_text="Thanks. Goodbye.", response_serial=3)
    await session._finish_call(
        SimpleNamespace(arguments={"farewell": "Goodbye."}, result_callback=AsyncMock(), llm=llm)
    )
    session._worker.queue_frame.assert_not_awaited()
    assert session.closing.guard()()
    session.closing.user_started()
    assert session.closing.guard() is None


async def test_corrected_identity_does_not_block_farewell_playback_confirmation():
    session = AndroidVoiceSession.__new__(AndroidVoiceSession)
    session.closing = ClosingWindow()
    session._identity_filter = SpeakerIdentityFilter("Alex")
    session._worker = SimpleNamespace(queue_frame=AsyncMock())
    await session._finish_call(
        SimpleNamespace(
            arguments={"farewell": "This is Alex. Goodbye."},
            result_callback=AsyncMock(),
            llm=SimpleNamespace(),
        )
    )
    speech = session._worker.queue_frame.call_args.args[0]
    assert speech.text == "This is Alex's AI assistant. Goodbye."
    session.closing.played(speech.text, interrupted=False)
    assert session.closing.guard()()
