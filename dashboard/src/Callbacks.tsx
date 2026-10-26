import { useEffect, useState } from "react";
import { api, message } from "./api";
import { SectionTabs } from "./Controls";
import Followups from "./Followups";
import {
  date,
  deviceZone,
  zonedInstant as instant,
  localInput,
} from "./workflow";
import type { Brief, Call, ScheduledCallback, Session } from "./types";

export default function Callbacks({
  session,
  navigate,
  onNew,
}: {
  session: Session;
  navigate: (path: string) => void;
  onNew: (brief?: Brief, followupId?: string) => void;
}) {
  const [tab, setTab] = useState(
      location.pathname.endsWith("/followups") ? "manual" : "scheduled",
    ),
    [filter, setFilter] = useState("open"),
    [rows, setRows] = useState<ScheduledCallback[]>([]),
    [offset, setOffset] = useState<number | null>(null);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [editing, setEditing] = useState<ScheduledCallback | "new" | null>(null);
  async function load(more = false) {
    setBusy(true);
    try {
      const data = await api<{
        callbacks: ScheduledCallback[];
        next_offset: number | null;
      }>(
        "/callbacks?" +
          new URLSearchParams({
            status: filter,
            offset: String(more ? offset || 0 : 0),
          }),
      );
      setRows((old) => (more ? [...old, ...data.callbacks] : data.callbacks));
      setOffset(data.next_offset);
      setError("");
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 10000);
    return () => clearInterval(timer);
  }, [filter]);
  async function action(row: ScheduledCallback, action: "cancel" | "call_now") {
    if (
      action === "call_now" &&
      !window.confirm(
        `Call ${row.destination} now for “${row.value.objective}”? This uses this request's one automatic attempt.`,
      )
    )
      return;
    setBusy(true);
    try {
      await api("/callbacks/" + row.schedule_id, "PUT", {
        expected_revision: row.revision,
        action,
        reason: "Owner request from dashboard",
      });
      await load();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="callbacks-workspace">
      <SectionTabs
        label="Callback workspace"
        value={tab}
        onChange={setTab}
        items={[
          { id: "scheduled", label: "Scheduled" },
          { id: "manual", label: "Follow-ups" },
        ]}
      >
        {tab === "manual" ? (
          <Followups session={session} navigate={navigate} onNew={onNew} />
        ) : (
          <section className="workflow-page" aria-label="Scheduled callbacks">
            <div className="workflow-content">
              {error && (
                <p className="error" role="alert">
                  {error}
                </p>
              )}
              <div className="workflow-toolbar">
                <label className="workflow-filter">
                  Status
                  <select
                    aria-label="Filter status"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                  >
                    {[
                      "open",
                      "all",
                      "needs_information",
                      "missed",
                      "cancelled",
                      "attempted",
                      "uncertain",
                    ].map((v) => (
                      <option key={v} value={v}>
                        {(v[0].toUpperCase() + v.slice(1)).replaceAll("_", " ")}
                      </option>
                    ))}
                  </select>
                </label>
                <button onClick={() => setEditing("new")}>
                  Schedule Callback
                </button>
              </div>
              {editing && (
                <ScheduleEditor
                  key={
                    typeof editing === "string" ? editing : editing.schedule_id
                  }
                  row={editing === "new" ? undefined : editing}
                  onClose={() => setEditing(null)}
                  onSaved={() => {
                    setEditing(null);
                    void load();
                  }}
                />
              )}
              {!rows.length && !editing && (
                <p className="empty-state">
                  {busy ? "Loading callbacks…" : "No callbacks"}
                </p>
              )}
              <div className="schedule-list">
                {rows.map((row) => (
                  <article className="schedule-row" key={row.schedule_id}>
                    <div className="section-heading">
                      <strong>{row.value.objective}</strong>
                      <span className="status-label">
                        {row.state.replaceAll("_", " ")}
                      </span>
                    </div>
                    <p>
                      {row.destination}
                      {row.value.alternate_number
                        ? " · Confirmed alternate number"
                        : ""}
                    </p>
                    <p>
                      {date(row.window_start, session.timezone)} –{" "}
                      {date(row.window_end, session.timezone)}{" "}
                      <span className="muted">{session.timezone}</span>
                    </p>
                    {row.reason && <p className="muted">{row.reason}</p>}
                    {row.value.needed_information?.length ? (
                      <RequiredInformation
                        row={row}
                        onSaved={() => void load()}
                      />
                    ) : null}
                    <div className="save-row">
                      <button
                        onClick={() =>
                          navigate(
                            "/calls/" + (row.call_id || row.source_call_id),
                          )
                        }
                      >
                        {row.call_id ? "Open Attempt" : "Source Conversation"}
                      </button>
                      {!row.dial_claimed &&
                        ![
                          "cancelled",
                          "attempted",
                          "completed",
                          "uncertain",
                        ].includes(row.state) && (
                          <>
                            <button
                              disabled={busy}
                              onClick={() => setEditing(row)}
                            >
                              Reschedule
                            </button>
                            <button
                              disabled={
                                busy ||
                                Boolean(row.value.needed_information?.length)
                              }
                              onClick={() => action(row, "call_now")}
                            >
                              Call Now
                            </button>
                            <button
                              disabled={busy}
                              onClick={() => action(row, "cancel")}
                            >
                              Cancel Callback
                            </button>
                          </>
                        )}
                    </div>
                  </article>
                ))}
              </div>
              {offset !== null && (
                <button disabled={busy} onClick={() => load(true)}>
                  Load More Callbacks
                </button>
              )}
            </div>
          </section>
        )}
      </SectionTabs>
    </div>
  );
}

