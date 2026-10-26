from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from pydantic import ValidationError

from app.db.control import RevisionConflict
from app.models import CallState
from app.reception_models import (
    BusinessSettings,
    CallbackPolicy,
    CallbackProposal,
    ContactWrite,
    MemoryWrite,
    ReviewCommit,
)
from tests.conftest import seed_call


async def completed(database, packet, identifier="call_memory"):
    await seed_call(
        database, packet, call_id=identifier, state=CallState.COMPLETED, openai_call_id=identifier
    )
    for n in range(3):
        await database.add_transcript_turn(
            call_id=identifier,
            turn_id=f"turn{n}",
            speaker="callee",
            text=f"Confirmed fact {n}",
            source_event_type="test",
            source_event_id=f"source{n}",
        )
    await database.execute(
        "INSERT INTO call_results VALUES (?, '{}','[]','2026-10-25')", (identifier,)
    )
    return await database.contact_link_call(
        call_id=identifier,
        phone=packet.target.phone,
        name=packet.target.name,
        direction="outbound",
    )


async def review_ready(database, packet):
    link = await completed(database, packet)
    job_id = await database.reception_enqueue_review(link["call_id"])
    job = await database.reception_claim_review()
    await database.reception_read_review(job_id, job["lease_token"], 0)
    payload = ReviewCommit(
        job_id=job_id,
        lease_token=job["lease_token"],
        request_id="save-one",
        transcript_digest=job["transcript_digest"],
        expected_memory_revision=0,
        expected_summary_revision=0,
        summary="The caller confirmed their preference.",
        outcome="achieved",
        evidence_turn_ids=["turn0"],
        memory_facts=[
            {"key": "preference", "text": "Prefers mornings", "evidence_turn_ids": ["turn1"]}
        ],
    ).model_dump(mode="json")
    return link, job, payload


async def test_contacts_revision_conflicts_shared_numbers_and_private_notes(database):
    payload = ContactWrite(
        expected_revision=0, name="A", numbers=["+34 614 000 000"], private_note="Owner only"
    ).model_dump()
    first = await database.contact_save(None, payload, "owner")
    second = await database.contact_save(None, {**payload, "name": "B"}, "owner")
    assert len(await database.contact_lookup("+34614000000")) == 2
    assert (await database.contact_get(first))["private_note"] == "Owner only"
    with pytest.raises(RevisionConflict):
        await database.contact_save(first, payload, "owner")
    await database.contact_save(first, {**payload, "expected_revision": 1, "numbers": []}, "owner")
    assert (await database.contact_lookup("+34614000000"))[0]["contact_id"] == second


async def test_reviews_one_claim_complete_pages_atomic_receipt_and_evidence(database, packet):
    link = await completed(database, packet)
    job_id = await database.reception_enqueue_review(link["call_id"])
    assert job_id == await database.reception_enqueue_review(link["call_id"])
    jobs = await asyncio.gather(
        database.reception_claim_review(), database.reception_claim_review()
    )
    assert sum(job is not None for job in jobs) == 1
    job = next(job for job in jobs if job)
    assert await database.reception_review_pending() == 0
    with pytest.raises(ValueError, match="in order"):
        await database.reception_read_review(job_id, job["lease_token"], 1)
    page = await database.reception_read_review(job_id, job["lease_token"], 0, 1)
    assert page["next_offset"] == 1
    payload = ReviewCommit(
        job_id=job_id,
        lease_token=job["lease_token"],
        request_id="r1",
        transcript_digest=job["transcript_digest"],
        expected_memory_revision=0,
        expected_summary_revision=0,
        summary="Asked about timing.",
        outcome="incomplete",
    ).model_dump(mode="json")
    with pytest.raises(ValueError, match="complete transcript"):
        await database.reception_commit_review(payload)
    await database.reception_read_review(job_id, job["lease_token"], 1)
    with pytest.raises(ValueError, match="Evidence"):
        await database.reception_commit_review({**payload, "evidence_turn_ids": ["foreign"]})
    receipt = await database.reception_commit_review(payload)
    assert await database.reception_commit_review(payload) == receipt
    assert receipt["summary_revision"] == 1 and await database.reception_review_pending() == 0
    with pytest.raises(ValueError, match="different review"):
        await database.reception_commit_review({**payload, "summary": "Other content"})


