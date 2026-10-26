# Brief and helper commands

Run the helper by its absolute path from any project:

```sh
python3 ~/.agents/skills/phone-calls/scripts/calling.py setup
python3 ~/.agents/skills/phone-calls/scripts/calling.py prepare /absolute/path/brief.json
```

A brief is the context object, without an API request wrapper:

```json
{
  "target": {
    "name": "Sam",
    "phone": "+12025550123",
    "background": "Our supplier; knows the delivery we are discussing."
  },
  "objective": "Confirm the delivery date and address.",
  "profile_id": "professional",
  "language": "en",
  "relevant_facts": ["The order reference is supplied by the owner."],
  "allowed_commitments": ["Confirm an existing delivery arrangement."],
  "hard_constraints": ["Do not approve additional charges."],
  "completion_criteria": "Delivery date and address are confirmed."
}
```

Use actual supplied facts. The example number is illustrative, not a destination
restriction. Read the current profiles; do not assume an example profile exists.
An unknown person gets an organization name or neutral recipient label, never an invented name.
The helper fills the saved owner, initial language, duration, and end-call
escalation defaults when omitted. `max_duration_seconds` overrides the default
within the service's current limits. Do not supply owner callback numbers or
deprecated `intention` categories. Use `business` for actual commercial-offer
facts and contact permission; a salesperson speaking style alone is not an offer.
Run `schema prepare_phone_call` for the live schema when additional fields matter.

Prepared plans remain under the helper's private data directory. `start` uses the
saved confirmation summary verbatim. A repeated start first returns plan-ID
recovery. Only after the original plan is confirmed unused and prepared may
`--retry-same-plan` retry that same plan. Never substitute another destination.

`get --call CALL_ID` reads status; `get --plan PLAN_ID` never dials. `history
--query TEXT` searches earlier calls. `contacts --query TEXT` and `contact
CONTACT_ID` read recipient context. A matching number does not authenticate a
person. Do not place sensitive history into a brief merely because a number matches.

`result CALL_ID --output /absolute/folder` chooses the local download folder.
Existing matching files are reused; differing files are preserved. The helper
does not copy Hermes-only file paths into the reply.

`doctor` checks access and readiness. `doctor --check-write-access` submits an
intentionally invalid request that must be rejected before plan creation. It
never prepares or starts a call. An unavailable phone or provider is an
availability problem, not permission to change routing or credentials.
