"""Authenticated dashboard operations for contacts, reception, and callbacks."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.control import ControlPlane
from app.control_auth import require_control
from app.db.control import RevisionConflict
from app.reception_models import (
    CallbackConfirmation,
    CallbackProposal,
    ContactLink,
    ContactWrite,
    HandsetAcceptance,
    HandsetWindow,
    MemoryWrite,
    ScheduleUpdate,
)

router = APIRouter(prefix="/admin/api", tags=["reception"])


@router.get("/inbound/acceptance")
async def acceptance_status(control: ControlPlane = Depends(require_control)) -> dict[str, Any]:
    return await control.service.inbound.acceptance_status()


@router.post("/inbound/test-window")
async def handset_window(
    body: HandsetWindow, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.service.inbound.test_window(body.open)
    except (ValueError, LookupError) as exc:
        raise problem(exc) from exc


@router.post("/inbound/acceptance")
async def accept_handset(
    body: HandsetAcceptance, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.service.inbound.accept_handset(body)
    except (ValueError, LookupError) as exc:
        raise problem(exc) from exc


def problem(exc: ValueError | LookupError) -> HTTPException:
    return HTTPException(
        409 if isinstance(exc, RevisionConflict) else 404 if isinstance(exc, LookupError) else 422,
        str(exc),
    )


@router.get("/contacts")
async def contacts(
    search: str = Query("", max_length=200),
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(40, ge=1, le=100),
    control: ControlPlane = Depends(require_control),
) -> dict[str, Any]:
    return await control.contacts.list(search, offset, limit)


@router.get("/contacts/{contact_id}")
async def contact(
    contact_id: str,
    offset: int = Query(0, ge=0, le=100000),
    control: ControlPlane = Depends(require_control),
) -> dict[str, Any]:
    try:
        return await control.contacts.get(contact_id, private=True, offset=offset)
    except (ValueError, LookupError) as exc:
        raise problem(exc) from exc


@router.post("/contacts")
async def create_contact(
    body: ContactWrite, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.contacts.save(None, body)
    except (ValueError, LookupError) as exc:
        raise problem(exc) from exc


@router.put("/contacts/{contact_id}")
async def save_contact(
    contact_id: str, body: ContactWrite, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.contacts.save(contact_id, body)
    except (ValueError, LookupError) as exc:
        raise problem(exc) from exc


@router.put("/contacts/{contact_id}/memory")
async def save_memory(
    contact_id: str, body: MemoryWrite, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.contacts.save_memory(contact_id, body)
    except (ValueError, LookupError) as exc:
        raise problem(exc) from exc


@router.post("/calls/{call_id}/review")
async def request_review(
    call_id: str, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.contacts.request_review(call_id)
    except (ValueError, LookupError) as exc:
        raise problem(exc) from exc


@router.put("/calls/{call_id}/contact")
async def link_contact(
    call_id: str, body: ContactLink, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.contacts.relink(call_id, body)
    except (ValueError, LookupError) as exc:
        raise problem(exc) from exc


@router.get("/callbacks")
async def callbacks(
    status: str = "open",
    offset: int = Query(0, ge=0, le=100000),
    control: ControlPlane = Depends(require_control),
) -> dict[str, Any]:
    try:
        return await control.callbacks.list(status, offset)
    except (ValueError, LookupError) as exc:
        raise problem(exc) from exc


@router.get("/callbacks/{schedule_id}")
async def callback(
    schedule_id: str, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.callbacks.get(schedule_id)
    except (ValueError, LookupError) as exc:
        raise problem(exc) from exc


@router.post("/calls/{call_id}/callbacks")
async def schedule_callback(
    call_id: str, body: CallbackProposal, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        proposal = await control.callbacks.propose(call_id, body)
        return await control.callbacks.confirm(
            call_id,
            CallbackConfirmation(
                proposal_id=proposal["proposal_id"],
                proposal_revision=proposal["revision"],
                readback_turn_id="owner",
                confirmation_turn_id="owner",
            ),
            owner=True,
        )
    except (ValueError, LookupError) as exc:
        raise problem(exc) from exc


@router.put("/callbacks/{schedule_id}")
async def update_callback(
    schedule_id: str, body: ScheduleUpdate, control: ControlPlane = Depends(require_control)
) -> dict[str, Any]:
    try:
        return await control.callbacks.update(schedule_id, body)
    except (ValueError, LookupError) as exc:
        raise problem(exc) from exc
