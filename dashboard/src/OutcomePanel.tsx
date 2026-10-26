import { useEffect, useState } from "react";
import { api, message, ApiError } from "./api";
import { SettingsDisclosure } from "./Controls";
import {
  deviceZone,
  followupStates,
  instant,
  lines,
  outcomes,
} from "./workflow";
import type {
  CallAssessment,
  CallbackDraft,
  Detail,
  Followup,
  OutcomeStatus,
} from "./types";

export default function OutcomePanel({
  detail,
  onUpdated,
  onFollowups,
  onEvidence,
}: {
  detail: Detail;
  onUpdated: (assessment: CallAssessment | null, followups: Followup[]) => void;
  onFollowups: () => void;
  onEvidence: () => void;
}) {
  const [record, setRecord] = useState<CallAssessment | null>(
    detail.assessment ?? null,
  );
  const [editing, setEditing] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [status, setStatus] = useState<OutcomeStatus>(
    record?.value.status || "uncertain",
  );
  const [explanation, setExplanation] = useState(
    record?.value.explanation || "",
  );
  const [facts, setFacts] = useState(
    record?.value.facts.map((f) => f.text).join("\n") || "",
  );
  const [evidence, setEvidence] = useState<string[]>(
    record?.value.evidence_turn_ids || [],
  );
  const [callback, setCallback] = useState(false),
    [objective, setObjective] = useState(""),
    [needed, setNeeded] = useState("");
  const [start, setStart] = useState(""),
    [end, setEnd] = useState("");
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const [callbackKey, setCallbackKey] = useState(() => crypto.randomUUID());
  const [conflict, setConflict] = useState(false);
  const turns = detail.transcript.filter(
    (t) => t.turn_id && t.speaker !== "system",
  );
  function load(next: CallAssessment | null) {
    setRecord(next);
    setStatus(next?.value.status || "uncertain");
    setExplanation(next?.value.explanation || "");
    setFacts(next?.value.facts.map((f) => f.text).join("\n") || "");
    setEvidence(next?.value.evidence_turn_ids || []);
    setConflict(false);
    setError("");
    setRequestId(crypto.randomUUID());
  }
  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const next = await api<{
          assessment: CallAssessment | null;
          followups: Followup[];
        }>(`/calls/${detail.call_id}/outcome`);
        if (!alive) return;
        onUpdated(next.assessment, next.followups);
        if (next.assessment?.revision !== record?.revision) {
          if (editing) setConflict(true);
          else load(next.assessment);
        }
      } catch {
        /* Keep the current review available during a transient refresh failure. */
      }
    };
    const timer = setInterval(refresh, 10000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [detail.call_id, editing, record?.revision]);
  async function reload() {
    const data = await api<{
      assessment: CallAssessment | null;
      followups: Followup[];
    }>(`/calls/${detail.call_id}/outcome`);
    load(data.assessment);
    onUpdated(data.assessment, data.followups);
  }
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const confirmed = lines(facts).map((text) => ({
        text,
        evidence_turn_ids:
          record?.value.facts.find((f) => f.text === text)?.evidence_turn_ids ||
          evidence,
      }));
      if (confirmed.some((f) => !f.evidence_turn_ids.length))
        throw new Error(
          "Select transcript lines to support the confirmed facts.",
        );
      const callbacks: CallbackDraft[] = callback
        ? [
            {
              source_key: callbackKey,
              source: "owner_request",
              objective,
              needed_information: lines(needed),
              evidence_turn_ids: evidence,
              window_start: instant(start),
              window_end: instant(end),
              timezone: start ? deviceZone() : null,
            },
          ]
        : [];
      await api(`/calls/${detail.call_id}/outcome`, "PUT", {
        expected_revision: record?.revision || 0,
        request_id: requestId,
        status,
        explanation,
        evidence_turn_ids: evidence,
        facts: confirmed,
        callbacks,
      });
      await reload();
      setCallback(false);
      setObjective("");
      setNeeded("");
      setStart("");
      setEnd("");
      setCallbackKey(crypto.randomUUID());
      setEditing(false);
    } catch (e) {
      setError(message(e));
      if (e instanceof ApiError && e.status === 409) setConflict(true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="reading outcome-panel">
      <div className="section-heading">
        <h3>Task outcome</h3>
        <button
          onClick={() => {
            if (editing) {
              load(record);
              setCallback(false);
              setObjective("");
              setNeeded("");
              setStart("");
              setEnd("");
              setCallbackKey(crypto.randomUUID());
            }
            setEditing(!editing);
          }}
          disabled={busy}
        >
          {editing ? "Cancel Editing" : record ? "Edit Outcome" : "Add Outcome"}
        </button>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {conflict && (
        <p role="alert">
          Another saved assessment is available. Your edits are retained.{" "}
          <button onClick={() => reload().catch((e) => setError(message(e)))}>
            Load Saved Outcome
          </button>
        </p>
      )}
      {editing ? (
        <form
          onSubmit={save}
          onChange={() => setRequestId(crypto.randomUUID())}
        >
          <label>
            Outcome
            <select
              aria-label="Outcome"
              value={status}
              onChange={(e) => setStatus(e.target.value as OutcomeStatus)}
            >
              {Object.entries(outcomes).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            What happened?
            <textarea
              aria-label="What happened?"
              required
              rows={3}
              maxLength={4000}
              value={explanation}
              onChange={(e) => setExplanation(e.target.value)}
            />
          </label>
          <label>
            Confirmed facts
            <textarea
              rows={3}
              value={facts}
              onChange={(e) => setFacts(e.target.value)}
            />
            <small>
              One fact per line. New facts need supporting transcript lines.
            </small>
          </label>
          <SettingsDisclosure
            title="Supporting transcript lines"
            summary={`${evidence.length} selected`}
          >
            <div className="evidence-picker">
              {turns.map((t) => (
                <label className="check" key={t.turn_id}>
                  <input
                    type="checkbox"
                    checked={evidence.includes(t.turn_id!)}
                    onChange={(e) =>
                      setEvidence((old) =>
                        e.target.checked
                          ? [...old, t.turn_id!]
                          : old.filter((id) => id !== t.turn_id),
                      )
                    }
                  />
                  <span>
                    {t.speaker}: {t.text}
                  </span>
                </label>
              ))}
            </div>
            {!turns.length && (
              <p className="muted">
                No transcript evidence is available. You can still save your own
                assessment.
              </p>
            )}
          </SettingsDisclosure>
          <label className="check">
            <input
              type="checkbox"
              checked={callback}
              onChange={(e) => setCallback(e.target.checked)}
            />
            Add a callback
          </label>
          {callback && (
            <fieldset>
              <legend>Callback work</legend>
              <label>
                Callback task
                <textarea
                  required
                  rows={2}
                  maxLength={4000}
                  value={objective}
                  onChange={(e) => setObjective(e.target.value)}
                />
              </label>
              <label>
                Information needed first
                <textarea
                  rows={2}
                  value={needed}
                  onChange={(e) => setNeeded(e.target.value)}
                />
                <small>One missing fact per line. Leave empty if ready.</small>
              </label>
              <SettingsDisclosure
                title="Agreed callback window"
                summary="Optional; only if a time was agreed"
              >
                <p className="muted">
                  Times use this device’s timezone: {deviceZone()}.
                </p>
                <div className="two-fields">
                  <label>
                    From
                    <input
                      type="datetime-local"
                      value={start}
                      onChange={(e) => setStart(e.target.value)}
                    />
                  </label>
                  <label>
                    Until
                    <input
                      type="datetime-local"
                      value={end}
                      onChange={(e) => setEnd(e.target.value)}
                    />
                  </label>
                </div>
              </SettingsDisclosure>
              <p className="muted">
                This creates follow-up work. Use Prepare Callback when you are
                ready to review a call.
              </p>
            </fieldset>
          )}
          <button type="submit" className="primary" disabled={busy || conflict}>
            {busy ? "Saving…" : "Save Outcome"}
          </button>
        </form>
      ) : record ? (
        <>
          <p>
            <strong>{outcomes[record.value.status]}</strong>{" "}
            <span className="muted">
              ·{" "}
              {record.actor === "hermes"
                ? "Hermes assessment"
                : "Your assessment"}
            </span>
          </p>
          <p className="preserve">{record.value.explanation}</p>
          {!!record.value.facts.length && (
            <>
              <h4>Confirmed facts</h4>
              <ul>
                {record.value.facts.map((f, i) => (
                  <li key={i}>{f.text}</li>
                ))}
              </ul>
            </>
          )}
          {!!record.value.evidence_turn_ids.length && (
            <button onClick={onEvidence}>Read Supporting Transcript</button>
          )}
        </>
      ) : (
        <p className="muted">No outcome recorded.</p>
      )}
      <div className="section-heading">
        <h3>Follow-ups</h3>
        <button onClick={onFollowups}>Manage Follow-ups</button>
      </div>
      {detail.followups?.length ? (
        <ul className="plain-list">
          {detail.followups.map((f) => (
            <li key={f.followup_id}>
              <strong>{f.value.objective}</strong>
              <p className="muted">
                {followupStates[f.status]}
                {f.value.window_start
                  ? ` · ${new Date(f.value.window_start).toLocaleString()}`
                  : " · No agreed time"}
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No follow-ups</p>
      )}
    </section>
  );
}
