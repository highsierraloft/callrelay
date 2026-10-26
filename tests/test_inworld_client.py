from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pipecat")

from app.inworld_client import InworldVoiceTTSService


async def test_interrupt_before_timestamps_never_commits_unsaid_text():
    service = InworldVoiceTTSService(api_key="test-only", sample_rate=8000)
    service._context_texts["interrupted"] = "A long answer that was never heard."
    service.push_frame = AsyncMock()
    service._send_close_context = AsyncMock()
    await service.on_audio_context_interrupted("interrupted")
    service.push_frame.assert_not_awaited()
    assert "interrupted" not in service._context_texts


async def test_completed_untimed_audio_keeps_the_normal_adapter_fallback():
    service = InworldVoiceTTSService(api_key="test-only", sample_rate=8000)
    service._context_texts["complete"] = "A completed answer."
    service.push_frame = AsyncMock()
    await service._maybe_push_fallback_text("complete")
    service.push_frame.assert_awaited_once()
    assert service.push_frame.call_args.args[0].text == "A completed answer."
