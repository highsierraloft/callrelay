import { useEffect, useState, type ReactNode } from "react";
import { api, ApiError, message } from "./api";
import type { Revision } from "./types";
import { ActionMenu } from "./Controls";

type RevisionFormProps<T extends object> = {
  entity: string;
  title: string;
  children: (value: T, change: (value: T) => void) => ReactNode;
  onSaved?: () => Promise<void>;
  onDirtyChange?: (dirty: boolean) => void;
};

export default function RevisionForm<T extends object>(
  props: RevisionFormProps<T>,
) {
  return <RevisionEditor key={props.entity} {...props} />;
}

function RevisionEditor<T extends object>({
  entity,
  title,
  children,
  onSaved,
  onDirtyChange,
}: RevisionFormProps<T>) {
  const [record, setRecord] = useState<Revision<T>>(),
    [value, setValue] = useState<T>();
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<Revision<T>[] | null>(null),
    [raw, setRaw] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const dirty =
    !!record &&
    (raw !== null
      ? raw !== JSON.stringify(record.value, null, 2)
      : JSON.stringify(value) !== JSON.stringify(record.value));
  async function load() {
    try {
      const next = await api<Revision<T>>("/configuration/" + entity);
      setRecord(next);
      setValue(next.value);
      setRaw(null);
      setError("");
      setConflict(false);
    } catch (e) {
      setError(message(e));
    }
  }
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => {
    void load();
  }, [entity]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  async function save(replacement?: T) {
    if (!record) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const candidate =
        replacement || (raw !== null ? (JSON.parse(raw) as T) : value);
      const next = await api<Revision<T>>("/configuration/" + entity, "PUT", {
        expected_revision: record.revision,
        value: candidate,
      });
      setRecord(next);
      setValue(next.value);
      setRaw(null);
      setHistory(null);
      setConflict(false);
      setNotice("Saved for future calls");
      await onSaved?.();
    } catch (e) {
      setError(message(e));
      setConflict(e instanceof ApiError && e.status === 409);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="configuration-editor revision-form">
      <header className="editor-heading">
        <div>
          <div className="editor-title">
            <h2>{title}</h2>
            {record && (
              <span className="version-label">Version {record.revision}</span>
            )}
          </div>
        </div>
        <div className="editor-save">
          {dirty && <span className="change-label">Unsaved changes</span>}
          <button
            className={dirty ? "primary" : ""}
            title="Applies to future calls; existing calls keep their settings."
            disabled={!dirty || busy}
            onClick={() => save()}
          >
            {busy ? "Saving…" : "Save Changes"}
          </button>
          <ActionMenu>
            <button
              onClick={() => {
                if (!record || !value) return;
                if (raw === null) setRaw(JSON.stringify(value, null, 2));
                else {
                  try {
                    const candidate = JSON.parse(raw);
                    if (
                      !candidate ||
                      typeof candidate !== "object" ||
                      Array.isArray(candidate)
                    )
                      throw new Error("Configuration must be an object.");
                    for (const [key, previous] of Object.entries(
                      record.value,
                    )) {
                      if (
                        !(key in candidate) ||
                        (Array.isArray(previous)
                          ? !Array.isArray(candidate[key])
                          : previous !== null &&
                            typeof candidate[key] !== typeof previous)
                      )
                        throw new Error(
                          "Keep the configuration fields and their value types.",
                        );
                    }
                    setValue(candidate as T);
                    setRaw(null);
                    setError("");
                  } catch {
                    setError(
                      "Correct the JSON before opening the guided editor.",
                    );
                  }
                }
              }}
            >
              {raw === null ? "Full Text Editor" : "Guided Editor"}
            </button>
            <button
              onClick={() =>
                api<Revision<T>[]>(`/configuration/${entity}/history`)
                  .then(setHistory)
                  .catch((e) => setError(message(e)))
              }
            >
              History
            </button>
            <button
              onClick={() => {
                if (
                  !dirty ||
                  window.confirm(
                    "Discard these unsaved changes and load the current version?",
                  )
                )
                  void load();
              }}
            >
              Reload Saved Values
            </button>
          </ActionMenu>
        </div>
      </header>
      {error && (
        <p className="error" role="alert">
          {error} Your unsaved edits remain here.
        </p>
      )}
      {notice && (
        <p className="success-notice" role="status">
          {notice}
        </p>
      )}
      {conflict && (
        <button
          disabled={busy}
          onClick={async () => {
            if (!record) return;
            try {
              const candidate = raw !== null ? (JSON.parse(raw) as T) : value;
              if (!candidate) return;
              const latest = await api<Revision<T>>("/configuration/" + entity);
              const edits = Object.fromEntries(
                Object.entries(candidate).filter(
                  ([key, field]) =>
                    JSON.stringify(field) !==
                    JSON.stringify(
                      (record.value as Record<string, unknown>)[key],
                    ),
                ),
              );
              setRecord(latest);
              setValue({ ...latest.value, ...edits });
              setRaw(null);
              setConflict(false);
              setError("");
              setNotice(
                "Latest version loaded with your edited fields. Review them, then save.",
              );
            } catch (e) {
              setError(message(e));
            }
          }}
        >
          Review My Edits Against Latest Version
        </button>
      )}
      <fieldset className="editor-fields" disabled={busy}>
        {!value ? (
          <p role="status">Loading settings…</p>
        ) : raw !== null ? (
          <label>
            Configuration JSON
            <textarea
              className="raw-editor"
              rows={18}
              value={raw}
              onChange={(e) => setRaw(e.target.value)}
              spellCheck={false}
            />
          </label>
        ) : (
          children(value, setValue)
        )}
      </fieldset>
      {history && (
        <section className="history-panel">
          <div className="section-heading">
            <h3>Version history</h3>
            <button onClick={() => setHistory(null)}>Close History</button>
          </div>
          <p className="muted">
            Restoring creates a new version. Existing call snapshots stay
            unchanged.
          </p>
          {history.map((row) => (
            <details key={row.revision}>
              <summary>
                Version {row.revision} ·{" "}
                {new Date(row.created_at).toLocaleString()}
              </summary>
              <pre>{JSON.stringify(row.value, null, 2)}</pre>
              <button
                disabled={busy || row.revision === record?.revision}
                onClick={() => save(row.value)}
              >
                Restore This Version
              </button>
            </details>
          ))}
        </section>
      )}
    </section>
  );
}
