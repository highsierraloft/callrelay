from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_evaluation_is_loopback_only_and_has_no_provider_credentials():
    compose = (ROOT / "compose.yaml").read_text()
    assert "127.0.0.1:8000:8000" in compose
    assert "AGENT_CALL_PROFILE: evaluation" in compose
    assert "OPENAI_API_KEY" not in compose and "TWILIO_AUTH_TOKEN" not in compose
    assert "privileged: true" not in compose


def test_community_phone_stack_needs_an_explicit_lan_and_isolates_usb():
    compose = (ROOT / "deploy/compose.phone.yaml").read_text()
    assert "CALLRELAY_LAN_ADDRESS:?" in compose
    assert "127.0.0.1:9120:8080" in compose
    assert "8000:8000" not in compose
    assert "network_mode: none" in compose
    assert "privileged: true" not in compose
    assert "../private/" in compose
    assert "external: true" not in compose


def test_no_maintainer_deployment_job_or_nested_git_history():
    assert not (ROOT / ".github/workflows/fly-deploy.yml").exists()
    assert not (ROOT / "deploy/maintainer").exists()
    assert not (ROOT / "phone/.git").exists()
    assert "--uid 10001" in (ROOT / "Dockerfile").read_text()


def test_current_guides_are_present_and_credit_both_upstreams():
    for name in [
        "README.md",
        "UPSTREAM.md",
        "LICENSES.md",
        "docs/self-hosting.md",
        "docs/integrations.md",
        "docs/architecture.md",
    ]:
        assert (ROOT / name).is_file()
    credits = (ROOT / "UPSTREAM.md").read_text()
    assert "XiyaoWang0519/agent-call" in credits
    assert "sparkcyf/android-voice-sip-agent" in credits
    assert "GPL" in (ROOT / "phone/README.md").read_text()


def test_mcp_proxy_keeps_owner_checks_and_does_not_inject_admin_auth():
    caddy = (ROOT / "deploy/control/Caddyfile").read_text()
    assert caddy.index("@notOwner") < caddy.index("@mcp")
    assert caddy.index("@untrusted") < caddy.index("@mcp")
    assert "header_up -X-Control-Bridge" in caddy
    assert "header_up X-Control-Bridge" in caddy
