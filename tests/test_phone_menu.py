from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.android_voice import AndroidVoiceSession, ClosingWindow
from app.asterisk_client import AsteriskError
from app.call_ai_inputs import call_state_notice, model_tools
from app.phone_menu import (
    MAX_KEYPAD_ACTIONS,
    PhoneMenu,
    is_hold_prompt,
    is_phone_menu_prompt,
    validate_digits,
)
from tests.test_asterisk_client import client


@pytest.mark.parametrize("digits", ["2", "1234#", "*0", "0123456789012345"])
async def test_keypad_reaches_only_the_owned_cellular_channel(digits):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"state": "Up"})

    ast = client(handle)
    try:
        await ast.send_dtmf("call_test", digits)
        assert len(requests) == 2
        assert requests[1].url.path == "/ari/channels/call_test-cell/dtmf"
        assert requests[1].method == "POST"
        assert json.loads(requests[1].content) == {
            "dtmf": digits,
            "before": 0,
            "between": 100,
            "duration": 160,
            "after": 0,
        }
        assert digits not in requests[1].url.query.decode()
    finally:
        await ast.close()


async def test_incoming_keypad_uses_adopted_channel_and_never_originates():
    paths = []

    def handle(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"state": "Up"})

    ast = client(handle)
    try:
        ast.adopt_incoming("call_inbound", "1760000.42")
        await ast.send_dtmf("call_inbound", "#")
        assert paths == ["/ari/channels/1760000.42", "/ari/channels/1760000.42/dtmf"]
    finally:
        await ast.close()


@pytest.mark.parametrize(
    "digits", ["", "1" * 17, "1w2", "D", "A", "2 3", "+1234", "1;hangup", None, 2]
)
async def test_invalid_keypad_cannot_reach_transport(digits):
    send = AsyncMock()
    with pytest.raises(ValueError):
        await PhoneMenu(send).press("request", digits, 1)
    send.assert_not_called()
    with pytest.raises(ValueError):
        validate_digits(digits)


@pytest.mark.parametrize("state,guard", [("Ringing", True), ("Up", False)])
async def test_hangup_or_unanswered_channel_blocks_keypad(state, guard):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"state": state})

    ast = client(handle)
    try:
        with pytest.raises(ValueError):
            await ast.send_dtmf("call_test", "2", guard=lambda: guard)
        assert [r.method for r in requests] == ["GET"]
    finally:
        await ast.close()


async def test_duplicate_and_parallel_menu_commands_send_once_per_recipient_turn():
    send = AsyncMock()
    menu = PhoneMenu(send)
    first, duplicate, extra = await asyncio.gather(
        menu.press("first", "2", 1), menu.press("first", "2", 1), menu.press("extra", "3", 1)
    )
    assert first["status"] == "accepted" and duplicate["duplicate"]
    assert extra["status"] == "not_sent"
    send.assert_awaited_once_with("2")
    assert (await menu.press("submenu", "4#", 2))["status"] == "accepted"
    assert send.await_count == 2
    with pytest.raises(ValueError):
        await menu.press("first", "9", 3)


async def test_timeout_retains_unknown_outcome_without_resending_or_exposing_details():
    send = AsyncMock(side_effect=AsteriskError("private server details"))
    menu = PhoneMenu(send)
    first = await menu.press("first", "2", 1)
    assert first["status"] == "unknown"
    assert "private" not in json.dumps(first)
    assert (await menu.press("first", "2", 2))["duplicate"]
    assert (await menu.press("another", "2", 1))["status"] == "not_sent"
    send.assert_awaited_once()


async def test_cancellation_after_dispatch_cannot_authorize_a_resend():
    entered = asyncio.Event()

    async def send(_digits):
        entered.set()
        await asyncio.Event().wait()

    menu = PhoneMenu(send)
    task = asyncio.create_task(menu.press("first", "2", 1))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    result = await menu.press("first", "2", 2)
    assert result["duplicate"] and result["status"] == "unknown"


async def test_navigation_limit_and_browser_simulation_have_no_telephone_effects():
    menu = PhoneMenu(None)
    for revision in range(MAX_KEYPAD_ACTIONS):
        result = await menu.press(str(revision), "2", revision)
        assert result == {"status": "simulated", "digits": "2", "sent_to_phone": False}
    assert (await menu.press("exhausted", "2", 99))["status"] == "not_sent"


async def test_menu_tools_remain_silent_and_closing_blocks_digits():
    session = object.__new__(AndroidVoiceSession)
    session.closing = ClosingWindow()
    session._recipient_message_revision = 1
    send = AsyncMock()
    session._phone_menu = PhoneMenu(send)
    params = SimpleNamespace(
        tool_call_id="dtmf", arguments={"digits": "2"}, result_callback=AsyncMock()
    )
    await session._send_dtmf(params)
    assert params.result_callback.call_args.args[0]["status"] == "accepted"
    assert params.result_callback.call_args.kwargs["properties"].run_llm is False
    # Consecutive menu transcripts need not have an intervening spoken bot turn
    # or a new VAD start. The new transcript permits exactly one next choice.
    session._recipient_message_revision += 1
    params.tool_call_id = "submenu"
    params.arguments = {"digits": "123#"}
    await session._send_dtmf(params)
    assert send.await_count == 2
    await session._wait_for_phone_menu(params)
    assert params.result_callback.call_args.args[0]["listening"]
    assert params.result_callback.call_args.kwargs["properties"].run_llm is False
    session.closing.request("Goodbye")
    await session._send_dtmf(params)
    assert params.result_callback.call_args.args[0]["status"] == "not_sent"
    assert send.await_count == 2


