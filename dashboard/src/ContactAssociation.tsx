import { useEffect, useState } from "react";
import { api, message } from "./api";
import type { Contact, Detail } from "./types";

export default function ContactAssociation({
  detail,
  onUpdated,
}: {
  detail: Detail;
  onUpdated: (contact: Detail["contact"]) => void;
}) {
  const [editing, setEditing] = useState(false),
    [query, setQuery] = useState(""),
    [contacts, setContacts] = useState<Contact[]>([]),
    [selected, setSelected] = useState(detail.contact?.contact_id || ""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!editing) return;
    const abort = new AbortController();
    const timer = setTimeout(() => {
      api<{ contacts: Contact[] }>(
        "/contacts?" + new URLSearchParams({ limit: "100", search: query }),
        "GET",
        undefined,
        abort.signal,
      )
        .then((data) => setContacts(data.contacts))
        .catch((e) => {
          if (!abort.signal.aborted) setError(message(e));
        });
    }, 180);
    return () => {
      clearTimeout(timer);
      abort.abort();
    };
  }, [editing, query]);
  async function save() {
    setBusy(true);
    try {
      const data = await api<{ contact: Detail["contact"] }>(
        "/calls/" + detail.call_id + "/contact",
        "PUT",
        {
          expected_contact_id: detail.contact?.contact_id || null,
          contact_id: selected || null,
        },
      );
      onUpdated(data.contact);
      setEditing(false);
      setError("");
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="contact-association">
      {detail.contact?.recognition &&
        ["shared", "identity_mismatch"].includes(
          detail.contact.recognition,
        ) && (
          <p className="muted">
            Caller identity needs review. This conversation will not update a
            matched person's memory automatically.
          </p>
        )}
      <button onClick={() => setEditing(!editing)}>
        {editing ? "Close Contact Selection" : "Change Contact Association"}
      </button>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {editing && (
        <div className="form-section">
          <label>
            Find a contact
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Name, company, or number"
            />
          </label>
          <label>
            Contact
            <select
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
            >
              <option value="">Unassigned</option>
              {contacts.map((contact) => (
                <option key={contact.contact_id} value={contact.contact_id}>
                  {contact.name ||
                    contact.numbers[0]?.phone ||
                    "Unknown caller"}
                </option>
              ))}
            </select>
          </label>
          <p className="field-help">
            This moves the history link. It does not merge people, change number
            associations, or rewrite the call's original memory snapshot. Review
            the call again after correcting its contact.
          </p>
          <button disabled={busy} onClick={save}>
            Save Association
          </button>
        </div>
      )}
    </div>
  );
}
