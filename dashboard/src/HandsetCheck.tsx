import { useEffect, useState } from "react";
import { api, message } from "./api";
import type { Call, ScheduledCallback } from "./types";

type Acceptance = { passed: boolean; window_until: string | null };
export default function HandsetCheck() {
  const [status, setStatus] = useState<Acceptance>(),
    [calls, setCalls] = useState<Call[]>([]),
    [callbacks, setCallbacks] = useState<ScheduledCallback[]>([]);
  const [open, setOpen] = useState(false),
    [call, setCall] = useState(""),
    [callback, setCallback] = useState(""),
    [number, setNumber] = useState("");
  const [greeting, setGreeting] = useState(false),
    [recording, setRecording] = useState(false),
    [fallback, setFallback] = useState(false),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function refresh() {
    try {
      setStatus(await api<Acceptance>("/inbound/acceptance"));
      if (open) {
        const history = await api<{ calls: Call[] }>(
          "/calls?direction=inbound&limit=100",
        );
        setCalls(history.calls.filter((c) => c.state === "completed"));
        const scheduled = await api<{ callbacks: ScheduledCallback[] }>(
          "/callbacks?status=all",
        );
        setCallbacks(scheduled.callbacks);
      }
    } catch (e) {
      setError(message(e));
    }
  }
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 10000);
    return () => clearInterval(timer);
  }, [open]);
  async function windowChange(active: boolean) {
    setBusy(true);
    setError("");
    try {
      setStatus(
        await api<Acceptance>("/inbound/test-window", "POST", { open: active }),
      );
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function accept() {
    setBusy(true);
    setError("");
    try {
      setStatus(
        await api<Acceptance>("/inbound/acceptance", "POST", {
          inbound_call_id: call,
          callback_schedule_id: callback,
          caller_number: number,
          greeting_heard: greeting,
          recording_checked: recording,
          native_fallback_checked: fallback,
        }),
      );
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <details
      className="handset-check"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>
        Handset acceptance
        {status?.passed ? " · Passed" : " · Required before enabling reception"}
      </summary>
      <p>
        Finish business setup, then call the Selected SIM line from a phone you
        control. A test window allows incoming reception for five minutes while
        the answering switch remains off.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {status?.window_until ? (
        <div className="save-row">
          <p role="status">
            Test answering until{" "}
            {new Date(status.window_until).toLocaleTimeString()}.
          </p>
          <button disabled={busy} onClick={() => windowChange(false)}>
            Close Test Window
          </button>
        </div>
      ) : (
        <button disabled={busy} onClick={() => windowChange(true)}>
          Open Five-Minute Test Window
        </button>
      )}
      <ol>
        <li>
          Listen for a complete greeting and check the displayed caller number.
        </li>
        <li>
          During that call, agree on a callback within your callback policy.
          Answer the scheduled callback and finish it.
        </li>
        <li>Play the incoming call's recording in Calls.</li>
        <li>
          Close the test window with answering off. Call again and check that
          the native phone still rings.
        </li>
      </ol>
      <p className="muted">
        These are live handset checks. Practice and synthetic tests cannot
        establish what a person hears.
      </p>
      <div className="form-section">
        <label>
          Completed incoming test
          <select
            value={call}
            onChange={(e) => {
              setCall(e.target.value);
              setCallback("");
            }}
          >
            <option value="">Choose a completed incoming call</option>
            {calls.map((row) => (
              <option key={row.call_id} value={row.call_id}>
                {row.target.phone} · {new Date(row.created_at).toLocaleString()}
              </option>
            ))}
          </select>
        </label>
        <label>
          Number you called from
          <input
            type="tel"
            value={number}
            onChange={(e) => setNumber(e.target.value)}
            placeholder="Full international number"
          />
        </label>
        <label>
          Scheduled callback from that conversation
          <select
            value={callback}
            onChange={(e) => setCallback(e.target.value)}
          >
            <option value="">Choose the answered callback</option>
            {callbacks
              .filter((row) => row.source_call_id === call && row.call_id)
              .map((row) => (
                <option key={row.schedule_id} value={row.schedule_id}>
                  {row.destination} ·{" "}
                  {new Date(row.window_start).toLocaleString()}
                </option>
              ))}
          </select>
        </label>
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={greeting}
            onChange={(e) => setGreeting(e.target.checked)}
          />
          I heard the complete greeting and clear conversation audio.
        </label>
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={recording}
            onChange={(e) => setRecording(e.target.checked)}
          />
          I played the incoming recording and checked both sides.
        </label>
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={fallback}
            onChange={(e) => setFallback(e.target.checked)}
          />
          With answering off and the test window closed, the native phone rang
          normally.
        </label>
        <button
          className="primary"
          disabled={
            busy ||
            !call ||
            !callback ||
            !number ||
            !greeting ||
            !recording ||
            !fallback
          }
          onClick={accept}
        >
          Save Handset Acceptance
        </button>
        {status?.passed && (
          <p role="status" className="success-notice">
            Handset acceptance is recorded. You can now enable incoming
            reception.
          </p>
        )}
      </div>
    </details>
  );
}
