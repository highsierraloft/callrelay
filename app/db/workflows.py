"""Revisioned outcomes and callback work, never edits to transcript evidence."""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import TYPE_CHECKING, Any

import aiosqlite

from app.db.control import RevisionConflict
from app.db.engine import _iso_now

if TYPE_CHECKING:
    from app.db.protocols import DatabaseAccess

WORKFLOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS call_assessments (
 call_id TEXT NOT NULL REFERENCES calls(call_id), revision INTEGER NOT NULL,
 value_json TEXT NOT NULL, actor TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(call_id,revision)
);
CREATE TABLE IF NOT EXISTS call_review_requests (
 call_id TEXT NOT NULL REFERENCES calls(call_id), request_id TEXT NOT NULL,
 payload_hash TEXT NOT NULL, result_json TEXT NOT NULL,
 PRIMARY KEY(call_id,request_id)
);
CREATE TABLE IF NOT EXISTS call_followups (
 followup_id TEXT PRIMARY KEY, source_call_id TEXT NOT NULL REFERENCES calls(call_id),
 source_key TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL,
 value_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 actor TEXT NOT NULL, UNIQUE(source_call_id,source_key)
);
CREATE TABLE IF NOT EXISTS call_followup_history (
 followup_id TEXT NOT NULL REFERENCES call_followups(followup_id), revision INTEGER NOT NULL,
 value_json TEXT NOT NULL, actor TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(followup_id,revision)
);
CREATE TABLE IF NOT EXISTS call_followup_plans (
 followup_id TEXT NOT NULL REFERENCES call_followups(followup_id),
 plan_id TEXT PRIMARY KEY REFERENCES plans(plan_id), created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS call_followups_status ON call_followups(status,created_at);
"""


def workflow_row(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result["value"] = json.loads(result.pop("value_json"))
    return result


async def save_manual_followups(
    conn: aiosqlite.Connection, call_id: str, drafts: list[dict[str, Any]], actor: str, now: str
) -> list[str]:
    callbacks: list[str] = []
    for callback in drafts:
        cursor = await conn.execute(
            "SELECT followup_id FROM call_followups WHERE source_call_id=? AND source_key=?",
            (call_id, callback["source_key"]),
        )
        existing = await cursor.fetchone()
        if existing:
            # A later assessment never resets work someone already edited or completed.
            callbacks.append(existing["followup_id"])
            continue
        followup_id = "followup_" + secrets.token_urlsafe(16)
        state = "needs_information" if callback["needed_information"] else "ready"
        encoded_callback = json.dumps(
            {**callback, "status": state, "note": "", "completion_call_id": None},
            ensure_ascii=False,
        )
        await conn.execute(
            "INSERT INTO call_followups VALUES (?,?,?,1,?,?,?,?,?)",
            (
                followup_id,
                call_id,
                callback["source_key"],
                state,
                encoded_callback,
                now,
                now,
                actor,
            ),
        )
        await conn.execute(
            "INSERT INTO call_followup_history VALUES (?,1,?,?,?)",
            (followup_id, encoded_callback, actor, now),
        )
        callbacks.append(followup_id)
    return callbacks


class WorkflowMixin:
    async def workflow_assessment(self: DatabaseAccess, call_id: str) -> dict[str, Any] | None:
        return workflow_row(
            await self.fetch_one(
                "SELECT * FROM call_assessments WHERE call_id=? ORDER BY revision DESC LIMIT 1",
                (call_id,),
            )
        )

    async def workflow_save_outcome(
        self: DatabaseAccess, call_id: str, payload: dict[str, Any], actor: str
    ) -> dict[str, Any]:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        now = _iso_now()
        async with self._immediate_transaction() as conn:
            cursor = await conn.execute(
                "SELECT payload_hash,result_json FROM call_review_requests WHERE call_id=? AND request_id=?",
                (call_id, payload["request_id"]),
            )
            saved = await cursor.fetchone()
            if saved:
                if saved["payload_hash"] != digest:
                    raise ValueError("This request ID was already used for different content")
                result: dict[str, Any] = json.loads(saved["result_json"])
                return result
            if actor == "hermes":
                cursor = await conn.execute(
                    "SELECT 1 FROM reception_reviews WHERE call_id=? UNION ALL SELECT 1 FROM call_contacts WHERE call_id=? AND automatic_review=1 LIMIT 1",
                    (call_id, call_id),
                )
                if await cursor.fetchone():
                    raise ValueError(
                        "Use claim_call_review and commit_call_review for this call; a separate outcome save cannot bypass its review lease"
                    )
            cursor = await conn.execute(
                "SELECT COALESCE(MAX(revision),0) FROM call_assessments WHERE call_id=?", (call_id,)
            )
            row = await cursor.fetchone()
            assert row is not None
            revision = row[0]
            if revision != payload["expected_revision"]:
                raise RevisionConflict(
                    "The outcome changed in another window. Read the current call result before saving again."
                )
            value = {
                k: v
                for k, v in payload.items()
                if k not in {"expected_revision", "request_id", "callbacks"}
            }
            await conn.execute(
                "INSERT INTO call_assessments VALUES (?,?,?,?,?)",
                (call_id, revision + 1, json.dumps(value, ensure_ascii=False), actor, now),
            )
            callbacks = await save_manual_followups(
                conn, call_id, payload.get("callbacks", []), actor, now
            )
            result = {"call_id": call_id, "revision": revision + 1, "followup_ids": callbacks}
            await conn.execute(
                "INSERT INTO call_review_requests VALUES (?,?,?,?)",
                (call_id, payload["request_id"], digest, json.dumps(result)),
            )
            return result

    async def workflow_update_followup(
        self: DatabaseAccess, followup_id: str, payload: dict[str, Any], actor: str
    ) -> dict[str, Any]:
        now = _iso_now()
        async with self._immediate_transaction() as conn:
            cursor = await conn.execute(
                "SELECT * FROM call_followups WHERE followup_id=?", (followup_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                raise LookupError("Follow-up not found")
            cursor = await conn.execute(
                "SELECT 1 FROM call_followup_plans fp JOIN calls c USING(plan_id) "
                "WHERE fp.followup_id=? AND c.state NOT IN ('completed','failed','timed_out','transferred') LIMIT 1",
                (followup_id,),
            )
            if await cursor.fetchone():
                raise ValueError("A linked call is active; wait before changing this follow-up")
            if row["revision"] != payload["expected_revision"]:
                raise RevisionConflict(
                    "This follow-up changed in another window. Reload it before saving."
                )
            value = {
                **json.loads(row["value_json"]),
                **{k: v for k, v in payload.items() if k != "expected_revision"},
            }
            revision = row["revision"] + 1
            encoded = json.dumps(value, ensure_ascii=False)
            await conn.execute(
                "UPDATE call_followups SET revision=?,status=?,value_json=?,actor=?,updated_at=? WHERE followup_id=?",
                (revision, payload["status"], encoded, actor, now, followup_id),
            )
            await conn.execute(
                "INSERT INTO call_followup_history VALUES (?,?,?,?,?)",
                (followup_id, revision, encoded, actor, now),
            )
            return {
                "followup_id": followup_id,
                "source_call_id": row["source_call_id"],
                "revision": revision,
                "status": payload["status"],
                "value": value,
                "actor": actor,
                "created_at": row["created_at"],
                "updated_at": now,
            }
