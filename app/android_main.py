"""Load the deployment's private files without placing secrets in shared environment variables."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI

from app.android_config import AndroidConfig
from app.main import create_app
from app.settings import Settings


def build_app() -> FastAPI:
    os.umask(0o077)
    values = json.loads(Path("/run/secrets/agent-call.json").read_text())
    settings = Settings.from_values(**values["settings"])
    if settings.call_backend != "android_openrouter" or settings.post_call_extraction_enabled:
        raise ValueError("Private deployment requires the Android transcript-only backend")
    return create_app(settings, android_config=AndroidConfig.model_validate(values["android"]))
