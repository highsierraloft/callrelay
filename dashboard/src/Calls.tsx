import { useEffect, useRef, useState } from "react";
import {
  Search,
  ChevronLeft,
  ChevronRight,
  Download,
  FileText,
  Square,
  RefreshCw,
} from "lucide-react";
import { api, message } from "./api";
import AIInputs from "./AIInputs";
import ContactAssociation from "./ContactAssociation";
import OutcomePanel from "./OutcomePanel";
import { CostBreakdown } from "./Costs";
import { date, outcomes } from "./workflow";
import { tabKeys } from "./a11y";
import { SettingsDisclosure } from "./Controls";
import {
  languages,
  terminal,
  type Brief,
  type Call,
  type Detail,
  type Session,
  type AIInputInspection,
} from "./types";

export { date } from "./workflow";
const stateLabel = (state: string) =>
  state.replaceAll("_", " ").replace(/^./, (v) => v.toUpperCase());
export default function Calls({
  selected,
  session,
  navigate,
  onNew,
}: {
  selected?: string;
  session: Session;
  navigate: (path: string) => void;
  onNew: (brief?: Brief) => void;
}) {
  const [calls, setCalls] = useState<Call[]>([]),
    [q, setQ] = useState(""),
    [state, setState] = useState(""),
    [outcomeFilter, setOutcomeFilter] = useState(""),
    [direction, setDirection] = useState(""),
    [language, setLanguage] = useState(""),
    [profile, setProfile] = useState(""),
    [start, setStart] = useState(""),
    [end, setEnd] = useState("");
  const [cursor, setCursor] = useState<string | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [loadingMore, setLoadingMore] = useState(false),
    [refresh, setRefresh] = useState(0);
  const paginated = useRef(false),
    pageRequest = useRef(false),
    filterKey = useRef("");
  filterKey.current = JSON.stringify([
    q,
    state,
    outcomeFilter,
    direction,
    language,
    profile,
    start,
    end,
  ]);
  useEffect(() => {
    paginated.current = false;
  }, [q, state, outcomeFilter, direction, language, profile, start, end]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(
      () => {
        setLoading(true);
        const params = new URLSearchParams({
          q,
          state,
          outcome: outcomeFilter,
          direction,
          language,
          profile,
          start,
          end,
        });
        api<{ calls: Call[]; next_cursor: string | null }>(
          "/calls?" + params,
          undefined,
          undefined,
          controller.signal,
        )
          .then((r) => {
            setCalls(r.calls);
            setCursor(r.next_cursor);
            setError("");
          })
          .catch((e) => {
            if (e.name !== "AbortError") setError(message(e));
          })
          .finally(() => {
            if (!controller.signal.aborted) setLoading(false);
          });
      },
      q ? 250 : 0,
    );
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [
    q,
    state,
    outcomeFilter,
    direction,
    language,
    profile,
    start,
    end,
    refresh,
  ]);
  useEffect(() => {
    const timer = setInterval(() => {
      if (!paginated.current && !pageRequest.current) setRefresh((v) => v + 1);
    }, 15000);
    return () => clearInterval(timer);
  }, []);
  async function more() {
    if (!cursor || pageRequest.current || loading) return;
    const requestedFilters = filterKey.current;
    pageRequest.current = true;
    setLoadingMore(true);
    try {
      const r = await api<{ calls: Call[]; next_cursor: string | null }>(
        "/calls?" +
          new URLSearchParams({
            q,
            state,
            outcome: outcomeFilter,
            direction,
            language,
            profile,
            start,
            end,
            cursor,
          }),
      );
      if (requestedFilters !== filterKey.current) return;
      paginated.current = true;
      setCalls((v) => [
        ...v,
        ...r.calls.filter((c) => !v.some((old) => old.call_id === c.call_id)),
      ]);
      setCursor(r.next_cursor);
    } catch (e) {
      setError(message(e));
    } finally {
      pageRequest.current = false;
      setLoadingMore(false);
    }
  }
  return (
    <div className={"calls-layout " + (selected ? "has-selection" : "")}>
      <section className="call-list-pane" aria-label="Call history">
        <div className="list-controls">
          <label className="search-field">
            <Search size={17} />
            <input
              aria-label="Search transcripts and recipients"
              placeholder="Search calls and transcripts"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </label>
          <details className="compact-details">
            <summary>
              Filters
              {[
                state,
                outcomeFilter,
                direction,
                language,
                profile,
                start,
                end,
              ].filter(Boolean).length > 0 &&
                ` · ${[state, outcomeFilter, direction, language, profile, start, end].filter(Boolean).length} active`}
            </summary>
            <div className="filter-row">
              <select
                aria-label="Filter by direction"
                value={direction}
                onChange={(e) => setDirection(e.target.value)}
              >
                <option value="">All directions</option>
                <option value="inbound">Incoming</option>
                <option value="outbound">Outgoing</option>
              </select>
              <select
                aria-label="Filter by status"
                value={state}
                onChange={(e) => setState(e.target.value)}
              >
                <option value="">All statuses</option>
                {[
                  "completed",
                  "failed",
                  "timed_out",
                  "active",
                  "terminating",
                ].map((v) => (
                  <option key={v} value={v}>
                    {stateLabel(v)}
                  </option>
                ))}
              </select>
              <select
                aria-label="Filter by language"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
              >
                <option value="">All languages</option>
                {Object.entries(languages).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
              <button
                className="icon-button"
                aria-label="Refresh calls"
                disabled={loadingMore}
                onClick={() => {
                  paginated.current = false;
                  setRefresh((v) => v + 1);
                }}
              >
                <RefreshCw size={16} />
              </button>
            </div>

            <label>
              Task outcome
              <select
                value={outcomeFilter}
                onChange={(e) => setOutcomeFilter(e.target.value)}
              >
                <option value="">All outcomes</option>
                <option value="not_recorded">Not recorded</option>
                {Object.entries(outcomes).map(([v, label]) => (
                  <option key={v} value={v}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Personality
              <select
                value={profile}
                onChange={(e) => setProfile(e.target.value)}
              >
                <option value="">All personalities</option>
                {session.setup.profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="two-fields">
              <label>
                From
                <input
                  type="date"
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                />
              </label>
              <label>
                Through
                <input
                  type="date"
                  value={end}
                  onChange={(e) => setEnd(e.target.value)}
                />
              </label>
            </div>
          </details>
        </div>
        {error && (
          <p role="alert" className="error inset">
            {error}
          </p>
        )}
        {loading && !calls.length && (
          <p className="muted inset" role="status">
            Loading calls…
          </p>
        )}
        <div className="call-rows">
          {calls.map((call) => (
            <button
              className={
                "call-row " + (selected === call.call_id ? "selected" : "")
              }
              key={call.call_id}
              onClick={() => navigate("/calls/" + call.call_id)}
              aria-current={selected === call.call_id ? "true" : undefined}
            >
              <span className="row-top">
                <strong>{call.target.name}</strong>
                <time>{date(call.created_at, session.timezone, true)}</time>
              </span>
              <span className="row-number">{call.target.phone}</span>
              <span className="row-objective">{call.objective}</span>
              {(call.outcome_status || !!call.pending_followups) && (
                <span className="caption muted">
                  {call.outcome_status
                    ? outcomes[call.outcome_status]
                    : "Outcome not recorded"}
                  {call.pending_followups
                    ? ` · ${call.pending_followups} open follow-up(s)`
                    : ""}
                </span>
              )}
              <span className="row-footer">
                <span
                  className={
                    "status-label " + (call.state === "failed" ? "bad" : "")
                  }
                >
                  {stateLabel(call.state)}
                </span>
                <span>
                  {languages[call.language] || "Language not recorded"}
                </span>
              </span>
            </button>
          ))}
        </div>
        {!loading && !calls.length && (
          <div className="empty-state">
            {q || state || language || start || end
              ? "No matching calls"
              : "No calls yet"}
          </div>
        )}
        {cursor && (
          <button
            className="load-more"
            onClick={more}
            disabled={loadingMore || loading}
          >
            {loadingMore ? "Loading…" : "Load More"}
          </button>
        )}
      </section>
      {selected ? (
        <CallDetail
          key={selected}
          id={selected}
          session={session}
          onBack={() => navigate("/calls")}
          onReuse={onNew}
          onFollowups={() => navigate("/calls/followups")}
        />
      ) : (
        <section className="empty-state call-welcome">Select a call</section>
      )}
    </div>
  );
}

function CallDetail({
  id,
  session,
  onBack,
  onReuse,
  onFollowups,
}: {
  id: string;
  session: Session;
  onBack: () => void;
  onReuse: (brief: Brief) => void;
  onFollowups: () => void;
}) {
  const [detail, setDetail] = useState<Detail | null>(null),
    [error, setError] = useState(""),
    [tab, setTab] = useState("transcript"),
    [note, setNote] = useState(""),
    [tags, setTags] = useState(""),
    [saved, setSaved] = useState(false),
    [ending, setEnding] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [inputs, setInputs] = useState<AIInputInspection | null>(null);
  async function showInputs() {
    setInputs(await api<AIInputInspection>("/calls/" + id + "/ai-inputs"));
  }
  useEffect(() => {
    if (!detail || terminal(detail.state)) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [detail?.state]);
  const last = useRef(0),
    initialized = useRef(false);
  useEffect(() => {
    let alive = true,
      busy = false,
      done = false;
    const abort = new AbortController();
    async function load() {
      if (busy || done) return;
      busy = true;
      try {
        let after = last.current;
        const turns: Detail["transcript"] = [];
        let result: Detail;
        do {
          result = await api<Detail>(
            `/calls/${id}?after=${after}`,
            undefined,
            undefined,
            abort.signal,
          );
          turns.push(...result.transcript);
          after = result.next_transcript_cursor ?? 0;
        } while (after);
        if (!alive) return;
        last.current = turns.at(-1)?.sequence_number ?? last.current;
        setDetail((old) => ({
          ...result,
          transcript: [
            ...new Map(
              [...(old?.transcript || []), ...turns].map((t) => [
                t.sequence_number,
                t,
              ]),
            ).values(),
          ],
        }));
        done = terminal(result.state) && result.cost?.coverage !== "pending";
        if (!initialized.current) {
          setNote(result.annotation.note);
          setTags(result.annotation.tags.join(", "));
          initialized.current = true;
        }
      } catch (e) {
        if (alive) setError(message(e));
      } finally {
        busy = false;
      }
    }
    load();
    const timer = setInterval(load, 2000);
    return () => {
      alive = false;
      abort.abort();
      clearInterval(timer);
    };
  }, [id]);
  async function end() {
    setEnding(true);
    try {
      await api(`/calls/${id}/end`, "POST");
    } catch (e) {
      setError(message(e));
    } finally {
      setEnding(false);
    }
  }
  async function saveNote() {
    try {
      await api(`/calls/${id}/annotation`, "PUT", {
        note,
        tags: tags
          .split(",")
          .map((x) => x.trim())
          .filter(Boolean),
      });
      setSaved(true);
    } catch (e) {
      setError(message(e));
    }
  }
  if (!detail)
    return (
      <section className="detail-pane">
        <button className="back-button" onClick={onBack}>
          <ChevronLeft size={18} />
          Calls
        </button>
        <p
          role={error ? "alert" : "status"}
          className={error ? "error" : "muted"}
        >
          {error || "Opening call…"}
        </p>
      </section>
    );
  const elapsed = Math.max(
    0,
    Math.floor(
      ((detail.timing.ended_at ? Date.parse(detail.timing.ended_at) : now) -
        Date.parse(detail.timing.answered_at || detail.timing.created_at)) /
        1000,
    ),
  );
  const durationLabel = `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`;
  const mp3 = detail.artifacts.files.some((f) => f.name === "recording.mp3"),
    md = detail.artifacts.files.some((f) => f.name === "transcript.md");
  return (
    <section className="detail-pane">
      <div className="call-detail-heading">
        <button className="back-button" onClick={onBack}>
          <ChevronLeft size={18} />
          Calls
        </button>
        <div className="row-top">
          <div>
            <h2>{detail.brief.target.name}</h2>
            <p className="call-date muted">
              {date(detail.timing.created_at, session.timezone)}
            </p>
            <span className="muted">{detail.brief.target.phone}</span>
          </div>
          <span className="pill">
            {stateLabel(detail.state)} ·{" "}
            <span aria-label="Elapsed call time">{durationLabel}</span>
          </span>
        </div>
        <p className="call-objective">{detail.brief.objective}</p>
        <div className="call-actions">
          {!terminal(detail.state) && (
            <button className="danger" disabled={ending} onClick={end}>
              <Square size={15} />
              {ending ? "Ending…" : "End Call"}
            </button>
          )}
          <button onClick={() => onReuse(detail.brief)}>
            Use This Brief Again <ChevronRight size={15} />
          </button>
        </div>
        {mp3 ? (
          <div className="recording">
            <div className="row-top">
              <strong>Recording</strong>
              <a
                href={`/admin/api/calls/${id}/files/recording.mp3`}
                download="recording.mp3"
              >
                <Download size={14} />
                MP3
              </a>
            </div>
            <audio
              controls
              preload="metadata"
              src={`/admin/api/calls/${id}/files/recording.mp3`}
            />
          </div>
        ) : (
          <div className="recording muted">
            <FileText size={18} />
            <span>
              {!terminal(detail.state)
                ? "The recording will be available after the call."
                : `Recording ${detail.artifacts.audio_status}`}
            </span>
          </div>
        )}
        <div
          className="detail-tabs"
          role="tablist"
          aria-label="Call details"
          onKeyDown={tabKeys}
        >
          {["transcript", "outcome", "brief", "notes", "details"].map((t) => (
            <button
              key={t}
              role="tab"
              id={`call-tab-${t}`}
              aria-controls={`call-panel-${t}`}
              tabIndex={tab === t ? 0 : -1}
              aria-selected={tab === t}
              onClick={() => setTab(t)}
            >
              {t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div
        className="detail-content"
        role="tabpanel"
        id={`call-panel-${tab}`}
        aria-labelledby={`call-tab-${tab}`}
      >
        {tab === "transcript" && (
          <>
            <div className="section-heading">
              <span className="muted">
                {detail.transcript.length} entries · times in {session.timezone}
              </span>
              {md && (
                <a
                  href={`/admin/api/calls/${id}/files/transcript.md`}
                  download="transcript.md"
                >
                  <Download size={14} />
                  Full Markdown
                </a>
              )}
            </div>
            <p className="caption muted">
              Entry times are not exact audio timestamps.
            </p>
            {detail.transcript.length ? (
              detail.transcript.map((turn, i) => (
                <article
                  className="transcript-turn"
                  key={turn.sequence_number ?? i}
                >
                  <header>
                    <strong>
                      {turn.speaker === "assistant"
                        ? "Calling agent"
                        : "Recipient"}
                    </strong>
                    {turn.created_at && (
                      <time>
                        {new Intl.DateTimeFormat("en-GB", {
                          timeZone: session.timezone,
                          hour: "2-digit",
                          minute: "2-digit",
                          second: "2-digit",
                        }).format(new Date(turn.created_at))}
                      </time>
                    )}
                    {turn.source_event_type?.includes("interrupted") && (
                      <span className="caption">Interrupted</span>
                    )}
                  </header>
                  <p>{turn.text}</p>
                </article>
              ))
            ) : (
              <p className="muted">No speech has been transcribed.</p>
            )}
          </>
        )}
        {tab === "outcome" && (
          <section className="reading call-summary">
            <div className="section-heading">
              <h3>Conversation summary</h3>
              {!["pending", "leased"].includes(detail.review?.state || "") &&
                terminal(detail.state) && (
                  <button
                    onClick={async () => {
                      try {
                        await api(
                          "/calls/" + detail.call_id + "/review",
                          "POST",
                        );
                        setDetail((old) =>
                          old ? { ...old, review: { state: "pending" } } : old,
                        );
                      } catch (e) {
                        setError(message(e));
                      }
                    }}
                  >
                    {detail.review
                      ? "Review Again with Hermes"
                      : "Queue review"}
                  </button>
                )}
            </div>
            <p>
              {detail.summary?.value.text ||
                (detail.review?.state === "pending" ||
                detail.review?.state === "leased"
                  ? "Agent review pending."
                  : "No summary recorded.")}
            </p>
            {detail.contact?.contact_id && (
              <a href={"/contacts/" + detail.contact.contact_id}>
                Open Contact Memory & History
              </a>
            )}
            {terminal(detail.state) && detail.contact && (
              <ContactAssociation
                detail={detail}
                onUpdated={(contact) =>
                  setDetail((old) => (old ? { ...old, contact } : old))
                }
              />
            )}
          </section>
        )}
        {tab === "outcome" &&
          (terminal(detail.state) ? (
            <OutcomePanel
              detail={detail}
              onFollowups={onFollowups}
              onEvidence={() => setTab("transcript")}
              onUpdated={(assessment, followups) =>
                setDetail((old) =>
                  old ? { ...old, assessment, followups } : old,
                )
              }
            />
          ) : (
            <p className="muted">
              The outcome can be assessed after the call ends.
            </p>
          ))}
        {tab === "brief" && (
          <div className="reading">
            <h3>Approved brief</h3>
            <dl className="facts">
              <dt>Recipient</dt>
              <dd>{detail.brief.target.name}</dd>
              <dt>About this person</dt>
              <dd className="preserve">
                {detail.brief.target.background || "Not supplied"}
              </dd>
              <dt>Task</dt>
              <dd className="preserve">{detail.brief.objective}</dd>
              <dt>Done when</dt>
              <dd className="preserve">
                {detail.brief.completion_criteria || "Not supplied"}
              </dd>
              {detail.configuration?.prompt_contract_version !== 2 &&
                detail.brief.intention && (
                  <>
                    <dt>Legacy purpose</dt>
                    <dd>{detail.brief.intention}</dd>
                  </>
                )}
              <dt>Initial language</dt>
              <dd>{languages[detail.brief.language]}</dd>
              <dt>Duration budget</dt>
              <dd>{detail.brief.max_duration_seconds / 60} minutes</dd>
            </dl>
            {[
              ["Relevant facts", detail.brief.relevant_facts],
              ["Allowed commitments", detail.brief.allowed_commitments],
              ["Limits", detail.brief.hard_constraints],
            ].map(([label, values]) => (
              <div key={String(label)}>
                <h4>{String(label)}</h4>
                <p className="preserve">
                  {(values as string[]).join("\n") || "None supplied"}
                </p>
              </div>
            ))}
          </div>
        )}
        {tab === "details" && (
          <SettingsDisclosure
            title="Settings Used"
            summary={
              detail.configuration
                ? detail.configuration.profile_name +
                  " · " +
                  detail.configuration.voice.inworld_voice
                : "Not recorded"
            }
            initialOpen
          >
            {detail.configuration ? (
              <div className="reading call-snapshot">
                <CostBreakdown cost={detail.cost} />
                <h3>{detail.configuration.profile_name}</h3>
                <p className="muted">
                  Personality v{detail.configuration.profile_revision} · Global
                  settings v{detail.configuration.configuration_revision}
                </p>
                <dl className="facts">
                  <dt>Model</dt>
                  <dd>{detail.configuration.voice.dialogue_model}</dd>
                  <dt>Voice</dt>
                  <dd>{detail.configuration.voice.inworld_voice}</dd>
                  <dt>Voice model</dt>
                  <dd>{detail.configuration.voice.inworld_model}</dd>
                </dl>
                <button
                  onClick={() =>
                    showInputs().catch((e) => setError(message(e)))
                  }
                >
                  Inspect AI Inputs
                </button>
                <details>
                  <summary>Recorded starting system prompt</summary>
                  <pre className="prompt-preview">{detail.prompt}</pre>
                </details>
                <details>
                  <summary>All settings used</summary>
                  <pre>
                    {JSON.stringify(detail.configuration.voice, null, 2)}
                  </pre>
                </details>
              </div>
            ) : (
              <div className="empty small">
                <h3>Configuration not recorded</h3>
                <p>
                  This older call has no saved configuration snapshot. Its
                  original brief is still available.
                </p>
              </div>
            )}
            {inputs && (
              <AIInputs
                data={inputs}
                onClose={() => setInputs(null)}
                onRefresh={showInputs}
              />
            )}
          </SettingsDisclosure>
        )}
        {tab === "details" && (
          <SettingsDisclosure
            title="Diagnostics"
            summary={
              detail.timing.termination_reason?.replaceAll("_", " ") ||
              "Call in progress"
            }
          >
            <div className="reading">
              <h3>Call diagnostics</h3>
              <dl className="facts">
                <dt>Ended because</dt>
                <dd>
                  {detail.timing.termination_reason?.replaceAll("_", " ") ||
                    "Still in progress"}
                </dd>
                <dt>Answered</dt>
                <dd>{date(detail.timing.answered_at, session.timezone)}</dd>
                <dt>Ended</dt>
                <dd>{date(detail.timing.ended_at, session.timezone)}</dd>
              </dl>
              <h4>Server reply delays</h4>
              <p className="muted">
                These measurements exclude carrier and handset delay.
              </p>
              {detail.latency.length ? (
                detail.latency.map((m, i) => (
                  <details key={i} className="metric">
                    <summary>
                      Reply {i + 1}
                      <strong>{m.seconds.toFixed(2)} s</strong>
                    </summary>
                    {m.parts?.map((p, j) => (
                      <div className="row-top" key={j}>
                        <span>{p.stage.replaceAll("_", " ")}</span>
                        <span>{p.seconds.toFixed(2)} s</span>
                      </div>
                    ))}
                  </details>
                ))
              ) : (
                <p className="muted">
                  No timing measurements were recorded for this call.
                </p>
              )}
            </div>
          </SettingsDisclosure>
        )}
        {tab === "notes" && (
          <div className="reading">
            <label>
              Notes
              <textarea
                rows={7}
                value={note}
                onChange={(e) => {
                  setNote(e.target.value);
                  setSaved(false);
                }}
              />
            </label>
            <label>
              Quality tags
              <input
                value={tags}
                placeholder="Natural voice, long pauses, wrong language"
                onChange={(e) => {
                  setTags(e.target.value);
                  setSaved(false);
                }}
              />
            </label>
            <div className="save-row">
              <button className="primary" onClick={saveNote}>
                Save Notes
              </button>
              {saved && <span role="status">Saved</span>}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
