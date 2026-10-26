import { useEffect, useRef, useState } from "react";
import { FlaskConical, Square } from "lucide-react";
import { api, message } from "./api";
import { stopVoicePreview } from "./VoicePreview";
import type { ConfigurationCheck as Check, Brief } from "./types";

const active = (run: Check | null) =>
  !!run && ["running", "stopping"].includes(run.state);
const labels: Record<string, string> = {
  corrected_details: "Corrected details",
  missing_facts: "Missing facts and callbacks",
  unauthorized_commitment: "Authority and commitments",
  refusal: "Refusal and opt-out",
  language_switching: "Language switching",
  premature_ending: "Ending at the right time",
};
export function useConfigurationCheck(
  profileId: string,
  context: Brief,
  overrides: () => unknown,
  onActivity: () => Promise<unknown>,
) {
  const [run, setRun] = useState<Check | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [visible, setVisible] = useState(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    let valid = true;
    api<{ checks: Check[]; active_id: string | null }>(
      "/checks?profile_id=" + encodeURIComponent(profileId),
    )
      .then(async (data) => {
        const next = data.active_id
          ? await api<Check>("/checks/" + data.active_id)
          : data.checks[0] || null;
        if (valid) setRun(next);
      })
      .catch(() => {});
    return () => {
      valid = false;
    };
  }, [profileId]);
  useEffect(() => {
    if (!active(run)) return;
    let valid = true,
      pending = false;
    const poll = async () => {
      if (pending) return;
      pending = true;
      try {
        const next = await api<Check>("/checks/" + run!.id);
        if (valid) {
          setRun(next);
          if (!active(next)) void onActivity();
        }
      } catch (e) {
        if (valid) setError(message(e));
      } finally {
        pending = false;
      }
    };
    const timer = setInterval(poll, 1500);
    return () => {
      valid = false;
      clearInterval(timer);
    };
  }, [run?.id, run?.state]);
  async function start() {
    stopVoicePreview();
    setBusy(true);
    setError("");
    try {
      const next = await api<Check>("/checks", "POST", {
        context,
        overrides: overrides(),
        mode: "text",
      });
      if (alive.current) {
        setRun(next);
        setVisible(true);
        void onActivity();
      }
    } catch (e) {
      if (alive.current) setError(message(e));
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  async function stop() {
    if (!run) return;
    setBusy(true);
    try {
      await api("/checks/" + run.id, "DELETE");
      if (alive.current) setRun(await api<Check>("/checks/" + run.id));
      void onActivity();
    } catch (e) {
      if (alive.current) setError(message(e));
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  return {
    run,
    busy,
    error,
    visible,
    setVisible,
    checking: active(run),
    start,
    stop,
  };
}
export function CheckLauncher({
  check,
  disabled,
}: {
  check: ReturnType<typeof useConfigurationCheck>;
  disabled: boolean;
}) {
  return (
    <section className="check-launch" aria-label="Configuration testing">
      <button
        type="button"
        disabled={check.busy || (!check.checking && disabled)}
        onClick={check.checking ? check.stop : check.start}
      >
        {check.checking ? <Square size={16} /> : <FlaskConical size={16} />}{" "}
        {check.checking
          ? "Stop Check"
          : check.busy
            ? "Starting Check…"
            : "Check Configuration"}
      </button>
      <p className="caption muted">
        Six synthetic conversations check the selected model and prompts.
        Provider usage applies. No phone call is placed.
      </p>
      {check.run && (
        <button className="text-button" onClick={() => check.setVisible(true)}>
          {check.checking
            ? `View Progress · ${check.run.results.length}/${check.run.scenario_count}`
            : "View Last Check"}
        </button>
      )}
      {check.error && (
        <p role="alert" className="error">
          {check.error}
        </p>
      )}
    </section>
  );
}
export function CheckResults({
  check,
}: {
  check: ReturnType<typeof useConfigurationCheck>;
}) {
  const run = check.run;
  if (!run) return null;
  return (
    <section
      className="configuration-check-results"
      aria-label="Configuration check results"
    >
      <div className="section-heading">
        <div>
          <h3>
            {check.checking ? "Checking configuration" : `Check ${run.state}`}
          </h3>
          <p className="muted">
            {run.configuration.profile_name} ·{" "}
            {run.configuration.voice.dialogue_model}
          </p>
        </div>
        <button onClick={() => check.setVisible(false)}>
          Back to Conversation
        </button>
      </div>
      <p className="caption muted">
        {new Date(run.created_at).toLocaleString()} ·{" "}
        {run.configuration.unsaved
          ? "Unsaved settings captured at start"
          : `Saved settings · profile ${run.configuration.profile_revision}, global ${run.configuration.configuration_revision}`}
      </p>
      <p className="muted">
        Results apply to the settings captured when this check started. Run
        again after edits. Voice quality and telephone delay need a listening
        test.
      </p>
      {check.checking && (
        <>
          <progress
            aria-label="Configuration check progress"
            value={run.results.length}
            max={run.scenario_count}
          />
          <button className="danger" disabled={check.busy} onClick={check.stop}>
            <Square size={15} />
            Stop Check
          </button>
        </>
      )}
      {run.error && (
        <p role="alert" className="error">
          {run.error}
        </p>
      )}
      <div className="check-scenarios">
        {run.scenario_names.map((name, i) => {
          const result = run.results.find((r) => r.name === name);
          return (
            <details key={name}>
              <summary>
                <strong>{labels[name] || name.replaceAll("_", " ")}</strong>
                <span
                  className={result?.status === "failed" ? "error" : "muted"}
                >
                  {result?.status ||
                    (check.checking && i === run.results.length
                      ? "Running"
                      : check.checking
                        ? "Waiting"
                        : "Not run")}
                </span>
              </summary>
              {result ? (
                <>
                  <p>{result.reason}</p>
                  {result.objective && (
                    <p>
                      <strong>Task:</strong> {result.objective}
                    </p>
                  )}
                  {result.messages?.length ? (
                    <>
                      <h4>Test messages</h4>
                      <ol>
                        {result.messages.map((v, n) => (
                          <li key={n}>{v}</li>
                        ))}
                      </ol>
                    </>
                  ) : null}
                  {!!result.responses.length && (
                    <>
                      <h4>Agent responses</h4>
                      {result.responses.map((r, n) => (
                        <p key={n} className="preserve">
                          {r.text ||
                            (r.type === "function_call"
                              ? "The agent invoked a call-control tool."
                              : "No text response.")}
                        </p>
                      ))}
                    </>
                  )}
                  {result.duration_ms != null && (
                    <small className="muted">
                      Scenario duration:{" "}
                      {(result.duration_ms / 1000).toFixed(1)} seconds. This
                      includes evaluation time.
                    </small>
                  )}
                </>
              ) : (
                <p className="muted">No result yet.</p>
              )}
            </details>
          );
        })}
      </div>
      <details>
        <summary>Tested settings</summary>
        <pre tabIndex={0}>
          {JSON.stringify(
            {
              prompts: run.configuration.prompts,
              personality: run.configuration.personality_prompt,
              voice: run.configuration.voice,
            },
            null,
            2,
          )}
        </pre>
        <p className="caption muted">
          Evaluation judge: {run.judge_model}. It is used only during this
          check.
        </p>
      </details>
    </section>
  );
}
