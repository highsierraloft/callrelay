"""Versioned configuration and private review metadata."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from app.db.engine import _iso_now
from app.provider_usage import estimate_cost

if TYPE_CHECKING:
    from app.db.protocols import DatabaseAccess


class RevisionConflict(ValueError):
    pass


CONTROL_SCHEMA = """
CREATE TABLE IF NOT EXISTS control_revisions (
    entity TEXT NOT NULL, revision INTEGER NOT NULL, value_json TEXT NOT NULL,
    actor TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(entity, revision)
);
CREATE TABLE IF NOT EXISTS plan_configurations (
    plan_id TEXT PRIMARY KEY REFERENCES plans(plan_id), snapshot_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS call_annotations (
    call_id TEXT PRIMARY KEY REFERENCES calls(call_id), note TEXT NOT NULL,
    tags_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS voice_measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT NOT NULL REFERENCES calls(call_id),
    data_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS control_activity (
    name TEXT PRIMARY KEY, observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS configuration_checks (
 check_id TEXT PRIMARY KEY, state TEXT NOT NULL, configuration_json TEXT NOT NULL, result_json TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS call_provider_usage (
 call_id TEXT PRIMARY KEY REFERENCES calls(call_id), usage_json TEXT NOT NULL, pricing_json TEXT, estimate_json TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS call_model_requests (
    call_id TEXT PRIMARY KEY REFERENCES calls(call_id), request_json TEXT NOT NULL
);
"""


def revision_record(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result["value"] = json.loads(result.pop("value_json"))
    return result


class ControlMixin:
    async def control_usage(
        self: DatabaseAccess, call_id: str, usage: dict[str, Any], pricing: dict[str, Any] | None
    ) -> None:
        await self.execute(
            "INSERT INTO call_provider_usage VALUES (?,?,?,?,?) ON CONFLICT(call_id) DO UPDATE SET usage_json=excluded.usage_json,pricing_json=excluded.pricing_json,estimate_json=excluded.estimate_json,recorded_at=excluded.recorded_at",
            (
                call_id,
                json.dumps(usage),
                json.dumps(pricing) if pricing else None,
                json.dumps(estimate_cost(usage, pricing)),
                _iso_now(),
            ),
        )

    async def control_model_request(
        self: DatabaseAccess, call_id: str, request: dict[str, Any]
    ) -> None:
        encoded = await asyncio.to_thread(json.dumps, request, ensure_ascii=False)
        await self.execute(
            "INSERT INTO call_model_requests VALUES (?,?) ON CONFLICT(call_id) "
            "DO UPDATE SET request_json=excluded.request_json",
            (call_id, encoded),
        )

    async def control_seed(self: DatabaseAccess, entity: str, value: dict[str, Any]) -> None:
        await self.execute(
            "INSERT OR IGNORE INTO control_revisions VALUES (?,1,?,'initial deployment',?)",
            (entity, json.dumps(value, ensure_ascii=False), _iso_now()),
        )

    async def control_current(self: DatabaseAccess, entity: str) -> dict[str, Any] | None:
        return revision_record(
            await self.fetch_one(
                "SELECT * FROM control_revisions WHERE entity=? ORDER BY revision DESC LIMIT 1",
                (entity,),
            )
        )

    async def control_history(self: DatabaseAccess, entity: str) -> list[dict[str, Any]]:
        rows = await self.fetch_all(
            "SELECT * FROM control_revisions WHERE entity=? ORDER BY revision DESC LIMIT 100",
            (entity,),
        )
        return [record for row in rows if (record := revision_record(row)) is not None]

    async def control_entities(self: DatabaseAccess) -> list[dict[str, Any]]:
        rows = await self.fetch_all(
            "SELECT r.* FROM control_revisions r JOIN (SELECT entity,MAX(revision) revision "
            "FROM control_revisions GROUP BY entity) h USING(entity,revision) ORDER BY r.entity"
        )
        return [record for row in rows if (record := revision_record(row)) is not None]

    async def control_save(
        self: DatabaseAccess, entity: str, value: dict[str, Any], expected: int, actor: str
    ) -> None:
        async with self._immediate_transaction() as conn:
            cursor = await conn.execute(
                "SELECT COALESCE(MAX(revision),0) FROM control_revisions WHERE entity=?", (entity,)
            )
            row = await cursor.fetchone()
            assert row is not None
            current = row[0]
            if current != expected:
                raise RevisionConflict(
                    "This configuration changed in another window. Reload it before saving."
                )
            await conn.execute(
                "INSERT INTO control_revisions VALUES (?,?,?,?,?)",
                (entity, current + 1, json.dumps(value, ensure_ascii=False), actor, _iso_now()),
            )

    async def control_migrate(
        self: DatabaseAccess, changes: list[tuple[str, dict[str, Any], int]]
    ) -> None:
        """Save the complete contract migration atomically, with optimistic checks."""
        async with self._immediate_transaction() as conn:
            for entity, value, expected in changes:
                cursor = await conn.execute(
                    "SELECT MAX(revision) FROM control_revisions WHERE entity=?", (entity,)
                )
                row = await cursor.fetchone()
                if row is None or row[0] != expected:
                    raise RevisionConflict("Configuration changed during prompt migration")
                await conn.execute(
                    "INSERT INTO control_revisions VALUES (?,?,?,?,?)",
                    (
                        entity,
                        expected + 1,
                        json.dumps(value, ensure_ascii=False),
                        "prompt contract v2 migration",
                        _iso_now(),
                    ),
                )

    async def control_plan(self: DatabaseAccess, plan_id: str) -> dict[str, Any] | None:
        row = await self.fetch_one(
            "SELECT snapshot_json FROM plan_configurations WHERE plan_id=?", (plan_id,)
        )
        return json.loads(row["snapshot_json"]) if row else None

    async def control_annotation(self: DatabaseAccess, call_id: str) -> dict[str, Any]:
        row = await self.fetch_one(
            "SELECT note,tags_json FROM call_annotations WHERE call_id=?", (call_id,)
        )
        return (
            {"note": row["note"], "tags": json.loads(row["tags_json"])}
            if row
            else {"note": "", "tags": []}
        )

    async def control_annotate(
        self: DatabaseAccess, call_id: str, note: str, tags: list[str]
    ) -> None:
        await self.execute(
            "INSERT INTO call_annotations VALUES (?,?,?,?) ON CONFLICT(call_id) "
            "DO UPDATE SET note=excluded.note,tags_json=excluded.tags_json,updated_at=excluded.updated_at",
            (call_id, note, json.dumps(tags), _iso_now()),
        )

    async def control_measure(self: DatabaseAccess, call_id: str, data: dict[str, Any]) -> None:
        await self.execute(
            "INSERT INTO voice_measurements(call_id,data_json,created_at) VALUES (?,?,?)",
            (call_id, json.dumps(data), _iso_now()),
        )

    async def control_seen(self: DatabaseAccess, name: str) -> None:
        await self.execute(
            "INSERT INTO control_activity VALUES (?,?) ON CONFLICT(name) "
            "DO UPDATE SET observed_at=excluded.observed_at",
            (name, _iso_now()),
        )
