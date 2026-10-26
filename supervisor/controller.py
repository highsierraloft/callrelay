"""Recover existing Android tools. This service never dials or answers a call."""

import json
import os
import re
import shlex
import signal
import subprocess
import time
from pathlib import Path

PACKAGE = "io.github.sparkcyf.voicesipagent.callrelay"
APP_NAMESPACE = "io.github.sparkcyf.voicesipagent.app"
SHIZUKU = "moe.shizuku.privileged.api"
RECEIVER = PACKAGE + "/" + APP_NAMESPACE + ".AdbControlReceiver"
PERMISSION = "android.permission.READ_PHONE_NUMBERS"


class Adb:
    def __init__(self, serial):
        self.serial = serial

    def command(self, *args, timeout=15):
        result = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout.strip()

    def shell(self, *args, timeout=15):
        return self.command("-s", self.serial, "shell", shlex.join(args), timeout=timeout)


def call_states(raw):
    return [int(value) for value in re.findall(r"^\s*mCallState=(\d+)", raw, re.M)]


def inspect_and_recover(adb, expected_model, selected_slot=1):
    if selected_slot not in (1, 2):
        raise ValueError("Select SIM slot 1 or 2")
    code, state = adb.command("-s", adb.serial, "get-state")
    if code or state != "device":
        return {"phase": "waiting_for_device_or_authorization", "control_ready": False}
    code, model = adb.shell("getprop", "ro.product.model")
    if code or model != expected_model:
        return {"phase": "unexpected_device", "control_ready": False}
    code, booted = adb.shell("getprop", "sys.boot_completed")
    if code or booted != "1":
        return {"phase": "waiting_for_boot", "control_ready": False}
    code, user_state = adb.shell("am", "get-started-user-state", "0")
    if code or "RUNNING_UNLOCKED" not in user_state:
        return {"phase": "waiting_for_user_unlock", "control_ready": False}
    code, registry = adb.shell("dumpsys", "telephony.registry")
    states = call_states(registry) if code == 0 else []
    if not states:
        return {"phase": "call_state_unknown", "control_ready": False}
    if any(states):
        # Status is read-only. Never start, grant, restart, or reconfigure during a call.
        code, gateway = adb.shell("am", "broadcast", "-n", RECEIVER, "--es", "op", "status")
        report = gateway_report(code, gateway, selected_slot)
        report["phase"] = "call_in_progress_no_recovery"
        report["call_states"] = states
        return report
    for package in [PACKAGE, SHIZUKU]:
        code, apk = adb.shell("pm", "path", package)
        if code or not apk.startswith("package:/data/app/"):
            return {"phase": "required_app_missing", "control_ready": False}

    recovered = []
    code, details = adb.shell("dumpsys", "package", PACKAGE)
    if code:
        return {"phase": "permission_state_unknown", "control_ready": False}
    if PERMISSION + ": granted=true" not in details:
        code, _ = adb.shell("pm", "grant", PACKAGE, PERMISSION)
        if code:
            return {"phase": "required_permission_denied", "control_ready": False}
        recovered.append("phone_account_permission")

    code, pid = adb.shell("pidof", "shizuku_server")
    if code or not pid:
        code, apk = adb.shell("pm", "path", SHIZUKU)
        path = apk.removeprefix("package:").rsplit("/", 1)[0] + "/lib/arm64/libshizuku.so"
        if code or not path.startswith("/data/app/") or "\n" in path:
            return {"phase": "shizuku_launcher_unavailable", "control_ready": False}
        code, _ = adb.shell(path, timeout=30)
        if code:
            return {"phase": "shizuku_start_failed", "control_ready": False}
        started = time.monotonic() + 5
        while time.monotonic() < started:
            code, pid = adb.shell("pidof", "shizuku_server")
            if code == 0 and pid:
                break
            time.sleep(0.2)
        else:
            return {"phase": "shizuku_start_not_confirmed", "control_ready": False}
        recovered.append("shizuku")

    if recovered:
        code, registry = adb.shell("dumpsys", "telephony.registry")
        states = call_states(registry) if code == 0 else []
        if not states or any(states):
            return {"phase": "call_changed_during_recovery", "control_ready": False}
        adb.shell("am", "start", "-W", "-n", PACKAGE + "/" + APP_NAMESPACE + ".MainActivity")

    code, gateway = adb.shell("am", "broadcast", "-n", RECEIVER, "--es", "op", "status")
    report = gateway_report(code, gateway, selected_slot)
    if not report["control_ready"]:
        return report
    _, sim_state = adb.shell("getprop", "gsm.sim.state")
    sim_states = sim_state.split(",")
    selected_ready = len(sim_states) >= selected_slot and sim_states[selected_slot - 1] in {
        "LOADED",
        "READY",
    }
    report["call_ready"] = report.get("call_ready", False) and selected_ready
    report.update(
        sim_ready=selected_ready,
        recovered=recovered,
        call_states=states,
    )
    return report


