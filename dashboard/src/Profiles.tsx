import { useEffect, useState, useRef } from "react";
import {
  Plus,
  Copy,
  History,
  Code2,
  AudioLines,
  FileText,
  Archive,
} from "lucide-react";
import { api, message, ApiError } from "./api";
import {
  sampleBrief,
  type Draft,
  type Global,
  type Guide,
  type Profile,
  type Revision,
  type Session,
  type AIInputInspection,
  type Voice,
} from "./types";
import VoiceFields from "./VoiceFields";
import AIInputs from "./AIInputs";
import { ActionMenu, SectionTabs, SettingsDisclosure } from "./Controls";

type Editable = Global | Profile | Guide;
export default function Profiles({
  session,
  onTest,
  onSaved,
}: {
  session: Session;
  onTest: (draft: Draft) => void;
  onSaved: () => Promise<void>;
}) {
  const [profiles, setProfiles] = useState<Revision<Profile>[]>([]),
    [entity, setEntity] = useState("profile:warm"),
    [fresh, setFresh] = useState<Revision<Profile>>(),
    [refresh, setRefresh] = useState(0),
    [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    api<Revision<Profile>[]>("/profiles")
      .then(setProfiles)
      .catch((e) => setError(message(e)));
  }, [refresh]);
  const changed = async () => {
    setFresh(undefined);
    setRefresh((x) => x + 1);
    await onSaved();
  };
  function create(source?: Profile) {
    if (
      dirty &&
      !window.confirm(
        "Discard unsaved changes before opening another personality?",
      )
    )
      return;
    const id = "profile-" + crypto.randomUUID().slice(0, 8);
    const value: Profile = source
      ? {
          ...structuredClone(source),
          name: source.name + " Copy",
          archived: false,
        }
      : {
          name: "New Personality",
          description: "",
          personality: "warm",
          prompt:
            "Be friendly, curious, and concise. Respond to the person naturally and stay within the approved brief.",
          voice_overrides: {},
          archived: false,
        };
    setFresh({
      entity: "profile:" + id,
      revision: 0,
      value,
      actor: "",
      created_at: "",
    });
    setEntity("profile:" + id);
  }
  function selectEntity(next: string) {
    if (dirty && !window.confirm("Discard unsaved changes?")) return;
    setFresh(undefined);
    setEntity(next);
  }
  return (
    <div className="editor-layout">
      <aside className="profile-list">
        <div className="section-heading">
          <span className="list-label">Your Personalities</span>
          <button
            className="icon-button"
            aria-label="New personality"
            onClick={() => create()}
          >
            <Plus size={18} />
          </button>
        </div>
        <label className="profile-picker">
          Editing
          <select value={entity} onChange={(e) => selectEntity(e.target.value)}>
            {fresh && !profiles.some((p) => p.entity === fresh.entity) && (
              <option value={fresh.entity}>{fresh.value.name}</option>
            )}
            <optgroup label="Personalities">
              {profiles.map((p) => (
                <option key={p.entity} value={p.entity}>
                  {p.value.name}
                  {p.value.archived ? " (archived)" : ""}
                </option>
              ))}
            </optgroup>
          </select>
        </label>
        {profiles.map((p) => (
          <button
            className={"profile-row " + (p.entity === entity ? "selected" : "")}
            key={p.entity}
            onClick={() => selectEntity(p.entity)}
          >
            <span>
              {p.value.name}
              {p.value.archived ? (
                <small>Archived</small>
              ) : p.value.description &&
                p.value.description !== "Built-in starting personality" ? (
                <small>{p.value.description}</small>
              ) : null}
            </span>
          </button>
        ))}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </aside>
      <ConfigurationEditor
        key={entity}
        entity={entity}
        initial={fresh}
        session={session}
        onSaved={changed}
        onTest={onTest}
        onDuplicate={create}
        onDirty={setDirty}
      />
    </div>
  );
}

