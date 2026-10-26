#!/usr/bin/env python3
"""Private calling-service client. Uses the owner's existing Tailscale identity."""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CONFIG = Path.home() / ".config/callrelay/client.json"
TERMINAL = {"completed", "failed", "timed_out", "transferred"}


class ClientError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ClientError("The calling endpoint redirected; check the configured address.", code)


def identifier(value: str, kind: str) -> str:
    if not re.fullmatch(re.escape(kind) + r"_[A-Za-z0-9_-]{8,128}", value):
        raise ClientError(f"Invalid {kind} identifier.")
    return value


def private_dir(path: Path) -> Path:
    if path.is_symlink():
        raise ClientError("The private calling directory must not be a symlink.")
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not existed:
        path.chmod(0o700)
    return path


def write_json(path: Path, value: Any) -> None:
    if path.is_symlink():
        raise ClientError("Refusing to replace a symlink.")
    private_dir(path.parent)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as file:
            temporary = Path(file.name)
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_object(path: str | Path) -> dict[str, Any]:
    import sys

    text = sys.stdin.read() if str(path) == "-" else Path(path).expanduser().read_text()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ClientError("Expected a JSON object.")
    return value


class CallingClient:
    def __init__(self, config: dict[str, Any], opener=None):
        address = config.get("base_url", "").rstrip("/")
        parsed = urllib.parse.urlsplit(address)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ClientError("Configure an HTTPS origin without credentials, a path, or a query.")
        self.origin = address
        self.data_dir = Path(
            config.get("data_dir", "~/.local/share/callrelay")
        ).expanduser()
        if not self.data_dir.is_absolute():
            raise ClientError("data_dir must be an absolute path.")
        self.opener = opener or urllib.request.build_opener(
            NoRedirect(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        self._session: dict[str, Any] | None = None

    def session(self) -> dict[str, Any]:
        if self._session is None:
            self._session = self.request("GET", "/session")
        return self._session

    def request(self, method: str, path: str, value: Any = None) -> Any:
        headers = {"Accept": "application/json", "Origin": self.origin}
        data = None
        if method != "GET":
            headers["X-CSRF-Token"] = self.session()["csrf"]
        if value is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(value, ensure_ascii=False).encode()
        req = urllib.request.Request(
            self.origin + "/admin/api" + path, data=data, headers=headers, method=method
        )
        try:
            with self.opener.open(req, timeout=30) as response:
                if response.status == 204:
                    return None
                body = response.read(16 * 1024 * 1024 + 1)
                if len(body) > 16 * 1024 * 1024:
                    raise ClientError("The API response is too large.")
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read(65536)).get("detail", f"HTTP {exc.code}")
            except (ValueError, AttributeError):
                detail = f"HTTP {exc.code}"
            raise ClientError(str(detail), exc.code) from None
        except urllib.error.URLError:
            raise ClientError(
                "Calling service unreachable. Check Tailscale and the service status."
            ) from None

    def plan_path(self, plan_id: str) -> Path:
        return self.data_dir / "plans" / (identifier(plan_id, "plan") + ".json")

    def prepare(self, brief: dict[str, Any], followup_id: str | None = None) -> dict[str, Any]:
        setup = self.session()["setup"]
        context = dict(brief)
        context.setdefault("owner", setup["owner"])
        context.setdefault("escalation", {"mode": "end_call"})
        context.setdefault("max_duration_seconds", setup["defaults"]["default_call_seconds"])
        languages = setup["defaults"]["conversation_languages"]
        if languages:
            context.setdefault("language", languages[0])
        profile = next((p for p in setup["profiles"] if p["id"] == context.get("profile_id")), None)
        if profile:
            context.setdefault("personality", profile["personality"])
        target = dict(context.get("target") or {})
        if not target.get("name"):
            target["name"] = target.get("organization") or "Recipient at supplied number"
        if isinstance(target.get("phone"), str):
            target["phone"] = re.sub(r"[\s().-]", "", target["phone"])
        context["target"] = target
        result = self.request(
            "POST",
            "/calls/prepare",
            {
                "context": context,
                "followup_id": followup_id,
                "requested_by_owner": True,
                "authority_basis": "Owner requested this call through Codex",
            },
        )
        if result.get("plan_id"):
            private_dir(self.data_dir)
            path = self.plan_path(result["plan_id"])
            write_json(path, {"prepared": result, "brief": context, "start_attempted": False})
            result["local_plan"] = str(path)
        return result

    def start(self, plan_id: str, confirmed: bool, retry_same_plan: bool = False) -> dict[str, Any]:
        if not confirmed:
            raise ClientError("Start requires --confirmed for an owner-authorized call.")
        path = self.plan_path(plan_id)
        saved = load_object(path)
        prepared = saved["prepared"]
        if prepared.get("plan_id") != plan_id:
            raise ClientError("The saved plan does not match its identifier.")
        summary = prepared.get("confirmation_summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ClientError("The plan has no confirmation summary.")
        if saved.get("start_attempted"):
            recovered = self.request("GET", "/plans/" + plan_id)
            if recovered.get("call_id") or not retry_same_plan:
                return {"recovered": True, **recovered, "local_plan": str(path)}
            if recovered.get("state") != "prepared":
                raise ClientError(
                    "The original plan is not unused and prepared; do not start another call."
                )
        saved["start_attempted"] = True
        saved["start_state"] = "unknown"
        write_json(path, saved)
        try:
            result = self.request(
                "POST",
                "/calls/start",
                {
                    "plan_id": plan_id,
                    "confirmation_text": summary,
                    "explicit_confirmation": True,
                },
            )
        except Exception:
            raise ClientError(
                f"Start outcome needs recovery. Run get --plan {plan_id}; do not prepare a replacement."
            ) from None
        saved.update(start_state="returned", start_result=result)
        write_json(path, saved)
        return result

    def detail(self, call_id: str) -> dict[str, Any]:
        return self.request("GET", "/calls/" + identifier(call_id, "call"))

    def poll(self, call_id: str, seconds: int) -> dict[str, Any]:
        deadline = time.monotonic() + seconds
        while True:
            result = self.detail(call_id)
            if result["state"] in TERMINAL or time.monotonic() >= deadline:
                return compact(result)
            time.sleep(min(2, max(0, deadline - time.monotonic())))

    def download(self, call_id: str, metadata: dict[str, Any], folder: Path) -> dict[str, Any]:
        name = metadata.get("name")
        if name not in {"recording.mp3", "transcript.md"}:
            raise ClientError("Unknown artifact name.")
        destination = private_dir(folder) / name
        if destination.is_symlink():
            raise ClientError("Refusing to replace an artifact symlink.")
        expected_hash = metadata.get("sha256")
        expected_size = metadata.get("size_bytes")
        if destination.exists():
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            if expected_hash and digest == expected_hash:
                return {
                    "name": name,
                    "path": str(destination.resolve()),
                    "sha256": digest,
                    "cached": True,
                }
            raise ClientError("An existing artifact differs; choose another --output directory.")
        temporary = None
        try:
            request = urllib.request.Request(
                self.origin + "/admin/api/calls/" + identifier(call_id, "call") + "/files/" + name
            )
            digest = hashlib.sha256()
            size = 0
            with (
                self.opener.open(request, timeout=90) as response,
                tempfile.NamedTemporaryFile(dir=folder, delete=False) as file,
            ):
                temporary = Path(file.name)
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > 512 * 1024 * 1024:
                        raise ClientError("Artifact exceeds the download limit.")
                    digest.update(chunk)
                    file.write(chunk)
            if expected_size is not None and size != expected_size:
                raise ClientError("Artifact size did not match the server's metadata.")
            if expected_hash and digest.hexdigest() != expected_hash:
                raise ClientError("Artifact hash did not match the server's metadata.")
            os.replace(temporary, destination)
            return {
                "name": name,
                "path": str(destination.resolve()),
                "bytes": size,
                "sha256": digest.hexdigest(),
            }
        except urllib.error.URLError:
            raise ClientError(
                "Artifact download failed; retry result after checking the service."
            ) from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def result(self, call_id: str, output: str | None = None) -> dict[str, Any]:
        detail = self.detail(call_id)
        if not output:
            private_dir(self.data_dir)
            private_dir(self.data_dir / "calls")
        folder = (
            Path(output).expanduser().resolve() if output else self.data_dir / "calls" / call_id
        )
        files, errors = [], []
        for metadata in detail.get("artifacts", {}).get("files", []):
            try:
                files.append(self.download(call_id, metadata, folder))
            except ClientError as exc:
                errors.append({"name": metadata.get("name"), "error": str(exc)})
        return {
            **compact(detail),
            "files": files,
            "download_errors": errors,
            "audio_status": detail.get("artifacts", {}).get("audio_status"),
            "missing_files": sorted(
                {"recording.mp3", "transcript.md"} - {f["name"] for f in files}
            ),
        }


def compact(detail: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "call_id",
        "state",
        "result",
        "summary",
        "review",
        "assessment",
        "timing",
        "recipient_opted_out",
        "next_action",
        "next_transcript_cursor",
    )
    return {key: detail[key] for key in keys if key in detail}


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--config", type=Path, default=CONFIG)
    sub = cli.add_subparsers(dest="action", required=True)
    sub.add_parser("setup", help="Read current guide, profiles, defaults, and readiness.")
    check = sub.add_parser("doctor", help="Check access without preparing or placing a call.")
    check.add_argument("--check-write-access", action="store_true")
    schema = sub.add_parser("schema", help="Read a current calling tool schema.")
    schema.add_argument("tool", nargs="?", default="prepare_phone_call")
    prepare = sub.add_parser("prepare", help="Validate a brief and save an unused plan.")
    prepare.add_argument("brief", help="JSON context file, or - for standard input.")
    prepare.add_argument("--followup-id")
    start = sub.add_parser("start", help="Start the saved, owner-authorized plan once.")
    start.add_argument("plan_id")
    start.add_argument("--confirmed", action="store_true")
    start.add_argument("--retry-same-plan", action="store_true")
    get = sub.add_parser("get")
    group = get.add_mutually_exclusive_group(required=True)
    group.add_argument("--call")
    group.add_argument("--plan")
    wait = sub.add_parser("poll")
    wait.add_argument("call_id")
    wait.add_argument("--seconds", type=int, choices=range(1, 31), default=20)
    end = sub.add_parser("end")
    end.add_argument("call_id")
    result = sub.add_parser("result", help="Download original MP3 and full Markdown transcript.")
    result.add_argument("call_id")
    result.add_argument("--output")
    for name in ("history", "contacts"):
        find = sub.add_parser(name)
        find.add_argument("--query", default="")
        find.add_argument("--cursor", default="")
    contact = sub.add_parser("contact")
    contact.add_argument("contact_id")
    return cli


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        client = CallingClient(load_object(args.config))
        action = args.action
        if action == "setup":
            result = {
                "setup": client.session()["setup"],
                "status": client.request("GET", "/status"),
            }
        elif action == "doctor":
            client.session()
            result = {"authenticated": True, "status": client.request("GET", "/status")}
            if args.check_write_access:
                try:
                    client.request("POST", "/calls/prepare", {})
                except ClientError as exc:
                    if exc.status != 422:
                        raise
                    result["write_access_verified"] = True
                else:
                    raise ClientError(
                        "Invalid brief unexpectedly accepted; stop and inspect the service."
                    )
        elif action == "schema":
            tools = client.request("GET", "/integration")["tools"]
            result = next((tool for tool in tools if tool["name"] == args.tool), None)
            if result is None:
                raise ClientError("That calling tool is not exposed.")
        elif action == "prepare":
            result = client.prepare(load_object(args.brief), args.followup_id)
        elif action == "start":
            result = client.start(args.plan_id, args.confirmed, args.retry_same_plan)
        elif action == "get":
            result = (
                client.request("GET", "/plans/" + identifier(args.plan, "plan"))
                if args.plan
                else compact(client.detail(args.call))
            )
        elif action == "poll":
            result = client.poll(args.call_id, args.seconds)
        elif action == "end":
            result = client.request("POST", "/calls/" + identifier(args.call_id, "call") + "/end")
        elif action == "result":
            result = client.result(identifier(args.call_id, "call"), args.output)
        elif action in {"history", "contacts"}:
            query = {"q": args.query}
            if args.cursor:
                query["cursor" if action == "history" else "offset"] = args.cursor
            result = client.request(
                "GET",
                ("/calls" if action == "history" else "/contacts")
                + "?"
                + urllib.parse.urlencode(query),
            )
        else:
            result = client.request("GET", "/contacts/" + identifier(args.contact_id, "contact"))
            result.pop("private_note", None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ClientError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