def gateway_report(code, gateway, selected_slot=1):
    if code or "result=1" not in gateway:
        return {"phase": "gateway_status_unavailable", "control_ready": False}
    selected_inbound = "incoming=selected-only" in gateway and "inbound_contract=2" in gateway
    if (
        selected_slot not in (1, 2)
        or not all(value in gateway for value in [f"sim_slot={selected_slot} ", "sim_pinned=true"])
        or not ("incoming=off" in gateway or selected_inbound)
    ):
        return {"phase": "gateway_sim_policy_mismatch", "control_ready": False}
    takeover = "takeover=on" in gateway
    ready = "ready=true" in gateway and "audioReady=true" in gateway
    return {
        "phase": "gateway_ready" if ready else ("gateway_unready" if takeover else "standby"),
        "control_ready": True,
        "call_ready": ready,
        "gateway_takeover": takeover,
        "echo_mode": "loopback=on" in gateway,
        "shizuku_running": True,
        "gateway_package": PACKAGE,
        "selected_sim_slot": selected_slot,
        "incoming_calls_disabled": not selected_inbound,
        "incoming_selected_only": selected_inbound,
        "inbound_contract": 2 if selected_inbound else 0,
    }


def usb_nodes(root=Path("/dev/bus/usb")):
    return sorted(
        (str(path), path.stat().st_rdev)
        for path in root.glob("*/*")
        if path.is_char_device() and os.access(path, os.R_OK | os.W_OK)
    )


def main():
    os.umask(0o077)
    key = Path("/home/phone/.android/adbkey")
    if not key.is_file():
        raise RuntimeError(
            "Persistent trusted ADB identity is missing; refusing to create a replacement"
        )
    config = json.loads(Path("/run/phone/device.json").read_text())
    if not re.fullmatch(r"[A-Za-z0-9 _.-]{1,80}", config.get("model", "")) or not re.fullmatch(
        r"[A-Za-z0-9_.:-]+", config.get("serial", "")
    ):
        raise RuntimeError("Unexpected phone identity configuration")
    if config.get("selected_sim_slot") not in (1, 2):
        raise RuntimeError("Configure an explicit SIM slot")
    if config.get("gateway_package") != PACKAGE:
        raise RuntimeError("Configured gateway does not match the guarded application")
    adb = Adb(config["serial"])
    status_file = Path("/status/status.json")
    stop = False

    def request_stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    previous_nodes = None
    previous_phase = None
    try:
        while not stop:
            try:
                current_nodes = usb_nodes()
                if current_nodes != previous_nodes:
                    adb.command("kill-server")
                    adb.command("start-server")
                    previous_nodes = current_nodes
                report = inspect_and_recover(adb, config["model"], config["selected_sim_slot"])
            except (OSError, subprocess.TimeoutExpired, ValueError):
                report = {"phase": "probe_failed", "control_ready": False}
            report["heartbeat_epoch"] = time.time()
            temporary = status_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(report, sort_keys=True))
            temporary.replace(status_file)
            if report["phase"] != previous_phase or report.get("recovered"):
                print(json.dumps(report, sort_keys=True), flush=True)
                previous_phase = report["phase"]
            for _ in range(15):
                if stop:
                    break
                time.sleep(1)
    finally:
        adb.command("kill-server")


if __name__ == "__main__":
    main()