def test_menu_tools_are_opt_in_for_new_voice_plans_only():
    from pipecat.adapters.schemas.function_schema import FunctionSchema

    def names(tools):
        return {tool["function"]["name"] for tool in tools}

    assert "send_dtmf" not in names(model_tools())
    assert "send_dtmf" not in names(model_tools("text", phone_menu_enabled=True))
    assert names(model_tools(phone_menu_enabled=True)) >= {
        "finish_call",
        "send_dtmf",
        "wait_for_phone_menu",
    }
    for tool in model_tools(phone_menu_enabled=True):
        function = tool["function"]
        actual = FunctionSchema(
            name=function["name"],
            description=function["description"],
            properties=function["parameters"]["properties"],
            required=function["parameters"]["required"],
        ).to_default_dict()
        assert actual == function  # The inspector must show the actual Pipecat schema.


def test_interrupted_goodbye_updates_runtime_state_without_claiming_hangup():
    closing = ClosingWindow()
    closing.request("Hasta luego.")
    closing.played("Gracias", interrupted=True)
    closing.user_started()
    assert closing.cancelled and closing.farewell is None
    notice = call_state_notice(cancelled=closing.cancelled)
    assert "cancelled" in notice and "still connected" in notice
    assert "did not end" in notice
    closing.request("Bye")
    assert not closing.cancelled


@pytest.mark.parametrize(
    "text",
    [
        "For services press 2.",
        "Para servicios marque dos.",
        "Нажмите два.",
        "Натисніть два.",
        "Please hold.",
        "Le paso con la operadora.",
    ],
)
def test_keypad_and_hold_cues_identify_menu_speech(text):
    assert is_phone_menu_prompt(text)


@pytest.mark.parametrize(
    "text",
    [
        "Please hold.",
        "Por favor, manténgase a la espera.",
        "Оставайтесь на линии.",
        "Залишайтеся на лінії.",
    ],
)
def test_explicit_hold_announcements_are_silent_in_each_supported_language(text):
    assert is_hold_prompt(text)


@pytest.mark.parametrize(
    "text",
    [
        "Please hold or press 2 for services.",
        "Manténgase a la espera o marque 2.",
        "Hello, Ana speaking.",
    ],
)
def test_menu_options_and_human_greetings_are_not_forced_into_hold(text):
    assert not is_hold_prompt(text)


async def test_menu_preambles_are_discarded_but_normal_speech_still_streams(monkeypatch):
    from pipecat.frames.frames import (
        FunctionCallsStartedFrame,
        InterruptionFrame,
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection
    from pipecat.services.openrouter.llm import OpenRouterLLMService

    from app.openrouter_client import OpenRouterVoiceLLMService

    forward = AsyncMock()
    monkeypatch.setattr(OpenRouterLLMService, "push_frame", forward)
    monkeypatch.setattr(OpenRouterLLMService, "process_frame", AsyncMock())
    llm = object.__new__(OpenRouterVoiceLLMService)
    llm._buffer_menu_speech = True
    llm._silent_hold_turn = False
    llm._menu_text = []
    llm._menu_tool_started = False
    await llm.push_frame(LLMFullResponseStartFrame())
    forward.reset_mock()
    await llm.push_frame(LLMTextFrame("The services option is two."))
    forward.assert_not_awaited()
    await llm.push_frame(FunctionCallsStartedFrame(function_calls=[]))
    await llm.push_frame(LLMFullResponseEndFrame())
    assert all(not isinstance(c.args[0], LLMTextFrame) for c in forward.call_args_list)
    assert llm.generated_response_text == ""
    # A human discussing a menu is not silenced if the model chooses a spoken answer.
    await llm.push_frame(LLMFullResponseStartFrame())
    await llm.push_frame(LLMTextFrame("Yes, I can help with that menu."))
    await llm.push_frame(LLMFullResponseEndFrame())
    assert "Yes, I can help" in llm.generated_response_text
    # Interruptions must never flush stale buffered text into the next turn.
    await llm.push_frame(LLMFullResponseStartFrame())
    await llm.push_frame(LLMTextFrame("Stale response"))
    await llm.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
    await llm.push_frame(LLMFullResponseEndFrame())
    assert "Stale" not in llm.generated_response_text
    # A provider may ignore the wait tool and emit an acknowledgement on hold.
    llm._silent_hold_turn = True
    await llm.push_frame(LLMFullResponseStartFrame())
    await llm.push_frame(LLMTextFrame("Okay, I'll hold."))
    await llm.push_frame(LLMFullResponseEndFrame())
    assert llm.generated_response_text == ""
    # The ordinary conversational path forwards the first chunk immediately.
    llm._buffer_menu_speech = False
    await llm.push_frame(LLMFullResponseStartFrame())
    forward.reset_mock()
    await llm.push_frame(LLMTextFrame("Hello Ana."))
    forward.assert_awaited_once()
    assert forward.call_args.args[0].text == "Hello Ana."
    assert not is_phone_menu_prompt("Hello, this is Ana from services. How can I help?")
