# Development

Use Python 3.12+, Node 24, `uv`, FFmpeg, and Git.
Android tests also need Java 17+ and the Gradle wrapper.

## Backend

```bash
uv sync --extra android --all-groups --frozen
bash scripts/verify.sh
```

The shared gate checks formatting, lint, types, tests, and coverage.
External services are mocked. The gate does not place calls or read operator credentials.

## Dashboard

```bash
cd dashboard
npm ci --include=optional
npm run build
npx playwright install chromium webkit
npm test
```

Browser tests use local mocked APIs and fictional data.
Keep test screenshots out of Git, except the reviewed examples in `docs/images/`.

## Gateway and supervisor

From the repository root, run:

```bash
python3 -m unittest discover -s supervisor
cd phone
./gradlew :gateway:test :sip:test --no-daemon
```

The pure JVM gateway tests need no phone or provider credentials.
The Android APK build is a separate step in the [gateway guide](../phone/README.md).

## Synthetic fallback audio

The eight short service prompts are generated from `app/reception_audio.py`.
They contain no caller speech. Their manifest includes the source text and hashes.
Install eSpeak NG and FFmpeg, then regenerate them with:

```bash
uv run python scripts/generate_fallback_audio.py
```

Normal conversation speech still uses the configured provider.

## Privacy checks

Run the publication check against Git-tracked source:

```bash
uv run python scripts/check_public_release.py
gitleaks git --redact --no-banner
```

Do not add an allowlist for a real credential or operator record.
Keep generated settings, build output, transcripts, recordings, and device identifiers outside Git.
Review staged files before every public push.

## Live checks

Run live provider and handset checks only with explicit operator approval and controlled recipients.
Label provider simulation separately from actual carrier delivery.
The inherited live-phone harness is an advanced Twilio test tool, not the community setup path.

## Updating dependencies

Keep lock files in Git. Change one dependency group at a time and run its relevant checks.
Do not remove a lock file to fix a local runtime version problem.
Keep docs, examples, API types, and deployment templates aligned with behavior changes.

Version-update pull requests are opt-in for the initial release. CI dependency audits and GitHub security alerts remain enabled.