async def test_owner_memory_edit_conflict_and_removed_evidence_cannot_be_revived(database, packet):
    link, job, payload = await review_ready(database, packet)
    owner = MemoryWrite(
        expected_revision=0, facts=[{"key": "preference", "text": "Prefers afternoons"}]
    ).model_dump(mode="json")
    await database.contact_save_memory(link["contact_id"], owner)
    with pytest.raises(RevisionConflict, match="memory changed"):
        await database.reception_commit_review(payload)
    payload["expected_memory_revision"] = 1
    await database.reception_commit_review(payload)
    contact = await database.contact_get(link["contact_id"])
    assert contact["memory"]["facts"][0]["text"] == "Prefers afternoons"
    await database.contact_save_memory(link["contact_id"], {"expected_revision": 2, "facts": []})
    from app.db.contact_registry import merge_review_memory

    memory = (await database.contact_get(link["contact_id"]))["memory"]
    merged = merge_review_memory(memory, payload["memory_facts"], link["call_id"])
    assert merged["facts"] == []
    assert "preference" in merged["removed_keys"]


async def test_review_expiry_reclaims_without_old_lease_writing(database, packet):
    link, first, payload = await review_ready(database, packet)
    await database.execute(
        "UPDATE reception_reviews SET lease_until='2000-01-01' WHERE job_id=?", (first["job_id"],)
    )
    assert await database.reception_review_pending() == 1
    second = await database.reception_claim_review()
    assert second["lease_token"] != first["lease_token"]
    with pytest.raises(ValueError, match="expired"):
        await database.reception_commit_review(payload)
    assert not await database.reception_release_review(first["job_id"], first["lease_token"], "old")
    assert await database.reception_release_review(
        second["job_id"], second["lease_token"], "Model unavailable"
    )
    assert await database.reception_review_pending() == 0


async def test_memory_source_tombstones_block_same_evidence_under_another_key(database, packet):
    link, job, payload = await review_ready(database, packet)
    await database.reception_commit_review(payload)
    await database.contact_save_memory(link["contact_id"], {"expected_revision": 1, "facts": []})
    from app.db.contact_registry import merge_review_memory

    memory = (await database.contact_get(link["contact_id"]))["memory"]
    changed_key = {**payload["memory_facts"][0], "key": "new_preference"}
    assert merge_review_memory(memory, [changed_key], link["call_id"])["facts"] == []


async def test_historical_named_people_sharing_number_are_not_merged(database, packet):
    first = await completed(database, packet)
    other = packet.model_copy(deep=True)
    other.target.name = "Different person"
    second = await completed(database, other, "call_other")
    assert first["contact_id"] != second["contact_id"]
    assert second["recognition"] == "shared"
    await seed_call(
        database,
        packet,
        call_id="call_incoming",
        state=CallState.COMPLETED,
        openai_call_id="rtc_incoming",
    )
    incoming = await database.contact_link_call(
        call_id="call_incoming", phone=packet.target.phone, name="", direction="inbound"
    )
    assert incoming["contact_id"] is None and incoming["recognition"] == "shared"


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-03-29T01:15:00+00:00", "2026-03-29T01:30:00+00:00"),
        ("2026-10-25T01:15:00+02:00", "2026-10-25T01:30:00+02:00"),
    ],
)
def test_callback_times_reject_nonexistent_wall_time_or_wrong_offset(start, end):
    with pytest.raises(ValidationError, match="does not exist"):
        CallbackProposal(
            request_key="test",
            objective="Follow up",
            destination="+34614000000",
            window_start=start,
            window_end=end,
            timezone="Europe/London",
        )