function ScheduleEditor({
  row,
  onClose,
  onSaved,
}: {
  row?: ScheduledCallback;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [calls, setCalls] = useState<Call[]>([]),
    [source, setSource] = useState(row?.source_call_id || ""),
    [objective, setObjective] = useState(row?.value.objective || ""),
    [number, setNumber] = useState(row?.destination || "");
  const [needed, setNeeded] = useState(
    (row?.value.needed_information || []).join("\n"),
  );
  const [start, setStart] = useState(row ? localInput(row.window_start) : ""),
    [end, setEnd] = useState(row ? localInput(row.window_end) : ""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [reviewing, setReviewing] = useState(false);
  useEffect(() => {
    if (!row)
      api<{ calls: Call[] }>("/calls?limit=100")
        .then((data) => setCalls(data.calls))
        .catch((e) => setError(message(e)));
  }, [row]);
  async function save() {
    setBusy(true);
    setError("");
    try {
      const proposal = {
        request_key: row?.value.request_key || "owner_" + crypto.randomUUID(),
        objective,
        destination: number,
        window_start: instant(start),
        window_end: instant(end),
        timezone: deviceZone(),
        language: row?.value.language,
        needed_information: needed
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
      };
      if (!proposal.window_start || !proposal.window_end)
        throw new Error("Choose both ends of the agreed window.");
      await api(
        row
          ? "/callbacks/" + row.schedule_id
          : "/calls/" + source + "/callbacks",
        row ? "PUT" : "POST",
        row
          ? {
              expected_revision: row.revision,
              action: "reschedule",
              proposal,
              reason: "Owner confirmed new callback details",
            }
          : proposal,
      );
      onSaved();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="schedule-editor">
      <div className="section-heading">
        <h3>{row ? "Reschedule callback" : "Schedule a callback"}</h3>
        <button onClick={onClose}>Close</button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!reviewing ? (
        <div className="form-section">
          {!row && (
            <label>
              Source conversation
              <select
                value={source}
                onChange={(e) => {
                  setSource(e.target.value);
                  const call = calls.find((c) => c.call_id === e.target.value);
                  if (call) {
                    setNumber(
                      call.target.phone.startsWith("+")
                        ? call.target.phone
                        : "",
                    );
                    setObjective(call.objective);
                  }
                }}
              >
                <option value="">Choose a conversation</option>
                {calls.map((call) => (
                  <option key={call.call_id} value={call.call_id}>
                    {call.target.name} · {call.target.phone} ·{" "}
                    {new Date(call.created_at).toLocaleDateString()}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            Callback task
            <textarea
              rows={3}
              value={objective}
              onChange={(e) => setObjective(e.target.value)}
            />
          </label>
          <label>
            Confirmed number
            <input
              type="tel"
              value={number}
              onChange={(e) => setNumber(e.target.value)}
              placeholder="+34…"
            />
          </label>
          <p className="field-help">
            The agreed task keeps the source call's authority and limits.
            Requests needing facts wait until you supply the answers.
          </p>
          <label>
            Required information (optional)
            <textarea
              rows={3}
              value={needed}
              onChange={(e) => setNeeded(e.target.value)}
              placeholder="Questions that must be answered before calling"
            />
          </label>
          <div className="form-grid">
            <label>
              Window starts
              <input
                type="datetime-local"
                value={start}
                onChange={(e) => {
                  setStart(e.target.value);
                  if (!end && e.target.value)
                    setEnd(
                      localInput(
                        new Date(
                          new Date(e.target.value).getTime() + 15 * 60000,
                        ).toISOString(),
                      ),
                    );
                }}
              />
            </label>
            <label>
              Window ends
              <input
                type="datetime-local"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
            </label>
          </div>
          <p className="field-help">
            Times shown in {deviceZone()}. The server also checks the saved
            callback hours and timezone.
          </p>
          <button
            className="primary"
            disabled={!source || !objective.trim() || !number || !start || !end}
            onClick={() => setReviewing(true)}
          >
            Review Schedule
          </button>
        </div>
      ) : (
        <div className="form-section">
          <p>
            Call <strong>{number}</strong> about <strong>{objective}</strong>.
          </p>
          <p>
            {date(instant(start)!, deviceZone())} –{" "}
            {date(instant(end)!, deviceZone())} ({deviceZone()}).
          </p>
          <p>
            {needed.trim()
              ? "This request will wait for the required information. It cannot dial until you resolve those questions."
              : "One automatic dialing attempt. This authorizes the calling service to start within this window."}
          </p>
          <div className="save-row">
            <button onClick={() => setReviewing(false)}>Edit Details</button>
            <button className="primary" disabled={busy} onClick={save}>
              {busy ? "Saving…" : "Confirm Schedule"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function RequiredInformation({
  row,
  onSaved,
}: {
  row: ScheduledCallback;
  onSaved: () => void;
}) {
  const [facts, setFacts] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <details className="settings-disclosure">
      <summary>Waiting for required information</summary>
      <ul>
        {row.value.needed_information?.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      {row.state === "needs_information" && (
        <>
          <label>
            Verified answers
            <textarea
              rows={3}
              value={facts}
              onChange={(e) => setFacts(e.target.value)}
            />
          </label>
          <p className="field-help">
            Confirm that these answers resolve every question above. The
            callback can then run within its agreed window.
          </p>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          <button
            disabled={busy || !facts.trim()}
            onClick={async () => {
              setBusy(true);
              setError("");
              try {
                await api("/callbacks/" + row.schedule_id, "PUT", {
                  expected_revision: row.revision,
                  action: "resolve_information",
                  needed_information: [],
                  new_facts: facts
                    .split("\n")
                    .map((s) => s.trim())
                    .filter(Boolean),
                  reason: "Owner supplied and verified the required answers",
                });
                onSaved();
              } catch (e) {
                setError(message(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Confirm Answers and Release Callback
          </button>
        </>
      )}
    </details>
  );
}
