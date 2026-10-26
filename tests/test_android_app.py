from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.android_config import AndroidConfig
from app.main import create_app


def test_private_backend_has_no_twilio_or_openai_routes(settings):
    settings.call_backend = "android_openrouter"
    settings.post_call_extraction_enabled = False
    config = AndroidConfig(
        selected_sim_slot=1, caller_number="+14155550199", allowed_destinations=["+14155550123"]
    )
    app = create_app(settings, android_config=config)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/phone-media" in paths
    assert "/mcp" in paths
    assert not any("twilio" in path or "openai" in path or path == "/calls" for path in paths)
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/phone-media"):
            pass


def test_private_backend_rejects_missing_configuration(settings):
    settings.call_backend = "android_openrouter"
    with pytest.raises(ValueError, match="private Android configuration"):
        create_app(settings)