def test_callback_policy_and_daylight_saving_fold():
    for offset in ("+01:00", "+00:00"):
        proposal = CallbackProposal(
            request_key="test",
            objective="Follow up",
            destination="+34614000000",
            window_start=f"2026-10-25T01:15:00{offset}",
            window_end=f"2026-10-25T01:30:00{offset}",
            timezone="Europe/London",
        )
        assert not CallbackPolicy().permits(proposal.window_start, proposal.window_end)
    assert CallbackPolicy().permits(
        datetime.fromisoformat("2026-10-26T09:00:00+00:00"),
        datetime.fromisoformat("2026-10-26T09:15:00+00:00"),
    )
    assert not BusinessSettings().configured
    with pytest.raises(ValidationError):
        CallbackPolicy(opens="18:00", closes="09:00")


async def test_name_mismatch_cannot_write_memory_on_initial_or_later_review(database, packet):
    link, job, payload = await review_ready(database, packet)
    payload.update(recipient_name="Different person", identity_evidence_turn_ids=["turn0"])
    with pytest.raises(ValueError, match="name differs"):
        await database.reception_commit_review(payload)
    facts = payload["memory_facts"]
    payload["memory_facts"] = []
    await database.reception_commit_review(payload)
    assert (
        await database.fetch_one(
            "SELECT recognition FROM call_contacts WHERE call_id=?", (link["call_id"],)
        )
    )["recognition"] == "identity_mismatch"
    assert (await database.contact_get(link["contact_id"]))["memory"]["facts"] == []
    assert await database.reception_enqueue_review(link["call_id"], reprocess=True) == job["job_id"]
    next_job = await database.reception_claim_review()
    await database.reception_read_review(next_job["job_id"], next_job["lease_token"], 0)
    payload.update(
        lease_token=next_job["lease_token"],
        request_id="second_review",
        expected_summary_revision=1,
        recipient_name=None,
        memory_facts=facts,
    )
    with pytest.raises(ValueError, match="Resolve this shared"):
        await database.reception_commit_review(payload)


async def test_owner_relink_invalidates_review_lease_even_with_same_memory_revision(
    database, packet
):
    link, job, payload = await review_ready(database, packet)
    contact = await database.contact_save(
        None,
        ContactWrite(expected_revision=0, name="Correct person").model_dump(mode="json"),
        "owner",
    )
    await database.contact_relink_call(link["call_id"], link["contact_id"], contact)
    with pytest.raises(RevisionConflict, match="changed this call's contact"):
        await database.reception_commit_review(payload)
    assert not await database.fetch_all("SELECT * FROM call_summaries")


async def test_review_tracks_unscheduled_promise_atomically_and_idempotently(database, packet):
    from app.workflow_models import CallbackDraft

    link, job, payload = await review_ready(database, packet)
    payload["callbacks"] = [
        CallbackDraft(
            source_key="check-price",
            objective="Call back with the approved price",
            source="agent_promise",
            evidence_turn_ids=["turn0"],
            needed_information=["Confirm price with owner"],
        ).model_dump(mode="json")
    ]
    result = await database.reception_commit_review(payload)
    assert len(result["followup_ids"]) == 1
    assert await database.reception_commit_review(payload) == result
    row = await database.fetch_one(
        "SELECT status FROM call_followups WHERE followup_id=?", (result["followup_ids"][0],)
    )
    assert row["status"] == "needs_information"
    assert not await database.fetch_all("SELECT * FROM callback_schedules")


async def test_owner_memory_save_keeps_unchanged_fact_automatic_and_removed_call_blocked(
    database, packet
):
    from app.db.contact_registry import merge_review_memory

    link, job, payload = await review_ready(database, packet)
    await database.reception_commit_review(payload)
    contact = await database.contact_get(link["contact_id"])
    kept = contact["memory"]["facts"][0]
    changed = await database.contact_save_memory(
        link["contact_id"],
        {
            "expected_revision": 1,
            "facts": [
                kept,
                {"key": "owner_note", "text": "Approved fact", "evidence_turn_ids": []},
            ],
        },
    )
    assert not changed["facts"][0]["owner_protected"]
    assert changed["facts"][1]["owner_protected"]
    removed = await database.contact_save_memory(
        link["contact_id"], {"expected_revision": 2, "facts": [changed["facts"][1]]}
    )
    merged = merge_review_memory(
        removed,
        [{"key": "different", "text": "Revived fact", "evidence_turn_ids": ["turn2"]}],
        "call_memory",
    )
    assert len(merged["facts"]) == 1
