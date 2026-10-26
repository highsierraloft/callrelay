from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.android_config import AndroidConfig
from app.call_planning import prepare_call
from app.models import BusinessContext, PreparePhoneCallInput
from app.policy import validate_destination
from app.voice_prompts import voice_instructions


async def test_sales_preparation_collects_business_facts_before_persisting_plan(settings, packet):
    packet.intention = "sales"
    packet.personality = "salesperson"
    db = AsyncMock()
    request = PreparePhoneCallInput(
        context=packet, requested_by_owner=True, authority_basis="Owner requested this sales call"
    )
    result = await prepare_call(settings, db, request)
    assert result.plan_id is None
    assert set(result.missing_fields) == {
        "business.name",
        "business.description",
        "business.offer",
        "business.desired_next_step",
        "business.contact_permission",
    }
    db.create_plan.assert_not_awaited()
    packet.business = BusinessContext(
        name="Example",
        description="Appointment software",
        offer="A short product demo",
        desired_next_step="Ask if a follow-up meeting is wanted",
        pricing="Twenty euros monthly",
        contact_permission="recipient_requested",
    )
    result = await prepare_call(settings, db, request)
    assert result.plan_id
    assert "salesperson" in result.confirmation_summary
    db.create_plan.assert_awaited_once()


@pytest.mark.parametrize(
    "number",
    ["+442079460123", "+34911234567", "+380441234567", "+81312345678", "+6903101", "+6837012"],
)
def test_explicit_worldwide_configuration_accepts_international_destinations(settings, number):
    settings.allow_all_country_codes = True
    assert validate_destination(number, settings).allowed


def test_production_call_budget_is_configurable_and_bounded():
    config = AndroidConfig(
        selected_sim_slot=1, caller_number="+12025550199", allow_any_destination=True
    )
    assert config.call_duration(None) == 1800
    assert config.call_duration(3600) == 3600
    assert config.call_duration(7200) == 7200
    with pytest.raises(ValueError):
        config.call_duration(7201)
    with pytest.raises(ValidationError):
        AndroidConfig(selected_sim_slot=1, caller_number="+12025550199")


def test_styles_do_not_expand_authority_and_fiction_rules_are_universal(packet):
    packet.personality = "salesperson"
    prompts = []
    for intention in ["sales", "conversation", "prank"]:
        packet.intention = intention
        prompt = voice_instructions(packet)
        assert "approved claims only" in prompt and "Respect a refusal" in prompt
        assert "harmless fiction" in prompt and "fictional" in prompt
        prompts.append(prompt)
    assert len(set(prompts)) == 1
