"""The private dashboard API. It never accepts an infrastructure or provider credential."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, WebSocket
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.control import ControlPlane
from app.control_auth import (
    authenticate,
    check_csrf,
    csrf_token,
    require_control,
    valid_csrf_cookie,
)
from app.control_models import CallAnnotation, RevisionSave
from app.db.control import RevisionConflict
from app.models import ContextPacket, PreparePhoneCallInput
from app.voice_preview import PreviewUnavailable
from app.workflow_models import FollowupUpdate, OutcomeWrite

router = APIRouter(prefix="/admin/api", tags=["control"])


class PracticeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["voice", "text"] = "voice"
    context: ContextPacket
    overrides: dict[str, Any] | None = None


class TextInput(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class StartInput(BaseModel):
    plan_id: str
    confirmation_text: str
    explicit_confirmation: bool


@router.get("/session")
async def session(request: Request, response: Response) -> dict[str, Any]:
    control, access = authenticate(request)
    existing = request.cookies.get("__Host-calling-csrf", "")
    token = existing if valid_csrf_cookie(existing, access) else csrf_token(access)
    response.set_cookie(
        "__Host-calling-csrf",
        token,
        secure=True,
        httponly=True,
        samesite="strict",
        max_age=43200,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {
        "csrf": token,
        "owner_login": access.owner_login,
        "timezone": access.timezone,
        "hermes_url": access.hermes_url,
        "setup": await control.setup(),
    }


@router.get("/status")
async def status(control: ControlPlane = Depends(require_control)) -> dict[str, Any]:
    return await control.status()


@router.get("/catalog")
async def catalog(control: ControlPlane = Depends(require_control)) -> dict[str, Any]:
    return await control.catalog.get()


@router.get("/voice-preview")
async def voice_preview(
    voice: str = Query(min_length=1, max_length=100),
    model: str = Query(min_length=1, max_length=100),
    control: ControlPlane = Depends(require_control),
) -> Response:
    try:
        data = await control.voice_previews.get(voice, model)
    except PreviewUnavailable as exc:
        raise HTTPException(
            exc.status,
            str(exc),
            headers={"Retry-After": str(exc.retry_after)} if exc.retry_after else None,
        ) from exc
    return Response(
        data,
        media_type="audio/mpeg",
        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/costs")
async def costs(
    month: str = Query(pattern=r"^\d{4}-\d{2}$"),
    offset: int = Query(0, ge=0, le=100000),
    control: ControlPlane = Depends(require_control),
) -> dict[str, Any]:
    try:
        return await control.spending.monthly(month, offset)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/profiles")
async def profiles(control: ControlPlane = Depends(require_control)) -> list[dict[str, Any]]:
    return await control.profiles(archived=True)


@router.get("/configuration/{entity}")
async def configuration(
    entity: str, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.current(entity)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/configuration/{entity}/history")
async def history(
    entity: str, control: ControlPlane = Depends(require_control)
) -> list[dict[str, Any]]:
    try:
        return await control.history(entity)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put("/configuration/{entity}")
async def save(
    entity: str,
    body: RevisionSave,
    request: Request,
    control: ControlPlane = Depends(require_control),
) -> dict[str, Any]:
    try:
        return await control.save(
            entity, body.value, body.expected_revision, request.headers["tailscale-user-login"]
        )
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/checks")
async def start_check(
    body: PracticeInput, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.checks.start(body.context, body.overrides)
    except (ValueError, LookupError) as exc:
        raise HTTPException(409 if "active" in str(exc) else 422, str(exc)) from exc


@router.get("/checks")
async def recent_checks(
    profile_id: str, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    return await control.checks.recent(profile_id)


@router.get("/checks/{check_id}")
async def get_check(
    check_id: str, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.checks.get(check_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/checks/{check_id}", status_code=204)
async def cancel_check(check_id: str, control: ControlPlane = Depends(require_control)) -> Response:
    await control.checks.cancel(check_id)
    return Response(status_code=204)


@router.post("/prompt-preview")
async def preview(
    body: PracticeInput, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        packet, snapshot = await control.practice_snapshot(body.context, body.overrides, body.mode)
        return {
            "prompt": control.prompt(packet, snapshot),
            "configuration": snapshot,
            "configured": snapshot["ai_inputs"],
            "last_request": None,
        }
    except (ValueError, LookupError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/calls")
async def calls(
    q: str = "",
    state: str = "",
    outcome: str = "",
    direction: str = "",
    language: str = "",
    profile: str = "",
    start: str = "",
    end: str = "",
    cursor: str = "",
    limit: int = Query(default=40, ge=1, le=100),
    control: ControlPlane = Depends(require_control),
) -> dict[str, Any]:
    try:
        return await control.calls(
            query=q,
            state=state,
            outcome=outcome,
            direction=direction,
            language=language,
            profile=profile,
            start=start,
            end=end,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/plans/{plan_id}")
async def plan_status(
    plan_id: str, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.service.get_plan_status(plan_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/calls/{call_id}")
async def detail(
    call_id: str,
    after: int = Query(default=0, ge=0),
    control: ControlPlane = Depends(require_control),
) -> dict[str, Any]:
    try:
        return await control.detail(call_id, after)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/calls/{call_id}/outcome")
async def outcome(call_id: str, control: ControlPlane = Depends(require_control)) -> dict[str, Any]:
    try:
        await control.workflows._call(call_id)
        return {
            "assessment": await control.workflows.assessment(call_id),
            "followups": (await control.workflows.list_followups(status="all", call_id=call_id))[
                "followups"
            ],
        }
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put("/calls/{call_id}/outcome")
async def save_outcome(
    call_id: str, body: OutcomeWrite, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.workflows.save_outcome(call_id, body, "owner")
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/followups")
async def followups(
    status: str = "open",
    call_id: str | None = None,
    offset: int = Query(0, ge=0, le=100000),
    control: ControlPlane = Depends(require_control),
) -> dict[str, Any]:
    try:
        return await control.workflows.list_followups(status=status, call_id=call_id, offset=offset)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/followups/{followup_id}/brief")
async def followup_brief(
    followup_id: str, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.workflows.callback_brief(followup_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/followups/{followup_id}")
async def update_followup(
    followup_id: str, body: FollowupUpdate, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.workflows.update_followup(followup_id, body, "owner")
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/calls/{call_id}/ai-inputs")
async def call_ai_inputs(
    call_id: str, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.call_inputs(call_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put("/calls/{call_id}/annotation", status_code=204)
async def annotate(
    call_id: str, body: CallAnnotation, control: ControlPlane = Depends(require_control)
) -> Response:
    try:
        await control.annotate(call_id, body.note, body.tags)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(status_code=204)


@router.get("/calls/{call_id}/files/{name}")
async def artifact(
    call_id: str,
    name: Literal["recording.mp3", "transcript.md"],
    control: ControlPlane = Depends(require_control),
) -> FileResponse:
    try:
        result = await control.service.get_result(call_id)
        if not any(f["name"] == name for f in result["artifacts"]["files"]):
            raise HTTPException(404, "This file is unavailable for this call")
        path = control.service.artifacts.directory(call_id) / name
        if not path.is_file() or path.is_symlink():
            raise HTTPException(404, "File unavailable")
        return FileResponse(
            path,
            media_type="audio/mpeg" if name.endswith(".mp3") else "application/octet-stream",
            filename=name,
            content_disposition_type="inline" if name.endswith(".mp3") else "attachment",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )
    except LookupError as exc:
        raise HTTPException(404, "Call not found") from exc


@router.post("/calls/prepare")
async def prepare(
    body: PreparePhoneCallInput, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return (await control.service.prepare(body)).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/calls/start")
async def start_call(
    body: StartInput, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return (await control.service.start(**body.model_dump())).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/calls/{call_id}/end")
async def end_call(
    call_id: str, control: ControlPlane = Depends(require_control)
) -> dict[str, bool]:
    return {"ended": await control.service.terminate_call(call_id, "owner_requested")}


@router.get("/integration")
async def integration(
    request: Request, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    tools = await request.app.state.mcp.list_tools()
    return {
        "setup": await control.setup(),
        "tools": [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in tools
        ],
    }


@router.post("/practice")
async def practice(
    body: PracticeInput, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.practice.create(body.mode, body.context, body.overrides)
    except (ValueError, LookupError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/practice/{session_id}")
async def practice_state(
    session_id: str, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return control.practice.get(session_id).view()
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/practice/{session_id}/ai-inputs")
async def practice_ai_inputs(
    session_id: str, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.practice.inputs(session_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/practice/{session_id}", status_code=204)
async def close_practice(
    session_id: str, control: ControlPlane = Depends(require_control)
) -> Response:
    await control.practice.close(session_id)
    return Response(status_code=204)


@router.post("/practice/{session_id}/text")
async def practice_text(
    session_id: str, body: TextInput, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.practice.text(session_id, body.text)
    except (ValueError, LookupError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.websocket("/practice/{session_id}/audio")
async def practice_audio(session_id: str, websocket: WebSocket) -> None:
    try:
        control, access = authenticate(websocket)
        check_csrf(websocket, access, websocket=True)
        await control.practice.voice(session_id, websocket)
    except (HTTPException, LookupError):
        await websocket.close(code=1008)
