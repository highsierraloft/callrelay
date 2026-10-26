"""Native Hermes cron pre-check. Read the queue without constructing an AI agent.

The installed calling-agent MCP entry supplies the existing private URL and headers.
This script never prints headers, configuration, transcripts, or exception messages.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


def decode(raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8")
    if text.lstrip().startswith("{"):
        return dict(json.loads(text))
    for line in text.splitlines():
        if line.startswith("data:"):
            value = json.loads(line[5:].strip())
            if "result" in value or "error" in value:
                return dict(value)
    raise ValueError("No MCP result")


def queue_available(entry: dict[str, Any]) -> int:
    url = str(entry["url"])
    headers = {key: os.path.expandvars(str(value)) for key, value in entry["headers"].items()}
    headers.update(
        {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    )

    def send(body: dict[str, Any], *, notification: bool = False) -> dict[str, Any]:
        request = Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
        with urlopen(request, timeout=8) as response:  # noqa: S310 - existing owner-configured MCP URL
            session = response.headers.get("Mcp-Session-Id")
            if session:
                headers["Mcp-Session-Id"] = session
            raw = response.read(1024 * 1024)
        if notification:
            return {}
        result = decode(raw)
        if "error" in result:
            raise ValueError("MCP request failed")
        return dict(result["result"])

    try:
        initialized = send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "calling-review-precheck", "version": "1"},
                },
            }
        )
        headers["MCP-Protocol-Version"] = initialized["protocolVersion"]
        send({"jsonrpc": "2.0", "method": "notifications/initialized"}, notification=True)
        result = send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "calling_review_queue", "arguments": {}},
            }
        )
        if result.get("isError"):
            raise ValueError("Review queue unavailable")
        data = result.get("structuredContent")
        if not isinstance(data, dict):
            data = json.loads(
                next(
                    item["text"] for item in result.get("content", []) if item.get("type") == "text"
                )
            )
        pending = data["pending"]
        if not isinstance(pending, int) or pending < 0:
            raise ValueError("Invalid queue count")
        return pending
    finally:
        if headers.get("Mcp-Session-Id"):
            try:
                with urlopen(Request(url, headers=headers, method="DELETE"), timeout=3):
                    pass
            except Exception:
                pass


def main() -> None:
    os.umask(0o077)
    report: dict[str, Any] = {
        "checked_at": datetime.now(UTC).isoformat(),
        "available": False,
        "pending": 0,
    }
    try:
        sys.path.insert(0, "/opt/hermes")
        import hermes_yaml as yaml  # type: ignore[import-not-found]

        config = yaml.safe_load(Path("/opt/data/config.yaml").read_text())
        pending = queue_available(config["mcp_servers"]["calling-agent"])
        report.update(available=True, pending=pending)
    except Exception as exc:
        report["error_category"] = type(exc).__name__
    status = Path("/opt/data/calling-agent/review/precheck-status.json")
    try:
        status.parent.mkdir(parents=True, exist_ok=True)
        temporary = status.with_suffix(".tmp")
        temporary.write_text(json.dumps(report))
        temporary.replace(status)
    except OSError:
        pass
    # Hermes skips a model only when a successful pre-check returns this explicit gate.
    print(
        json.dumps(
            {
                "wakeAgent": report["available"] and report["pending"] > 0,
                "pending": report["pending"],
                "queue_available": report["available"],
            }
        )
    )


if __name__ == "__main__":
    main()