export function ConfigurationEditor({
  entity,
  initial,
  session,
  onSaved,
  onTest,
  onDuplicate,
  onDirty,
}: {
  entity: string;
  initial?: Revision<Editable>;
  session: Session;
  onSaved: () => Promise<void>;
  onTest?: (draft: Draft) => void;
  onDuplicate?: (profile: Profile) => void;
  onDirty?: (dirty: boolean) => void;
}) {
  const editor = useRef<HTMLElement>(null);
  const [conflict, setConflict] = useState<Revision<Editable> | null>(null);
  const [record, setRecord] = useState<Revision<Editable> | null>(
      initial || null,
    ),
    [value, setValue] = useState<Editable | null>(initial?.value || null),
    [raw, setRaw] = useState(""),
    [rawMode, setRawMode] = useState(false);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [history, setHistory] = useState<Revision<Editable>[]>([]),
    [showHistory, setShowHistory] = useState(false),
    [preview, setPreview] = useState<AIInputInspection | null>(null),
    [section, setSection] = useState("instructions");
  useEffect(() => {
    if (initial) return;
    let alive = true;
    api<Revision<Editable>>("/configuration/" + entity)
      .then((v) => {
        if (alive) {
          setRecord(v);
          setValue(v.value);
        }
      })
      .catch((e) => {
        if (alive) setError(message(e));
      });
    return () => {
      alive = false;
    };
  }, [entity, initial]);
  const candidate = () => (rawMode ? (JSON.parse(raw) as Editable) : value!);
  const dirty =
    !!record &&
    (rawMode
      ? raw !== JSON.stringify(record.value, null, 2)
      : JSON.stringify(value) !== JSON.stringify(record.value));
  useEffect(() => {
    onDirty?.(dirty);
  }, [dirty, onDirty]);
  useEffect(() => {
    const unload = (e: BeforeUnloadEvent) => {
      if (dirty) e.preventDefault();
    };
    addEventListener("beforeunload", unload);
    return () => removeEventListener("beforeunload", unload);
  }, [dirty]);
  useEffect(() => {
    function saveKey(e: KeyboardEvent) {
      if (
        (e.metaKey || e.ctrlKey) &&
        e.key === "s" &&
        editor.current?.contains(document.activeElement)
      ) {
        e.preventDefault();
        if (!busy && record) void save();
      }
    }
    addEventListener("keydown", saveKey);
    return () => removeEventListener("keydown", saveKey);
  });
  function edit(next: Editable) {
    setValue(next);
    setNotice("");
    setPreview(null);
  }
  function toggleRaw() {
    try {
      if (rawMode) {
        edit(candidate());
        setRawMode(false);
      } else {
        setRaw(JSON.stringify(value, null, 2));
        setRawMode(true);
      }
      setError("");
    } catch (e) {
      setError("Fix the JSON before switching views: " + message(e));
    }
  }
  async function save(replacement?: Editable) {
    if (!record) return;
    setBusy(true);
    setError("");
    try {
      const next = await api<Revision<Editable>>(
        "/configuration/" + entity,
        "PUT",
        {
          expected_revision: record.revision,
          value: replacement || candidate(),
        },
      );
      setRecord(next);
      setValue(next.value);
      setRaw(JSON.stringify(next.value, null, 2));
      setNotice("Saved. New calls will use this version.");
      await onSaved();
      if (showHistory)
        setHistory(
          await api<Revision<Editable>[]>(
            "/configuration/" + entity + "/history",
          ),
        );
    } catch (e) {
      setError(message(e));
      if (e instanceof ApiError && e.status === 409) {
        try {
          setConflict(
            await api<Revision<Editable>>("/configuration/" + entity),
          );
        } catch {
          /* Keep the original edits. */
        }
      }
    } finally {
      setBusy(false);
    }
  }
  function draft(): Draft {
    const edited = candidate();
    return entity === "global"
      ? { profileId: "warm", global: edited as Global }
      : {
          profileId: record?.revision ? entity.slice(8) : "warm",
          profile: edited as Profile,
        };
  }
  async function showPrompt() {
    setError("");
    try {
      const d = draft();
      const context = sampleBrief(session.setup, d.profileId);
      context.language = (d.profile?.voice_overrides.conversation_languages ||
        d.global?.voice.conversation_languages ||
        session.setup.defaults.conversation_languages)[0];
      const result = await api<AIInputInspection>("/prompt-preview", "POST", {
        context,
        overrides: {
          ...(d.global ? { global: d.global } : {}),
          ...(d.profile ? { profile: d.profile } : {}),
        },
      });
      setPreview(result);
    } catch (e) {
      setError(message(e));
    }
  }
  async function loadHistory() {
    if (!record?.revision) return;
    try {
      setHistory(
        await api<Revision<Editable>[]>(
          "/configuration/" + entity + "/history",
        ),
      );
      setShowHistory((v) => !v);
    } catch (e) {
      setError(message(e));
    }
  }
  if (!value || !record)
    return (
      <section className="configuration-editor" ref={editor}>
        <p
          role={error ? "alert" : "status"}
          className={error ? "error" : "muted"}
        >
          {error || "Loading settings…"}
        </p>
      </section>
    );
  const isProfile = entity.startsWith("profile:"),
    title =
      entity === "global"
        ? "Global Defaults"
        : entity === "guide"
          ? "Calling Guide"
          : (value as Profile).name;
  return (
    <section className="configuration-editor" ref={editor}>
      <div className="editor-topbar">
        <header className="editor-heading">
          <div>
            <div className="editor-title">
              <h2>{title}</h2>
              <span className="version-label">
                {record.revision ? "Version " + record.revision : "New"}
              </span>
            </div>
          </div>
          <div className="editor-save">
            {dirty && <span className="change-label">Unsaved changes</span>}
            <button
              className={dirty || record.revision === 0 ? "primary" : ""}
              disabled={busy || (!dirty && record.revision > 0)}
              onClick={() => save()}
            >
              {busy ? "Saving…" : "Save Changes"}
            </button>
            <ActionMenu>
              <button onClick={toggleRaw} disabled={busy}>
                <Code2 size={16} />
                {rawMode ? "Guided Editor" : "Full Text Editor"}
              </button>
              <button disabled={busy || !record.revision} onClick={loadHistory}>
                <History size={16} />
                History
              </button>
              {isProfile && (
                <button
                  disabled={busy}
                  onClick={() => onDuplicate?.(candidate() as Profile)}
                >
                  <Copy size={16} />
                  Duplicate
                </button>
              )}
            </ActionMenu>
          </div>
        </header>
        <div className="editor-tools">
          {entity !== "guide" && (
            <>
              <button
                onClick={() => {
                  try {
                    onTest?.(draft());
                  } catch (e) {
                    setError(message(e));
                  }
                }}
                disabled={busy}
              >
                <AudioLines size={16} />
                Try in Playground
              </button>
              <button
                className="quiet-button"
                onClick={showPrompt}
                disabled={busy}
              >
                <FileText size={16} />
                Inspect AI Inputs
              </button>
            </>
          )}
        </div>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="success-notice" role="status">
          {notice}
        </p>
      )}
      {conflict && (
        <section className="history-panel">
          <h3>A newer version is available</h3>
          <p>
            Your edits are still here. Review version {conflict.revision} before
            choosing which values to keep.
          </p>
          <details>
            <summary>Newer saved values</summary>
            <pre>{JSON.stringify(conflict.value, null, 2)}</pre>
          </details>
          <div className="save-row">
            <button
              onClick={() => {
                setRecord(conflict);
                setValue(conflict.value);
                setRaw(JSON.stringify(conflict.value, null, 2));
                setConflict(null);
                setError("");
              }}
            >
              Load Latest
            </button>
            <button
              onClick={() => {
                setRecord(conflict);
                setConflict(null);
                setError("");
                setNotice(
                  "Your edits are retained. Save Changes to create a new version.",
                );
              }}
            >
              Keep My Edits
            </button>
          </div>
        </section>
      )}
      {showHistory && (
        <section className="history-panel">
          <h3>Version history</h3>
          <p className="muted">
            Restoring creates a new version. Earlier calls keep their original
            settings.
          </p>
          {history.map((h) => (
            <details key={h.revision}>
              <summary>
                Version {h.revision}
                <span>
                  {new Date(h.created_at).toLocaleString("en-GB", {
                    timeZone: session.timezone,
                  })}
                </span>
              </summary>
              <pre>{JSON.stringify(h.value, null, 2)}</pre>
              <button
                disabled={busy || h.revision === record.revision}
                onClick={() => save(h.value)}
              >
                Restore This Version
              </button>
            </details>
          ))}
        </section>
      )}
      {preview !== null && (
        <AIInputs
          data={preview}
          onClose={() => setPreview(null)}
          onRefresh={showPrompt}
        />
      )}
      <fieldset className="editor-values" disabled={busy}>
        {rawMode ? (
          <label className="raw-editor">
            Configuration
            <textarea
              aria-label="Configuration"
              spellCheck={false}
              rows={28}
              value={raw}
              onChange={(e) => {
                setRaw(e.target.value);
                setNotice("");
              }}
            />
            <small>
              This edits the same values as the guided editor. Credentials are
              not part of this configuration.
            </small>
          </label>
        ) : entity === "guide" ? (
          <label>
            Instructions
            <textarea
              aria-label="Instructions"
              rows={20}
              value={(value as Guide).text}
              onChange={(e) => edit({ text: e.target.value })}
            />
          </label>
        ) : (
          <SectionTabs
            label="Personality settings"
            value={section}
            onChange={setSection}
            items={[
              { id: "instructions", label: "Instructions" },
              { id: "voice", label: "Voice & Model" },
              { id: "call", label: "Call Settings" },
            ]}
          >
            {section === "instructions" ? (
              entity === "global" ? (
                <GlobalFields value={value as Global} onChange={edit} />
              ) : (
                <ProfileFields
                  value={value as Profile}
                  onChange={edit}
                  canArchive={
                    ![
                      "warm",
                      "professional",
                      "salesperson",
                      "playful",
                    ].includes(entity.slice(8))
                  }
                />
              )
            ) : (
              <>
                {entity !== "global" && (
                  <p className="caption muted">
                    Inherits Settings defaults unless overridden.
                  </p>
                )}
                <VoiceFields
                  section={section as "voice" | "call"}
                  value={
                    entity === "global"
                      ? (value as Global).voice
                      : (value as Profile).voice_overrides
                  }
                  base={
                    entity === "global"
                      ? (value as Global).voice
                      : session.setup.defaults
                  }
                  overrides={entity !== "global"}
                  onChange={(v) =>
                    entity === "global"
                      ? edit({ ...(value as Global), voice: v as Voice })
                      : edit({ ...(value as Profile), voice_overrides: v })
                  }
                />
              </>
            )}
          </SectionTabs>
        )}
      </fieldset>
    </section>
  );
}

