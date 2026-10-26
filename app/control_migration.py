"""Upgrade current configuration without rewriting historical revisions or plans."""

from __future__ import annotations

from app import control_legacy
from app.call_personalities import PERSONALITIES
from app.control_models import DEFAULT_CALLING_GUIDE, GlobalConfiguration
from app.db import Database
from app.voice_prompts import DEFAULT_CONVERSATION, DEFAULT_OPENING


async def migrate_contract(db: Database) -> None:
    current = await db.control_current("global")
    if not current or current["value"].get("prompt_contract_version") == 2:
        return
    old = current["value"]
    value = GlobalConfiguration.model_validate(old).model_dump(mode="json")
    for name, previous, replacement in [
        ("conversation", control_legacy.DEFAULT_CONVERSATION, DEFAULT_CONVERSATION),
        ("opening", control_legacy.DEFAULT_OPENING, DEFAULT_OPENING),
    ]:
        if value["prompts"][name] == previous:
            value["prompts"][name] = replacement
    changes = [("global", value, current["revision"])]
    guide = await db.control_current("guide")
    if guide and guide["value"]["text"] == control_legacy.DEFAULT_CALLING_GUIDE:
        changes.append(("guide", {"text": DEFAULT_CALLING_GUIDE}, guide["revision"]))
    for name, prompt in PERSONALITIES.items():
        profile = await db.control_current("profile:" + name)
        if profile and profile["value"]["prompt"] == control_legacy.PERSONALITIES[name]:
            if profile["value"]["prompt"] != prompt:
                changes.append(
                    (profile["entity"], {**profile["value"], "prompt": prompt}, profile["revision"])
                )
    await db.control_migrate(changes)


async def migrate_workflow_guide(db: Database) -> None:
    guide = await db.control_current("guide")
    if guide and guide["value"]["text"] == control_legacy.WORKFLOW_PREVIOUS_GUIDE:
        await db.control_save(
            "guide",
            {"text": DEFAULT_CALLING_GUIDE},
            guide["revision"],
            "call workflow guide migration",
        )


async def migrate_reception_guide(db: Database) -> None:
    from app.reception_instructions import RECEPTION_GUIDE

    if await db.fetch_one("SELECT 1 FROM reception_state WHERE name='guide_migrated'"):
        return
    guide = await db.control_current("guide")
    if guide and "Reception, contacts, and confirmed callbacks" not in guide["value"]["text"]:
        await db.control_save(
            "guide",
            {"text": guide["value"]["text"] + "\n" + RECEPTION_GUIDE},
            guide["revision"],
            "reception workflow migration",
        )
    await db.execute("INSERT OR IGNORE INTO reception_state VALUES ('guide_migrated','true')")
