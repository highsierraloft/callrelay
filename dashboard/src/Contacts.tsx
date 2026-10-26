import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowDownLeft,
  ArrowUpRight,
  ChevronRight,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import { api, message } from "./api";
import { ActionMenu, SectionTabs, SettingsDisclosure } from "./Controls";
import type { Contact, MemoryFact } from "./types";

export default function Contacts({
  selected,
  navigate,
  timezone,
}: {
  selected?: string;
  timezone: string;
  navigate: (path: string) => void;
}) {
  const [query, setQuery] = useState(""),
    [rows, setRows] = useState<Contact[]>([]),
    [offset, setOffset] = useState<number | null>(null);
  const [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setLoading(true);
    const timer = setTimeout(
      () =>
        api<{ contacts: Contact[]; next_offset: number | null }>(
          "/contacts?" + new URLSearchParams({ search: query }),
          "GET",
          undefined,
          abort.signal,
        )
          .then((data) => {
            setRows(data.contacts);
            setOffset(data.next_offset);
            setError("");
            setLoading(false);
          })
          .catch((e) => {
            if (!abort.signal.aborted) {
              setError(message(e));
              setLoading(false);
            }
          }),
      180,
    );
    return () => {
      clearTimeout(timer);
      abort.abort();
    };
  }, [query, refresh]);
  async function more() {
    try {
      const data = await api<{
        contacts: Contact[];
        next_offset: number | null;
      }>(
        "/contacts?" +
          new URLSearchParams({ search: query, offset: String(offset) }),
      );
      setRows((old) => [...old, ...data.contacts]);
      setOffset(data.next_offset);
    } catch (e) {
      setError(message(e));
    }
  }
  return (
    <div className={"contacts-layout " + (selected ? "has-selection" : "")}>
      <aside className="contact-list" aria-label="Contact list">
        <div className="contact-search">
          <label className="search-field">
            <Search size={16} />
            <input
              aria-label="Search contacts"
              placeholder="Search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <button
            className="icon-button"
            aria-label="New contact"
            onClick={() => navigate("/contacts/new")}
          >
            <Plus size={18} />
          </button>
        </div>
        {loading && !rows.length && (
          <p className="inset muted" role="status">
            Loading contacts…
          </p>
        )}
        {error && (
          <p role="alert" className="error inset">
            {error}
          </p>
        )}
        {!loading && !rows.length && (
          <p className="inset muted">
            {query ? "No matching contacts." : "No contacts"}
          </p>
        )}
        <ul className="contact-rows" aria-busy={loading}>
          {rows.map((row) => (
            <li key={row.contact_id}>
              <a
                className={selected === row.contact_id ? "selected" : ""}
                href={"/contacts/" + row.contact_id}
                aria-current={selected === row.contact_id ? "true" : undefined}
                title={[row.name, ...row.numbers.map((n) => n.phone)]
                  .filter(Boolean)
                  .join(" · ")}
                onClick={(e) => {
                  e.preventDefault();
                  navigate("/contacts/" + row.contact_id);
                }}
              >
                <strong>{row.name || "Unknown caller"}</strong>
                <span>{row.numbers[0]?.phone || "No number"}</span>
              </a>
            </li>
          ))}
        </ul>
        {offset !== null && (
          <button className="load-more" onClick={more}>
            Load More Contacts
          </button>
        )}
      </aside>
      <section className="contact-detail">
        {selected ? (
          <ContactDetail
            key={selected}
            id={selected}
            timezone={timezone}
            navigate={navigate}
            onSaved={() => setRefresh((x) => x + 1)}
          />
        ) : (
          <div className="empty-detail">
            <p>Select a contact</p>
          </div>
        )}
      </section>
    </div>
  );
}

