import { useEffect, useRef, useState } from "react";
import { api, message } from "./api";
import { SettingsDisclosure } from "./Controls";
import {
  date,
  deviceZone,
  followupStates,
  instant,
  lines,
  localInput,
  outcomes,
} from "./workflow";
import type { Brief, Followup, FollowupStatus, Session } from "./types";

export default function Followups({
  session,
  navigate,
  onNew,
}: {
  session: Session;
  navigate: (path: string) => void;
  onNew: (brief?: Brief, followupId?: string) => void;
}) {
  const [rows, setRows] = useState<Followup[]>([]),
    [filter, setFilter] = useState("open"),
    [offset, setOffset] = useState<number | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [selected, setSelected] = useState<string | null>(null);
  const generation = useRef(0);
  async function load(more = false) {
    const request = ++generation.current;
    setLoading(true);
    setError("");
    try {
      const data = await api<{
        followups: Followup[];
        next_offset: number | null;
      }>(
        "/followups?" +
          new URLSearchParams({
            status: filter,
            offset: String(more ? offset || 0 : 0),
          }),
      );
      if (request !== generation.current) return;
      setRows((old) => (more ? [...old, ...data.followups] : data.followups));
      setOffset(data.next_offset);
    } catch (e) {
      if (request === generation.current) setError(message(e));
    } finally {
      if (request === generation.current) setLoading(false);
    }
  }
  useEffect(() => {
    void load();
    return () => {
      generation.current++;
    };
  }, [filter]);
  async function prepare(row: Followup) {
    try {
      const data = await api<{ context: Brief; followup_id: string }>(
        `/followups/${row.followup_id}/brief`,
      );
      onNew(data.context, data.followup_id);
    } catch (e) {
      setError(message(e));
    }
  }
  return (
    <section className="workflow-page" aria-label="Callback work">
      <div className="workflow-content">
        <div className="workflow-toolbar">
          <label className="workflow-filter">
            Status
            <select
              aria-label="Filter status"
              value={filter}
              onChange={(e) => {
                setSelected(null);
                setFilter(e.target.value);
              }}
            >
              <option value="open">Open follow-ups</option>
              <option value="all">All follow-ups</option>
              {Object.entries(followupStates).map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <button onClick={() => load()} disabled={loading}>
            Refresh
          </button>
        </div>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        {loading && !rows.length && <p role="status">Loading follow-ups…</p>}
        {!loading && !rows.length && (
          <div className="empty-state">No follow-ups</div>
        )}
        <div className="followup-list">
          {rows.map((row) => (
            <article key={row.followup_id} className="followup-row">
              <div className="section-heading">
                <div>
                  <h3>{row.target.name}</h3>
                  <span className="muted">
                    {followupStates[row.status]} ·{" "}
                    {row.value.window_start
                      ? date(row.value.window_start, session.timezone)
                      : "No agreed time"}
                  </span>
                </div>
                <button
                  onClick={() =>
                    setSelected(
                      selected === row.followup_id ? null : row.followup_id,
                    )
                  }
                  aria-expanded={selected === row.followup_id}
                >
                  {selected === row.followup_id ? "Close Details" : "Review"}
                </button>
              </div>
              <p className="preserve">{row.value.objective}</p>
              {!!row.value.needed_information.length && (
                <p className="muted">
                  Needed: {row.value.needed_information.join("; ")}
                </p>
              )}
              {row.recipient_opted_out && (
                <p className="error">
                  This recipient requested no further calls.
                </p>
              )}
              <div className="save-row">
                <button
                  onClick={() => navigate("/calls/" + row.source_call_id)}
                >
                  Original Call
                </button>
                {row.status === "ready" && (
                  <button
                    className="primary"
                    disabled={row.recipient_opted_out}
                    onClick={() => prepare(row)}
                  >
                    Prepare Callback
                  </button>
                )}
              </div>
              {selected === row.followup_id && (
                <FollowupEditor
                  key={row.followup_id + ":" + row.revision}
                  row={row}
                  onSaved={(updated) =>
                    setRows((old) =>
                      old.map((v) =>
                        v.followup_id === updated.followup_id ? updated : v,
                      ),
                    )
                  }
                  navigate={navigate}
                />
              )}
            </article>
          ))}
        </div>
        {offset !== null && (
          <button onClick={() => load(true)} disabled={loading}>
            Load More Follow-ups
          </button>
        )}
      </div>
    </section>
  );
}
function FollowupEditor({
  row,
  onSaved,
  navigate,
}: {
  row: Followup;
  onSaved: (row: Followup) => void;
  navigate: (path: string) => void;
}) {
  const [state, setState] = useState(row.status),
    [objective, setObjective] = useState(row.value.objective),
    [needed, setNeeded] = useState(row.value.needed_information.join("\n")),
    [facts, setFacts] = useState((row.value.new_facts || []).join("\n")),
    [note, setNote] = useState(row.value.note),
    [start, setStart] = useState(localInput(row.value.window_start)),
    [end, setEnd] = useState(localInput(row.value.window_end)),
    [completion, setCompletion] = useState(row.value.completion_call_id || ""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const timeChanged =
        start !== localInput(row.value.window_start) ||
        end !== localInput(row.value.window_end);
      const result = await api<Followup>(
        `/followups/${row.followup_id}`,
        "PUT",
        {
          expected_revision: row.revision,
          status: state,
          objective,
          needed_information: lines(needed),
          new_facts: lines(facts),
          note,
          window_start: timeChanged ? instant(start) : row.value.window_start,
          window_end: timeChanged ? instant(end) : row.value.window_end,
          timezone: timeChanged
            ? start
              ? deviceZone()
              : null
            : row.value.timezone,
          completion_call_id: completion || null,
        },
      );
      onSaved(result);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="followup-editor" onSubmit={save}>
      {error && (
        <p role="alert" className="error">
          {error} Refresh the list to load newer changes if needed.
        </p>
      )}
      <label>
        Status
        <select
          value={state}
          onChange={(e) => setState(e.target.value as FollowupStatus)}
        >
          {Object.entries(followupStates).map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
      </label>
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
        Information still needed
        <textarea
          rows={2}
          value={needed}
          onChange={(e) => setNeeded(e.target.value)}
        />
      </label>
      <label>
        Facts for the callback
        <textarea
          rows={3}
          value={facts}
          onChange={(e) => setFacts(e.target.value)}
        />
        <small>
          Verified information gathered since the original call. It will be
          included in the new brief.
        </small>
      </label>
      <SettingsDisclosure
        title="Callback window"
        summary={
          row.value.window_start
            ? "An agreed time is recorded"
            : "No time agreed"
        }
      >
        <p className="muted">
          Shown in this device’s timezone: {deviceZone()}.
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
      <label>
        Progress note
        <textarea
          required={state === "completed"}
          rows={2}
          maxLength={3000}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      {!!row.attempts.length && (
        <>
          <label>
            Completion call
            <select
              value={completion}
              onChange={(e) => setCompletion(e.target.value)}
            >
              <option value="">Resolved without a linked call</option>
              {row.attempts
                .filter(
                  (a) =>
                    a.call_id &&
                    [
                      "completed",
                      "failed",
                      "timed_out",
                      "transferred",
                    ].includes(a.state || ""),
                )
                .map((a) => (
                  <option key={a.call_id} value={a.call_id!}>
                    {a.created_at
                      ? new Date(a.created_at).toLocaleString()
                      : "Callback"}{" "}
                    · {a.outcome ? outcomes[a.outcome] : "No outcome"}
                  </option>
                ))}
            </select>
          </label>
          <h4>Call attempts</h4>
          <ul className="plain-list">
            {row.attempts.map((a) => (
              <li key={a.plan_id}>
                {a.call_id ? (
                  <button
                    type="button"
                    onClick={() => navigate("/calls/" + a.call_id)}
                  >
                    {a.state?.replaceAll("_", " ")} ·{" "}
                    {a.outcome ? outcomes[a.outcome] : "Outcome not recorded"}
                  </button>
                ) : (
                  <span className="muted">Prepared; not dialed</span>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      <button type="submit" className="primary" disabled={busy}>
        {busy ? "Saving…" : "Save Follow-up"}
      </button>
      <p className="muted">
        Saving work or a time window does not schedule or place a call.
      </p>
    </form>
  );
}
