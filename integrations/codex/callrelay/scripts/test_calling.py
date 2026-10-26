"""Run with python3 -m unittest discover -s scripts. All HTTP operations are fake."""

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from calling import CallingClient, ClientError, NoRedirect, identifier, load_object

PLAN = "plan_abcdefgh12345678"
CALL = "call_abcdefgh12345678"


class Response(io.BytesIO):
    status = 200

    def __init__(self, value):
        super().__init__(json.dumps(value).encode() if not isinstance(value, bytes) else value)


class Transport:
    def __init__(self):
        self.requests = []
        self.failed_start = False
        self.plan_state = {"state": "prepared", "call_id": None}
        self.artifact = b"an original artifact"
        self.artifact_hash = hashlib.sha256(self.artifact).hexdigest()

    def open(self, request, timeout=30):
        method = request.get_method()
        path = request.full_url.split("/admin/api", 1)[1]
        body = json.loads(request.data) if request.data else None
        self.requests.append((method, path, body, dict(request.header_items())))
        if path == "/session":
            return Response(
                {
                    "csrf": "test-nonce",
                    "setup": {
                        "owner": {"display_name": "Owner", "timezone": "Atlantic/Canary"},
                        "profiles": [{"id": "custom", "personality": "professional"}],
                        "defaults": {
                            "default_call_seconds": 1800,
                            "conversation_languages": ["es", "en"],
                        },
                    },
                }
            )
        if path == "/calls/prepare":
            return Response({"plan_id": PLAN, "confirmation_summary": "Exact approved summary."})
        if path == "/calls/start":
            if self.failed_start:
                raise ConnectionError("Response lost after dispatch")
            self.plan_state = {"state": "connected", "call_id": CALL}
            return Response({"call_id": CALL, "state": "starting"})
        if path == "/plans/" + PLAN:
            return Response(self.plan_state)
        if path == "/calls/" + CALL:
            return Response(
                {
                    "call_id": CALL,
                    "state": "completed",
                    "artifacts": {
                        "audio_status": "available",
                        "files": [
                            {
                                "name": "recording.mp3",
                                "size_bytes": len(self.artifact),
                                "sha256": self.artifact_hash,
                            }
                        ],
                    },
                }
            )
        if path == "/calls/" + CALL + "/files/recording.mp3":
            return Response(self.artifact)
        raise AssertionError("Unexpected operation: " + method + " " + path)


class CallingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.transport = Transport()
        self.client = CallingClient(
            {"base_url": "https://calling.example", "data_dir": str(self.root / "data")},
            self.transport,
        )

    def prepare(self):
        return self.client.prepare(
            {
                "target": {"phone": "+1 (202) 555-0123"},
                "objective": "Confirm the time",
                "profile_id": "custom",
            }
        )

    def test_prepare_uses_live_defaults_and_private_plan(self):
        result = self.prepare()
        saved = load_object(result["local_plan"])
        self.assertEqual(saved["brief"]["target"]["phone"], "+12025550123")
        self.assertEqual(saved["brief"]["target"]["name"], "Recipient at supplied number")
        self.assertEqual(saved["brief"]["language"], "es")
        self.assertEqual(saved["brief"]["personality"], "professional")
        self.assertEqual(saved["brief"]["max_duration_seconds"], 1800)
        self.assertEqual(saved["brief"]["owner"]["display_name"], "Owner")
        self.assertFalse(saved["start_attempted"])
        self.assertEqual(Path(result["local_plan"]).stat().st_mode & 0o777, 0o600)

    def test_post_uses_csrf_and_origin_without_forged_identity(self):
        self.prepare()
        headers = self.transport.requests[-1][3]
        self.assertEqual(headers["X-csrf-token"], "test-nonce")
        self.assertEqual(headers["Origin"], "https://calling.example")
        self.assertNotIn("Authorization", headers)
        self.assertNotIn("Tailscale-user-login", headers)

    def test_start_requires_owner_authorization_and_exact_saved_summary(self):
        self.prepare()
        with self.assertRaises(ClientError):
            self.client.start(PLAN, False)
        self.assertFalse(any(r[1] == "/calls/start" for r in self.transport.requests))
        self.client.start(PLAN, True)
        payload = next(r[2] for r in self.transport.requests if r[1] == "/calls/start")
        self.assertEqual(payload["confirmation_text"], "Exact approved summary.")
        self.assertTrue(payload["explicit_confirmation"])

    def test_lost_start_response_recovers_existing_call_without_another_dial(self):
        self.prepare()
        self.transport.failed_start = True
        with self.assertRaisesRegex(ClientError, "recovery"):
            self.client.start(PLAN, True)
        self.transport.plan_state = {"state": "connected", "call_id": CALL}
        result = self.client.start(PLAN, True)
        self.assertEqual(result["call_id"], CALL)
        self.assertEqual(sum(r[1] == "/calls/start" for r in self.transport.requests), 1)

    def test_unused_plan_is_not_retried_implicitly(self):
        self.prepare()
        self.transport.failed_start = True
        with self.assertRaises(ClientError):
            self.client.start(PLAN, True)
        self.transport.failed_start = False
        result = self.client.start(PLAN, True)
        self.assertIsNone(result["call_id"])
        self.assertEqual(sum(r[1] == "/calls/start" for r in self.transport.requests), 1)
        self.client.start(PLAN, True, retry_same_plan=True)
        self.assertEqual(sum(r[1] == "/calls/start" for r in self.transport.requests), 2)

    def test_expired_plan_cannot_be_retried(self):
        self.prepare()
        self.transport.failed_start = True
        with self.assertRaises(ClientError):
            self.client.start(PLAN, True)
        self.transport.plan_state = {"state": "expired", "call_id": None}
        with self.assertRaises(ClientError):
            self.client.start(PLAN, True, retry_same_plan=True)
        self.assertEqual(sum(r[1] == "/calls/start" for r in self.transport.requests), 1)

    def test_result_checks_hash_and_reports_missing_markdown(self):
        result = self.client.result(CALL)
        self.assertEqual(Path(result["files"][0]["path"]).read_bytes(), self.transport.artifact)
        self.assertEqual(result["missing_files"], ["transcript.md"])
        self.assertEqual(result["download_errors"], [])
        self.assertEqual(Path(result["files"][0]["path"]).stat().st_mode & 0o777, 0o600)

    def test_hash_mismatch_does_not_leave_a_partial_artifact(self):
        self.transport.artifact_hash = "0" * 64
        result = self.client.result(CALL)
        self.assertEqual(result["files"], [])
        self.assertEqual(len(result["download_errors"]), 1)
        self.assertFalse((self.client.data_dir / "calls" / CALL / "recording.mp3").exists())

    def test_existing_edited_artifact_is_preserved(self):
        first = self.client.result(CALL)
        path = Path(first["files"][0]["path"])
        path.write_bytes(b"owner edit")
        again = self.client.result(CALL)
        self.assertEqual(path.read_bytes(), b"owner edit")
        self.assertEqual(len(again["download_errors"]), 1)

    def test_artifact_symlink_is_rejected(self):
        folder = self.root / "output"
        folder.mkdir()
        target = self.root / "owner-file"
        target.write_bytes(b"preserve")
        (folder / "recording.mp3").symlink_to(target)
        result = self.client.result(CALL, str(folder))
        self.assertEqual(target.read_bytes(), b"preserve")
        self.assertEqual(len(result["download_errors"]), 1)

    def test_custom_output_directory_permissions_are_preserved(self):
        folder = self.root / "owner-output"
        folder.mkdir(mode=0o750)
        self.client.result(CALL, str(folder))
        self.assertEqual(folder.stat().st_mode & 0o777, 0o750)

    def test_identifiers_and_redirects_cannot_escape_the_calling_endpoint(self):
        for value in ["../../etc/passwd", CALL + "/files", "https://another.example"]:
            with self.assertRaises(ClientError):
                identifier(value, "call")
        with self.assertRaises(ClientError):
            NoRedirect().redirect_request(
                None, None, 302, "redirect", {}, "https://another.example"
            )


if __name__ == "__main__":
    unittest.main()
