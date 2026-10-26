"""Create a new operator installation. Never contacts a phone or a server."""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.android_config import AndroidConfig
from app.control_auth import ControlAccess
from app.settings import Settings

PACKAGE = "io.github.sparkcyf.voicesipagent.callrelay"


def private_address(value: str) -> str:
    address = ipaddress.IPv4Address(value)
    if (
        not address.is_private
        or address.is_loopback
        or address.is_unspecified
        or address.is_multicast
    ):
        raise ValueError("Use a private LAN IPv4 address")
    return str(address)


def render(answers: dict[str, Any], keys: dict[str, str]) -> dict[str, str]:
    """Return validated files without writing, dialing, or changing the host."""
    lan = private_address(answers["server_address"])
    phone = private_address(answers["phone_address"])
    if lan == phone:
        raise ValueError("The server and phone need different LAN addresses")
    network = ipaddress.IPv4Network(answers.get("container_subnet", "172.29.90.0/24"))
    if not network.is_private or network.prefixlen != 24 or network.network_address.is_loopback:
        raise ValueError("Use a private /24 container subnet")
    if ipaddress.ip_address(lan) in network or ipaddress.ip_address(phone) in network:
        raise ValueError("The container subnet must not overlap the phone LAN")
    for name in ("source_phone", "owner_phone"):
        if not re.fullmatch(r"\+[1-9][0-9]{7,14}", answers[name]):
            raise ValueError("Phone numbers must include the country code")
    if answers["source_phone"] == answers["owner_phone"]:
        raise ValueError("Use a separate owner callback number")
    serial, model = answers["device_serial"], answers["device_model"]
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", serial):
        raise ValueError("Invalid USB serial")
    if not re.fullmatch(r"[A-Za-z0-9 _.-]{1,80}", model):
        raise ValueError("Invalid device model")
    vendor, country = answers["usb_vendor"], answers["sim_country"]
    if not re.fullmatch(r"[0-9a-fA-F]{4}", vendor) or not re.fullmatch(r"[a-z]{2}", country):
        raise ValueError("Supply a four-digit USB vendor and two-letter SIM country")
    uid, gid = (
        int(answers.get("runtime_uid", os.getuid())),
        int(answers.get("runtime_gid", os.getgid())),
    )
    usb_gid = int(answers["usb_group_id"])
    if not (0 < uid < 65535 and 0 < gid < 65535 and 0 < usb_gid < 65535):
        raise ValueError("Use non-root runtime and USB group IDs")
    usb_group = answers.get("usb_group", "plugdev")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", usb_group):
        raise ValueError("Invalid USB group name")
    timezone = answers.get("timezone", "UTC")
    ZoneInfo(timezone)
    for name in ("openrouter", "soniox", "inworld"):
        key = keys[name].strip()
        if len(key) < 20 or any(c.isspace() for c in key):
            raise ValueError(f"Invalid {name} key")
    if not keys["openrouter"].strip().startswith("sk-or-v1-"):
        raise ValueError("Use an OpenRouter API key")

    def token() -> str:
        return secrets.token_urlsafe(36)

    ari, media, sip = token(), token(), token()
    origin = answers["dashboard_origin"].rstrip("/")
    access = ControlAccess(
        bridge_token=token(),
        owner_login=answers["owner_login"],
        public_origin=origin,
        trusted_proxy_ips=[str(network.network_address + 1)],
        timezone=timezone,
        hermes_url=answers.get("hermes_url") or None,
    )
    settings = Settings.from_values(
        call_backend="android_openrouter",
        post_call_extraction_enabled=False,
        database_url="sqlite:////data/calls.db",
        owner_display_name=answers["owner_name"],
        owner_phone_e164=answers["owner_phone"],
        allowed_agent_user_id="owner-agent",
        mcp_bearer_token=token(),
        debug_api_token=token(),
        deploy_guard_token=token(),
        allow_all_country_codes=True,
        public_base_url=origin,
    )
    settings.require_runtime_configuration()
    android = AndroidConfig(
        selected_sim_slot=int(answers["sim_slot"]),
        caller_number=answers["source_phone"],
        line_label=answers.get("line_label") or "Selected SIM",
        allow_any_destination=True,
        recording_enabled=True,
        dashboard_config_file=Path("/run/secrets/control-access.json"),
        stt_transport="soniox",
        tts_transport="inworld",
        inworld_model="inworld-tts-2",
        inworld_voice="Wade",
        conversation_languages=["en", "es", "ru", "uk"],
    )

    def secret_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2) + "\n"

    settings_data = settings.model_dump(mode="json")
    for name in ["mcp_bearer_token", "debug_api_token", "deploy_guard_token"]:
        settings_data[name] = getattr(settings, name).get_secret_value()
    access_data = access.model_dump(mode="json")
    access_data["bridge_token"] = access.bridge_token.get_secret_value()
    files = {
        "agent-call.json": secret_json(
            {"settings": settings_data, "android": android.model_dump(mode="json")}
        ),
        "control-access.json": secret_json(access_data),
        "asterisk.json": secret_json(
            {
                "external_address": lan,
                "phone_address": phone,
                "sip_password": sip,
                "ari_password": ari,
                "media_password": media,
            }
        ),
        "worker-asterisk.json": secret_json({"ari_password": ari, "media_password": media}),
        "device.json": secret_json(
            {
                "serial": serial,
                "model": model,
                "gateway_package": PACKAGE,
                "selected_sim_slot": android.selected_sim_slot,
            }
        ),
        "mcp-client.json": secret_json(
            {
                "mcpServers": {
                    "callrelay": {
                        "url": origin + "/mcp/",
                        "headers": {
                            "Authorization": "Bearer "
                            + settings.mcp_bearer_token.get_secret_value(),
                            "X-Agent-User-Id": "owner-agent",
                        },
                    }
                }
            }
        ),
        "codex-phone-client.json": secret_json(
            {"base_url": origin, "data_dir": "~/.local/share/callrelay"}
        ),
        "compose.env": f"CALLRELAY_LAN_ADDRESS={lan}\nCALLRELAY_SUBNET={network}\nCALLRELAY_UID={uid}\nCALLRELAY_GID={gid}\nCALLRELAY_USB_GID={usb_gid}\n",
        "99-callrelay-phone.rules": f'SUBSYSTEM=="usb", ENV{{DEVTYPE}}=="usb_device", ATTR{{idVendor}}=="{vendor.lower()}", ATTR{{serial}}=="{serial}", GROUP="{usb_group}", MODE="0660"\n',
    }
    for name, value in keys.items():
        files[name + "-api-key"] = value.strip() + "\n"
    return files


