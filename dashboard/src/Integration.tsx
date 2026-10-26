import { useEffect, useState } from "react";
import { ArrowUpRight, ChevronRight } from "lucide-react";
import { api, message } from "./api";
import { tabKeys } from "./a11y";
import type { Session, Setup, Status } from "./types";
import { ConfigurationEditor } from "./Profiles";

export default function Integration({
  session,
  onSaved,
  status,
}: {
  session: Session;
  onSaved: () => Promise<void>;
  status: Status | null;
}) {
  const [data, setData] = useState<{
      setup: Setup;
      tools: { name: string; description: string; parameters: unknown }[];
    } | null>(null),
    [error, setError] = useState(""),
    [tab, setTab] = useState("guide");
  useEffect(() => {
    api<typeof data>("/integration")
      .then(setData)
      .catch((e) => setError(message(e)));
  }, []);
  return (
    <div className="integration-page">
      {session.hermes_url && (
        <header className="integration-heading">
          <a
            className="button"
            href={session.hermes_url}
            target="_blank"
            rel="noreferrer"
          >
            Open Hermes <ArrowUpRight size={15} />
          </a>
        </header>
      )}
      <div
        className="detail-tabs"
        role="tablist"
        aria-label="Integration details"
        onKeyDown={tabKeys}
      >
        <button
          role="tab"
          aria-selected={tab === "guide"}
          tabIndex={tab === "guide" ? 0 : -1}
          id="integration-tab-guide"
          aria-controls="integration-panel-guide"
          onClick={() => setTab("guide")}
        >
          Calling Guide
        </button>
        <button
          role="tab"
          aria-selected={tab === "tools"}
          tabIndex={tab === "tools" ? 0 : -1}
          id="integration-tab-tools"
          aria-controls="integration-panel-tools"
          onClick={() => setTab("tools")}
        >
          MCP Tools
        </button>
        <button
          role="tab"
          aria-selected={tab === "setup"}
          tabIndex={tab === "setup" ? 0 : -1}
          id="integration-tab-setup"
          aria-controls="integration-panel-setup"
          onClick={() => setTab("setup")}
        >
          Connection
        </button>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {
        <div
          hidden={tab !== "guide"}
          role="tabpanel"
          id="integration-panel-guide"
          aria-labelledby="integration-tab-guide"
        >
          <ConfigurationEditor
            entity="guide"
            session={session}
            onSaved={onSaved}
          />
        </div>
      }
      {tab === "tools" && (
        <section
          className="integration-content"
          role="tabpanel"
          id="integration-panel-tools"
          aria-labelledby="integration-tab-tools"
        >
          {data?.tools.map((tool) => (
            <details className="tool-row" key={tool.name}>
              <summary>
                <code>{tool.name}</code>
              </summary>
              <p>{tool.description}</p>
              <details>
                <summary>Input schema</summary>
                <pre>{JSON.stringify(tool.parameters, null, 2)}</pre>
              </details>
            </details>
          ))}
        </section>
      )}
      {tab === "setup" && (
        <section
          className="integration-content reading"
          role="tabpanel"
          id="integration-panel-setup"
          aria-labelledby="integration-tab-setup"
        >
          <h3>How the connection works</h3>
          <div className="integration-flow">
            <span>MCP client</span>
            <ChevronRight size={16} />
            <span>Calling MCP</span>
            <ChevronRight size={16} />
            <span>Voice agent</span>
            <ChevronRight size={16} />
            <span>{status?.source_line || session.setup.source_line}</span>
          </div>
          <p className="muted">
            Last MCP setup request:{" "}
            {status?.last_hermes_request?.observed_at
              ? new Date(status.last_hermes_request.observed_at).toLocaleString(
                  "en-GB",
                  { timeZone: session.timezone },
                )
              : "No request recorded yet"}
          </p>
          <p>
            The calling guide goes to your MCP client. Inspect AI Inputs in
            Personalities shows the voice agent’s prompt.
          </p>
          <ol>
            <li>Enable the calling service in your client's MCP settings.</li>
            <li>
              Make <code>get_calling_setup</code> available alongside prepare,
              start, poll, end, and result tools.
            </li>
            <li>
              Before preparing a call, read the setup tool’s guide and choose a
              profile ID from its current library.
            </li>
            <li>
              Supply the recipient in <code>target.phone</code>. Omit owner
              callback fields; the server fills them in.
            </li>
            <li>
              Poll until completion, then return the full Markdown transcript
              and MP3 using their native file paths.
            </li>
          </ol>

          <details>
            <summary>Private connection template</summary>
            <pre>
              {JSON.stringify(
                {
                  name: "calling-agent",
                  url: "http://agent-call:8000/mcp/",
                  headers: {
                    Authorization: "Bearer <existing private MCP credential>",
                    "X-Agent-User-Id": "<configured owner ID>",
                  },
                },
                null,
                2,
              )}
            </pre>
            <p className="muted">
              This address is internal to Hermes’s private server network.
              Credentials remain in its existing private setup.
            </p>
          </details>
        </section>
      )}
    </div>
  );
}
