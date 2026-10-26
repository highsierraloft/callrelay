# Upstream projects and contributions

CallRelay extends existing open-source work.
It does not claim that its calling engine or Android audio bridge started from scratch.

## Agent Call

- Project: [XiyaoWang0519/agent-call](https://github.com/XiyaoWang0519/agent-call)
- License: MIT. The original notice remains in [LICENSE](LICENSE).
- Upstream base: `613a6a8f567fb4c1430207010ed92339f02d2f83`.

Agent Call supplied the MCP lifecycle, call plans, persistence, authentication, Twilio/OpenAI path, and their tests.
The extension adds the Android/Asterisk/Pipecat route, dashboard, profiles, dynamic briefs, browser practice, reception, memory, callbacks, and phone-menu navigation.

## Android Voice SIP Agent

- Project: [sparkcyf/android-voice-sip-agent](https://github.com/sparkcyf/android-voice-sip-agent)
- License: GPL-2.0-or-later, with the upstream Apache-2.0 linking exception.
- Upstream base: `a134b8e5e0424024870b53252eb0d1b9f4d3195e`.

The original project supplied the cellular/SIP bridge, PJSIP integration, audio control, and Shizuku integration.
The gateway changes add explicit SIM selection, pinned account checks, selected-SIM inbound admission, native fallback, and media readiness signaling.

CallRelay gives this Android edition a separate application ID and signing-property prefix.
See [phone/UPSTREAM.md](phone/UPSTREAM.md), [the gateway license](phone/LICENSE), and [third-party notices](phone/THIRD_PARTY_NOTICES.md).

## Other building blocks

| Project | Use |
| --- | --- |
| [Pipecat](https://github.com/pipecat-ai/pipecat) | Streaming voice pipeline and browser transport |
| [Asterisk](https://github.com/asterisk/asterisk) | SIP, media, keypad signals, and recordings |
| [FastAPI](https://github.com/fastapi/fastapi) | HTTP and WebSocket application |
| [React](https://github.com/facebook/react) | Dashboard |
| [Caddy](https://github.com/caddyserver/caddy) | Private dashboard proxy |
| [Tailscale](https://tailscale.com/docs/features/tailscale-serve) | Private HTTPS access and owner identity |
| [eSpeak NG](https://github.com/espeak-ng/espeak-ng) | Reproducible synthetic fallback prompts |

These projects retain their own licenses and notices.

## Repository history

This edition starts with a new Git history after a source and privacy review.
It does not import operator data, credentials, deployment logs, or earlier Git history.
Source references and retained notices preserve provenance.
