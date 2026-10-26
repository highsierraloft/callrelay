"""Contacts and review context; private notes never enter voice or Hermes review input."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.db.control import revision_record
from app.reception_models import (
    ContactLink,
    ContactWrite,
    MemoryWrite,
    ReviewCommit,
    normalize_number,
)

if TYPE_CHECKING:
    from app.control import ControlPlane


class ContactMemory:
    def __init__(self, control: ControlPlane) -> None:
        self.control = control
        self.db = control.db

    async def migrate_history(self) -> None:
        """Link historical calls once, without inventing summaries or queueing reviews."""
        marker = await self.db.fetch_one(
            "SELECT 1 FROM reception_state WHERE name='history_linked'"
        )
        if marker:
            return
        rows = await self.db.fetch_all(
            "SELECT c.call_id,p.context_json FROM calls c JOIN plans p USING(plan_id) "
            "LEFT JOIN call_contacts l USING(call_id) WHERE l.call_id IS NULL ORDER BY c.created_at"
        )
        for row in rows:
            target = json.loads(row["context_json"]).get("target", {})
            try:
                phone = normalize_number(target["phone"])
            except (KeyError, ValueError):
                phone = None
            await self.db.contact_link_call(
                call_id=row["call_id"],
                phone=phone,
                name=target.get("name", ""),
                direction="outbound",
                automatic_review=False,
            )
        await self.db.execute(
            "INSERT OR IGNORE INTO reception_state VALUES ('history_linked','true')"
        )

    async def list(self, search: str = "", offset: int = 0, limit: int = 40) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("Invalid contact page")
        rows = await self.db.fetch_all(
            "SELECT c.*, (SELECT COUNT(*) FROM call_contacts l WHERE l.contact_id=c.contact_id) call_count "
            "FROM contacts c WHERE (?='' OR instr(casefold(c.name || ' ' || c.background),casefold(?))>0 "
            "OR EXISTS(SELECT 1 FROM contact_numbers n WHERE n.contact_id=c.contact_id AND instr(n.phone,?)>0)) "
            "ORDER BY c.updated_at DESC,c.contact_id LIMIT ? OFFSET ?",
            (search, search, search, limit + 1, offset),
        )
        for row in rows:
            row.pop("private_note", None)
            row["numbers"] = await self.db.fetch_all(
                "SELECT phone,confirmed,source FROM contact_numbers WHERE contact_id=? ORDER BY phone",
                (row["contact_id"],),
            )
        return {
            "contacts": rows[:limit],
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    async def get(
        self, contact_id: str, *, private: bool = False, offset: int = 0
    ) -> dict[str, Any]:
        row = await self.db.contact_get(contact_id)
        if row is None:
            raise LookupError("Contact not found")
        if not private:
            row.pop("private_note", None)
        if offset < 0:
            raise ValueError("Invalid history page")
        calls = await self.db.fetch_all(
            "SELECT c.call_id,c.state,c.created_at,c.ended_at,l.direction,l.phone,l.recognition,"
            "s.value_json summary_json,r.state review_state FROM call_contacts l JOIN calls c USING(call_id) "
            "LEFT JOIN call_summaries s ON s.call_id=c.call_id AND s.revision=(SELECT MAX(revision) FROM call_summaries WHERE call_id=c.call_id) "
            "LEFT JOIN reception_reviews r ON r.call_id=c.call_id WHERE l.contact_id=? ORDER BY c.created_at DESC,c.call_id LIMIT 41 OFFSET ?",
            (contact_id, offset),
        )
        for call in calls:
            call["summary"] = json.loads(call.pop("summary_json") or "null")
        row["calls"], row["next_offset"] = calls[:40], offset + 40 if len(calls) > 40 else None
        row["callbacks"] = await self.db.fetch_all(
            "SELECT schedule_id,state,destination,window_start,window_end,call_id FROM callback_schedules WHERE contact_id=? ORDER BY created_at DESC LIMIT 100",
            (contact_id,),
        )
        return row

    async def lookup(self, number: str) -> dict[str, Any]:
        phone = normalize_number(number)
        matches = await self.db.contact_lookup(phone)
        unresolved = await self.unresolved_number(phone)
        return {
            "phone": phone,
            "recognition": "shared"
            if unresolved or len(matches) > 1
            else "exact"
            if matches
            else "new",
            "contacts": matches,
            "authenticated": False,
        }

    async def unresolved_number(self, phone: str) -> bool:
        return bool(
            await self.db.fetch_one(
                "SELECT 1 FROM call_contacts WHERE phone=? AND recognition='identity_mismatch' LIMIT 1",
                (phone,),
            )
        )

    async def save(self, contact_id: str | None, payload: ContactWrite) -> dict[str, Any]:
        identifier = await self.db.contact_save(
            contact_id, payload.model_dump(mode="json"), "owner"
        )
        return await self.get(identifier, private=True)

    async def save_memory(self, contact_id: str, payload: MemoryWrite) -> dict[str, Any]:
        return await self.db.contact_save_memory(contact_id, payload.model_dump(mode="json"))

    async def snapshot(self, phone: str | None, name: str | None = None) -> dict[str, Any]:
        empty: dict[str, Any] = {
            "contact_id": None,
            "revision": 0,
            "facts": [],
            "authenticated": False,
        }
        if phone is None:
            return {**empty, "recognition": "withheld"}
        matches = await self.db.contact_lookup(phone)
        if await self.unresolved_number(phone):
            return {**empty, "recognition": "shared"}
        if len(matches) != 1:
            return {**empty, "recognition": "shared" if matches else "new"}
        contact = await self.db.contact_get(matches[0]["contact_id"])
        assert contact is not None
        if name and contact["name"] and contact["name"].casefold() != name.casefold():
            return {**empty, "recognition": "different_recipient"}
        return await self.snapshot_contact(contact["contact_id"], current=contact)

    async def callback_snapshot(self, source_call_id: str) -> dict[str, Any]:
        source = await self.db.fetch_one(
            "SELECT contact_id,recognition FROM call_contacts WHERE call_id=?", (source_call_id,)
        )
        if (
            not source
            or not source["contact_id"]
            or source["recognition"] in {"shared", "identity_mismatch", "unresolved_callback"}
        ):
            return {
                "contact_id": None,
                "revision": 0,
                "facts": [],
                "authenticated": False,
                "recognition": "unresolved_callback",
            }
        snapshot = await self.snapshot_contact(source["contact_id"])
        snapshot["recognition"] = "confirmed_callback_source"
        return snapshot

    async def snapshot_contact(
        self, contact_id: str, *, current: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        contact = current if current is not None else await self.db.contact_get(contact_id)
        if contact is None:
            raise LookupError("Contact not found")
        # Bound what enters a live prompt independently of the size of the contact timeline.
        facts, used = [], 0
        source_ids = {
            fact["source_call_id"]
            for fact in contact["memory"]["facts"]
            if fact.get("source_call_id")
        }
        sources = set()
        if source_ids:
            slots = ",".join("?" for _ in source_ids)
            sources = {
                row["call_id"]
                for row in await self.db.fetch_all(
                    f"SELECT call_id FROM call_contacts WHERE contact_id=? AND call_id IN ({slots})",  # noqa: S608
                    (contact["contact_id"], *source_ids),
                )
            }
        for fact in contact["memory"]["facts"]:
            if (
                fact.get("source_call_id")
                and fact["source_call_id"] not in sources
                and not fact.get("owner_protected")
            ):
                continue
            if used + len(fact["text"]) > 6000:
                break
            facts.append(fact)
            used += len(fact["text"])
        return {
            "authenticated": False,
            "contact_id": contact["contact_id"],
            "recognition": "exact",
            "name": contact["name"],
            "background": contact["background"][:2000],
            "revision": contact["memory"]["revision"],
            "facts": facts,
        }

    async def relink(self, call_id: str, payload: ContactLink) -> dict[str, Any]:
        await self.db.contact_relink_call(call_id, payload.expected_contact_id, payload.contact_id)
        return await self.call_context(call_id)

    async def call_context(self, call_id: str) -> dict[str, Any]:
        link = await self.db.fetch_one("SELECT * FROM call_contacts WHERE call_id=?", (call_id,))
        summary = revision_record(
            await self.db.fetch_one(
                "SELECT * FROM call_summaries WHERE call_id=? ORDER BY revision DESC LIMIT 1",
                (call_id,),
            )
        )
        review = await self.db.fetch_one(
            "SELECT job_id,state,error,attempts,updated_at FROM reception_reviews WHERE call_id=?",
            (call_id,),
        )
        if link:
            link["memory_snapshot"] = json.loads(link.pop("memory_json"))
        return {
            "direction": link["direction"] if link else "outbound",
            "contact": link,
            "summary": summary,
            "review": review,
        }

    async def review_context(self, job_id: str, lease_token: str) -> dict[str, Any]:
        from app.db.engine import _iso_now

        job = await self.db.fetch_one(
            "SELECT call_id FROM reception_reviews WHERE job_id=? AND lease_token=? AND state='leased' AND lease_until>?",
            (job_id, lease_token, _iso_now()),
        )
        if job is None:
            raise ValueError("Review lease expired; claim the review again")
        call = await self.db.get_call(job["call_id"])
        assert call is not None
        plan = await self.db.get_plan_record(call["plan_id"])
        assert plan is not None
        context = await self.call_context(job["call_id"])
        contact_id = (context["contact"] or {}).get("contact_id")
        contact = await self.db.contact_get(contact_id) if contact_id else None
        if contact:
            contact.pop("private_note", None)
        business = await self.control.current("business")
        return {
            **context,
            "call_id": job["call_id"],
            "brief": plan.context.model_dump(mode="json"),
            "contact_memory": contact["memory"] if contact else {"revision": 0, "facts": []},
            "instructions": business["value"]["memory_review_instructions"],
            "callback_receipts": await self.db.fetch_all(
                "SELECT schedule_id,state,destination,window_start,window_end,json_extract(value_json,'$.objective') objective,json_extract(value_json,'$.needed_information') needed_information FROM callback_schedules WHERE source_call_id=?",
                (job["call_id"],),
            ),
        }

    async def commit_review(self, payload: ReviewCommit) -> dict[str, Any]:
        return await self.db.reception_commit_review(payload.model_dump(mode="json"))

    async def request_review(self, call_id: str) -> dict[str, Any]:
        job_id = await self.db.reception_enqueue_review(call_id, reprocess=True)
        return {"job_id": job_id, **await self.call_context(call_id)}

    async def recover_pending_reviews(self) -> None:
        rows = await self.db.fetch_all(
            "SELECT l.call_id FROM call_contacts l JOIN calls c USING(call_id) JOIN call_results r USING(call_id) "
            "LEFT JOIN reception_reviews q USING(call_id) WHERE l.automatic_review=1 AND q.job_id IS NULL "
            "AND c.state IN ('completed','failed','timed_out','transferred') LIMIT 100"
        )
        for row in rows:
            await self.db.reception_enqueue_review(row["call_id"])
