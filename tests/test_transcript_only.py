from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP

from app.finalizer import Finalizer
from app.mcp_tools import register_tools
from app.models import CallState
from tests.conftest import seed_call


class ForbiddenExtractor:
    async def parse(self, **kwargs):
        raise AssertionError("Transcript-only mode must never call the extraction provider")


@pytest.mark.parametrize("state", [CallState.COMPLETED, CallState.FAILED, CallState.TIMED_OUT])
async def test_transcript_only_persists_without_provider_and_survives_reload(
    settings, service, packet, state
):
    settings.post_call_extraction_enabled = False
    call_id = await seed_call(service.db, packet, state=state)
    original = " Sí, mañana a las tres. "
    await service.db.add_transcript_turn(
        call_id=call_id,
        turn_id="turn_original",
        speaker="callee",
        text=original,
        source_event_type="test.transcript",
        source_event_id="event_original",
        start_ms=100,
        end_ms=900,
    )
    provider = SimpleNamespace(responses=ForbiddenExtractor())
    result = await Finalizer(settings, service.db, provider).finalize(call_id)
    assert result.finalization_status == "succeeded"
    assert result.result_source == "transcript_only"
    assert result.outcome == ("failed" if state == CallState.FAILED else "unknown")
    assert result.commitments == []
    assert result.confirmation_numbers == []
    saved = await service.db.get_transcript(call_id)
    assert saved[0].text == original
    assert saved[0].start_ms == 100
    assert saved[0].end_ms == 900
    assert await Finalizer(settings, service.db, provider).finalize(call_id) == result


async def test_transcript_only_does_not_validate_old_context_or_claim_missing_speech(
    settings, service, packet
):
    settings.post_call_extraction_enabled = False
    call_id = await seed_call(service.db, packet, state=CallState.COMPLETED)
    call = await service.db.get_call(call_id)
    await service.db.execute(
        "UPDATE plans SET context_json='{}' WHERE plan_id=?", (call["plan_id"],)
    )
    result = await Finalizer(
        settings, service.db, SimpleNamespace(responses=ForbiddenExtractor())
    ).finalize(call_id)
    assert result.result_source == "transcript_only"
    assert result.outcome == "unknown"
    assert result.transcript_complete is False
    assert all(turn.speaker == "system" for turn in await service.db.get_transcript(call_id))


async def test_mcp_returns_every_transcript_fragment_with_cursor(settings, service, packet):
    settings.post_call_extraction_enabled = False
    service.finalizer = Finalizer(
        settings, service.db, SimpleNamespace(responses=ForbiddenExtractor())
    )
    call_id = await seed_call(service.db, packet, state=CallState.COMPLETED)
    for index in range(103):
        await service.db.add_transcript_turn(
            call_id=call_id,
            turn_id=f"turn_{index}",
            speaker="callee" if index % 2 else "assistant",
            text=f" fragment {index} ",
            source_event_type="test.transcript",
            source_event_id=f"event_{index}",
        )
    mcp = FastMCP("transcript-test")
    register_tools(mcp, lambda: service)
    async with Client(mcp) as client:
        first = (await client.call_tool("get_call_result", {"call_id": call_id})).data
        assert first["result"]["result_source"] == "transcript_only"
        assert first["result"]["outcome"] == "unknown"
        assert first["next_transcript_cursor"] == 100
        second = (
            await client.call_tool(
                "get_call_result",
                {"call_id": call_id, "after_sequence": first["next_transcript_cursor"]},
            )
        ).data
    turns = first["transcript"] + second["transcript"]
    assert [turn["text"] for turn in turns] == [f" fragment {index} " for index in range(103)]
    assert [turn["sequence_number"] for turn in turns] == list(range(1, 104))
    assert second["next_transcript_cursor"] is None
    assert first["transcript_is_call_content"] is True


async def test_active_call_result_never_runs_finalization(settings, service, packet):
    settings.post_call_extraction_enabled = False
    service.finalizer = Finalizer(
        settings, service.db, SimpleNamespace(responses=ForbiddenExtractor())
    )
    call_id = await seed_call(service.db, packet, state=CallState.ACTIVE)
    result = await service.get_result(call_id)
    assert result["result"] is None
    assert result["transcript"] == []
    assert await service.db.get_result(call_id) is None


async def test_unknown_call_cannot_read_transcript_and_negative_cursor_is_rejected(service):
    with pytest.raises(LookupError):
        await service.get_result("call_missing")
    with pytest.raises(ValueError, match="non-negative"):
        await service.get_result("call_missing", after_sequence=-1)