def write_installation(destination: Path, files: dict[str, str]) -> None:
    if destination.exists() or destination.is_symlink():
        raise ValueError("The destination already exists. Existing credentials are never replaced.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".callrelay-setup-", dir=destination.parent))
    try:
        stage.chmod(0o700)
        for name, value in files.items():
            if Path(name).name != name:
                raise ValueError("Invalid settings file name")
            path = stage / name
            path.write_text(value)
            path.chmod(0o600)
        for name in ["data", "recordings", "status", "adb-state"]:
            (stage / name).mkdir(mode=0o700)
        stage.rename(destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--answers", type=Path, help="Read operator values from a private JSON file"
    )
    parser.add_argument(
        "--key-directory", type=Path, help="Read provider keys from three private *-api-key files"
    )
    parser.add_argument("--output", type=Path, default=Path("private"))
    args = parser.parse_args()
    answers = json.loads(args.answers.read_text()) if args.answers else {}
    prompts = {
        "owner_name": "Your display name",
        "owner_phone": "Your callback number (+country code)",
        "source_phone": "The gateway SIM number (+country code)",
        "line_label": "A label for that SIM",
        "sim_slot": "SIM slot (1 or 2)",
        "sim_country": "SIM country code (two lowercase letters)",
        "device_model": "Exact Android model (adb shell getprop ro.product.model)",
        "device_serial": "Exact USB serial (adb get-serialno)",
        "usb_vendor": "USB vendor ID (four hex digits)",
        "usb_group_id": "Linux USB group ID (getent group plugdev)",
        "server_address": "Server LAN IPv4 address",
        "phone_address": "Phone Wi-Fi IPv4 address",
        "dashboard_origin": "Dashboard origin (https://your-node.your-tailnet.ts.net:8444)",
        "owner_login": "Your Tailscale account login",
        "timezone": "Timezone (for example UTC)",
    }
    if args.answers:
        missing = set(prompts) - answers.keys()
        if missing:
            parser.error("Missing fields: " + ", ".join(sorted(missing)))
    else:
        for name, prompt in prompts.items():
            answers[name] = input(prompt + ": ").strip()
    keys = {
        name: (args.key_directory / (name + "-api-key")).read_text().strip()
        if args.key_directory
        else getpass.getpass(name.title() + " API key: ").strip()
        for name in ["openrouter", "soniox", "inworld"]
    }
    try:
        write_installation(args.output.absolute(), render(answers, keys))
    except (ValueError, OSError, KeyError) as exc:
        # Pydantic inputs can include owner values. Keep error output value-free.
        print(
            f"Setup did not complete ({type(exc).__name__}). Check the input fields and destination.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    print("Created private settings. No service, firewall rule, or phone setting changed.")
    print("Continue with docs/self-hosting.md. Do not commit the private directory.")


if __name__ == "__main__":
    main()
