# Assistant integrations

CallRelay exposes MCP over Streamable HTTP at `/mcp/`.
A client needs private network access, the generated bearer token, and the allowed agent ID.
Browser administration uses a separate owner authentication path.

## Standard workflow

1. Read `get_calling_setup` for the current guide and profiles.
2. Collect missing recipient, task, language, facts, and authority details.
3. Call `prepare_phone_call` without dialing.
4. Save the returned plan ID and exact confirmation summary.
5. After owner authorization, call `start_phone_call` once.
6. Poll `phone_call_poll` until the call ends.
7. Read every transcript page and return both result files.

After an uncertain start, call `get_phone_call` with the original `plan_id`.
Do not prepare another call to recover a lost response.

An explicit owner request to make a specified call is authorization. A vague future idea is not.
Phone-menu choices do not require another client tool. The live voice agent handles them within the approved task.

## Client settings

The setup tool creates `private/mcp-client.json` with your private URL and credentials.
Client formats vary. Copy the URL and headers into the client's MCP settings.

```json
{
  "mcpServers": {
    "callrelay": {
      "url": "https://your-node.your-tailnet.ts.net:8444/mcp/",
      "headers": {
        "Authorization": "Bearer YOUR_GENERATED_TOKEN",
        "X-Agent-User-Id": "owner-agent"
      }
    }
  }
}
```

A colocated client on the private Compose network can use `http://agent-call:8000/mcp/`.
It still needs both MCP headers. Do not publish that worker port to the internet.
Cloud clients without access to your tailnet cannot use the private endpoint.

## Codex skill

The optional [phone-calls skill](../integrations/codex/callrelay/SKILL.md) uses the authenticated dashboard API.
It saves plans before dialing, recovers uncertain starts, and downloads the original MP3 and Markdown with checksum checks.

Do not overwrite an existing skill or client file without reviewing it first.

```bash
mkdir -p ~/.agents/skills ~/.config/callrelay
cp -R integrations/codex/callrelay ~/.agents/skills/
install -m 600 private/codex-phone-client.json ~/.config/callrelay/client.json
python3 ~/.agents/skills/callrelay/scripts/calling.py doctor
```

The skill has no provider keys or server-admin credentials.
Its helper needs Python 3 and a working Tailscale connection.

## Hermes

Enable the generated MCP connection through Hermes's supported MCP setup.
Make the calling setup, prepare, start, poll, end, and result tools available.
The guide from `get_calling_setup` is the workflow source. Prompt edits do not require a Hermes restart.

The default result transport uses native MCP audio and resource attachments.
Some clients limit attachment size. For long calls, use dashboard downloads or configure shared-file delivery.

Shared-file delivery uses these Android settings:

```json
{
  "artifact_export_directory": "/call-output",
  "artifact_client_directory": "/callrelay-files"
}
```

Mount the same private directory at `/call-output` in the worker and `/callrelay-files` in the client.
The client mount can be read-only. Keep both paths outside the repository.
The result then includes native `MEDIA:` paths. Those paths are valid only inside the configured client environment.

## Summaries and contact memory

The review queue is independent of the live voice pipeline.
An assistant can poll `calling_review_queue` and skip model work when the queue is empty.

A reviewer uses `claim_call_review`, reads every page with `read_call_review`, and submits `commit_call_review`.
`release_call_review` returns failed work to the queue.
The commit saves the summary, outcome, and eligible memory together.

For unattended reviews, give a separate job only these review tools.
Do not give that job call-start or messaging tools.
The community setup does not install a cron job or change an existing Hermes installation.

## Other clients

Any compatible MCP client with private access can use the lifecycle.
The client must preserve plan IDs, handle uncertain responses, and support the returned attachment format.
Use the full transcript as evidence. A call marked completed can still have an incomplete task outcome.
