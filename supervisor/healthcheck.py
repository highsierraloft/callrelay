"""Container liveness only; status.json reports separate phone/call readiness."""

import json
import sys
import time
from pathlib import Path

try:
    state = json.loads(Path("/status/status.json").read_text())
    healthy = 0 <= time.time() - state["heartbeat_epoch"] < 180
except (OSError, ValueError, KeyError, TypeError):
    healthy = False
sys.exit(0 if healthy else 1)