function ContactDetail({
  id,
  navigate,
  onSaved,
  timezone,
}: {
  id: string;
  timezone: string;
  navigate: (path: string) => void;
  onSaved: () => void;
}) {
  const [contact, setContact] = useState<Contact>(),
    [name, setName] = useState(""),
    [background, setBackground] = useState(""),
    [note, setNote] = useState(""),
    [numbers, setNumbers] = useState("");
  const [facts, setFacts] = useState<MemoryFact[]>([]),
    [section, setSection] = useState("history"),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  const dirty =
    name !== (contact?.name || "") ||
    background !== (contact?.background || "") ||
    note !== (contact?.private_note || "") ||
    numbers !== (contact?.numbers.map((n) => n.phone).join("\n") || "") ||
    JSON.stringify(facts) !== JSON.stringify(contact?.memory.facts || []);
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;
  useEffect(() => {
    const navigation = (event: Event) => {
      if (
        dirtyRef.current &&
        (event as CustomEvent<string>).detail !== "/contacts/" + id &&
        !window.confirm("Discard the unsaved contact changes?")
      )
        event.preventDefault();
    };
    const unload = (event: BeforeUnloadEvent) => {
      if (dirtyRef.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("calling-navigation", navigation);
    window.addEventListener("beforeunload", unload);
    return () => {
      window.removeEventListener("calling-navigation", navigation);
      window.removeEventListener("beforeunload", unload);
    };
  }, [id]);
  function assign(row: Contact) {
    setContact(row);
    setName(row.name);
    setBackground(row.background);
    setNote(row.private_note || "");
    setNumbers(row.numbers.map((n) => n.phone).join("\n"));
    setFacts(row.memory.facts);
  }
  useEffect(() => {
    if (id === "new") {
      setSection("details");
      return;
    }
    let alive = true;
    api<Contact>("/contacts/" + id)
      .then((row) => {
        if (alive) assign(row);
      })
      .catch((e) => {
        if (alive) setError(message(e));
      });
    return () => {
      alive = false;
    };
  }, [id]);
  async function save(memory = false) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (memory && contact) {
        await api("/contacts/" + id + "/memory", "PUT", {
          expected_revision: contact.memory.revision,
          facts,
        });
        const next = await api<Contact>("/contacts/" + id);
        setContact((old) => (old ? { ...old, memory: next.memory } : next));
        setFacts(next.memory.facts);
      } else {
        const row = await api<Contact>(
          "/contacts" + (id === "new" ? "" : "/" + id),
          id === "new" ? "POST" : "PUT",
          {
            expected_revision: contact?.revision || 0,
            name,
            background,
            private_note: note,
            numbers: numbers
              .split("\n")
              .map((s) => s.trim())
              .filter(Boolean),
          },
        );
        setContact((old) =>
          old ? { ...old, ...row, memory: old.memory } : row,
        );
        setName(row.name);
        setBackground(row.background);
        setNote(row.private_note || "");
        setNumbers(row.numbers.map((n) => n.phone).join("\n"));
        if (id === "new") {
          dirtyRef.current = false;
          navigate("/contacts/" + row.contact_id);
        }
      }
      setNotice("Saved");
      onSaved();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function review(callId: string) {
    setBusy(true);
    try {
      await api("/calls/" + callId + "/review", "POST");
      const next = await api<Contact>("/contacts/" + id);
      setContact((old) =>
        old
          ? { ...old, calls: next.calls, next_offset: next.next_offset }
          : next,
      );
      setNotice("Review queued");
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function more() {
    if (!contact) return;
    try {
      const next = await api<Contact>(
        `/contacts/${id}?offset=${contact.next_offset}`,
      );
      setContact({ ...next, calls: [...contact.calls, ...next.calls] });
    } catch (e) {
      setError(message(e));
    }
  }
  return (
    <>
      <button className="mobile-back" onClick={() => navigate("/contacts")}>
        <ArrowLeft size={17} />
        Contacts
      </button>
      <header className="contact-heading">
        <h2>
          {id === "new"
            ? "New contact"
            : contact?.name || contact?.numbers[0]?.phone || "Unknown caller"}
        </h2>
        <p className="muted">
          {contact?.numbers.map((n) => n.phone).join(" · ")}
        </p>
        {dirty && <p className="change-label">Unsaved changes</p>}
      </header>
      {error && (
        <p className="error inset" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="success-notice inset">
          {notice}
        </p>
      )}
      {!contact && id !== "new" ? (
        <p className="inset" role="status">
          Loading contact…
        </p>
      ) : (
        <SectionTabs
          label="Contact workspace"
          value={section}
          onChange={setSection}
          items={[
            { id: "history", label: "Conversations" },
            { id: "memory", label: "Memory" },
            { id: "details", label: "Details" },
          ]}
        >
          {section === "history" ? (
            <div className="contact-history">
              {!contact?.calls.length && (
                <div className="empty-state">No calls yet</div>
              )}
              {contact?.calls.map((call) => {
                const incoming = call.direction === "inbound";
                const pending = ["pending", "leased"].includes(
                  call.review_state || "",
                );
                const stamp = new Intl.DateTimeFormat("en-GB", {
                  dateStyle: "medium",
                  timeStyle: "short",
                  timeZone: timezone,
                }).format(new Date(call.created_at));
                const state = pending
                  ? "Review pending"
                  : (call.summary?.outcome || call.state).replaceAll("_", " ");
                return (
                  <article className="timeline-call" key={call.call_id}>
                    <a
                      className="conversation-link"
                      href={"/calls/" + call.call_id}
                      aria-label={`Open ${incoming ? "incoming" : "outgoing"} call from ${stamp}`}
                      onClick={(event) => {
                        event.preventDefault();
                        navigate("/calls/" + call.call_id);
                      }}
                    >
                      <span className="conversation-meta">
                        <span className="conversation-direction">
                          {incoming ? (
                            <ArrowDownLeft size={15} />
                          ) : (
                            <ArrowUpRight size={15} />
                          )}
                          {incoming ? "Incoming" : "Outgoing"}
                        </span>
                        <time dateTime={call.created_at}>{stamp}</time>
                      </span>
                      <span
                        className={
                          "conversation-status " +
                          (["failed", "timed_out"].includes(call.state)
                            ? "issue"
                            : "")
                        }
                      >
                        {state}
                      </span>
                      {call.summary?.text && (
                        <p className="conversation-summary">
                          {call.summary.text}
                        </p>
                      )}
                      <ChevronRight
                        size={16}
                        className="conversation-chevron"
                        aria-hidden="true"
                      />
                    </a>
                    {!pending && (
                      <ActionMenu label="Call actions" iconOnly>
                        <button
                          disabled={busy}
                          onClick={() => review(call.call_id)}
                        >
                          {call.review_state
                            ? "Review Again with Hermes"
                            : "Queue review"}
                        </button>
                      </ActionMenu>
                    )}
                  </article>
                );
              })}
              {contact?.next_offset !== null &&
                contact?.next_offset !== undefined && (
                  <button onClick={more}>Load Earlier Calls</button>
                )}
              {!!contact?.callbacks.length && (
                <section>
                  <h3>Related callbacks</h3>
                  {contact.callbacks.map((row) => (
                    <p key={row.schedule_id}>
                      <a
                        href="/callbacks"
                        onClick={(e) => {
                          e.preventDefault();
                          navigate("/callbacks");
                        }}
                      >
                        {row.destination} · {row.state.replaceAll("_", " ")} ·{" "}
                        {new Date(row.window_start).toLocaleString()}
                      </a>
                    </p>
                  ))}
                </section>
              )}
            </div>
          ) : section === "memory" ? (
            <div className="contact-form">
              {!facts.length && (
                <div className="empty-state">No saved memory</div>
              )}
              {facts.map((fact, index) => (
                <div className="memory-fact" key={fact.key}>
                  <label>
                    Fact {index + 1}
                    <textarea
                      rows={2}
                      value={fact.text}
                      onChange={(e) =>
                        setFacts((old) =>
                          old.map((f, i) =>
                            i === index ? { ...f, text: e.target.value } : f,
                          ),
                        )
                      }
                    />
                  </label>
                  <button
                    className="icon-button"
                    aria-label={"Remove fact " + (index + 1)}
                    onClick={() =>
                      setFacts((old) => old.filter((_, i) => i !== index))
                    }
                  >
                    <Trash2 size={17} />
                  </button>
                  {fact.source_call_id && (
                    <a
                      href={"/calls/" + fact.source_call_id}
                      onClick={(e) => {
                        e.preventDefault();
                        navigate("/calls/" + fact.source_call_id);
                      }}
                    >
                      Source conversation
                    </a>
                  )}
                </div>
              ))}
              <p className="field-help">
                Your corrections take priority over automatic reviews.
              </p>
              <div className="save-row">
                <button
                  disabled={facts.length >= 40}
                  onClick={() =>
                    setFacts((old) => [
                      ...old,
                      {
                        key: "owner_" + crypto.randomUUID().slice(0, 8),
                        text: "",
                        evidence_turn_ids: [],
                        source_call_id: null,
                        owner_protected: true,
                      },
                    ])
                  }
                >
                  Add Fact
                </button>
                <button
                  className="primary"
                  disabled={
                    busy || !contact || facts.some((f) => !f.text.trim())
                  }
                  onClick={() => save(true)}
                >
                  Save Memory
                </button>
              </div>
            </div>
          ) : (
            <div className="contact-form">
              <label>
                Name
                <input value={name} onChange={(e) => setName(e.target.value)} />
              </label>
              <label>
                About this person
                <textarea
                  rows={4}
                  value={background}
                  onChange={(e) => setBackground(e.target.value)}
                />
              </label>
              <label>
                Phone numbers
                <textarea
                  rows={3}
                  value={numbers}
                  onChange={(e) => setNumbers(e.target.value)}
                  placeholder="One international number per line"
                />
              </label>
              <SettingsDisclosure
                title="Private owner notes"
                summary="Excluded from calls and Agent reviews"
              >
                <label>
                  Notes
                  <textarea
                    rows={6}
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                  />
                </label>
              </SettingsDisclosure>
              <button
                className="primary"
                disabled={busy || (!name.trim() && !numbers.trim())}
                onClick={() => save()}
              >
                Save Contact
              </button>
            </div>
          )}
        </SectionTabs>
      )}
    </>
  );
}
