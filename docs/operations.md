# Operate CallRelay

## Everyday use

Prepare a brief in the dashboard or through an assistant.
Supply the recipient, current task, known facts, language, and commitment limits.
Use a personality for speaking style.

Save Changes applies to future preparations. Existing plans and active conversations keep their saved settings.
Use the AI input inspector to see the assembled prompt and tool definitions.

The Calls view keeps the original transcript and recording.
Notes, tags, summaries, and outcomes remain separate records.

## Inbound reception

Supply the business name, description, and approved information in Settings.
Choose the inbound personality, greeting, questions, and limits.
Complete the handset acceptance checks before switching answering on.

Answering policy and phone readiness are separate states.
Turning answering off leaves an answered conversation running.
A phone-number match does not prove the caller's identity.

## Callbacks

Configure the allowed days, hours, timezone, and call window in Outbound.
The default policy uses weekdays, 09:00–18:00, with a 15-minute window.
Check the timezone before enabling automatic callbacks.

A saved schedule receipt is required before a callback is described as scheduled.
A mention in a summary alone does not create a schedule.
Missed windows and unsuccessful attempts remain visible for owner review.

## Backups

Back up `private/data`, `private/recordings`, installation settings, and the ADB identity.
Keep backups private and outside the source repository.
Use SQLite's backup API or stop the worker before copying a database with active writes.

Test restoration on a separate installation. Do not start restored pending callbacks against a real phone during a recovery exercise.

## Updates

1. Read the release notes and check for active calls or practice.
2. Back up the private data and current settings.
3. Build the new images before stopping a running service.
4. Update only when the telephone line is idle.
5. Start the new images and check health, SIP registration, and phone readiness.
6. Check that earlier transcripts, recordings, and settings remain available.

Do not run a second worker against the same database and phone.
Do not rotate working credentials as part of an ordinary update.

## Logs and support

```bash
docker compose --env-file private/compose.env -f deploy/compose.phone.yaml ps
docker compose --env-file private/compose.env -f deploy/compose.phone.yaml logs --tail=100 agent-call
```

Review logs before sharing them. Remove phone numbers, conversation text, tokens, and device identifiers.
Never publish `private/`, full database exports, or an ADB key.

## Costs and retention

Provider usage and carrier charges are separate.
Missing usage is unknown, not zero. Cost estimates do not replace provider invoices.
This release does not automatically delete call records.
The operator controls retention, backups, and access to the private storage.