function GlobalFields({
  value,
  onChange,
}: {
  value: Global;
  onChange: (value: Global) => void;
}) {
  return (
    <>
      <section className="form-section">
        <label>
          Global Instructions
          <textarea
            rows={8}
            value={value.prompts.conversation}
            onChange={(e) =>
              onChange({
                ...value,
                prompts: { ...value.prompts, conversation: e.target.value },
              })
            }
          />
        </label>
        <label>
          Opening instructions
          <textarea
            rows={4}
            value={value.prompts.opening}
            onChange={(e) =>
              onChange({
                ...value,
                prompts: { ...value.prompts, opening: e.target.value },
              })
            }
          />
        </label>
      </section>
    </>
  );
}
function ProfileFields({
  value,
  onChange,
  canArchive,
}: {
  value: Profile;
  onChange: (v: Profile) => void;
  canArchive: boolean;
}) {
  return (
    <>
      <section className="form-section">
        <label>
          Personality prompt
          <textarea
            rows={8}
            value={value.prompt}
            onChange={(e) => onChange({ ...value, prompt: e.target.value })}
          />
        </label>
        <SettingsDisclosure
          title="Personality Details"
          summary={value.name + " · " + value.personality}
          initialOpen={value.name === "New Personality"}
        >
          <div className="two-fields">
            <label>
              Name
              <input
                value={value.name}
                onChange={(e) => onChange({ ...value, name: e.target.value })}
              />
            </label>
            <label>
              Base style
              <select
                value={value.personality}
                onChange={(e) =>
                  onChange({
                    ...value,
                    personality: e.target.value as Profile["personality"],
                  })
                }
              >
                {["professional", "warm", "salesperson", "playful"].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
          </div>
          <label>
            Description
            <input
              aria-label="Description"
              value={value.description}
              onChange={(e) =>
                onChange({ ...value, description: e.target.value })
              }
            />
            <small>Helps you and your assistant choose this personality.</small>
          </label>
        </SettingsDisclosure>
      </section>
      {canArchive && (
        <section className="form-section">
          <label className="check">
            <input
              type="checkbox"
              checked={value.archived}
              onChange={(e) =>
                onChange({ ...value, archived: e.target.checked })
              }
            />
            <Archive size={16} />
            Archive this personality
          </label>
          <p className="muted">
            Archived personalities remain in previous call records and cannot be
            selected for new calls.
          </p>
        </section>
      )}
    </>
  );
}
