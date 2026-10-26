# Security

Report vulnerabilities through [GitHub private vulnerability reporting](https://github.com/highsierraloft/callrelay/security/advisories/new).
Do not place credentials or personal call data in a public issue.

## Scope

CallRelay targets a single owner on a private network.
The dashboard uses Tailscale identity, an authenticated proxy connection, origin checks, and CSRF protection.
MCP uses a bearer credential and an allowed agent identifier.
The worker has no ADB access. The USB supervisor has no network access.
The gateway pins one SIM and never falls back to another line.

## Operator data

Keep provider keys, SIP credentials, ADB identities, phone numbers, and call files in ignored private storage.
Keep signing keys outside the repository.
Back up private data without adding it to source control.
The browser bundle must never contain provider or infrastructure credentials.
Audio and conversation text go to the configured speech and dialogue providers.
Self-hosting the control plane does not make that inference local.

## Release review

This community edition starts with fresh Git history.
It excludes runtime data, personal deployment targets, operator logs, and private signing material.
Fallback clips are synthetic prompts generated from source text.
Repository scans and automated tests reduce risk. They are not proof that every possible vulnerability is absent.

Hardware compatibility, carrier behavior, and each new installation still require operator acceptance checks.
