from __future__ import annotations

import json
import stat

import pytest

from app.android_config import AndroidConfig
from app.control_auth import ControlAccess
from app.settings import Settings
from scripts.check_public_release import inspect
from scripts.configure_phone import PACKAGE, render, write_installation


@pytest.fixture
def answers():
    return {
        "owner_name": "Alex",
        "owner_phone": "+12025550123",
        "source_phone": "+12025550199",
        "server_address": "192.168.50.10",
        "phone_address": "192.168.50.20",
        "device_model": "ExamplePhone",
        "device_serial": "SYNTHETIC-PHONE",
        "usb_vendor": "04e8",
        "sim_country": "us",
        "sim_slot": 2,
        "owner_login": "owner@example.test",
        "dashboard_origin": "https://phone.example.ts.net:8444",
        "runtime_uid": 1200,
        "runtime_gid": 1200,
        "usb_group_id": 46,
        "timezone": "UTC",
    }


@pytest.fixture
def keys():
    return {
        "openrouter": "sk-or-v1-" + "synthetic" * 7,
        "soniox": "synthetic-soniox-credential",
        "inworld": "synthetic-inworld-credential",
    }


def test_generated_installation_has_separate_credentials_and_no_masked_secrets(
    answers, keys, tmp_path
):
    files = render(answers, keys)
    settings = json.loads(files["agent-call.json"])
    model = Settings.from_values(**settings["settings"])
    model.require_runtime_configuration()
    config = AndroidConfig.model_validate(settings["android"])
    assert (
        config.selected_sim_slot == 2
        and config.stt_transport == "soniox"
        and config.tts_transport == "inworld"
    )
    assert json.loads(files["device.json"])["gateway_package"] == PACKAGE
    access = ControlAccess.model_validate_json(files["control-access.json"])
    assert access.hermes_url is None
    assert access.trusted_proxy_ips == ["172.29.90.1"]
    pbx = json.loads(files["asterisk.json"])
    credentials = (
        [pbx[k] for k in ["sip_password", "ari_password", "media_password"]]
        + [
            settings["settings"][k]
            for k in ["mcp_bearer_token", "debug_api_token", "deploy_guard_token"]
        ]
        + [access.bridge_token.get_secret_value()]
    )
    assert len(set(credentials)) == 7 and all(len(v) >= 32 and "***" not in v for v in credentials)
    second = render(answers, keys)
    assert second["asterisk.json"] != files["asterisk.json"]
    target = tmp_path / "private"
    write_installation(target, files)
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert all(stat.S_IMODE((target / name).stat().st_mode) == 0o600 for name in files)
    original = (target / "asterisk.json").read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        write_installation(target, second)
    assert (target / "asterisk.json").read_bytes() == original


@pytest.mark.parametrize(
    "name,value",
    [
        ("server_address", "0.0.0.0"),
        ("server_address", "8.8.8.8"),
        ("container_subnet", "192.168.50.0/24"),
        ("device_serial", 'phone"; bad-command'),
        ("sim_slot", 3),
        ("runtime_uid", 0),
        ("dashboard_origin", "http://phone.example.ts.net"),
        ("dashboard_origin", "https://phone.example.ts.net/admin"),
        ("owner_login", "owner\nother-route"),
    ],
)
def test_invalid_setup_fails_before_writing_anything(answers, keys, name, value):
    answers[name] = value
    with pytest.raises(ValueError):
        render(answers, keys)


def test_setup_refuses_a_symlink_destination(tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    destination = tmp_path / "private"
    destination.symlink_to(existing, target_is_directory=True)
    with pytest.raises(ValueError):
        write_installation(destination, {"key": "a new value"})
    assert not list(existing.iterdir())


def test_public_scan_reports_categories_without_printing_values(tmp_path):
    personal = "/Users/" + "example-person" + "/private/key"
    (tmp_path / "bad.py").write_text(personal)
    (tmp_path / "private").mkdir()
    (tmp_path / "private/settings.json").write_text("private-data")
    findings = inspect(tmp_path, ["bad.py", "private/settings.json"])
    assert len(findings) == 2 and personal not in str(findings)
    assert any("personal Mac path" in line for line in findings)


def test_public_docker_context_includes_synthetic_audio_but_excludes_operator_material():
    from tests.test_container_security import _dockerignore_excludes

    assert not _dockerignore_excludes("deploy/asterisk/sounds/reception/reception-failure-en.wav")
    for name in [
        "private/agent-call.json",
        ".release/source-baseline.json",
        "app/private/key.json",
        "dashboard/control-access.json",
        "app/adbkey",
        "app/provider-api-key",
    ]:
        assert _dockerignore_excludes(name), name
