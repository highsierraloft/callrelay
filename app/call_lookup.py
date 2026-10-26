"""Read-only recovery of an uncertain start using the original prepared plan."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.db import Database


async def lookup_plan(db: Database, plan_id: str) -> dict[str, Any]:
    plan = await db.get_plan_record(plan_id)
    if plan is None:
        raise LookupError("Plan not found")
    if plan.call_id:
        return {"plan_id": plan_id, "call_id": plan.call_id}
    return {
        "plan_id": plan_id,
        "call_id": None,
        "state": "expired"
        if datetime.fromisoformat(plan.expires_at) <= datetime.now(UTC)
        else "prepared",
        "expires_at": plan.expires_at,
    }
