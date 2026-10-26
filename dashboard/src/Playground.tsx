import { useEffect, useRef, useState } from "react";
import { PipecatClient } from "@pipecat-ai/client-js";
import {
  WebSocketTransport,
  WavMediaManager,
} from "@pipecat-ai/websocket-transport";
import { Mic, Square, Send, MessageSquare, FileText } from "lucide-react";
import { api, message } from "./api";
import AIInputs from "./AIInputs";
import VoiceFields from "./VoiceFields";
import {
  CheckLauncher,
  CheckResults,
  useConfigurationCheck,
} from "./ConfigurationCheck";
import BriefFields from "./BriefFields";
import { stopVoicePreview } from "./VoicePreview";
import { SectionTabs, ActionMenu, SettingsDisclosure } from "./Controls";
import {
  languages,
  sampleBrief,
  type Draft,
  type Brief,
  type Practice,
  type Session,
  type Status,
  type Language,
  type AIInputInspection,
  type Profile,
  type Revision,
  type Voice,
  type Contact,
} from "./types";

export default function Playground({
  session,
  draft,
  clearDraft,
  status,
  onActivity,
}: {
  session: Session;
  draft?: Draft;
  clearDraft: () => void;
  status: Status | null;
  onActivity: () => Promise<unknown>;
}) {
  const [brief, setBrief] = useState<Brief>(() => ({
      ...sampleBrief(session.setup, draft?.profileId),
      direction: draft?.direction || "outbound",
      ...(draft?.direction === "inbound"
        ? {
            objective:
              "Receive this incoming business call, understand today's need, and agree on a useful next step.",
            target: { name: "Incoming caller", phone: "withheld" },
          }
        : {}),
      language: (draft?.profile?.voice_overrides.conversation_languages ||
        draft?.global?.voice.conversation_languages ||
        session.setup.defaults.conversation_languages)[0],
    })),
    [mode, setMode] = useState<"voice" | "text">("voice"),
    [practice, setPractice] = useState<Practice | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [text, setText] = useState(""),
    [partial, setPartial] = useState(""),
    [preview, setPreview] = useState<AIInputInspection | null>(null),
    [previewLastSession, setPreviewLastSession] = useState(false),
    [settingsSection, setSettingsSection] = useState("voice");
  const [voiceOverrides, setVoiceOverrides] = useState<Partial<Voice>>({});
  const [contacts, setContacts] = useState<Contact[]>([]),
    [simulationContact, setSimulationContact] = useState("");
  useEffect(() => {
    api<{ contacts: Contact[] }>("/contacts")
      .then((data) => setContacts(data.contacts))
      .catch((e) => setError(message(e)));
  }, []);
  const [profileVoice, setProfileVoice] = useState<Voice>(
    session.setup.defaults,
  );
  const [settingsReady, setSettingsReady] = useState(false);
  useEffect(() => {
    let alive = true;
    setSettingsReady(false);
    setVoiceOverrides({});
    api<Revision<Profile>>("/configuration/profile:" + brief.profile_id)
      .then((record) => {
        if (!alive) return;
        setProfileVoice({
          ...(draft?.global?.voice || session.setup.defaults),
          ...(draft?.profile || record.value).voice_overrides,
        });
        setSettingsReady(true);
      })
      .catch((e) => {
        if (alive) setError(message(e));
      });
    return () => {
      alive = false;
    };
  }, [brief.profile_id, draft, session.setup.defaults]);
  const client = useRef<PipecatClient | null>(null),
    id = useRef<string | null>(null),
    alive = useRef(true),
    closing = useRef(false);
  const running = !!practice && !["stopped", "failed"].includes(practice.state);
  useEffect(() => {
    alive.current = true;
    const release = () => {
      closing.current = true;
      client.current?.disconnect().catch(() => {});
      if (id.current)
        void api(
          "/practice/" + id.current,
          "DELETE",
          undefined,
          undefined,
          true,
        ).catch(() => {});
    };
    addEventListener("pagehide", release);
    return () => {
      alive.current = false;
      removeEventListener("pagehide", release);
      release();
    };
  }, []);

  useEffect(() => {
    if (!practice || ["stopped", "failed"].includes(practice.state)) return;
    let pending = false;
    const poll = async () => {
      if (pending) return;
      pending = true;
      try {
        const r = await api<Practice>("/practice/" + practice.id);
        if (alive.current) {
          setPractice(r);
          if (["stopped", "failed"].includes(r.state)) {
            closing.current = true;
            await client.current?.disconnect();
            id.current = null;
            void onActivity();
          }
        }
      } catch (e) {
        if (alive.current) setError(message(e));
      } finally {
        pending = false;
      }
    };
    const timer = setInterval(poll, 1200);
    return () => clearInterval(timer);
  }, [practice?.id, practice?.state]);
  const overrides = () => {
    const value = {
      ...(draft?.global ? { global: draft.global } : {}),
      ...(draft?.profile ? { profile: draft.profile } : {}),
      ...(draft?.inbound ? { inbound: draft.inbound } : {}),
      ...(simulationContact
        ? { simulation_contact_id: simulationContact }
        : {}),
      ...(Object.keys(voiceOverrides).length ? { voice: voiceOverrides } : {}),
    };
    return Object.keys(value).length ? value : undefined;
  };
  const check = useConfigurationCheck(
    brief.profile_id || "warm",
    brief,
    overrides,
    onActivity,
  );
  async function start() {
    stopVoicePreview();
    check.setVisible(false);
    let connectionTimer: ReturnType<typeof setTimeout> | undefined;
    setError("");
    setBusy(true);
    closing.current = false;
    try {
      if (mode === "voice") {
        if (!navigator.mediaDevices?.getUserMedia)
          throw new Error(
            "Microphone access is unavailable. Open the private HTTPS dashboard in a browser that supports microphone input.",
          );
        const permission = await navigator.mediaDevices.getUserMedia({
          audio: true,
        });
        permission.getTracks().forEach((track) => track.stop());
        if (!alive.current) return;
      }
      const r = await api<Practice>("/practice", "POST", {
        mode,
        context: { ...brief, max_duration_seconds: undefined },
        overrides: overrides(),
      });
      if (!alive.current) {
        await api("/practice/" + r.id, "DELETE");
        return;
      }
      id.current = r.id;
      setPractice(r);
      void onActivity();
      if (mode === "voice") {
        const pc = new PipecatClient({
          transport: new WebSocketTransport({
            mediaManager: new WavMediaManager(512, 16000),
            recorderSampleRate: 16000,
            playerSampleRate: 24000,
          }),
          enableMic: true,
          enableCam: false,
          callbacks: {
            onUserTranscript: (turn) => {
              if (alive.current) setPartial(turn.final ? "" : turn.text);
            },
            onError: async () => {
              if (!alive.current || closing.current) return;
              if (id.current) {
                const latest = await api<Practice>(
                  "/practice/" + id.current,
                ).catch(() => null);
                if (
                  latest &&
                  ["stopping", "stopped", "failed"].includes(latest.state)
                ) {
                  if (alive.current) setPractice(latest);
                  return;
                }
              }
              if (alive.current && !closing.current)
                setError(
                  "The audio connection failed. Check microphone access, then try again.",
                );
            },
          },
        });
        client.current = pc;
        await Promise.race([
          pc.connect({
            wsUrl:
              location.origin.replace(/^http/, "ws") +
              "/admin/api/practice/" +
              r.id +
              "/audio",
          }),
          new Promise<never>((_, reject) => {
            connectionTimer = setTimeout(
              () =>
                reject(
                  new Error(
                    "Voice connection timed out. Check your settings and try again.",
                  ),
                ),
              20000,
            );
          }),
        ]);
      }
    } catch (e) {
      setError(message(e));
      closing.current = true;
      if (id.current) {
        await api("/practice/" + id.current, "DELETE").catch(() => {});
        id.current = null;
      }
      await client.current?.disconnect().catch(() => {});
      setPractice((p) => (p ? { ...p, state: "stopped" } : p));
      void onActivity();
    } finally {
      clearTimeout(connectionTimer);
      if (alive.current) setBusy(false);
    }
  }
  async function stop() {
    setBusy(true);
    closing.current = true;
    try {
      await client.current?.disconnect();
      if (id.current) await api("/practice/" + id.current, "DELETE");
      id.current = null;
      setPractice((p) => (p ? { ...p, state: "stopped" } : p));
      setPartial("");
      void onActivity();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!practice || !text.trim()) return;
    setBusy(true);
    setError("");
    const input = text;
    setText("");
    try {
      setPractice(
        await api<Practice>("/practice/" + practice.id + "/text", "POST", {
          text: input,
        }),
      );
    } catch (e) {
      setError(message(e));
      setText(input);
    } finally {
      setBusy(false);
    }
  }
  async function showSessionInputs() {
    if (!practice) return;
    try {
      setPreview(
        await api<AIInputInspection>("/practice/" + practice.id + "/ai-inputs"),
      );
      setPreviewLastSession(true);
    } catch (e) {
      setError(message(e));
    }
  }
  async function showPrompt() {
    if (running) return showSessionInputs();
    try {
      setPreviewLastSession(false);
      const r = await api<AIInputInspection>("/prompt-preview", "POST", {
        mode,
        context: { ...brief, max_duration_seconds: undefined },
        overrides: overrides(),
      });
      setPreview(r);
    } catch (e) {
      setError(message(e));
    }
  }
  const blocked =
    !!status?.active_call ||
    check.checking ||
    !!status?.check_active ||
    (!running && !!status?.practice_active && !practice);
  return (
    <div className="playground-layout">
      <aside className="practice-setup">
        {draft && (
          <div className="draft-notice">
            <strong>Testing unsaved changes</strong>
            <button onClick={clearDraft} disabled={running}>
              Use Saved Settings
            </button>
          </div>
        )}
        <fieldset disabled={running || busy || check.checking}>
          <label>
            Call direction
            <select
              value={brief.direction || "outbound"}
              onChange={(e) => {
                const direction = e.target.value as "inbound" | "outbound";
                clearDraft();
                setBrief((old) => ({
                  ...old,
                  direction,
                  objective:
                    direction === "inbound"
                      ? "Receive this incoming business call, understand today's need, and agree on a useful next step."
                      : sampleBrief(session.setup).objective,
                  profile_id:
                    direction === "inbound"
                      ? session.setup.reception?.inbound.profile_id || "warm"
                      : old.profile_id,
                }));
              }}
            >
              <option value="outbound">Outbound</option>
              <option value="inbound">Inbound reception</option>
            </select>
          </label>
          <label>
            Caller context
            <select
              value={simulationContact}
              onChange={(e) => setSimulationContact(e.target.value)}
            >
              <option value="">Unknown caller</option>
              {contacts.map((contact) => (
                <option key={contact.contact_id} value={contact.contact_id}>
                  Known:{" "}
                  {contact.name ||
                    contact.numbers[0]?.phone ||
                    "Unknown caller"}
                </option>
              ))}
            </select>
          </label>
          <p className="field-help">
            Contacts, memory, and callbacks stay unchanged.
          </p>
          <div className="segmented" role="group" aria-label="Practice mode">
            <button
              type="button"
              aria-pressed={mode === "voice"}
              onClick={() => setMode("voice")}
            >
              <Mic size={15} />
              Voice
            </button>
            <button
              type="button"
              aria-pressed={mode === "text"}
              onClick={() => setMode("text")}
            >
              <MessageSquare size={15} />
              Text
            </button>
          </div>

          <label>
            Personality
            <select
              value={brief.profile_id}
              onChange={(e) => {
                clearDraft();
                setBrief((b) => ({ ...b, profile_id: e.target.value }));
              }}
            >
              {session.setup.profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <BriefFields brief={brief} onChange={setBrief} />
          <label>
            Initial language
            <select
              value={brief.language}
              onChange={(e) =>
                setBrief((b) => ({
                  ...b,
                  language: e.target.value as Language,
                }))
              }
            >
              {Object.entries(languages).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
        </fieldset>
        <div className="practice-actions">
          {running ? (
            <button className="danger" disabled={busy} onClick={stop}>
              <Square size={16} />
              {busy ? "Stopping…" : "End Practice"}
            </button>
          ) : (
            <button
              className="primary"
              disabled={
                busy ||
                blocked ||
                !settingsReady ||
                !brief.objective.trim() ||
                !brief.target.name.trim()
              }
              onClick={start}
            >
              {mode === "voice" ? (
                <Mic size={16} />
              ) : (
                <MessageSquare size={16} />
              )}{" "}
              {busy
                ? "Connecting…"
                : mode === "voice"
                  ? "Start Voice Practice"
                  : "Start Text Practice"}
            </button>
          )}
          <ActionMenu label="Inspect">
            <button onClick={showPrompt} disabled={!practice && !settingsReady}>
              <FileText size={15} />
              Inspect AI Inputs
            </button>
            {practice && !running && (
              <button onClick={showSessionInputs}>Inspect Last Session</button>
            )}
          </ActionMenu>
        </div>
        {blocked && (
          <p className="muted">
            A call or another practice session is active. End it before starting
            practice.
          </p>
        )}
        <CheckLauncher
          check={check}
          disabled={running || busy || blocked || !settingsReady}
        />
        <fieldset
          className="practice-model-settings"
          disabled={running || busy || check.checking}
        >
          <SettingsDisclosure
            title="Voice and model choices"
            summary="Temporary choices for this session"
          >
            <p className="muted">
              To use these choices for calls, save them in Personalities.
            </p>
            {settingsReady ? (
              <SectionTabs
                label="Practice settings"
                value={settingsSection}
                onChange={setSettingsSection}
                items={[
                  { id: "voice", label: "Voice & Model" },
                  { id: "call", label: "Call Settings" },
                ]}
              >
                <VoiceFields
                  section={settingsSection as "voice" | "call"}
                  value={voiceOverrides}
                  base={profileVoice}
                  onChange={(v) => {
                    setVoiceOverrides(v);
                    setPreview(null);
                  }}
                  practice
                />
              </SectionTabs>
            ) : (
              <p role="status">Loading personality settings…</p>
            )}
            {!!Object.keys(voiceOverrides).length && (
              <button type="button" onClick={() => setVoiceOverrides({})}>
                Reset Practice Choices
              </button>
            )}
          </SettingsDisclosure>
        </fieldset>
        <p className="caption muted">
          Provider usage applies. Browser audio does not include cellular
          carrier delay.
        </p>
      </aside>
      <section className="practice-conversation">
        {check.visible && check.run ? (
          <CheckResults check={check} />
        ) : (
          <>
            <header className="row-top">
              <div>
                <h3>
                  {running
                    ? "Practice in progress"
                    : practice
                      ? "Practice complete"
                      : "Conversation preview"}
                </h3>
                <span className="muted">
                  {practice?.configuration.profile_name ||
                    draft?.profile?.name ||
                    session.setup.profiles.find(
                      (p) => p.id === brief.profile_id,
                    )?.name}
                </span>
              </div>
              {running && (
                <span className="pill">
                  <Mic size={13} />
                  {mode === "voice"
                    ? practice?.state === "waiting"
                      ? "Connecting audio"
                      : "Microphone active"
                    : "Text session"}
                </span>
              )}
            </header>
            {error && (
              <p role="alert" className="error">
                {error}
              </p>
            )}
            {practice?.error && (
              <p role="alert" className="error">
                {practice.error}
              </p>
            )}
            {preview !== null && (
              <AIInputs
                data={preview}
                onClose={() => setPreview(null)}
                onRefresh={previewLastSession ? showSessionInputs : showPrompt}
              />
            )}
            <div className="practice-turns" aria-live="polite">
              {practice?.messages.map((turn, i) => (
                <article className="transcript-turn" key={i}>
                  <header>
                    <strong>
                      {turn.speaker === "assistant" ? "Calling agent" : "You"}
                    </strong>
                    {turn.interrupted && (
                      <span className="caption">Interrupted</span>
                    )}
                  </header>
                  <p>{turn.text}</p>
                </article>
              ))}
              {partial && <p className="partial">{partial}</p>}
              {!practice?.messages.length && (
                <div className="empty-state">
                  {running
                    ? mode === "voice"
                      ? "Listening…"
                      : "Send a message to begin"
                    : "Start a practice session"}
                </div>
              )}
            </div>
            {running && mode === "text" && (
              <form className="text-composer" onSubmit={send}>
                <input
                  aria-label="Practice message"
                  placeholder="Say something to the agent…"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  disabled={busy}
                />
                <button
                  className="primary"
                  aria-label="Send practice message"
                  disabled={busy || !text.trim()}
                >
                  <Send size={17} />
                </button>
              </form>
            )}
            {!!practice?.callback_previews?.length && (
              <section className="practice-callback-previews">
                <h3>Practice callback receipts</h3>
                <p className="muted">Simulation only. No call is scheduled.</p>
                {practice.callback_previews.map((receipt) => (
                  <p key={receipt.schedule_id}>
                    {receipt.destination} · {receipt.state} ·{" "}
                    {receipt.window_start}
                  </p>
                ))}
              </section>
            )}
            {!!practice?.metrics.length && (
              <footer className="practice-metrics">
                <span className="caption">
                  {mode === "text" ? "MODEL RESPONSE" : "SERVER REPLY START"}
                </span>
                <strong>{practice.metrics.at(-1)!.seconds.toFixed(2)} s</strong>
                <span className="muted">Latest measured turn</span>
              </footer>
            )}
          </>
        )}
      </section>
    </div>
  );
}
