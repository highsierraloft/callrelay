"""Read the USB recovery controller's status without granting the voice worker ADB access."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

GUARDED_GATEWAY_PACKAGE = "io.github.sparkcyf.voicesipagent.callrelay"


class PhoneStatus:
    def __init__(self, path: Path, *, selected_slot: int) -> None:
        if selected_slot not in {1, 2}:
            raise ValueError("Selected SIM slot must be 1 or 2")
        self.path = path
        self.selected_slot = selected_slot

    def read(self) -> dict[str, Any] | None:
        try:
            if self.path.stat().st_size > 16384:
                return None
            state = json.loads(self.path.read_text())
            if not isinstance(state, dict):
                return None
            age = time.time() - float(state["heartbeat_epoch"])
            if not 0 <= age < 45:
                return None
            return state
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def policy_matches(self, state: dict[str, Any]) -> bool:
        return (
            state.get("gateway_package") == GUARDED_GATEWAY_PACKAGE
            and state.get("selected_sim_slot") == self.selected_slot
            and (
                state.get("incoming_calls_disabled") is True
                or (
                    state.get("incoming_selected_only") is True
                    and state.get("inbound_contract") == 2
                )
            )
        )

    def inbound_problem(self) -> str | None:
        state = self.read()
        if state is None:
            return "Phone supervision is unavailable or stale"
        if (
            not self.policy_matches(state)
            or state.get("inbound_contract") != 2
            or state.get("incoming_selected_only") is not True
        ):
            return "The selected-SIM inbound gateway contract is not confirmed"
        if (
            state.get("control_ready") is not True
            or state.get("call_ready") is not True
            or state.get("gateway_takeover") is not True
            or state.get("echo_mode") is not False
        ):
            return "The phone gateway is unavailable for inbound reception"
        return None

    def admission_problem(self) -> str | None:
        state = self.read()
        if state is None:
            return "Phone supervision is unavailable or stale"
        if state.get("phase") == "call_in_progress_no_recovery":
            return "The phone already has a call"
        if not self.policy_matches(state):
            return "The protected SIM policy is not confirmed"
        if state.get("control_ready") is not True or state.get("call_ready") is not True:
            return "The phone gateway is not ready for an outbound call"
        if state.get("gateway_takeover") is not True or state.get("echo_mode") is not False:
            return "The phone gateway is not in outbound SIP mode"
        return None

    def carrier_idle_after(self, requested_at: float) -> bool:
        state = self.read()
        return bool(
            state
            and self.policy_matches(state)
            and state.get("control_ready") is True
            and state.get("phase") in {"standby", "gateway_ready", "gateway_unready"}
            and float(state["heartbeat_epoch"]) > requested_at
        )
