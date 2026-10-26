"""Durable, leased transcript reviews shared by interactive and scheduled Hermes."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.db.contact_registry import memory_record, merge_review_memory
from app.db.control import RevisionConflict
from app.db.engine import _iso_now
from app.db.workflows import save_manual_followups

if TYPE_CHECKING:
    from app.db.protocols import DatabaseAccess


def transcript_digest(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ReceptionReviewsMixin:
    async def reception_enqueue_review(
        self: DatabaseAccess, call_id: str, *, reprocess: bool = False
    ) -> str:
        now = _iso_now()
        async with self._immediate_transaction() as conn:
            cursor = await conn.execute(
                "SELECT 1 FROM calls c JOIN call_results r USING(call_id) WHERE c.call_id=? "
                "AND c.state IN ('completed','failed','timed_out','transferred')",
                (call_id,),
            )
            if not await cursor.fetchone():
                raise ValueError(
                    "Wait until the final transcript is available before requesting a review"
                )
            cursor = await conn.execute(
                "SELECT job_id,state FROM reception_reviews WHERE call_id=?", (call_id,)
            )
            existing = await cursor.fetchone()
            if existing:
                if reprocess and existing["state"] == "done":
                    await conn.execute(
                        "UPDATE reception_reviews SET state='pending',lease_token=NULL,lease_until=NULL,lease_contact_id=NULL,available_at=?,updated_at=?,error=NULL,read_through=0 WHERE job_id=?",
                        (now, now, existing["job_id"]),
                    )
                return str(existing["job_id"])
            job_id = "review_" + secrets.token_urlsafe(16)
            await conn.execute(
                "INSERT INTO reception_reviews(job_id,call_id,state,available_at,created_at,updated_at) VALUES (?,?,'pending',?,?,?)",
                (job_id, call_id, now, now, now),
            )
            return job_id

    async def reception_review_pending(self: DatabaseAccess) -> int:
        now = _iso_now()
        row = await self.fetch_one(
            "SELECT COUNT(*) n FROM reception_reviews WHERE (state='pending' AND available_at<=?) "
            "OR (state='leased' AND lease_until<=?)",
            (now, now),
        )
        return int(row["n"]) if row else 0

    async def reception_claim_review(
        self: DatabaseAccess, call_id: str | None = None
    ) -> dict[str, Any] | None:
        now = _iso_now()
        until = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
        async with self._immediate_transaction() as conn:
            cursor = await conn.execute(
                "SELECT * FROM reception_reviews WHERE ((state='pending' AND available_at<=?) "
                "OR (state='leased' AND lease_until<=?)) AND (? IS NULL OR call_id=?) "
                "ORDER BY created_at LIMIT 1",
                (now, now, call_id, call_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            cursor = await conn.execute(
                "SELECT turn_id,speaker,text,sequence_number FROM transcripts WHERE call_id=? ORDER BY sequence_number",
                (row["call_id"],),
            )
            transcript = [dict(r) for r in await cursor.fetchall()]
            digest = transcript_digest(transcript)
            token = secrets.token_urlsafe(24)
            cursor = await conn.execute(
                "SELECT contact_id FROM call_contacts WHERE call_id=?", (row["call_id"],)
            )
            contact = await cursor.fetchone()
            await conn.execute(
                "UPDATE reception_reviews SET state='leased',lease_token=?,lease_until=?,attempts=attempts+1,"
                "transcript_digest=?,read_through=0,error=NULL,updated_at=?,lease_contact_id=? WHERE job_id=?",
                (
                    token,
                    until,
                    digest,
                    now,
                    contact["contact_id"] if contact else None,
                    row["job_id"],
                ),
            )
            return {
                "job_id": row["job_id"],
                "call_id": row["call_id"],
                "lease_token": token,
                "lease_until": until,
                "transcript_digest": digest,
                "transcript_turns": len(transcript),
            }

    async def reception_read_review(
        self: DatabaseAccess, job_id: str, lease_token: str, offset: int, limit: int = 100
    ) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("Invalid transcript page")
        now = _iso_now()
        async with self._immediate_transaction() as conn:
            cursor = await conn.execute(
                "SELECT * FROM reception_reviews WHERE job_id=? AND lease_token=? AND state='leased' AND lease_until>?",
                (job_id, lease_token, now),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("Review lease expired; claim the review again")
            if offset > row["read_through"]:
                raise ValueError("Read every transcript page in order before committing")
            cursor = await conn.execute(
                "SELECT turn_id,speaker,text,sequence_number FROM transcripts WHERE call_id=? ORDER BY sequence_number LIMIT ? OFFSET ?",
                (row["call_id"], limit + 1, offset),
            )
            turns = [dict(r) for r in await cursor.fetchall()]
            read_through = max(row["read_through"], offset + min(limit, len(turns)))
            await conn.execute(
                "UPDATE reception_reviews SET read_through=?,lease_until=?,updated_at=? WHERE job_id=?",
                (read_through, (datetime.now(UTC) + timedelta(minutes=5)).isoformat(), now, job_id),
            )
        return {
            "call_id": row["call_id"],
            "transcript": turns[:limit],
            "next_offset": offset + limit if len(turns) > limit else None,
            "transcript_digest": row["transcript_digest"],
        }

    async def reception_commit_review(
        self: DatabaseAccess, payload: dict[str, Any]
    ) -> dict[str, Any]:
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        now = _iso_now()
        async with self._immediate_transaction() as conn:
            cursor = await conn.execute(
                "SELECT * FROM reception_review_receipts WHERE request_id=?",
                (payload["request_id"],),
            )
            receipt = await cursor.fetchone()
            if receipt:
                if receipt["payload_hash"] != digest:
                    raise ValueError("This request ID already saved different review content")
                return dict(json.loads(receipt["result_json"]))
            cursor = await conn.execute(
                "SELECT * FROM reception_reviews WHERE job_id=? AND lease_token=? AND state='leased' AND lease_until>?",
                (payload["job_id"], payload["lease_token"], now),
            )
            job = await cursor.fetchone()
            if job is None:
                raise ValueError("Review lease expired; claim the review again")
            call_id = job["call_id"]
            cursor = await conn.execute(
                "SELECT turn_id,speaker,text,sequence_number FROM transcripts WHERE call_id=? ORDER BY sequence_number",
                (call_id,),
            )
            transcript = [dict(r) for r in await cursor.fetchall()]
            if job["read_through"] < len(transcript):
                raise ValueError("Read the complete transcript before committing the review")
            if (
                payload["transcript_digest"] != transcript_digest(transcript)
                or payload["transcript_digest"] != job["transcript_digest"]
            ):
                raise RevisionConflict("The transcript changed; release and claim the review again")
            evidence = set(payload["evidence_turn_ids"])
            evidence.update(payload.get("identity_evidence_turn_ids", []))
            callbacks = payload.get("callbacks", [])
            if len({callback["source_key"] for callback in callbacks}) != len(callbacks):
                raise ValueError("Callback source keys must be unique within a review")
            for callback in callbacks:
                if callback["source"] == "owner_request":
                    raise ValueError(
                        "Transcript-derived follow-ups use recipient_request or agent_promise, not new owner authority"
                    )
                evidence.update(callback["evidence_turn_ids"])
            if payload.get("recipient_name") and not payload.get("identity_evidence_turn_ids"):
                raise ValueError("A caller name needs transcript evidence")
            for fact in payload["memory_facts"]:
                if not fact["evidence_turn_ids"]:
                    raise ValueError("Memory changes need transcript evidence")
                evidence.update(fact["evidence_turn_ids"])
                if fact.get("source_call_id") not in (None, call_id) or fact.get("owner_protected"):
                    raise ValueError("Hermes cannot forge memory provenance or owner corrections")
            if evidence - {turn["turn_id"] for turn in transcript}:
                raise ValueError("Evidence must come from this call's transcript")
            if payload["outcome"] in {"achieved", "declined"} and not payload["evidence_turn_ids"]:
                raise ValueError("This outcome needs transcript evidence")
            if len({fact["key"] for fact in payload["memory_facts"]}) != len(
                payload["memory_facts"]
            ):
                raise ValueError("Memory fact keys must be unique")
            cursor = await conn.execute(
                "SELECT COALESCE(MAX(revision),0) FROM call_summaries WHERE call_id=?", (call_id,)
            )
            revision_row = await cursor.fetchone()
            assert revision_row is not None
            revision = revision_row[0]
            if revision != payload["expected_summary_revision"]:
                raise RevisionConflict("The call summary changed; read the current review context")
            cursor = await conn.execute(
                "SELECT contact_id,recognition FROM call_contacts WHERE call_id=?", (call_id,)
            )
            link = await cursor.fetchone()
            if (link["contact_id"] if link else None) != job["lease_contact_id"]:
                raise RevisionConflict(
                    "The owner changed this call's contact. Release the review and claim it again."
                )
            memory_revision = 0
            if link and link["contact_id"]:
                cursor = await conn.execute(
                    "SELECT name FROM contacts WHERE contact_id=?", (link["contact_id"],)
                )
                identity = await cursor.fetchone()
                different_name = bool(
                    link["recognition"] != "owner_linked"
                    and identity
                    and identity["name"]
                    and payload.get("recipient_name")
                    and identity["name"].strip().casefold()
                    != payload["recipient_name"].strip().casefold()
                )
                if different_name and payload["memory_facts"]:
                    raise ValueError(
                        "The caller's name differs from the matched contact. Save the summary without memory changes, then let the owner resolve the contact."
                    )
                if different_name:
                    await conn.execute(
                        "UPDATE call_contacts SET recognition='identity_mismatch' WHERE call_id=?",
                        (call_id,),
                    )
                old = await memory_record(conn, link["contact_id"])
                if old["revision"] != payload["expected_memory_revision"]:
                    raise RevisionConflict(
                        "Contact memory changed; read it again before applying this review"
                    )
                memory_revision = old["revision"]
                if payload.get("recipient_name") and link["recognition"] != "shared":
                    updated = await conn.execute(
                        "UPDATE contacts SET name=?,revision=revision+1,updated_at=? WHERE contact_id=? AND name='' AND actor='call'",
                        (payload["recipient_name"], now, link["contact_id"]),
                    )
                    if updated.rowcount:
                        cursor = await conn.execute(
                            "SELECT * FROM contacts WHERE contact_id=?", (link["contact_id"],)
                        )
                        named = await cursor.fetchone()
                        assert named is not None
                        await conn.execute(
                            "INSERT INTO contact_history VALUES (?,?,?,?,?)",
                            (
                                link["contact_id"],
                                named["revision"],
                                json.dumps(dict(named), ensure_ascii=False),
                                "hermes",
                                now,
                            ),
                        )
                if payload["memory_facts"]:
                    if link["recognition"] in {"shared", "identity_mismatch"}:
                        raise ValueError(
                            "Resolve this shared number's contact before updating memory"
                        )
                    value = merge_review_memory(old["value"], payload["memory_facts"], call_id)
                    memory_revision += 1
                    await conn.execute(
                        "INSERT INTO contact_memory VALUES (?,?,?,?,?)",
                        (
                            link["contact_id"],
                            memory_revision,
                            json.dumps(value, ensure_ascii=False),
                            "hermes",
                            now,
                        ),
                    )
            elif payload["memory_facts"]:
                raise ValueError("Link this call to a contact before updating memory")
            summary = {
                "text": payload["summary"],
                "outcome": payload["outcome"],
                "evidence_turn_ids": payload["evidence_turn_ids"],
                "transcript_digest": payload["transcript_digest"],
                "recipient_name": payload.get("recipient_name"),
            }
            await conn.execute(
                "INSERT INTO call_summaries VALUES (?,?,?,?,?)",
                (
                    call_id,
                    revision + 1,
                    json.dumps(summary, ensure_ascii=False),
                    "hermes",
                    now,
                ),
            )
            cursor = await conn.execute(
                "SELECT revision,actor FROM call_assessments WHERE call_id=? ORDER BY revision DESC LIMIT 1",
                (call_id,),
            )
            assessment = await cursor.fetchone()
            if assessment is None or assessment["actor"] != "owner":
                value = {
                    "status": payload["outcome"],
                    "explanation": payload["summary"],
                    "evidence_turn_ids": payload["evidence_turn_ids"],
                    "facts": [],
                }
                await conn.execute(
                    "INSERT INTO call_assessments VALUES (?,?,?,?,?)",
                    (
                        call_id,
                        (assessment["revision"] if assessment else 0) + 1,
                        json.dumps(value, ensure_ascii=False),
                        "hermes",
                        now,
                    ),
                )
            await conn.execute(
                "UPDATE reception_reviews SET state='done',lease_token=NULL,lease_until=NULL,updated_at=? WHERE job_id=?",
                (now, job["job_id"]),
            )
            followup_ids = await save_manual_followups(conn, call_id, callbacks, "hermes", now)
            result = {
                "job_id": job["job_id"],
                "call_id": call_id,
                "state": "done",
                "summary_revision": revision + 1,
                "memory_revision": memory_revision,
                "followup_ids": followup_ids,
            }
            await conn.execute(
                "INSERT INTO reception_review_receipts VALUES (?,?,?)",
                (
                    payload["request_id"],
                    digest,
                    json.dumps(result),
                ),
            )
            return result

    async def reception_release_review(
        self: DatabaseAccess, job_id: str, lease_token: str, error: str
    ) -> bool:
        return await self._execute_cas(
            "UPDATE reception_reviews SET state='pending',lease_token=NULL,lease_until=NULL,"
            "available_at=?,error=?,updated_at=? WHERE job_id=? AND lease_token=? AND state='leased'",
            (
                (datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
                error[:500],
                _iso_now(),
                job_id,
                lease_token,
            ),
        )
