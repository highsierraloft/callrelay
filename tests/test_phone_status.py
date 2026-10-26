from __future__ import annotations

import json
import time

import pytest

from app.phone_status import GUARDED_GATEWAY_PACKAGE, PhoneStatus


def ready_state():
    return {
        "heartbeat_epoch": time.time(),
        "gateway_package": GUARDED_GATEWAY_PACKAGE,
        "selected_sim_slot": 1,
        "incoming_calls_disabled": True,
        "control_ready": True,
        "call_ready": True,
        "gateway_takeover": True,
        "echo_mode": False,
        "phase": "gateway_ready",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"heartbeat_epoch": "past"},
        {"heartbeat_epoch": "future"},
        {"selected_sim_slot": 2},
        {"incoming_calls_disabled": False},
        {"gateway_package": "io.github.sparkcyf.voicesipagent"},
        {"echo_mode": True},
        {"gateway_takeover": False},
        {"call_ready": False},
        {"phase": "call_in_progress_no_recovery"},
    ],
)
def test_bad_or_busy_phone_never_admits_a_call(tmp_path, changes):
    path = tmp_path / "status.json"
    if changes.get("heartbeat_epoch") in {"past", "future"}:
        changes = changes | {
            "heartbeat_epoch": time.time() + (-60 if changes["heartbeat_epoch"] == "past" else 60)
        }
    path.write_text(json.dumps(ready_state() | changes))
    assert PhoneStatus(path, selected_slot=1).admission_problem() is not None


def test_carrier_teardown_requires_a_new_idle_observation(tmp_path):
    path = tmp_path / "status.json"
    state = ready_state()
    path.write_text(json.dumps(state))
    status = PhoneStatus(path, selected_slot=1)
    assert status.admission_problem() is None
    assert not status.carrier_idle_after(state["heartbeat_epoch"] + 1)
    assert status.carrier_idle_after(state["heartbeat_epoch"] - 1)
    path.write_text(json.dumps(state | {"phase": "call_in_progress_no_recovery"}))
    assert not status.carrier_idle_after(state["heartbeat_epoch"] - 1)


def test_missing_or_partial_status_is_not_readiness(tmp_path):
    path = tmp_path / "status.json"
    status = PhoneStatus(path, selected_slot=1)
    assert status.admission_problem()
    for text in ["{", "{}", "[]", '{"heartbeat_epoch":null}']:
        path.write_text(text)
        assert status.admission_problem()
