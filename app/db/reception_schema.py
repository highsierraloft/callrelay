"""Additive reception storage. Historical transcripts and plans are not rewritten."""

RECEPTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS call_opt_outs (
 call_id TEXT PRIMARY KEY REFERENCES calls(call_id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
 contact_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, name TEXT NOT NULL,
 background TEXT NOT NULL, private_note TEXT NOT NULL, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, actor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contact_numbers (
 contact_id TEXT NOT NULL REFERENCES contacts(contact_id), phone TEXT NOT NULL,
 source TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(contact_id,phone)
);
CREATE INDEX IF NOT EXISTS contact_numbers_phone ON contact_numbers(phone);
CREATE TABLE IF NOT EXISTS contact_history (
 contact_id TEXT NOT NULL REFERENCES contacts(contact_id), revision INTEGER NOT NULL,
 value_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(contact_id,revision)
);
CREATE TABLE IF NOT EXISTS contact_memory (
 contact_id TEXT NOT NULL REFERENCES contacts(contact_id), revision INTEGER NOT NULL,
 value_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(contact_id,revision)
);
CREATE TABLE IF NOT EXISTS call_contacts (
 call_id TEXT PRIMARY KEY REFERENCES calls(call_id), contact_id TEXT REFERENCES contacts(contact_id),
 direction TEXT NOT NULL CHECK(direction IN ('inbound','outbound')),
 phone TEXT, recognition TEXT NOT NULL, channel_id TEXT,
 memory_json TEXT NOT NULL, automatic_review INTEGER NOT NULL DEFAULT 0,
 answer_requested INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS call_contacts_contact ON call_contacts(contact_id,created_at);
CREATE TABLE IF NOT EXISTS call_summaries (
 call_id TEXT NOT NULL REFERENCES calls(call_id), revision INTEGER NOT NULL,
 value_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(call_id,revision)
);
CREATE TABLE IF NOT EXISTS reception_reviews (
 job_id TEXT PRIMARY KEY, call_id TEXT NOT NULL UNIQUE REFERENCES calls(call_id),
 state TEXT NOT NULL, lease_token TEXT, lease_until TEXT, lease_contact_id TEXT, attempts INTEGER NOT NULL DEFAULT 0,
 available_at TEXT NOT NULL, transcript_digest TEXT, read_through INTEGER NOT NULL DEFAULT 0,
 error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reception_reviews_due ON reception_reviews(state,available_at);
CREATE TABLE IF NOT EXISTS call_contact_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT NOT NULL REFERENCES calls(call_id),
 previous_contact_id TEXT, contact_id TEXT, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reception_review_receipts (
 request_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, result_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS callback_proposals (
 proposal_id TEXT PRIMARY KEY, call_id TEXT NOT NULL REFERENCES calls(call_id),
 request_key TEXT NOT NULL, revision INTEGER NOT NULL, value_json TEXT NOT NULL,
 readback TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 after_sequence INTEGER NOT NULL, UNIQUE(call_id,request_key)
);
CREATE TABLE IF NOT EXISTS callback_schedules (
 schedule_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL UNIQUE REFERENCES callback_proposals(proposal_id),
 source_call_id TEXT NOT NULL REFERENCES calls(call_id), contact_id TEXT REFERENCES contacts(contact_id),
 revision INTEGER NOT NULL, state TEXT NOT NULL, value_json TEXT NOT NULL,
 window_start TEXT NOT NULL, window_end TEXT NOT NULL, destination TEXT NOT NULL,
 plan_id TEXT UNIQUE REFERENCES plans(plan_id), call_id TEXT UNIQUE REFERENCES calls(call_id),
 dial_claimed INTEGER NOT NULL DEFAULT 0, reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS callback_schedules_due ON callback_schedules(state,window_start);
CREATE TABLE IF NOT EXISTS callback_schedule_history (
 schedule_id TEXT NOT NULL REFERENCES callback_schedules(schedule_id), revision INTEGER NOT NULL,
 value_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(schedule_id,revision)
);
CREATE TABLE IF NOT EXISTS callback_confirmation_receipts (
 proposal_id TEXT PRIMARY KEY REFERENCES callback_proposals(proposal_id),
 schedule_id TEXT NOT NULL REFERENCES callback_schedules(schedule_id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reception_state (
 name TEXT PRIMARY KEY, value_json TEXT NOT NULL
);
"""
