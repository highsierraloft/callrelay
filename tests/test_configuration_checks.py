from __future__ import annotations

import asyncio
import json
from pathlib import Path

import yaml

from app.configuration_checks import CASE_NAMES
from tests import test_android_calls as phone_tests
from tests import test_control_panel as panel_tests

android = phone_tests.android
panel = panel_tests.panel


async def test_check_captures_unsaved_settings_and_releases_capacity(panel, monkeypatch):
    control = panel.h.service.control
    gate = asyncio.Event()
    captured = []

    async def execute(job):
        captured.append(job.configuration)
        await gate.wait()
        job.results.extend(
            {
                "name": name,
                "status": "passed",
                "reason": "Expected facts preserved",
                "responses": [],
            }
            for name in CASE_NAMES
        )

    monkeypatch.setattr(control.checks, "_execute", execute)
    body = {
        "context": panel.h.packet.model_dump(mode="json"),
        "overrides": {"voice": {"dialogue_temperature": 0.7}},
    }
    response = await panel.client.post("/admin/api/checks", json=body)
    assert response.status_code == 200
    cid = response.json()["id"]
    assert (await panel.client.get("/admin/api/status")).json()["check_active"] is True
    assert (
        await panel.client.post(
            "/admin/api/practice", json={"context": body["context"], "mode": "text"}
        )
    ).status_code in {409, 422}
    assert await panel.h.service.acquire_deployment_lock() == 1
    current = await control.current("global")
    current["value"]["voice"]["dialogue_temperature"] = 0.1
    await control.save("global", current["value"], current["revision"], "owner")
    gate.set()
    await asyncio.wait_for(control.checks.active.task, 2)
    stored = (await panel.client.get("/admin/api/checks/" + cid)).json()
    assert (
        stored["state"] == "passed"
        and stored["configuration"]["voice"]["dialogue_temperature"] == 0.7
    )
    assert captured[0]["unsaved"] is True
    assert not control.checks.active and not panel.h.asterisk.dials
    assert await panel.h.service.db.fetch_all("SELECT call_id FROM calls") == []


async def test_checks_are_exclusive_and_cancel_cleanly(panel, monkeypatch):
    control = panel.h.service.control
    started = asyncio.Event()

    async def execute(job):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(control.checks, "_execute", execute)
    body = {"context": panel.h.packet.model_dump(mode="json")}
    result = (await panel.client.post("/admin/api/checks", json=body)).json()
    await started.wait()
    assert (await panel.client.post("/admin/api/checks", json=body)).status_code == 409
    plan = await phone_tests.prepare(panel.h)
    await panel.h.service.start(
        plan.plan_id, explicit_confirmation=True, confirmation_text=plan.confirmation_summary
    )
    assert (await panel.client.delete("/admin/api/checks/" + result["id"])).status_code == 204
    assert (await panel.client.get("/admin/api/checks/" + result["id"])).json()[
        "state"
    ] == "cancelled"
    assert not control.checks.active and not panel.h.asterisk.dials


async def test_provider_error_is_visible_without_leaking_exception_details(panel, monkeypatch):
    async def execute(job):
        raise RuntimeError("private-provider-detail")

    control = panel.h.service.control
    monkeypatch.setattr(control.checks, "_execute", execute)
    result = (
        await panel.client.post(
            "/admin/api/checks", json={"context": panel.h.packet.model_dump(mode="json")}
        )
    ).json()
    await asyncio.sleep(0.02)
    result = (await panel.client.get("/admin/api/checks/" + result["id"])).json()
    assert result["state"] == "failed" and "private-provider-detail" not in json.dumps(result)
    assert not control.checks.active


async def test_check_api_auth_and_restart_marks_abandoned_runs(panel):
    body = {"context": panel.h.packet.model_dump(mode="json")}
    assert (
        await panel.client.post("/admin/api/checks", json=body, headers={"x-csrf-token": "bad"})
    ).status_code == 403
    db = panel.h.service.db
    await db.execute(
        "INSERT INTO configuration_checks VALUES ('check_old','running','{}','[]',NULL,'2026-01-01','2026-01-01')"
    )
    panel.h.service.control._seeded = False
    await panel.h.service.control.initialize()
    assert (
        await db.fetch_one("SELECT state FROM configuration_checks WHERE check_id='check_old'")
    )["state"] == "interrupted"
    assert (await panel.client.get("/admin/api/checks/missing")).status_code == 404


def test_packaged_scenarios_cover_the_exposed_checks(tmp_path):
    cases = yaml.safe_load((Path(__file__).parents[1] / "app/check_scenarios.yaml").read_text())[
        "cases"
    ]
    assert set(CASE_NAMES) <= set(c["name"] for c in cases)
    assert all(c["turns"] for c in cases if c["name"] in CASE_NAMES)
    from pipecat.evals.scenario import EvalScenarioFile

    for case in cases:
        path = tmp_path / (case["name"] + ".yaml")
        path.write_text(
            yaml.safe_dump(
                {
                    "name": case["name"],
                    "user": {"modality": "text"},
                    "judge": {
                        "modality": "text",
                        "eval": {"factory": "app.configuration_checks.check_judge"},
                    },
                    "scenarios": [{"name": case["name"], "turns": case["turns"]}],
                }
            )
        )
        EvalScenarioFile.load(path)


async def test_repeated_stop_waits_for_the_same_provider_cleanup(panel, monkeypatch):
    control = panel.h.service.control
    connected = asyncio.Event()
    releasing = asyncio.Event()
    release = asyncio.Event()
    closed = asyncio.Event()

    async def execute(job):
        connected.set()
        try:
            await asyncio.Event().wait()
        finally:
            releasing.set()
            await release.wait()
            closed.set()

    monkeypatch.setattr(control.checks, "_execute", execute)
    job = await control.checks.start(panel.h.packet)
    await connected.wait()
    first = asyncio.create_task(control.checks.cancel(job["id"]))
    await releasing.wait()
    second = asyncio.create_task(control.checks.cancel(job["id"]))
    await asyncio.sleep(0)
    assert control.checks.active is not None
    release.set()
    await asyncio.gather(first, second)
    assert closed.is_set() and control.checks.active is None
    assert (await control.checks.get(job["id"]))["state"] == "cancelled"
