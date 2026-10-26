from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.call_planning import prepare_call
from app.errors import CallRefusal
from app.models import ContextPacket, PreparePhoneCallInput


def request_without_owner_numbers(packet):
    context = packet.model_dump()
    context["owner"].pop("callback_number")
    context["escalation"] = {"mode": "end_call"}
    context.update(intention="task", personality="salesperson", language="ru")
    return PreparePhoneCallInput(
        context=ContextPacket.model_validate(context),
        requested_by_owner=True,
        authority_basis="Owner requested a personal invitation",
    )


@pytest.mark.parametrize("destination", ["+37061234567", "+34911234567", "+380441234567"])
async def test_personal_destination_is_independent_of_saved_owner_numbers(
    settings, packet, database, destination
):
    settings.allow_all_country_codes = True
    packet.target.phone = destination
    request = request_without_owner_numbers(packet)
    result = await prepare_call(settings, database, request)
    assert result.plan_id and not result.missing_fields
    plan = await database.get_plan_record(result.plan_id)
    assert plan.context.target.phone == destination
    assert plan.context.owner.callback_number == settings.owner_phone_e164
    assert plan.context.escalation.owner_phone == settings.owner_phone_e164
    assert plan.context.personality == "salesperson"
    assert plan.context.intention == "task"
    assert request.context.owner.callback_number == "configured"
    assert request.context.escalation.owner_phone == "configured"


async def test_missing_server_owner_number_does_not_create_a_plan(settings, packet):
    settings.owner_phone_e164 = None
    database = AsyncMock()
    with pytest.raises(CallRefusal, match="owner_phone_not_configured"):
        await prepare_call(settings, database, request_without_owner_numbers(packet))
    database.create_plan.assert_not_awaited()


async def test_explicit_wrong_owner_number_is_still_rejected(settings, packet):
    request = request_without_owner_numbers(packet)
    request.context.owner.callback_number = packet.target.phone
    request.context.escalation.owner_phone = packet.target.phone
    database = AsyncMock()
    with pytest.raises(CallRefusal, match="configured_owner_phone_mismatch"):
        await prepare_call(settings, database, request)
    database.create_plan.assert_not_awaited()
