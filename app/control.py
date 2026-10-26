"""Application services for the private control panel and Hermes setup."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from datetime import UTC, datetime
from datetime import time as day_time
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from app.android_config import AndroidConfig
from app.call_ai_inputs import Mode, describe_inputs
from app.call_personalities import PERSONALITIES
from app.call_spending import CallSpending
from app.callbacks import Callbacks
from app.configuration_checks import ConfigurationChecks
from app.contact_memory import ContactMemory
from app.control_models import (
    DEFAULT_CALLING_GUIDE,
    CallingGuide,
    ConversationPrompts,
    GlobalConfiguration,
    ProfileConfiguration,
    VoiceSettings,
)
from app.db import Database
from app.errors import CallRefusal
from app.model_capabilities import validate_reasoning
from app.models import ContextPacket
from app.provider_usage import PricingSettings
from app.reception_models import BusinessSettings, CallbackPolicy, InboundSettings
from app.voice_catalog import VoiceCatalog
from app.voice_preview import VoicePreviews
from app.workflows import CallWorkflows

if TYPE_CHECKING:
    from app.android_calls import AndroidCallService


class ControlPlane:
    def __init__(self, service: AndroidCallService, db: Database) -> None:
        from app.playground import Playground

        self.service, self.db = service, db
        self._seed_lock = asyncio.Lock()
        self._seeded = False
        self.practice = Playground(self)
        self.workflows = CallWorkflows(self)
        self.contacts = ContactMemory(self)
        self.callbacks = Callbacks(self)
        self.spending = CallSpending(self)
        self.checks = ConfigurationChecks(self)
        self.catalog = VoiceCatalog(service.config, service._api_key)
        self.voice_previews = VoicePreviews(self.catalog)

    async def initialize(self) -> None:
        async with self._seed_lock:
            if self._seeded:
                return
            seeded_voice = {
                name: getattr(self.service.config, name) for name in VoiceSettings.model_fields
            }
            seeded_voice["default_call_seconds"] = min(
                seeded_voice["default_call_seconds"], seeded_voice["max_call_seconds"]
            )
            voice = VoiceSettings.model_validate(seeded_voice)
            await self.db.control_seed(
                "global", GlobalConfiguration(voice=voice).model_dump(mode="json")
            )
            await self.db.control_seed("guide", {"text": DEFAULT_CALLING_GUIDE})
            await self.db.control_seed("pricing", PricingSettings().model_dump(mode="json"))
            await self.db.control_seed("business", BusinessSettings().model_dump(mode="json"))
            await self.db.control_seed("inbound", InboundSettings().model_dump(mode="json"))
            await self.db.control_seed("callback_policy", CallbackPolicy().model_dump(mode="json"))
            for name, prompt in PERSONALITIES.items():
                value = ProfileConfiguration.model_validate(
                    {
                        "name": name.title(),
                        "personality": name,
                        "prompt": prompt,
                        "description": "Built-in starting personality",
                    }
                )
                await self.db.control_seed("profile:" + name, value.model_dump(mode="json"))
            from app.control_migration import (
                migrate_contract,
                migrate_reception_guide,
                migrate_workflow_guide,
            )

            await migrate_contract(self.db)
            await migrate_workflow_guide(self.db)
            await migrate_reception_guide(self.db)
            await self.contacts.migrate_history()
            await self.db.execute(
                "UPDATE configuration_checks SET state='interrupted',error='The service restarted before this check finished.' WHERE state IN ('running','stopping')"
            )
            self._seeded = True

    async def current(self, entity: str) -> dict[str, Any]:
        await self.initialize()
        if entity in {"outbound", "voice_defaults"}:
            row = await self.current("global")
            return self.scoped_global(row, entity)
        value = await self.db.control_current(entity)
        if value is None:
            raise LookupError("Configuration not found")
        return value

    @staticmethod
    def scoped_global(row: dict[str, Any], entity: str) -> dict[str, Any]:
        value = (
            ConversationPrompts.model_validate(row["value"]["prompts"]).model_dump(mode="json")
            if entity == "outbound"
            else row["value"]["voice"]
        )
        return {**row, "entity": entity, "value": value}

    async def profiles(self, *, archived: bool = False) -> list[dict[str, Any]]:
        await self.initialize()
        return [
            row
            for row in await self.db.control_entities()
            if row["entity"].startswith("profile:") and (archived or not row["value"]["archived"])
        ]

    async def save(
        self, entity: str, value: dict[str, Any], expected: int, actor: str
    ) -> dict[str, Any]:
        await self.initialize()
        if entity in {"outbound", "voice_defaults"}:
            current = await self.current("global")
            field = "prompts" if entity == "outbound" else "voice"
            await self.save("global", {**current["value"], field: value}, expected, actor)
            return await self.current(entity)
        if entity == "global":
            valid = GlobalConfiguration.model_validate(value)
            await self.validate_voice(valid.voice)
            await self.validate_inbound_voice(global_voice=valid.voice)
            for profile in await self.profiles():
                await self.validate_voice(
                    VoiceSettings.model_validate(
                        {**valid.voice.model_dump(), **profile["value"]["voice_overrides"]}
                    )
                )
            payload = valid.model_dump(mode="json")
        elif entity == "pricing":
            payload = PricingSettings.model_validate(value).model_dump(mode="json")
        elif entity == "guide":
            payload = CallingGuide.model_validate(value).model_dump(mode="json")
        elif entity == "business":
            business = BusinessSettings.model_validate(value)
            inbound = await self.current("inbound")
            if inbound["value"]["enabled"] and not business.configured:
                raise ValueError("Turn inbound answering off before clearing business setup")
            payload = business.model_dump(mode="json")
        elif entity == "callback_policy":
            payload = CallbackPolicy.model_validate(value).model_dump(mode="json")
        elif entity == "inbound":
            inbound_settings = InboundSettings.model_validate(value)
            await self.validate_inbound_voice(inbound=inbound_settings)
            profile = await self.current("profile:" + inbound_settings.profile_id)
            if profile["value"]["archived"]:
                raise ValueError("Choose an available personality")
            if inbound_settings.enabled:
                business = BusinessSettings.model_validate(
                    (await self.current("business"))["value"]
                )
                if not business.configured:
                    raise ValueError(
                        "Supply the business name, description, and approved facts first"
                    )
                acceptance = await self.db.fetch_one(
                    "SELECT value_json FROM reception_state WHERE name='handset_acceptance'"
                )
                if acceptance is None or not json.loads(acceptance["value_json"]).get("passed"):
                    raise ValueError(
                        "Complete the controlled DIGI handset acceptance before enabling inbound answering"
                    )
            payload = inbound_settings.model_dump(mode="json")
        elif re.fullmatch(r"profile:[a-z0-9][a-z0-9_-]{0,63}", entity):
            profile_value = ProfileConfiguration.model_validate(value)
            await self.validate_inbound_voice(
                profile=profile_value, profile_id=entity.removeprefix("profile:")
            )
            global_value = (await self.current("global"))["value"]
            await self.validate_voice(
                VoiceSettings.model_validate(
                    {**global_value["voice"], **profile_value.voice_overrides}
                )
            )
            if entity.removeprefix("profile:") in PERSONALITIES and profile_value.archived:
                raise ValueError(
                    "Built-in profiles cannot be archived because older clients use their names"
                )
            payload = profile_value.model_dump(mode="json")
        else:
            raise ValueError("Unknown configuration type")
        await self.db.control_save(entity, payload, expected, actor)
        return await self.current(entity)

    async def validate_inbound_voice(
        self,
        *,
        global_voice: VoiceSettings | None = None,
        inbound: InboundSettings | None = None,
        profile: ProfileConfiguration | None = None,
        profile_id: str | None = None,
    ) -> None:
        reception = inbound or InboundSettings.model_validate(
            (await self.current("inbound"))["value"]
        )
        if not reception.enabled or (profile_id and profile_id != reception.profile_id):
            return
        selected = profile or ProfileConfiguration.model_validate(
            (await self.current("profile:" + reception.profile_id))["value"]
        )
        if selected.archived:
            raise ValueError("Choose another inbound personality before archiving the selected one")
        base = global_voice or VoiceSettings.model_validate(
            (await self.current("global"))["value"]["voice"]
        )
        effective = VoiceSettings.model_validate({**base.model_dump(), **selected.voice_overrides})
        if reception.language not in effective.conversation_languages:
            raise ValueError(
                "The inbound greeting language must be enabled in its personality's voice settings"
            )

    async def validate_voice(self, voice: VoiceSettings) -> dict[str, Any]:
        model = await self.catalog.model(voice.dialogue_model)
        validate_reasoning(model, voice.reasoning_effort)
        if voice.inworld_model not in {"inworld-tts-2", "inworld-tts-2-flash"}:
            raise ValueError("Select Inworld TTS-2 or TTS-2 Flash.")
        await self.catalog.validate_stt(voice.soniox_model, voice.soniox_endpoint_latency_level)
        return model

    async def history(self, entity: str) -> list[dict[str, Any]]:
        await self.current(entity)
        if entity in {"outbound", "voice_defaults"}:
            return [
                self.scoped_global(row, entity) for row in await self.db.control_history("global")
            ]
        return await self.db.control_history(entity)

    async def resolve(
        self, packet: ContextPacket, *, overrides: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        global_record = await self.current("global")
        profile_record = await self.current("profile:" + (packet.profile_id or packet.personality))
        global_value = GlobalConfiguration.model_validate(
            (overrides or {}).get("global", global_record["value"])
        )
        profile = ProfileConfiguration.model_validate(
            (overrides or {}).get("profile", profile_record["value"])
        )
        if profile.archived:
            raise ValueError("This profile is archived. Select another profile.")
        voice = VoiceSettings.model_validate(
            {
                **global_value.voice.model_dump(),
                **profile.voice_overrides,
                **(overrides or {}).get("voice", {}),
            }
        )
        model = await self.validate_voice(voice)
        if packet.language is not None and packet.language not in voice.conversation_languages:
            raise CallRefusal(
                "language_required", "The initial language must be enabled in this profile"
            )
        snapshot = {
            "prompt_contract_version": 2,
            "phone_menu_version": 1,
            "profile_id": profile_record["entity"].removeprefix("profile:"),
            "profile_revision": profile_record["revision"],
            "configuration_revision": global_record["revision"],
            "profile_name": profile.name,
            "personality": profile.personality,
            "personality_prompt": profile.prompt,
            "prompts": global_value.prompts.model_dump(),
            "voice": voice.model_dump(mode="json"),
            "model_capabilities": model,
            "pricing": await self.spending.freeze(voice.model_dump(mode="json"), model),
            "unsaved": bool(overrides),
        }
        if packet.direction == "inbound":
            inbound_record, business_record = (
                await self.current("inbound"),
                await self.current("business"),
            )
            inbound = InboundSettings.model_validate(
                (overrides or {}).get("inbound", inbound_record["value"])
            )
            business = BusinessSettings.model_validate(
                (overrides or {}).get("business", business_record["value"])
            )
            snapshot.update(
                direction="inbound",
                reception_contract_version=1,
                inbound_revision=inbound_record["revision"],
                business_revision=business_record["revision"],
                business={
                    "name": business.name,
                    "description": business.description,
                    "approved_facts": business.approved_facts,
                },
                inbound=inbound.model_dump(mode="json"),
                prompts={
                    "conversation": inbound.instructions
                    + "\nCollect only relevant missing information: "
                    + "; ".join(inbound.intake_questions),
                    "opening": inbound.greeting,
                },
            )
        return snapshot

    def normalize_packet(self, packet: ContextPacket, snapshot: dict[str, Any]) -> ContextPacket:
        config = self.runtime_config(snapshot)
        requested_seconds = packet.max_duration_seconds
        if packet.direction == "inbound":
            requested_seconds = min(
                requested_seconds or config.default_call_seconds, config.max_call_seconds
            )
        return packet.model_copy(
            update={
                "personality": snapshot["personality"],
                "profile_id": snapshot["profile_id"],
                "max_duration_seconds": config.call_duration(requested_seconds),
            }
        )

    async def practice_snapshot(
        self, packet: ContextPacket, overrides: dict[str, Any] | None = None, mode: Mode = "voice"
    ) -> tuple[ContextPacket, dict[str, Any]]:
        configuration = await self.resolve(packet, overrides=overrides)
        if mode == "text":
            configuration.pop("phone_menu_version", None)
        configuration["callback_policy"] = (await self.current("callback_policy"))["value"]
        configuration["reception_contract_version"] = 1
        contact_id = (overrides or {}).get("simulation_contact_id")
        if contact_id:
            contact = await self.contacts.get(str(contact_id))
            configuration["contact_memory"] = {
                "recognition": "simulated_known",
                "name": contact["name"],
                "background": contact["background"],
                "revision": contact["memory"]["revision"],
                "facts": contact["memory"]["facts"][:10],
                "authenticated": False,
            }
        else:
            configuration["contact_memory"] = {
                "recognition": "simulated_unknown",
                "revision": 0,
                "facts": [],
                "authenticated": False,
            }
        packet = self.normalize_packet(packet, configuration)
        self.freeze_inputs(packet, configuration, mode)
        return packet, configuration

    def freeze_inputs(
        self, packet: ContextPacket, snapshot: dict[str, Any], mode: Mode = "voice"
    ) -> None:
        snapshot["approved_brief"] = packet.model_dump(mode="json")
        snapshot["effective_prompt"] = self.prompt(packet, snapshot)
        snapshot["ai_inputs"] = describe_inputs(
            packet, snapshot, self.runtime_config(snapshot), mode
        )

    async def call_inputs(self, call_id: str) -> dict[str, Any]:
        call = await self.db.get_call(call_id)
        if not call:
            raise LookupError("Call not found")
        snapshot = await self.db.control_plan(call["plan_id"])
        row = await self.db.fetch_one(
            "SELECT request_json FROM call_model_requests WHERE call_id=?", (call_id,)
        )
        latest = await asyncio.to_thread(json.loads, row["request_json"]) if row else None
        runtime = self.service._runtime
        if runtime and runtime.call_id == call_id and runtime.voice:
            latest = await asyncio.to_thread(runtime.voice.request_trace.view)
        return {
            "configured": snapshot.get("ai_inputs") if snapshot else None,
            "last_request": latest,
            "saved_system_prompt": snapshot.get("effective_prompt") if snapshot else None,
        }

    @staticmethod
    def reference(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
        if not snapshot:
            return None
        return {
            key: snapshot[key]
            for key in [
                "profile_id",
                "profile_revision",
                "configuration_revision",
                "profile_name",
                "prompt_contract_version",
            ]
            if key in snapshot
        }

    def runtime_config(self, snapshot: dict[str, Any] | None) -> AndroidConfig:
        if not snapshot:
            return self.service.config
        voice = VoiceSettings.model_validate(snapshot["voice"])
        return AndroidConfig.model_validate(
            {
                **self.service.config.model_dump(),
                **voice.model_dump(),
                "dialogue_supported_parameters": (snapshot.get("model_capabilities") or {}).get(
                    "supported_parameters"
                ),
            }
        )

    @staticmethod
    def prompt(packet: ContextPacket, snapshot: dict[str, Any]) -> str:
        if isinstance(snapshot.get("effective_prompt"), str):
            return str(snapshot["effective_prompt"])
        from app.reception_prompts import effective_sections

        return "".join(section["text"] for section in effective_sections(packet, snapshot))

    async def setup(self, *, from_mcp: bool = False) -> dict[str, Any]:
        if from_mcp:
            await self.db.control_seen("hermes_setup")
        global_record, guide = await self.current("global"), await self.current("guide")
        return {
            "guide": guide["value"]["text"],
            "guide_revision": guide["revision"],
            "owner": {
                "display_name": self.service.settings.owner_display_name,
                "timezone": self.access_timezone(),
            },
            "profiles": [
                {
                    "id": row["entity"].removeprefix("profile:"),
                    "revision": row["revision"],
                    "name": row["value"]["name"],
                    "description": row["value"]["description"],
                    "personality": row["value"]["personality"],
                }
                for row in await self.profiles()
            ],
            "defaults": global_record["value"]["voice"],
            "destination_policy": "any_valid_international_number"
            if self.service.config.allow_any_destination
            else "configured_allowlist",
            "source_line": self.service.config.line_label,
            "reception": {
                "business": (await self.current("business"))["value"],
                "inbound": (await self.current("inbound"))["value"],
                "callback_policy": (await self.current("callback_policy"))["value"],
            },
            "owner_numbers": "Omit callback_number and owner_phone; the server resolves them.",
        }

    def access_timezone(self) -> str:
        from app.control_auth import read_access

        access = read_access(self.service.config)
        return access.timezone if access else "UTC"

    async def status(self) -> dict[str, Any]:
        phone = self.service.phone.read()
        try:
            registered = await self.service.asterisk.gateway_registered()
        except Exception:
            registered = False
        active = await self.db.list_nonterminal_calls()
        return {
            "ready": self.service.supervision_ready()
            and not self.service.phone.admission_problem()
            and registered,
            "sip_registered": registered,
            "source_line": self.service.config.line_label,
            "phone": {
                key: (phone or {}).get(key) for key in ["phase", "control_ready", "call_ready"]
            },
            "active_call": active[0]["call_id"] if active else None,
            "practice_active": self.practice.active is not None,
            "check_active": self.checks.active is not None,
            "check_id": self.checks.active.id if self.checks.active else None,
            "review_worker": {
                "pending": await self.db.reception_review_pending(),
                "last_queue_check": await self.db.fetch_one(
                    "SELECT observed_at FROM control_activity WHERE name='hermes_review_queue'"
                ),
            },
            "last_hermes_request": await self.db.fetch_one(
                "SELECT observed_at FROM control_activity WHERE name='hermes_setup'"
            ),
            "measured_at": datetime.now(UTC).isoformat(),
            "inbound": await self.service.inbound.availability(),
        }

    async def calls(
        self,
        *,
        query: str = "",
        state: str = "",
        outcome: str = "",
        direction: str = "",
        language: str = "",
        profile: str = "",
        start: str = "",
        end: str = "",
        cursor: str = "",
        limit: int = 40,
    ) -> dict[str, Any]:
        clauses, values = ["1=1"], []
        if direction:
            if direction not in {"inbound", "outbound"}:
                raise ValueError("Unknown call direction")
            clauses.append(
                "COALESCE((SELECT direction FROM call_contacts l WHERE l.call_id=c.call_id),'outbound')=?"
            )
            values.append(direction)
        if outcome:
            if outcome not in {"achieved", "declined", "incomplete", "uncertain", "not_recorded"}:
                raise ValueError("Unknown outcome filter")
            clauses.append(
                "COALESCE((SELECT json_extract(a.value_json,'$.status') FROM call_assessments a WHERE a.call_id=c.call_id ORDER BY a.revision DESC LIMIT 1),'not_recorded')=?"
            )
            values.append(outcome)
        if query:
            needle = (
                "%"
                + query.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
            )
            clauses.append(
                "(casefold(p.context_json) LIKE ? ESCAPE '\\' OR EXISTS "
                "(SELECT 1 FROM transcripts t WHERE t.call_id=c.call_id AND casefold(t.text) LIKE ? ESCAPE '\\'))"
            )
            values.extend([needle, needle])
        for expr, value in [
            ("c.state", state),
            ("json_extract(p.context_json,'$.language')", language),
            (
                "COALESCE(json_extract(pc.snapshot_json,'$.profile_id'),json_extract(p.context_json,'$.personality'))",
                profile,
            ),
        ]:
            if value:
                clauses.append(expr + "=?")
                values.append(value)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
            start = (
                datetime.combine(
                    datetime.fromisoformat(start).date(),
                    day_time.min,
                    ZoneInfo(self.access_timezone()),
                )
                .astimezone(UTC)
                .isoformat()
            )
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
            end = (
                datetime.combine(
                    datetime.fromisoformat(end).date(),
                    day_time.max,
                    ZoneInfo(self.access_timezone()),
                )
                .astimezone(UTC)
                .isoformat()
            )
        for op, value in [(">=", start), ("<=", end)]:
            if value:
                clauses.append("c.created_at" + op + "?")
                values.append(value)
        if cursor:
            try:
                stamp, call_id = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
                if not isinstance(stamp, str) or not isinstance(call_id, str):
                    raise ValueError("Invalid cursor")
            except (ValueError, TypeError, UnicodeError) as exc:
                raise ValueError("Invalid call history cursor") from exc
            clauses.append("(c.created_at<? OR (c.created_at=? AND c.call_id<?))")
            values.extend([stamp, stamp, call_id])
        sql = "SELECT c.call_id,c.state,c.created_at,c.answered_at,c.ended_at,c.duration_seconds,c.termination_reason,p.context_json,pc.snapshot_json,(SELECT json_extract(a.value_json,'$.status') FROM call_assessments a WHERE a.call_id=c.call_id ORDER BY a.revision DESC LIMIT 1) outcome_status,(SELECT COUNT(*) FROM call_followups f WHERE f.source_call_id=c.call_id AND f.status IN ('ready','needs_information')) pending_followups "
        sql += (
            "FROM calls c JOIN plans p ON p.plan_id=c.plan_id LEFT JOIN plan_configurations pc ON pc.plan_id=p.plan_id WHERE "
            + " AND ".join(clauses)
        )
        rows = await self.db.fetch_all(
            sql + " ORDER BY c.created_at DESC,c.call_id DESC LIMIT ?", (*values, limit + 1)
        )
        result = []
        for row in rows[:limit]:
            context = json.loads(row.pop("context_json"))
            row.pop("context", None)
            snapshot = json.loads(row.pop("snapshot_json") or "{}")
            result.append(
                {
                    **row,
                    "target": context["target"],
                    "objective": context["objective"],
                    "language": context.get("language"),
                    "profile_id": snapshot.get("profile_id") or context.get("personality"),
                    "personality": context.get("personality"),
                    "direction": context.get("direction", "outbound"),
                }
            )
        return {
            "calls": result,
            "next_cursor": base64.urlsafe_b64encode(
                json.dumps([rows[limit - 1]["created_at"], rows[limit - 1]["call_id"]]).encode()
            ).decode()
            if len(rows) > limit
            else None,
        }

    async def detail(self, call_id: str, after: int = 0) -> dict[str, Any]:
        row = await self.db.get_call(call_id)
        if row is None:
            raise LookupError("Call not found")
        plan = await self.db.get_plan_record(row["plan_id"])
        result = await self.service.get_result(call_id, after_sequence=after)
        snapshot = await self.db.control_plan(row["plan_id"])
        metrics = await self.db.fetch_all(
            "SELECT data_json,created_at FROM voice_measurements WHERE call_id=? ORDER BY id",
            (call_id,),
        )
        return {
            **result,
            "brief": plan.context.model_dump(mode="json") if plan else None,
            "configuration": snapshot,
            "prompt": self.prompt(plan.context, snapshot) if plan and snapshot else None,
            "annotation": await self.db.control_annotation(call_id),
            "assessment": await self.workflows.assessment(call_id),
            "followups": (await self.workflows.list_followups(call_id=call_id, status="all"))[
                "followups"
            ],
            "timing": {
                key: row.get(key)
                for key in [
                    "created_at",
                    "answered_at",
                    "ended_at",
                    "duration_seconds",
                    "termination_reason",
                ]
            },
            "latency": [
                {**json.loads(m["data_json"]), "created_at": m["created_at"]} for m in metrics
            ],
        }

    async def annotate(self, call_id: str, note: str, tags: list[str]) -> None:
        if not await self.db.get_call(call_id):
            raise LookupError("Call not found")
        await self.db.control_annotate(call_id, note, tags)
