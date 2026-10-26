import { useId, useState } from "react";
import { Download, RefreshCw } from "lucide-react";
import { tabKeys } from "./a11y";
import { message } from "./api";
import type { AIInputInspection } from "./types";

export default function AIInputs({
  data,
  onClose,
  onRefresh,
}: {
  data: AIInputInspection;
  onClose: () => void;
  onRefresh?: () => Promise<void>;
}) {
  const [tab, setTab] = useState("instructions"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const id = useId(),
    configured = data.configured;
  const tabs = [
    ["instructions", "Instructions"],
    ["settings", "Model & Voice"],
    ["runtime", "Runtime & Tools"],
    ["request", "Latest Request"],
  ];
  function download() {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "call-ai-inputs.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function refresh() {
    if (!onRefresh) return;
    setBusy(true);
    setError("");
    try {
      await onRefresh();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="preview-panel ai-input-inspector"
      aria-label="Call AI inputs"
    >
      <div className="row-top">
        <div>
          <span className="eyebrow">AI SPEAKING ON THE CALL</span>
          <h3>Call AI Inputs</h3>
        </div>
        <button onClick={onClose}>Close Preview</button>
      </div>
      <p className="muted">
        The call model receives these instructions and the approved brief.
        Hermes’s calling guide controls how Hermes prepares the brief; it is
        separate from this model’s prompt.
      </p>
      {configured && (
        <dl className="facts ai-input-summary">
          <dt>Call model</dt>
          <dd>{configured.model_options.model}</dd>
          <dt>Request route</dt>
          <dd>OpenRouter</dd>
          <dt>Mode</dt>
          <dd>
            {configured.mode === "voice"
              ? "Telephone or browser voice"
              : "Text practice"}
          </dd>
        </dl>
      )}
      <div className="save-row">
        <button onClick={download}>
          <Download size={15} />
          Download Inputs
        </button>
        {onRefresh && (
          <button onClick={refresh} disabled={busy}>
            <RefreshCw size={15} />
            {busy ? "Refreshing…" : "Refresh Inputs"}
          </button>
        )}
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div
        className="detail-tabs"
        role="tablist"
        aria-label="AI input views"
        onKeyDown={tabKeys}
      >
        {tabs.map(([key, label]) => (
          <button
            key={key}
            role="tab"
            id={id + key}
            aria-controls={id + "-panel"}
            aria-selected={tab === key}
            tabIndex={tab === key ? 0 : -1}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={id + "-panel"} aria-labelledby={id + tab}>
        {tab !== "request" && !configured && (
          <>
            <h4>Full inputs were not recorded</h4>
            <p className="muted">
              This older call has no saved tool and request configuration.
              Current defaults cannot establish what it received.
            </p>
            {data.saved_system_prompt && (
              <details>
                <summary>Recorded starting system prompt</summary>
                <pre tabIndex={0}>{data.saved_system_prompt}</pre>
              </details>
            )}
          </>
        )}
        {tab === "instructions" && configured && (
          <>
            <h4>How the system prompt is assembled</h4>
            <p className="muted">
              Read top to bottom. Editable sections come from Global Defaults,
              the chosen personality, or the brief. Runtime rules are shown in
              full and remain read-only.
            </p>
            <ol className="input-layers">
              {configured.prompt_sections.map((part) => (
                <li key={part.id}>
                  <details>
                    <summary>
                      {part.label}
                      <span className="caption muted">
                        {part.source}
                        {part.editable ? " · Editable" : " · Runtime-managed"}
                      </span>
                    </summary>
                    <pre tabIndex={0}>
                      {part.text || "No additional instructions."}
                    </pre>
                  </details>
                </li>
              ))}
            </ol>
            <details>
              <summary>Assembled starting system prompt</summary>
              <pre tabIndex={0} className="prompt-preview">
                {configured.system_prompt}
              </pre>
            </details>
            <p className="muted">
              During a voice call, the runtime appends a timer notice to this
              prompt and adds the conversation history. See Runtime &amp; Tools
              and Latest Request.
            </p>
          </>
        )}
        {tab === "settings" && configured && (
          <>
            <h4>Call model parameters</h4>
            <p className="muted">
              These accompany the prompt in requests to OpenRouter. A parameter
              that is not sent uses the provider’s default.
            </p>
            <pre tabIndex={0}>
              {JSON.stringify(configured.model_options, null, 2)}
            </pre>
            <h4>Speech recognition and voice generation</h4>
            <p className="muted">
              Soniox transcribes the incoming audio; Inworld speaks the reply.
              Their settings are separate from the call model’s text
              instructions. Text practice does not use these speech services.
            </p>
            <pre tabIndex={0}>
              {JSON.stringify(configured.speech_settings, null, 2)}
            </pre>
            <h4>Runtime limits</h4>
            <pre tabIndex={0}>{JSON.stringify(configured.limits, null, 2)}</pre>
          </>
        )}
        {tab === "runtime" && configured && (
          <>
            <h4>Messages added during the conversation</h4>
            <pre tabIndex={0}>
              {JSON.stringify(configured.runtime_messages, null, 2)}
            </pre>
            <h4>Tools the call model can use</h4>
            <p className="muted">
              These tools belong to the voice conversation. Hermes’s prepare,
              start, poll, and result tools are shown under Integrations.
            </p>
            {configured.tools.map((tool) => (
              <details key={tool.function.name}>
                <summary>{tool.function.name}</summary>
                <p>{tool.function.description}</p>
                <pre tabIndex={0}>{JSON.stringify(tool, null, 2)}</pre>
              </details>
            ))}
            <details>
              <summary>Initial request example</summary>
              <p className="muted">
                The timer value is illustrative. The latest request tab shows
                captured outgoing data when a session has run.
              </p>
              <pre tabIndex={0}>
                {JSON.stringify(configured.initial_request_example, null, 2)}
              </pre>
            </details>
          </>
        )}
        {tab === "request" && (
          <>
            <h4>Latest outgoing model request</h4>
            {data.last_request ? (
              <>
                <p className="muted">
                  Captured{" "}
                  {new Date(data.last_request.recorded_at).toLocaleString()}.
                  Only the latest request is retained, including the history it
                  sent.
                </p>
                <p className="muted">{data.last_request.scope}</p>
                {data.last_request.body ? (
                  <pre tabIndex={0} className="request-body">
                    {JSON.stringify(data.last_request.body, null, 2)}
                  </pre>
                ) : (
                  <p>
                    The request exceeded the inspection size limit; its body was
                    not retained.
                  </p>
                )}
              </>
            ) : (
              <p className="muted">
                No request has been captured for this view. Run practice or
                inspect a new call to see its actual model, messages, tools, and
                parameters. No call starts from this inspector.
              </p>
            )}
          </>
        )}
      </div>
      <p className="caption muted">
        Provider credentials and authentication headers are excluded.
        Provider-internal instructions and defaults are outside this
        application’s visibility.
      </p>
    </section>
  );
}
