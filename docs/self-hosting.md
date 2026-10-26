# Install CallRelay

This guide creates a new installation on your own Linux server.
It does not migrate an existing deployment. Keep the source directory separate from operator data.

## Requirements

| Item | Requirement |
| --- | --- |
| Server | Linux, Docker Engine, Compose v2, and USB access |
| Phone | Spare Android 11+ phone with an active SIM and compatible audio control |
| Network | Server Ethernet and phone Wi-Fi on the same private LAN |
| Private access | Tailscale on the server and client device |
| Build tools | Python 3.12+, `uv`, Git, and Android platform tools |
| Provider accounts | OpenRouter, Soniox, and Inworld |
| Phone build | Android SDK, Java 17+, and the native toolchain described in [phone/README.md](../phone/README.md) |

The gateway source supports a range of Android versions. Audio support still depends on the handset and firmware.
Use a spare phone and test both audio directions before relying on unattended calls.

## 1. Get the source

```bash
git clone https://github.com/highsierraloft/callrelay.git
cd callrelay
uv sync --extra android --all-groups --frozen
```

For an isolated check without a phone, run:

```bash
uv run callrelay doctor --dummy
```

The `agent-call` command remains as a compatibility alias.
The evaluation commands `uv run agent-call doctor --prepare-only` and `uv run agent-call smoke-prepare` do not replace a handset test.
The legacy `uv run agent-call doctor --live-ready` checks the optional Twilio/OpenAI path.

## 2. Prepare the phone

1. Build the community gateway with the [Android guide](../phone/README.md).
2. Install it on the spare phone.
3. Install Shizuku from its official project.
4. Enable USB debugging and authorize your server.
5. Connect the phone with a data-capable USB cable.
6. Keep the gateway takeover switch off during setup.

The community package is `io.github.sparkcyf.voicesipagent.callrelay`.
It has separate app data and signing settings. Do not run two SIP gateways on one phone at the same time.

Read the device identifiers:

```bash
adb devices
adb get-serialno
adb shell getprop ro.product.model
lsusb
getent group plugdev
```

Record the USB serial, exact model, USB vendor ID, and USB group ID.
Use the slot label reported by Android. Select the intended SIM explicitly.

## 3. Create private settings

Reserve stable LAN addresses for the server and phone.
Use a Tailscale HTTPS origin such as `https://your-node.your-tailnet.ts.net:8444`.
Use your actual Tailscale login as the owner identity.

```bash
uv run python scripts/configure_phone.py
```

Run setup as a normal Linux user. Runtime containers use that user and group.

The tool asks for installation details and reads provider keys without echoing them.
It generates independent SIP, media, MCP, administration, and deployment credentials.
It writes files with mode `0600` in a mode-`0700` directory.
It refuses an existing output directory, so it cannot replace working credentials.

The tool creates these files:

| File | Purpose |
| --- | --- |
| `private/agent-call.json` | Worker settings and MCP credentials |
| `private/control-access.json` | Dashboard owner, origin, proxy trust, and bridge credential |
| `private/asterisk.json` | SIP and Asterisk credentials |
| `private/worker-asterisk.json` | Worker access to Asterisk |
| `private/device.json` | Exact handset identity and selected SIM slot |
| `private/*-api-key` | Provider credentials |
| `private/compose.env` | LAN address, container subnet, and user/group IDs |
| `private/mcp-client.json` | Private MCP connection example with your generated token |
| `private/codex-phone-client.json` | Optional Codex skill settings |
| `private/99-callrelay-phone.rules` | USB rule for this handset only |

The default Compose file expects `private/` at the repository root.
Keep this directory out of Git and support reports.

## 4. Authorize USB recovery

Install the generated rule on your Linux server:

```bash
sudo install -m 0644 private/99-callrelay-phone.rules /etc/udev/rules.d/99-callrelay-phone.rules
sudo udevadm control --reload-rules
```

Reconnect the handset after the rule reload.
The rule grants access to one USB serial, not every Android device.

Create a dedicated ADB identity:

```bash
adb keygen private/adb-state/adbkey
chmod 600 private/adb-state/adbkey
adb kill-server
```

The supervisor uses that identity. It refuses to create a replacement if the private key is missing.
Approve its USB debugging prompt on the handset after the supervisor starts.
Keep the host ADB server stopped while the supervisor owns the USB connection.

The supervisor can restart Shizuku and recover the gateway when the phone is idle.
It never enables takeover, answers a call, or changes the selected SIM.
A locked phone can still need an owner unlock after reboot. The supervisor does not bypass a lock screen or SIM PIN.

## 5. Build and start the stack

Check the generated Compose settings before starting services:

```bash
docker compose --env-file private/compose.env -f deploy/compose.phone.yaml config --quiet
docker compose --env-file private/compose.env -f deploy/compose.phone.yaml build
docker compose --env-file private/compose.env -f deploy/compose.phone.yaml up -d
```

Build before a service restart. The first Asterisk build compiles its pinned source and can take several minutes.

The worker publishes no host port. The dashboard binds to `127.0.0.1:9120`.
Only SIP and RTP publish on the specified LAN address. The supervisor has no network access.

Allow UDP `5060` and `12000–12019` only from the phone's address in your Docker-aware host firewall.
Do not forward these ports through an internet router.
The Asterisk endpoint also requires SIP authentication and the configured phone address.

## 6. Connect the gateway

Open the gateway settings on the handset.
Use the server LAN address, UDP port `5060`, and SIP username `android`.
Read the generated SIP password from `private/asterisk.json`. Do not paste it into an issue or chat.

Use the phone's gateway controls to select the intended SIM and its country.
The control protocol also supports `select-sim` with an explicit slot and country.
See [phone/README.md](../phone/README.md) for the command format.

Start Shizuku and grant the gateway access. Then enable takeover.
Keep loopback echo mode off for normal calls.

## 7. Open the private dashboard

On the server, start Tailscale Serve:

```bash
tailscale serve --bg --https=8444 http://127.0.0.1:9120
```

Use the same origin that you supplied to the setup tool.
Connect from a device associated with your personal Tailscale login.
Tagged client devices do not supply the required user identity header.

The proxy checks the exact host peer and owner identity before serving the dashboard or MCP.
Do not replace that check with a whole subnet or publish the dashboard port on every interface.
See the [Tailscale identity-header guidance](https://tailscale.com/docs/features/tailscale-serve#identity-headers).

If the dashboard returns `403`, read [troubleshooting](troubleshooting.md).

## 8. Connect an assistant

Use `private/mcp-client.json` with a compatible client on the tailnet.
For Codex skill use, follow [the integration guide](integrations.md#codex-skill).
For a local Hermes container, use the internal service address and the same generated MCP credentials.

The optional Hermes dashboard link is unset by default.
You can add `hermes_url` to `private/control-access.json` later. It must be a private Tailscale HTTPS address.

## 9. Accept your installation

1. Check that the dashboard reports the phone ready and SIP registered.
2. Start browser practice and check speech, interruption, and language changes.
3. Prepare a call to a phone you control.
4. Authorize one outbound test and check both audio directions.
5. Check the MP3 recording and full Markdown transcript.
6. Check keypad delivery against an automated menu you control.
7. Reconnect USB and check that control recovers without a call.
8. Restart the spare handset and check the unlock and recovery requirements.

For inbound reception, first supply business identity and approved facts in Settings.
Complete controlled incoming-call, caller-number, recording, native-fallback, and callback tests before enabling unattended reception.
Never test a protected or unselected SIM by placing a call through it.
