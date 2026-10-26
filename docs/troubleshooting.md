# Troubleshooting

## The dashboard returns 403

Check that the URL matches `public_origin` in the private access file.
Check that the client uses the configured personal Tailscale login.
Tagged client devices do not supply the required identity header.

Check that the Compose subnet matches `trusted_proxy_ips` in the access file.
The trusted peer is the host gateway for that subnet.
Do not fix this error by trusting every container address or removing the owner check.

## The phone is unavailable

Check USB authorization, Shizuku, the exact device model, the selected SIM, and gateway takeover.
Check that loopback echo mode is off.
The supervisor health check proves that its process is alive. It does not prove that a call can work.

If the phone restarted, unlock it when Android requires an unlock.
If the SIM requires a PIN, enter it on the handset.
The supervisor does not bypass these protections.

## SIP is offline

Check the server and phone LAN addresses.
Check the phone's SIP username and generated password.
Check the phone-specific firewall rule and UDP ports.
Do not enable an internet router port forward.

## The call starts but audio is missing

End the controlled test. Check both audio directions separately.
Gateway state alone does not prove that Android audio injection works.
Check the handset-specific guidance in the [upstream gateway project](https://github.com/sparkcyf/android-voice-sip-agent).

## A menu repeats

Check the call's AI input inspector for a `send_dtmf` result.
An accepted result proves dispatch to Asterisk, not acceptance by the remote carrier menu.
Check keypad delivery with a controlled recipient before repeating a business call.
Never create a replacement call after an uncertain start response.

## A result has no recording

Read the audio status and termination reason.
An unanswered call, failed capture, or missing file is not a successful recording.
The full transcript can still be available.

## The client cannot attach a large MP3

Use the authenticated dashboard download or the Codex helper.
For a colocated assistant, configure shared-file delivery and a read-only client mount.
See [integrations](integrations.md#hermes).

## Local frontend build fails

Use Node 24 or another version supported by the locked Vite release.
Install dependencies with `npm ci`.
Do not change the lock file to work around an older Node runtime.
