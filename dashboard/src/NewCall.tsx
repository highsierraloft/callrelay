import { useEffect, useRef, useState } from "react";
import { Phone, X, ChevronLeft } from "lucide-react";
import { api, message, ApiError } from "./api";
import BriefFields from "./BriefFields";
import { SettingsDisclosure } from "./Controls";
import {
  languages,
  sampleBrief,
  type Brief,
  type Session,
  type Language,
} from "./types";

export default function NewCall({
  session,
  initial,
  followupId,
  onClose,
  onStarted,
}: {
  session: Session;
  initial?: Brief;
  followupId?: string;
  onClose: () => void;
  onStarted: (id: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [brief, setBrief] = useState<Brief>(() =>
    initial
      ? {
          ...initial,
          intention: undefined,
          owner: session.setup.owner,
          escalation: { mode: "end_call" },
        }
      : {
          ...sampleBrief(session.setup),
          target: { name: "", phone: "" },
          objective: "",
        },
  );
  const [prepared, setPrepared] = useState<{
      plan_id: string;
      confirmation_summary: string;
    } | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [uncertain, setUncertain] = useState(false);
  useEffect(() => {
    dialog.current?.showModal();
    try {
      const saved = sessionStorage.getItem("calling-pending-start");
      if (saved) {
        const plan = JSON.parse(saved);
        setPrepared(plan);
        setUncertain(true);
        void recover(plan.plan_id);
      }
    } catch {
      /* A missing browser storage entry does not start a new call. */
    }
  }, []);
  async function recover(planId = prepared?.plan_id) {
    if (!planId) return;
    setBusy(true);
    try {
      const status = await api<{ call_id: string | null; state: string }>(
        "/plans/" + encodeURIComponent(planId),
      );
      if (status.call_id) {
        sessionStorage.removeItem("calling-pending-start");
        onStarted(status.call_id);
      } else if (status.state === "expired") {
        sessionStorage.removeItem("calling-pending-start");
        setUncertain(false);
        setPrepared(null);
        setError(
          "The original plan expired without starting a call. Review the brief again.",
        );
      } else {
        setUncertain(true);
        setError(
          "The original plan has not started yet. Check again, or retry this same plan safely.",
        );
      }
    } catch (e) {
      setUncertain(true);
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  const update = <K extends keyof Brief>(key: K, value: Brief[K]) =>
    setBrief((v) => ({ ...v, [key]: value }));
  async function prepare(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const r = await api<{
        plan_id: string | null;
        confirmation_summary: string;
        missing_fields: string[];
      }>("/calls/prepare", "POST", {
        context: {
          ...brief,
          target: {
            ...brief.target,
            phone: brief.target.phone.replace(/[\s().-]/g, ""),
          },
        },
        followup_id: followupId,
        requested_by_owner: true,
        authority_basis: "Owner reviewed this call in the private dashboard",
      });
      if (!r.plan_id)
        throw new Error(
          "Complete these details: " + r.missing_fields.join(", "),
        );
      setPrepared({
        plan_id: r.plan_id,
        confirmation_summary: r.confirmation_summary,
      });
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function start() {
    if (!prepared) return;
    setBusy(true);
    setError("");
    try {
      sessionStorage.setItem("calling-pending-start", JSON.stringify(prepared));
      const r = await api<{ call_id: string }>("/calls/start", "POST", {
        ...prepared,
        confirmation_text: prepared.confirmation_summary,
        confirmation_summary: undefined,
        explicit_confirmation: true,
      });
      sessionStorage.removeItem("calling-pending-start");
      onStarted(r.call_id);
    } catch (e) {
      setError(message(e));
      if (e instanceof TypeError || e instanceof ApiError) {
        setUncertain(true);
        await recover();
      }
    } finally {
      setBusy(false);
    }
  }
  return (
    <dialog
      ref={dialog}
      className="call-dialog"
      aria-labelledby="call-dialog-title"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onClose();
      }}
    >
      <header>
        <div>
          <h2 id="call-dialog-title">
            {prepared ? "Review Call" : "New Call"}
          </h2>
          <p className="muted">Calling through {session.setup.source_line}</p>
        </div>
        <button
          aria-label="Close call form"
          className="icon-button"
          onClick={onClose}
          disabled={busy}
        >
          <X size={20} />
        </button>
      </header>
      {error && (
        <p className="error" role="alert">
          {error}
          {uncertain && " Use Check Status to recover the original call."}
        </p>
      )}
      {prepared ? (
        <>
          <p className="review-summary">{prepared.confirmation_summary}</p>
          <p className="muted">
            This call will use the saved personality settings from this
            preparation.
          </p>
          <footer>
            {uncertain && (
              <button onClick={() => recover()} disabled={busy}>
                Check Status
              </button>
            )}
            <button
              onClick={() => setPrepared(null)}
              disabled={busy || uncertain}
            >
              <ChevronLeft size={16} />
              Edit Brief
            </button>
            <button className="primary" onClick={start} disabled={busy}>
              <Phone size={16} />
              {busy
                ? "Checking…"
                : uncertain
                  ? "Retry Same Plan"
                  : "Call " + brief.target.name}
            </button>
          </footer>
        </>
      ) : (
        <form onSubmit={prepare}>
          <label>
            Personality
            <select
              value={brief.profile_id || brief.personality || "warm"}
              onChange={(e) => update("profile_id", e.target.value)}
            >
              {session.setup.profiles.map((p) => (
                <option value={p.id} key={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <BriefFields brief={brief} onChange={setBrief} phone />
          <SettingsDisclosure
            title="Call Settings"
            summary={`${session.setup.profiles.find((p) => p.id === brief.profile_id)?.name || "Warm"} · ${languages[brief.language]} · ${brief.max_duration_seconds / 60} min`}
          >
            <div className="two-fields">
              <label>
                Initial language
                <select
                  value={brief.language}
                  onChange={(e) =>
                    update("language", e.target.value as Language)
                  }
                >
                  {Object.entries(languages).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Maximum minutes
                <input
                  type="number"
                  min="0.5"
                  max="120"
                  step="0.5"
                  value={brief.max_duration_seconds / 60}
                  onChange={(e) =>
                    update("max_duration_seconds", Number(e.target.value) * 60)
                  }
                />
              </label>
            </div>
          </SettingsDisclosure>
          <footer>
            <button type="button" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="primary" disabled={busy}>
              {busy ? "Preparing…" : "Review Call"}
            </button>
          </footer>
        </form>
      )}
    </dialog>
  );
}
