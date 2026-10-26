# CallRelay contributor instructions

Work only in this repository. It is independent from any deployed installation.
Never read operator credentials or connect to a real phone during development tests.
Keep generated settings, call data, transcripts, recordings, and build output out of Git.
Keep the selected-SIM guard, request authentication, CSRF checks, and immutable plans.
Keep provider credentials on the server. Browser practice must never dial a phone.
Use the same prompt, tool, and parameter builders for execution and inspection.
Keep the documentation and examples aligned with code changes.
Retain upstream notices. The Android gateway has its own GPL license.
Run `bash scripts/verify.sh` for backend changes.
Run `npm ci && npm run build && npm test` in `dashboard/` for interface changes.
Run `python -m unittest discover -s supervisor` for supervisor changes.
Run `./gradlew :gateway:test :sip:test --no-daemon` in `phone/` for gateway changes.
Use fake data and local services. A real handset test requires the operator's explicit request.
