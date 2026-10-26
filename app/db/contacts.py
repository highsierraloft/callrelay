"""Persist recipient opt-outs separately from the deployment's destination policy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.db.engine import _iso_now

if TYPE_CHECKING:
    from app.db.protocols import DatabaseAccess


class ContactsMixin:
    async def opt_out_call(self: DatabaseAccess, call_id: str, phone: str | None) -> None:
        now = _iso_now()
        async with self._immediate_transaction() as conn:
            await conn.execute("INSERT OR IGNORE INTO call_opt_outs VALUES (?,?)", (call_id, now))
            if phone:
                await conn.execute(
                    "INSERT OR IGNORE INTO destination_suppressions VALUES (?,?,?)",
                    (phone, call_id, now),
                )
            await conn.execute(
                "UPDATE callback_schedules SET state='cancelled',reason='Caller requested no further calls',updated_at=? "
                "WHERE dial_claimed=0 AND state IN ('scheduled','preparing','needs_information') AND (source_call_id=? OR destination=? OR json_extract(value_json,'$.original_number')=?)",
                (now, call_id, phone, phone),
            )

    async def record_call_opt_out(self: DatabaseAccess, call_id: str) -> None:
        await self.execute(
            "INSERT OR IGNORE INTO call_opt_outs VALUES (?,?)", (call_id, _iso_now())
        )

    async def suppress_destination(self: DatabaseAccess, phone: str, call_id: str) -> None:
        await self.execute(
            "INSERT OR IGNORE INTO destination_suppressions(phone, call_id, created_at) VALUES (?, ?, ?)",
            (phone, call_id, _iso_now()),
        )

    async def is_destination_suppressed(self: DatabaseAccess, phone: str) -> bool:
        return (
            await self.fetch_one(
                "SELECT phone FROM destination_suppressions WHERE phone=?", (phone,)
            )
            is not None
        )

    async def call_recorded_opt_out(self: DatabaseAccess, call_id: str) -> bool:
        return (
            await self.fetch_one(
                "SELECT 1 FROM destination_suppressions WHERE call_id=? UNION ALL SELECT 1 FROM call_opt_outs WHERE call_id=? LIMIT 1",
                (call_id, call_id),
            )
            is not None
        )
