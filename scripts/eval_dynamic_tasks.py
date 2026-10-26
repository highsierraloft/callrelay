"""Run bounded Pipecat Evals against the real voice pipeline, without a phone.

Uses only a loopback WebSocket and synthetic briefs. The provider keys stay in files.
The judge runs only in this diagnostic process, never in the calling service.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import socket
from contextlib import suppress
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI, WebSocket
from loguru import logger
from pipecat.evals.fastapi_transport import EvalFastAPIWebsocketTransport
from pipecat.evals.scenario import EvalScenarioFile
from pipecat.evals.serializer import EvalSerializer, parse_eval_connect
from pipecat.evals.session import EvalSession, EvalSessionParams
from pipecat.evals.transport import EvalTransportParams
from pipecat.services.openai.llm import OpenAILLMService

from app.android_config import AndroidConfig
from app.android_voice import AndroidVoiceSession, VoiceCallbacks
from app.models import ContextPacket
from app.voice_prompts import voice_instructions


def judge(config: dict[str, Any]) -> OpenAILLMService:
    return OpenAILLMService(
        api_key=Path(config["key_file"]).read_text().strip(),
        base_url="https://openrouter.ai/api/v1",
        settings=OpenAILLMService.Settings(
            model=config["model"],
            temperature=0,
            extra={"extra_body": {"reasoning": {"effort": "none"}}},
        ),
    )


def packet_for(case: dict[str, Any]) -> ContextPacket:
    return ContextPacket.model_validate(
        {
            "owner": {"display_name": "Alex", "timezone": "Europe/Madrid"},
            "target": {
                "name": "Sam",
                "phone": "+12025550123",
                "background": "Sam runs Example Cafe; Alex met Sam at a public demo last week.",
            },
            "language": "en",
            "personality": "warm",
            "max_duration_seconds": 300,
            "escalation": {"mode": "end_call"},
            **case["brief"],
        }
    )


async def run(args: argparse.Namespace) -> bool:
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = AndroidConfig.model_validate(json.loads(args.configuration.read_text())["android"])
    cases = yaml.safe_load(args.scenarios.read_text())["cases"]
    if args.only:
        cases = [c for c in cases if c["name"] in args.only.split(",")]
    if not cases or len(cases) > 20:
        raise ValueError("Choose between one and twenty diagnostic scenarios")
    by_name = {c["name"]: c for c in cases}
    app = FastAPI()
    active: set[asyncio.Task[Any]] = set()
    pipeline_errors: list[str] = []
    local_opt_outs: list[str] = []

    @app.websocket("/ws/{name}")
    async def conversation(ws: WebSocket, name: str) -> None:
        await ws.accept()
        flags = parse_eval_connect(await ws.receive_json())
        if flags is None or name not in by_name:
            await ws.close(code=1008)
            return
        case = by_name[name]
        packet = packet_for(case)
        transport = EvalFastAPIWebsocketTransport(
            ws,
            EvalTransportParams(
                serializer=EvalSerializer(),
                audio_in_enabled=True,
                audio_out_enabled=True,
                audio_in_sample_rate=16000,
                audio_out_sample_rate=24000,
            ),
            flags,
        )
        prompt = voice_instructions(
            packet,
            conversation_languages=list(config.conversation_languages),
            personality_prompt=case.get("personality_prompt"),
        )

        async def finish(guard: Any) -> bool:
            return bool(guard())

        async def suppress_recipient() -> None:
            local_opt_outs.append(name)

        def failed() -> None:
            pipeline_errors.append(name)

        session = AndroidVoiceSession(
            ws,
            call_id="eval_" + name,
            packet=packet,
            config=config,
            api_key=args.key_file.read_text().strip(),
            callbacks=VoiceCallbacks(lambda *a: None, failed, finish, suppress_recipient),
            configuration={"effective_prompt": prompt},
            browser=True,
            transport=transport,
        )
        task = asyncio.create_task(session.run())
        active.add(task)
        try:
            with suppress(asyncio.CancelledError):
                await task
        finally:
            active.discard(task)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    server_task = asyncio.create_task(server.serve(sockets=[sock]))
    results = []
    try:
        async with asyncio.timeout(5):
            while not server.started:  # noqa: ASYNC110 - bounded test-server startup
                await asyncio.sleep(0.02)
        for case in cases:
            scenario_path = args.output / (case["name"] + ".yaml")
            scenario_path.write_text(
                yaml.safe_dump(
                    {
                        "name": case["name"],
                        "user": {"modality": "text"},
                        "judge": {
                            "modality": "text",
                            "eval": {
                                "factory": "scripts.eval_dynamic_tasks.judge",
                                "model": args.judge_model,
                                "key_file": str(args.key_file),
                            },
                        },
                        "scenarios": [{"name": case["name"], "turns": case["turns"]}],
                    },
                    allow_unicode=True,
                )
            )
            scenario = EvalScenarioFile.load(scenario_path).scenarios[0]
            session = EvalSession.from_scenario(
                scenario,
                f"ws://127.0.0.1:{port}/ws/{case['name']}",
                params=EvalSessionParams(default_timeout_ms=15000, bot_ready_timeout_s=20),
            )
            result = await session.run()
            if active:
                await asyncio.wait(list(active), timeout=5)
            (args.output / (case["name"] + ".json")).write_text(
                json.dumps(dataclasses.asdict(result), ensure_ascii=False, indent=2, default=str)
            )
            results.append(
                {
                    "name": case["name"],
                    "passed": result.passed,
                    "failures": len(result.failures),
                    "duration_ms": result.duration_ms,
                }
            )
            print(json.dumps(results[-1]), flush=True)
    finally:
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        server.should_exit = True
        await asyncio.wait_for(server_task, 15)
        sock.close()
    report = {
        "cases": results,
        "pipeline_errors": pipeline_errors,
        "synthetic_opt_outs": local_opt_outs,
        "phone_calls": 0,
        "scope": "Pipecat Evals text inputs through the shared voice pipeline; not handset listening or audio timing",
    }
    (args.output / "summary.json").write_text(json.dumps(report, indent=2))
    return all(r["passed"] for r in results) and not pipeline_errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", type=Path, default=Path("/run/secrets/agent-call.json"))
    parser.add_argument("--key-file", type=Path, default=Path("/run/secrets/openrouter-api-key"))
    parser.add_argument(
        "--scenarios", type=Path, default=Path("tests/scenarios/dynamic-tasks.yaml")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only")
    parser.add_argument("--judge-model", default="openai/gpt-6-luna")
    args = parser.parse_args()
    logger.disable("pipecat")
    logger.disable("pipecat_asterisk")
    raise SystemExit(0 if asyncio.run(run(args)) else 1)


if __name__ == "__main__":
    main()
