"""Shared contacts with explicit number associations and revisioned memory."""

from __future__ import annotations

import json
import secrets
from typing import TYPE_CHECKING, Any

import aiosqlite

from app.db.control import RevisionConflict
from app.db.engine import _iso_now

if TYPE_CHECKING:
    from app.db.protocols import DatabaseAccess


async def memory_record(conn: aiosqlite.Connection, contact_id: str) -> dict[str, Any]:
    cursor = await conn.execute(
        "SELECT * FROM contact_memory WHERE contact_id=? ORDER BY revision DESC LIMIT 1",
        (contact_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return {"revision": 0, "value": {"facts": [], "removed_keys": [], "removed_sources": []}}
    return {**dict(row), "value": json.loads(row["value_json"])}


def merge_review_memory(
    old: dict[str, Any], facts: list[dict[str, Any]], call_id: str
) -> dict[str, Any]:
    """Review is a patch. Owner corrections and source tombstones always win."""
    current = {fact["key"]: fact for fact in old.get("facts", [])}
    removed_keys = set(old.get("removed_keys", []))
    removed_sources = set(old.get("removed_sources", []))
    if call_id in old.get("removed_calls", []):
        return old
    for fact in facts:
        if fact["key"] in removed_keys or current.get(fact["key"], {}).get("owner_protected"):
            continue
        if any(f"{call_id}:{turn}" in removed_sources for turn in fact["evidence_turn_ids"]):
            continue
        current[fact["key"]] = {**fact, "source_call_id": call_id, "owner_protected": False}
    if len(current) > 40:
        raise ValueError("Memory has reached 40 facts; review existing facts before adding more")
    return {**old, "facts": list(current.values())}


class ContactRegistryMixin:
    async def contact_relink_call(
        self: DatabaseAccess, call_id: str, expected: str | None, contact_id: str | None
    ) -> None:
        async with self._immediate_transaction() as conn:
            cursor = await conn.execute(
                "SELECT l.contact_id,c.state FROM call_contacts l JOIN calls c USING(call_id) WHERE l.call_id=?",
                (call_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise LookupError("Call contact record not found")
            if row["state"] not in {"completed", "failed", "timed_out", "transferred"}:
                raise ValueError("Wait until the call ends before changing its contact")
            if row["contact_id"] != expected:
                raise RevisionConflict("The call's contact changed. Reload it before linking.")
            if contact_id:
                cursor = await conn.execute(
                    "SELECT 1 FROM contacts WHERE contact_id=?", (contact_id,)
                )
                if await cursor.fetchone() is None:
                    raise LookupError("Contact not found")
            await conn.execute(
                "UPDATE call_contacts SET contact_id=?,recognition='owner_linked' WHERE call_id=?",
                (contact_id, call_id),
            )
            await conn.execute(
                "INSERT INTO call_contact_history(call_id,previous_contact_id,contact_id,actor,created_at) VALUES (?,?,?,'owner',?)",
                (call_id, expected, contact_id, _iso_now()),
            )

    async def contact_get(self: DatabaseAccess, contact_id: str) -> dict[str, Any] | None:
        row = await self.fetch_one("SELECT * FROM contacts WHERE contact_id=?", (contact_id,))
        if row is None:
            return None
        row["numbers"] = await self.fetch_all(
            "SELECT phone,source,confirmed FROM contact_numbers WHERE contact_id=? ORDER BY phone",
            (contact_id,),
        )
        async with self._read_connection() as conn:
            memory = await memory_record(conn, contact_id)
        row["memory"] = {"revision": memory["revision"], **memory["value"]}
        return row

    async def contact_lookup(self: DatabaseAccess, phone: str) -> list[dict[str, Any]]:
        return await self.fetch_all(
            "SELECT c.contact_id,c.name,c.background,c.revision,n.confirmed,n.source "
            "FROM contact_numbers n JOIN contacts c USING(contact_id) WHERE n.phone=? "
            "ORDER BY c.created_at,c.contact_id",
            (phone,),
        )

    async def contact_save(
        self: DatabaseAccess, contact_id: str | None, payload: dict[str, Any], actor: str
    ) -> str:
        identifier = contact_id or "contact_" + secrets.token_urlsafe(16)
        now = _iso_now()
        async with self._immediate_transaction() as conn:
            cursor = await conn.execute(
                "SELECT revision FROM contacts WHERE contact_id=?", (identifier,)
            )
            row = await cursor.fetchone()
            revision = row[0] if row else 0
            if revision != payload["expected_revision"]:
                raise RevisionConflict("This contact changed. Reload it before saving.")
            if contact_id and row is None:
                raise LookupError("Contact not found")
            await conn.execute(
                "INSERT INTO contacts VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(contact_id) "
                "DO UPDATE SET revision=excluded.revision,name=excluded.name,background=excluded.background,"
                "private_note=excluded.private_note,updated_at=excluded.updated_at,actor=excluded.actor",
                (
                    identifier,
                    revision + 1,
                    payload["name"],
                    payload["background"],
                    payload["private_note"],
                    now,
                    now,
                    actor,
                ),
            )
            # Replacing this contact's associations never removes another person's association.
            await conn.execute("DELETE FROM contact_numbers WHERE contact_id=?", (identifier,))
            await conn.executemany(
                "INSERT INTO contact_numbers VALUES (?,?,?,?)",
                [(identifier, phone, actor, int(actor == "owner")) for phone in payload["numbers"]],
            )
            await conn.execute(
                "INSERT INTO contact_history VALUES (?,?,?,?,?)",
                (
                    identifier,
                    revision + 1,
                    json.dumps(payload, ensure_ascii=False),
                    actor,
                    now,
                ),
            )
        return identifier

    async def contact_save_memory(
        self: DatabaseAccess, contact_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._immediate_transaction() as conn:
            cursor = await conn.execute("SELECT 1 FROM contacts WHERE contact_id=?", (contact_id,))
            if await cursor.fetchone() is None:
                raise LookupError("Contact not found")
            old = await memory_record(conn, contact_id)
            if old["revision"] != payload["expected_revision"]:
                raise RevisionConflict("Contact memory changed. Reload it before saving.")
            replacement = {fact["key"]: fact for fact in payload["facts"]}
            previous = {fact["key"]: fact for fact in old["value"].get("facts", [])}
            removed_keys = set(old["value"].get("removed_keys", []))
            removed_sources = set(old["value"].get("removed_sources", []))
            removed_calls = set(old["value"].get("removed_calls", []))
            for fact in old["value"].get("facts", []):
                if (
                    fact["key"] not in replacement
                    or fact["text"] != replacement[fact["key"]]["text"]
                ):
                    if fact["key"] not in replacement:
                        removed_keys.add(fact["key"])
                    removed_sources.update(
                        f"{fact.get('source_call_id')}:{turn}"
                        for turn in fact.get("evidence_turn_ids", [])
                    )
                    if fact.get("source_call_id"):
                        removed_calls.add(fact["source_call_id"])
            # Only the owner can restore a deleted key, by explicitly supplying it here.
            removed_keys -= set(replacement)
            value = {
                "facts": [
                    previous[fact["key"]]
                    if fact["key"] in previous and fact["text"] == previous[fact["key"]]["text"]
                    else {**fact, "owner_protected": True}
                    for fact in replacement.values()
                ],
                "removed_keys": sorted(removed_keys),
                "removed_sources": sorted(removed_sources),
                "removed_calls": sorted(removed_calls),
            }
            revision = old["revision"] + 1
            await conn.execute(
                "INSERT INTO contact_memory VALUES (?,?,?,?,?)",
                (
                    contact_id,
                    revision,
                    json.dumps(value, ensure_ascii=False),
                    "owner",
                    _iso_now(),
                ),
            )
        return {"revision": revision, **value}

    async def contact_link_call(
        self: DatabaseAccess,
        *,
        call_id: str,
        phone: str | None,
        name: str,
        direction: str,
        recognition: str | None = None,
        channel_id: str | None = None,
        automatic_review: bool = True,
        frozen_memory: dict[str, Any] | None = None,
        callback_source_call_id: str | None = None,
    ) -> dict[str, Any]:
        """Link once. Shared numbers remain ambiguous rather than merging people."""
        now = _iso_now()
        async with self._immediate_transaction() as conn:
            cursor = await conn.execute("SELECT * FROM call_contacts WHERE call_id=?", (call_id,))
            existing = await cursor.fetchone()
            if existing:
                return dict(existing)
            if callback_source_call_id:
                cursor = await conn.execute(
                    "SELECT contact_id,recognition FROM call_contacts WHERE call_id=?",
                    (callback_source_call_id,),
                )
                source = await cursor.fetchone()
                memory = frozen_memory or {}
                contact_id = memory.get("contact_id")
                if (
                    not source
                    or source["contact_id"] != contact_id
                    or source["recognition"]
                    in {"shared", "identity_mismatch", "unresolved_callback"}
                ):
                    contact_id = None
                state = "confirmed_callback_source" if contact_id else "unresolved_callback"
                # A confirmed alternative destination belongs to this call, not a new identity association.
                await conn.execute(
                    "INSERT INTO call_contacts(call_id,contact_id,phone,direction,recognition,channel_id,memory_json,automatic_review,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        call_id,
                        contact_id,
                        phone,
                        direction,
                        state,
                        channel_id,
                        json.dumps(memory, ensure_ascii=False),
                        int(automatic_review),
                        now,
                    ),
                )
                cursor = await conn.execute(
                    "SELECT * FROM call_contacts WHERE call_id=?", (call_id,)
                )
                linked = await cursor.fetchone()
                assert linked is not None
                return dict(linked)
            matches = []
            if phone:
                cursor = await conn.execute(
                    "SELECT c.* FROM contacts c JOIN contact_numbers n USING(contact_id) WHERE n.phone=?",
                    (phone,),
                )
                matches = list(await cursor.fetchall())
            contact_id = None
            cursor = await conn.execute(
                "SELECT 1 FROM call_contacts WHERE phone=? AND recognition='identity_mismatch' LIMIT 1",
                (phone,),
            )
            unresolved = await cursor.fetchone() is not None
            state = recognition or (
                "exact" if len(matches) == 1 else "shared" if matches else "new"
            )
            if unresolved:
                state = "shared"
            if len(matches) == 1 and not unresolved:
                match = matches[0]
                # Outbound named recipients sharing a number must remain separate identities.
                if (
                    direction == "inbound"
                    or not name
                    or not match["name"]
                    or name.casefold() == match["name"].casefold()
                ):
                    contact_id = match["contact_id"]
                else:
                    state = "shared"
            if not matches or (direction == "outbound" and contact_id is None and name):
                same_name = [r for r in matches if name.casefold() == r["name"].casefold()]
                if len(same_name) == 1:
                    contact_id = same_name[0]["contact_id"]
                elif phone or name or direction == "inbound":
                    contact_id = "contact_" + secrets.token_urlsafe(16)
                    await conn.execute(
                        "INSERT INTO contacts VALUES (?,1,?,'','',?,?,?)",
                        (
                            contact_id,
                            name,
                            now,
                            now,
                            "call history" if not automatic_review else "call",
                        ),
                    )
                    if phone:
                        await conn.execute(
                            "INSERT INTO contact_numbers VALUES (?,?,?,0)",
                            (
                                contact_id,
                                phone,
                                "caller ID" if direction == "inbound" else "approved brief",
                            ),
                        )
            if not phone:
                state = recognition or "withheld"
            memory = frozen_memory or {"revision": 0, "facts": [], "recognition": state}
            await conn.execute(
                "INSERT INTO call_contacts(call_id,contact_id,direction,phone,recognition,channel_id,memory_json,automatic_review,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    call_id,
                    contact_id,
                    direction,
                    phone,
                    state,
                    channel_id,
                    json.dumps(memory, ensure_ascii=False),
                    int(automatic_review),
                    now,
                ),
            )
            return {
                "call_id": call_id,
                "contact_id": contact_id,
                "direction": direction,
                "phone": phone,
                "recognition": state,
                "memory": memory,
            }
