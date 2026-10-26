# Release checks

The community source has its own checks and fresh Git history.
No operator database, credential file, device identity, or call recording is part of this repository.

## Local checks

| Check | Result |
| --- | --- |
| Backend suite | 1,100+ passed, two optional Docker checks skipped |
| Backend coverage | 87.26% |
| Chromium, light and dark | 94 passed locally |
| Linux CI browsers, including WebKit and phone sizes | 186 passed, two screenshot-only cases skipped |
| Gateway and SIP JVM tests | 34 passed |
| Supervisor tests | 11 passed |
| Optional Codex helper | 12 passed |
| Python runtime dependency audit | No known vulnerabilities reported for 115 locked packages |
| Node dependency audit | No known vulnerabilities reported |
| Source secret scan | No leaks detected |
| Gradle wrapper | Matches the official Gradle 9.6.0 checksum |

The lock includes compatible security updates, including Pipecat 1.14.0 and Pillow 13.
The source checks reject runtime artifacts and personal machine paths.
The synthetic fallback audio has a text and checksum manifest.
Documentation screenshots use test fixtures and a silent synthetic audio sample.

## CI and limits

GitHub CI runs the backend gate, browser checks, gateway tests, and source checks independently.
The status badges show the result for the current revision.
Linux CI passed the WebKit and phone checks. The local WebKit binary crashed before page load.

No live calls or provider conversations ran from this community copy.
A new Android installation still needs tests for SIM selection, two-way audio, keypad delivery, recording, recovery, and inbound handling.
Automated tests do not establish compatibility with every handset or carrier.
