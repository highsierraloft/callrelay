"""Bounded Pipecat Evals for saved settings and owner drafts. No dialing capability."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import secrets
import socket
from collections.abc import Generator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

import uvicorn
from fastapi import FastAPI, WebSocket

from app.callback_voice import PracticeCallbacks
from app.db.deployment import _lock_is_active
from app.models import ContextPacket

if TYPE_CHECKING:
    from app.control import ControlPlane

_JUDGE_KEY: ContextVar[str] = ContextVar("configuration_check_key")
JUDGE_MODEL = "openai/gpt-6-luna"
CASE_NAMES = [
    "corrected_details",
    "missing_facts",
    "unauthorized_commitment",
    "refusal",
    "language_switching",
    "premature_ending",
]
INBOUND_CASE_NAMES = [
    "inbound_intake",
    "inbound_returning",
    "inbound_missing_facts",
    "inbound_injection",
    "inbound_language",
    "inbound_callback",
]


def scenario_names(configuration: dict[str, Any]) -> list[str]:
    return INBOUND_CASE_NAMES if configuration.get("direction") == "inbound" else CASE_NAMES


def check_judge(config: dict[str, Any]) -> Any:
    from pipecat.services.openai.llm import OpenAILLMService

    return OpenAILLMService(
        api_key=_JUDGE_KEY.get(),
        base_url="https://openrouter.ai/api/v1",
        settings=OpenAILLMService.Settings(
            model=JUDGE_MODEL,
            temperature=0,
            extra={"extra_body": {"reasoning": {"effort": "none"}}},
        ),
    )


class _CheckServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        # The production server owns process signals; this local diagnostic listener does not.
        yield


@dataclass
class CheckRun:
    id: str
    configuration: dict[str, Any]
    state: str = "running"
    results: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    task: asyncio.Task[None] | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class ConfigurationChecks:
    def __init__(self, control: ControlPlane) -> None:
        self.control = control
        self.active: CheckRun | None = None

    async def start(
        self, packet: ContextPacket, overrides: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        async with self.control.service._operations:
            if (
                self.active
                or self.control.practice.active
                or await self.control.db.list_nonterminal_calls()
            ):
                raise ValueError(
                    "A call, practice session, or configuration check is already active"
                )
            lease = await self.control.db.fetch_one(
                "SELECT locked,locked_at FROM deployment_control WHERE singleton=1"
            )
            if lease and lease["locked"] and _lock_is_active(lease["locked_at"]):
                raise ValueError("The calling service is being updated. Try again shortly.")
            packet, configuration = await self.control.practice_snapshot(packet, overrides)
            behavior = {k: configuration[k] for k in ["prompts", "personality_prompt", "voice"]}
            configuration["check_fingerprint"] = hashlib.sha256(
                json.dumps(behavior, sort_keys=True).encode()
            ).hexdigest()
            job = CheckRun("check_" + secrets.token_urlsafe(18), configuration)
            self.active = job
            try:
                await self._persist(job)
                job.task = asyncio.create_task(self._run(job))
            except BaseException:
                self.active = None
                raise
            return await self.get(job.id)

    async def _persist(self, job: CheckRun) -> None:
        now = datetime.now(UTC).isoformat()
        await self.control.db.execute(
            "INSERT INTO configuration_checks VALUES (?,?,?,?,?,?,?) ON CONFLICT(check_id) DO UPDATE SET state=excluded.state,result_json=excluded.result_json,error=excluded.error,updated_at=excluded.updated_at",
            (
                job.id,
                job.state,
                json.dumps(job.configuration, ensure_ascii=False),
                json.dumps(job.results, ensure_ascii=False),
                job.error,
                job.created_at,
                now,
            ),
        )

    async def get(self, check_id: str) -> dict[str, Any]:
        row = await self.control.db.fetch_one(
            "SELECT * FROM configuration_checks WHERE check_id=?", (check_id,)
        )
        if row is None:
            raise LookupError("Configuration check not found")
        return {
            "id": row["check_id"],
            "state": row["state"],
            "configuration": json.loads(row["configuration_json"]),
            "results": json.loads(row["result_json"]),
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "scenario_count": len(scenario_names(json.loads(row["configuration_json"]))),
            "scenario_names": scenario_names(json.loads(row["configuration_json"])),
            "judge_model": JUDGE_MODEL,
            "scope": "Synthetic text conversations through the voice pipeline. No telephone is dialed. This checks prompt/model behavior, not audible quality or carrier latency. Provider usage applies and is separate from telephone cost totals.",
        }

    async def recent(self, profile_id: str) -> dict[str, Any]:
        rows = await self.control.db.fetch_all(
            "SELECT check_id FROM configuration_checks WHERE json_extract(configuration_json,'$.profile_id')=? ORDER BY created_at DESC LIMIT 5",
            (profile_id,),
        )
        return {
            "checks": [await self.get(r["check_id"]) for r in rows],
            "active_id": self.active.id if self.active else None,
        }

    async def cancel(self, check_id: str | None = None) -> None:
        job = self.active
        if job is None or (check_id is not None and job.id != check_id):
            return
        if job.task:
            if not job.task.cancelling():
                job.task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(job.task), 20)
            except asyncio.CancelledError:
                pass
            except TimeoutError:
                job.state = "stopping"
                job.error = "The check is still releasing its provider connections. Try Stop Check again shortly."
                await self._persist(job)

    async def _run(self, job: CheckRun) -> None:
        try:
            async with asyncio.timeout(300):
                await self._execute(job)
            job.state = (
                "passed"
                if len(job.results) == len(scenario_names(job.configuration))
                and all(r["status"] in {"passed", "skipped"} for r in job.results)
                else "failed"
            )
        except asyncio.CancelledError:
            job.state = "cancelled"
        except TimeoutError:
            job.state = "failed"
            job.error = (
                "The check reached its five-minute limit. Completed scenario results are retained."
            )
        except Exception:
            job.state = "failed"
            job.error = (
                "The provider or check connection failed. Check the saved settings and try again."
            )
        finally:
            try:
                await self._persist(job)
            finally:
                if self.active is job:
                    self.active = None

    async def _execute(self, job: CheckRun) -> None:
        import yaml  # type: ignore[import-untyped]
        from pipecat.evals.fastapi_transport import EvalFastAPIWebsocketTransport
        from pipecat.evals.scenario import EvalScenarioFile, EvalScriptScenario
        from pipecat.evals.serializer import EvalSerializer, parse_eval_connect
        from pipecat.evals.session import EvalSession, EvalSessionParams
        from pipecat.evals.transport import EvalTransportParams

        from app.android_voice import AndroidVoiceSession, VoiceCallbacks

        raw = yaml.safe_load((Path(__file__).with_name("check_scenarios.yaml")).read_text())
        names = scenario_names(job.configuration)
        cases = [c for name in names for c in raw["cases"] if c["name"] == name]
        if len(cases) != len(names):
            raise ValueError("The check scenario set is incomplete")
        config = self.control.runtime_config(job.configuration)
        allowed = set(config.conversation_languages)
        for case in cases:
            if case["name"] in {"language_switching", "inbound_language"}:
                codes = ["en", "es", "ru", "uk", "en"]
                case["turns"] = [
                    turn
                    for code, turn in zip(codes, case["turns"], strict=True)
                    if code in allowed or "user" not in turn
                ]
        by_name = {c["name"]: c for c in cases}
        app = FastAPI()
        capability = secrets.token_urlsafe(24)
        running: dict[asyncio.Task[Any], AndroidVoiceSession] = {}
        errors: set[str] = set()
        token = _JUDGE_KEY.set(self.control.service._api_key)

        @app.websocket("/" + capability + "/{name}")
        async def conversation(ws: WebSocket, name: str) -> None:
            if name not in by_name:
                await ws.close(code=1008)
                return
            await ws.accept()
            flags = parse_eval_connect(await ws.receive_json())
            if flags is None:
                await ws.close(code=1008)
                return
            case = by_name[name]
            packet = ContextPacket.model_validate(
                {
                    "owner": {"display_name": "Alex", "timezone": "Europe/Madrid"},
                    "target": {
                        "name": "Sam",
                        "phone": "+12025550123",
                        "background": "Sam runs Example Cafe. Alex met Sam at a public demo last week.",
                    },
                    "language": config.conversation_languages[0],
                    "personality": job.configuration["personality"],
                    "escalation": {"mode": "end_call"},
                    **case["brief"],
                }
            )
            snapshot = copy.deepcopy(job.configuration)
            snapshot.pop("effective_prompt", None)
            if snapshot.get("direction") == "inbound":
                packet = packet.model_copy(update={"direction": "inbound"})
            if case.get("personality_prompt"):
                snapshot["personality_prompt"] = case["personality_prompt"]
            if case.get("memory"):
                snapshot["contact_memory"] = case["memory"]
            packet = self.control.normalize_packet(packet, snapshot)
            self.control.freeze_inputs(packet, snapshot)
            transport = EvalFastAPIWebsocketTransport(
                ws,
                EvalTransportParams(
                    serializer=EvalSerializer(),  # type: ignore[no-untyped-call]
                    audio_in_enabled=True,
                    audio_out_enabled=True,
                    audio_in_sample_rate=16000,
                    audio_out_sample_rate=24000,
                ),
                flags,
            )

            async def finish(guard: Any) -> bool:
                return bool(guard())

            async def suppress_recipient() -> None:
                return None

            callbacks = PracticeCallbacks(snapshot["callback_policy"], packet.language or "en")

            def observed(speaker: str, text: str, source: str, interrupted: bool) -> None:
                callbacks.observe(speaker, text, interrupted)

            voice = AndroidVoiceSession(
                ws,
                call_id=job.id + "_" + name,
                packet=packet,
                config=config,
                api_key=self.control.service._api_key,
                configuration=snapshot,
                browser=True,
                transport=transport,
                callbacks=VoiceCallbacks(
                    observed,
                    lambda: errors.add(name),
                    finish,
                    suppress_recipient,
                    callback_tool=callbacks.tool,
                ),
            )
            task = asyncio.create_task(voice.run())
            running[task] = voice
            try:
                with suppress(asyncio.CancelledError):
                    await task
            finally:
                running.pop(task, None)

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = _CheckServer(uvicorn.Config(app, log_level="error", access_log=False))
        server_task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(5):
                while not server.started:  # noqa: ASYNC110 - bounded diagnostic listener startup
                    await asyncio.sleep(0.02)
            with TemporaryDirectory(prefix="calling-check-") as temporary:
                for case in cases:
                    name = case["name"]
                    if name in {"language_switching", "inbound_language"} and len(allowed) < 2:
                        job.results.append(
                            {
                                "name": name,
                                "status": "skipped",
                                "reason": "Enable at least two languages to check switching.",
                                "responses": [],
                            }
                        )
                        await self._persist(job)
                        continue
                    path = Path(temporary) / (name + ".yaml")
                    path.write_text(
                        yaml.safe_dump(
                            {
                                "name": name,
                                "user": {"modality": "text"},
                                "judge": {
                                    "modality": "text",
                                    "eval": {"factory": "app.configuration_checks.check_judge"},
                                },
                                "scenarios": [{"name": name, "turns": case["turns"]}],
                            },
                            allow_unicode=True,
                        )
                    )
                    scenario = EvalScenarioFile.load(path).scenarios[0]
                    assert isinstance(scenario, EvalScriptScenario)
                    session = EvalSession.from_scenario(
                        scenario,
                        f"ws://127.0.0.1:{port}/{capability}/{name}",
                        params=EvalSessionParams(default_timeout_ms=15000, bot_ready_timeout_s=20),
                    )
                    result = await session.run()
                    if running:
                        await asyncio.wait(list(running), timeout=5)
                    job.results.append(
                        {
                            "name": name,
                            "status": "passed"
                            if result.passed and name not in errors
                            else "failed",
                            "reason": "Provider pipeline error"
                            if name in errors
                            else "\n".join(f.reason[:1200] for f in result.failures)
                            or "Expected behavior observed.",
                            "duration_ms": result.duration_ms,
                            "objective": case["brief"]["objective"],
                            "messages": [turn["user"] for turn in case["turns"] if "user" in turn],
                            "responses": [
                                {"type": event["type"], "text": str(event.get("text", ""))[:2000]}
                                for event in result.events_seen
                                if event["type"] in {"llm_response", "function_call"}
                            ],
                        }
                    )
                    await self._persist(job)
        finally:
            for task, voice in list(running.items()):
                with suppress(Exception):
                    await asyncio.wait_for(voice.stop(), 5)
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)
            server.should_exit = True
            try:
                await asyncio.wait_for(server_task, 10)
            except TimeoutError:
                server.force_exit = True
                server_task.cancel()
                await asyncio.gather(server_task, return_exceptions=True)
            sock.close()
            _JUDGE_KEY.reset(token)
