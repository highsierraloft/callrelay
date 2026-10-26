import { useEffect, useRef, useState } from "react";
import { api, message } from "./api";
import { SettingsDisclosure } from "./Controls";
import { date, money } from "./workflow";
import type {
  MonthlyCosts,
  Pricing,
  ProviderCost,
  Revision,
  Session,
} from "./types";

export function CostBreakdown({ cost }: { cost?: ProviderCost }) {
  if (!cost || cost.coverage === "unavailable")
    return (
      <section>
        <h3>Provider cost</h3>
        <p className="muted">Usage not recorded.</p>
      </section>
    );
  if (cost.coverage === "pending")
    return (
      <section>
        <h3>Provider cost</h3>
        <p className="muted">Available after provider connections close.</p>
      </section>
    );
  return (
    <section className="cost-breakdown">
      <h3>Provider cost</h3>
      <p>
        <strong>{money(cost.total_usd ?? cost.known_subtotal_usd)}</strong> ·{" "}
        {cost.coverage === "partial" ? "Partial estimate" : "Estimated total"}
      </p>
      <table>
        <thead>
          <tr>
            <th>Provider</th>
            <th>Amount</th>
            <th>Basis</th>
          </tr>
        </thead>
        <tbody>
          {cost.providers.map((p) => (
            <tr key={p.provider}>
              <td>{p.provider}</td>
              <td>{money(p.usd)}</td>
              <td>
                {p.provider === "OpenRouter"
                  ? p.basis === "reported"
                    ? "Provider-reported usage"
                    : p.requests_missing
                      ? `${p.requests_missing} response(s) lack usage`
                      : "Catalog estimate"
                  : p.provider === "Soniox"
                    ? `${(p.session_seconds || 0).toFixed(1)} session seconds · approximate`
                    : `${(p.input_characters || 0).toLocaleString()} submitted characters`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted caption">{cost.note}</p>
      {cost.pricing && (
        <p className="muted caption">
          Pricing revision {cost.pricing.revision}, captured when this call was
          prepared.
        </p>
      )}
    </section>
  );
}
export default function Costs({
  session,
  navigate,
}: {
  session: Session;
  navigate: (path: string) => void;
}) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: session.timezone,
    year: "numeric",
    month: "2-digit",
  }).formatToParts(new Date());
  const [month, setMonth] = useState(
      parts.find((p) => p.type === "year")!.value +
        "-" +
        parts.find((p) => p.type === "month")!.value,
    ),
    [data, setData] = useState<MonthlyCosts | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [pricing, setPricing] = useState<Revision<Pricing> | null>(null),
    [draft, setDraft] = useState<Pricing | null>(null),
    [saved, setSaved] = useState(false);
  useEffect(() => {
    let alive = true;
    setData(null);
    setBusy(true);
    setError("");
    api<MonthlyCosts>("/costs?month=" + encodeURIComponent(month))
      .then((v) => {
        if (alive) setData(v);
      })
      .catch((e) => {
        if (alive) setError(message(e));
      })
      .finally(() => {
        if (alive) setBusy(false);
      });
    return () => {
      alive = false;
    };
  }, [month]);
  useEffect(() => {
    api<Revision<Pricing>>("/configuration/pricing")
      .then((v) => {
        setPricing(v);
        setDraft(v.value);
      })
      .catch((e) => setError(message(e)));
  }, []);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!pricing || !draft) return;
    setBusy(true);
    setSaved(false);
    try {
      const next = await api<Revision<Pricing>>(
        "/configuration/pricing",
        "PUT",
        { expected_revision: pricing.revision, value: draft },
      );
      setPricing(next);
      setDraft(next.value);
      setSaved(true);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  const currentMonth = useRef(month);
  currentMonth.current = month;
  async function more() {
    if (data?.next_offset == null) return;
    setBusy(true);
    try {
      const next = await api<MonthlyCosts>(
        "/costs?" +
          new URLSearchParams({ month, offset: String(data.next_offset) }),
      );
      if (currentMonth.current !== month) return;
      setData({ ...next, calls: [...data.calls, ...next.calls] });
    } catch (e) {
      if (currentMonth.current === month) setError(message(e));
    } finally {
      if (currentMonth.current === month) setBusy(false);
    }
  }
  return (
    <section className="workflow-page costs-page" aria-label="Call costs">
      <div className="workflow-content">
        <div className="workflow-toolbar cost-toolbar">
          <div className="cost-summary">
            {data && (
              <>
                <p className="cost-total">
                  <span>Estimated provider cost</span>
                  <strong>
                    {data.call_count
                      ? data.coverage.complete + data.coverage.partial
                        ? money(data.known_subtotal_usd)
                        : "Not recorded"
                      : money(0)}
                  </strong>
                </p>
                <p className="muted cost-coverage">
                  {data.call_count
                    ? [
                        data.coverage.complete &&
                          `${data.coverage.complete} complete`,
                        data.coverage.partial &&
                          `${data.coverage.partial} partial`,
                        data.coverage.unavailable &&
                          `${data.coverage.unavailable} without usage`,
                      ]
                        .filter(Boolean)
                        .join(" · ")
                    : "No calls"}
                </p>
              </>
            )}
            {busy && !data && <p role="status">Loading costs…</p>}
          </div>
          <label>
            Month
            <input
              type="month"
              value={month}
              onChange={(e) => e.target.value && setMonth(e.target.value)}
            />
          </label>
        </div>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        {data && (
          <>
            {!!data.providers.length && (
              <table className="provider-totals">
                <thead>
                  <tr>
                    <th>Provider</th>
                    <th>Estimate</th>
                  </tr>
                </thead>
                <tbody>
                  {data.providers.map((p) => (
                    <tr key={p.provider}>
                      <td>{p.provider}</td>
                      <td>{money(p.usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {!!data.calls.length && (
              <>
                <div className="cost-calls">
                  {data.calls.map((c) => (
                    <button
                      className="cost-call"
                      key={c.call_id}
                      onClick={() => navigate("/calls/" + c.call_id)}
                    >
                      <span>
                        <strong>{c.target}</strong>
                        <small>{date(c.created_at, session.timezone)}</small>
                      </span>
                      <span>
                        {money(c.cost.total_usd ?? c.cost.known_subtotal_usd)}
                        {c.cost.coverage === "partial" && (
                          <small>Partial estimate</small>
                        )}
                      </span>
                    </button>
                  ))}
                </div>
              </>
            )}
            {data.next_offset !== null && (
              <button onClick={more} disabled={busy}>
                Load More Costs
              </button>
            )}
          </>
        )}
        <SettingsDisclosure
          title="Pricing assumptions"
          summary="Changes apply to future call preparations"
        >
          <p className="muted">
            OpenRouter uses reported usage cost where available, then catalog
            rates. Soniox’s hourly equivalent is approximate because it bills
            tokens. Inworld uses submitted characters, including interrupted
            speech. Set speech rates to match your plan.
          </p>
          <p className="caption">
            <a
              href="https://soniox.com/pricing"
              target="_blank"
              rel="noreferrer"
            >
              Soniox pricing
            </a>{" "}
            ·{" "}
            <a
              href="https://inworld.ai/pricing"
              target="_blank"
              rel="noreferrer"
            >
              Inworld pricing
            </a>
          </p>
          {draft && (
            <form onSubmit={save}>
              {(
                [
                  ["soniox_usd_per_hour", "Soniox · USD per streaming hour"],
                  [
                    "inworld_tts2_usd_per_million_characters",
                    "Inworld TTS-2 · USD per million characters",
                  ],
                  [
                    "inworld_flash_usd_per_million_characters",
                    "Inworld Flash · USD per million characters",
                  ],
                ] as const
              ).map(([key, label]) => (
                <label key={key}>
                  {label}
                  <input
                    required
                    type="number"
                    min="0"
                    max="10000"
                    step="any"
                    value={Number.isFinite(draft[key]) ? draft[key] : ""}
                    onChange={(e) => {
                      setSaved(false);
                      setDraft({ ...draft, [key]: e.target.valueAsNumber });
                    }}
                  />
                </label>
              ))}
              <label>
                Pricing note
                <textarea
                  rows={3}
                  value={draft.note}
                  onChange={(e) => setDraft({ ...draft, note: e.target.value })}
                />
              </label>
              <button type="submit" disabled={busy}>
                {busy ? "Saving…" : "Save Pricing"}
              </button>
              {saved && (
                <p role="status">
                  Pricing saved. Earlier estimates keep their captured rates.
                </p>
              )}
            </form>
          )}
        </SettingsDisclosure>
      </div>
    </section>
  );
}
