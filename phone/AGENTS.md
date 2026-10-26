# Android gateway

Keep this component independent from the Python service.
Keep its GPL license and upstream notices. Do not reuse private signing keys.
Use the `io.github.sparkcyf.voicesipagent.callrelay` application ID.
Never install or configure a phone as part of source tests.
Never fall back to another SIM. Reject unknown or ambiguous phone accounts.
Keep pre-answer native fallback, call ownership, and audio cleanup intact.
Update the gateway guide when its settings or control protocol change.
