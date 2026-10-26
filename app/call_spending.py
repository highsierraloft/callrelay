"""Recorded provider usage and frozen estimates for telephone calls."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from app.provider_usage import PricingSettings, estimate_cost

if TYPE_CHECKING:
    from app.control import ControlPlane


class CallSpending:
    def __init__(self, control: ControlPlane) -> None:
        self.control = control
        self.db = control.db

    async def freeze(self, voice: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
        record = await self.control.current("pricing")
        return {
            "revision": record["revision"],
            "rates": PricingSettings.model_validate(record["value"]).model_dump(),
            "openrouter": model.get("pricing") or {},
            "dialogue_model": voice["dialogue_model"],
            "tts_model": voice["inworld_model"],
            "stt_model": voice["soniox_model"],
            "captured_at": datetime.now(UTC).isoformat(),
            "sources": [
                "https://soniox.com/pricing",
                "https://inworld.ai/pricing",
                "https://openrouter.ai/api/v1/models",
            ],
        }

    async def for_call(self, call_id: str) -> dict[str, Any]:
        row = await self.db.fetch_one(
            "SELECT estimate_json FROM call_provider_usage WHERE call_id=?", (call_id,)
        )
        if row:
            cost: dict[str, Any] = json.loads(row["estimate_json"])
            return cost
        result = estimate_cost(None, None)
        call = await self.db.get_call(call_id)
        if call:
            snapshot = await self.db.control_plan(call["plan_id"])
            ended = datetime.fromisoformat(call["ended_at"]) if call.get("ended_at") else None
            if (
                snapshot
                and snapshot.get("pricing")
                and (ended is None or (datetime.now(UTC) - ended).total_seconds() < 30)
            ):
                result.update(
                    coverage="pending",
                    note="Provider usage will be available after the call closes.",
                )
        return result

    async def monthly(self, month: str, offset: int = 0) -> dict[str, Any]:
        try:
            year, number = map(int, month.split("-"))
            zone = ZoneInfo(self.control.access_timezone())
            start = datetime(year, number, 1, tzinfo=zone)
            end = datetime(year + (number == 12), 1 if number == 12 else number + 1, 1, tzinfo=zone)
        except (ValueError, TypeError) as exc:
            raise ValueError("Use a valid month in YYYY-MM format") from exc
        bounds = (start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat())
        joins = "FROM calls c LEFT JOIN call_provider_usage u USING(call_id) WHERE c.created_at>=? AND c.created_at<? "
        groups = await self.db.fetch_all(
            "SELECT COALESCE(json_extract(u.estimate_json,'$.coverage'),'unavailable') coverage,COUNT(*) count,SUM(COALESCE(json_extract(u.estimate_json,'$.known_subtotal_usd'),0)) subtotal "
            + joins
            + " GROUP BY coverage",
            bounds,
        )
        providers = await self.db.fetch_all(
            "SELECT json_extract(p.value,'$.provider') provider,SUM(json_extract(p.value,'$.usd')) usd FROM calls c JOIN call_provider_usage u USING(call_id),json_each(u.estimate_json,'$.providers') p WHERE c.created_at>=? AND c.created_at<? AND json_extract(p.value,'$.usd') IS NOT NULL GROUP BY provider",
            bounds,
        )
        rows = await self.db.fetch_all(
            "SELECT c.call_id,c.state,c.created_at,c.duration_seconds,json_extract(p.context_json,'$.target.name') target,u.estimate_json FROM calls c JOIN plans p USING(plan_id) LEFT JOIN call_provider_usage u USING(call_id) WHERE c.created_at>=? AND c.created_at<? ORDER BY c.created_at DESC,c.call_id DESC LIMIT 41 OFFSET ?",
            (*bounds, offset),
        )
        counts = {"complete": 0, "partial": 0, "unavailable": 0}
        for group in groups:
            counts[group["coverage"]] = group["count"]
        calls = []
        for row in rows[:40]:
            raw = row.pop("estimate_json")
            calls.append({**row, "cost": json.loads(raw) if raw else estimate_cost(None, None)})
        return {
            "month": month,
            "timezone": str(zone),
            "currency": "USD",
            "scope": "Telephone calls only; excludes practice, configuration checks, carrier charges, taxes, and plan fees.",
            "call_count": sum(counts.values()),
            "coverage": counts,
            "known_subtotal_usd": round(sum(g["subtotal"] or 0 for g in groups), 8),
            "providers": providers,
            "calls": calls,
            "next_offset": offset + 40 if len(rows) > 40 else None,
        }
