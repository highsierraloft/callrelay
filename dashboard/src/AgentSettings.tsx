import { useState } from "react";
import RevisionForm from "./RevisionForm";
import VoiceFields from "./VoiceFields";
import HandsetCheck from "./HandsetCheck";
import { SectionTabs, SettingsDisclosure } from "./Controls";
import {
  languages,
  type BusinessSettings,
  type CallbackPolicy,
  type Draft,
  type Global,
  type InboundSettings,
  type Session,
  type Status,
  type Voice,
} from "./types";

const lines = (text: string) => text.split("\n");

export function WorkspaceSettings({
  onSaved,
}: {
  onSaved: () => Promise<void>;
}) {
  const [section, setSection] = useState("business"),
    [voiceSection, setVoiceSection] = useState("voice"),
    [dirty, setDirty] = useState(false);
  return (
    <div className="settings-workspace">
      <SectionTabs
        label="Workspace settings"
        value={section}
        onChange={(next) => {
          if (
            !dirty ||
            window.confirm("Discard unsaved changes in this section?")
          ) {
            setDirty(false);
            setSection(next);
          }
        }}
        items={[
          { id: "business", label: "Business" },
          { id: "voice", label: "Voice & Models" },
          { id: "memory", label: "Memory" },
        ]}
      >
        {section === "voice" ? (
          <RevisionForm<Voice>
            entity="voice_defaults"
            title="Shared voice and model defaults"
            onSaved={onSaved}
            onDirtyChange={setDirty}
          >
            {(value, change) => (
              <SectionTabs
                label="Voice settings"
                value={voiceSection}
                onChange={setVoiceSection}
                items={[
                  { id: "voice", label: "Voice & Models" },
                  { id: "call", label: "Languages & Timing" },
                ]}
              >
                <VoiceFields
                  section={voiceSection as "voice" | "call"}
                  value={value}
                  base={value}
                  onChange={(next) => change(next as Voice)}
                />
              </SectionTabs>
            )}
          </RevisionForm>
        ) : (
          <RevisionForm<BusinessSettings>
            key={section}
            entity="business"
            title={
              section === "business" ? "Business information" : "Memory review"
            }
            onSaved={onSaved}
            onDirtyChange={setDirty}
          >
            {(value, change) =>
              section === "business" ? (
                <div className="form-section">
                  <label>
                    Business name
                    <input
                      value={value.name}
                      onChange={(e) =>
                        change({ ...value, name: e.target.value })
                      }
                      maxLength={160}
                      autoComplete="organization"
                    />
                  </label>
                  <label>
                    What the business does
                    <textarea
                      rows={4}
                      value={value.description}
                      onChange={(e) =>
                        change({ ...value, description: e.target.value })
                      }
                    />
                  </label>
                  <label>
                    Approved information
                    <textarea
                      rows={7}
                      value={value.approved_facts.join("\n")}
                      onChange={(e) =>
                        change({
                          ...value,
                          approved_facts: lines(e.target.value),
                        })
                      }
                      placeholder="One approved fact per line"
                    />
                  </label>
                  <p className="field-help">
                    Add the services, capabilities, and answers the agent may
                    use. Leave unknown prices or availability out.
                  </p>
                  <label>
                    Workspace timezone
                    <input
                      value={value.timezone}
                      onChange={(e) =>
                        change({ ...value, timezone: e.target.value })
                      }
                      placeholder="Europe/London"
                    />
                  </label>
                </div>
              ) : (
                <div className="form-section">
                  <label>
                    Review instructions
                    <textarea
                      rows={10}
                      value={value.memory_review_instructions}
                      onChange={(e) =>
                        change({
                          ...value,
                          memory_review_instructions: e.target.value,
                        })
                      }
                    />
                  </label>
                  <p className="field-help">
                    Owner corrections take precedence. Private contact notes are
                    excluded from calls and automated review. Historical calls
                    enter memory only when you choose Queue review.
                  </p>
                </div>
              )
            }
          </RevisionForm>
        )}
      </SectionTabs>
    </div>
  );
}

export function OutboundSettings({
  onSaved,
}: {
  onSaved: () => Promise<void>;
}) {
  const [section, setSection] = useState("instructions"),
    [dirty, setDirty] = useState(false);
  return (
    <div className="settings-workspace">
      <SectionTabs
        label="Outbound settings"
        value={section}
        onChange={(next) => {
          if (
            !dirty ||
            window.confirm("Discard unsaved changes in this section?")
          ) {
            setDirty(false);
            setSection(next);
          }
        }}
        items={[
          { id: "instructions", label: "Instructions" },
          { id: "callbacks", label: "Automatic Callbacks" },
        ]}
      >
        {section === "instructions" ? (
          <RevisionForm<Global["prompts"]>
            entity="outbound"
            title="Outbound instructions"
            onSaved={onSaved}
            onDirtyChange={setDirty}
          >
            {(value, change) => (
              <div className="form-section">
                <label>
                  Conversation instructions
                  <textarea
                    rows={9}
                    value={value.conversation}
                    onChange={(e) =>
                      change({ ...value, conversation: e.target.value })
                    }
                  />
                </label>
                <label>
                  Opening instructions
                  <textarea
                    rows={7}
                    value={value.opening}
                    onChange={(e) =>
                      change({ ...value, opening: e.target.value })
                    }
                  />
                </label>
              </div>
            )}
          </RevisionForm>
        ) : (
          <RevisionForm<CallbackPolicy>
            entity="callback_policy"
            title="Automatic callback policy"
            onSaved={onSaved}
            onDirtyChange={setDirty}
          >
            {(value, change) => (
              <div className="form-section">
                <label className="toggle-row">
                  <input
                    type="checkbox"
                    checked={value.enabled}
                    onChange={(e) =>
                      change({ ...value, enabled: e.target.checked })
                    }
                  />
                  Allow automatic callbacks after caller confirmation
                </label>
                <label>
                  Timezone
                  <input
                    value={value.timezone}
                    onChange={(e) =>
                      change({ ...value, timezone: e.target.value })
                    }
                  />
                </label>
                <fieldset className="day-picker">
                  <legend>Days</legend>
                  {[
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Saturday",
                    "Sunday",
                  ].map((day, index) => (
                    <label key={day}>
                      <input
                        type="checkbox"
                        checked={value.weekdays.includes(index)}
                        onChange={(e) =>
                          change({
                            ...value,
                            weekdays: e.target.checked
                              ? [...value.weekdays, index].sort()
                              : value.weekdays.filter((x) => x !== index),
                          })
                        }
                      />
                      {day}
                    </label>
                  ))}
                </fieldset>
                <div className="form-grid">
                  <label>
                    From
                    <input
                      type="time"
                      value={value.opens}
                      onChange={(e) =>
                        change({ ...value, opens: e.target.value })
                      }
                    />
                  </label>
                  <label>
                    Until
                    <input
                      type="time"
                      value={value.closes}
                      onChange={(e) =>
                        change({ ...value, closes: e.target.value })
                      }
                    />
                  </label>
                </div>
                <label>
                  Default window (minutes)
                  <input
                    type="number"
                    min={5}
                    max={60}
                    value={value.default_window_minutes}
                    onChange={(e) =>
                      change({
                        ...value,
                        default_window_minutes: Number(e.target.value),
                      })
                    }
                  />
                </label>
                <SettingsDisclosure
                  title="Additional callback rules"
                  summary="Confirmed numbers and one dialing attempt"
                >
                  <label className="toggle-row">
                    <input
                      type="checkbox"
                      checked={value.allow_alternate_number}
                      onChange={(e) =>
                        change({
                          ...value,
                          allow_alternate_number: e.target.checked,
                        })
                      }
                    />
                    Allow a different number after readback and confirmation
                  </label>
                  <label>
                    Maximum window (minutes)
                    <input
                      type="number"
                      min={5}
                      max={120}
                      value={value.max_window_minutes}
                      onChange={(e) =>
                        change({
                          ...value,
                          max_window_minutes: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                  <p>
                    One automatic attempt per request. Busy calls wait within
                    the agreed window; expired windows need review.
                  </p>
                </SettingsDisclosure>
              </div>
            )}
          </RevisionForm>
        )}
      </SectionTabs>
    </div>
  );
}

export function InboundSettingsPage({
  session,
  status,
  onSaved,
  onTest,
}: {
  session: Session;
  status: Status | null;
  onSaved: () => Promise<void>;
  onTest: (draft: Draft) => void;
}) {
  const ready = status?.inbound;
  return (
    <div className="settings-workspace">
      <HandsetCheck />
      <div className="reception-status" role="status">
        <strong>
          {ready?.answering_on ? "Answering On" : "Answering Off"}
        </strong>
        <span>{ready?.availability || "Checking readiness"}</span>
        {ready?.reason && <span className="muted">{ready.reason}</span>}
      </div>
      <RevisionForm<InboundSettings>
        entity="inbound"
        title="Inbound reception"
        onSaved={onSaved}
      >
        {(value, change) => (
          <div className="form-section">
            <label className="toggle-row">
              <input
                type="checkbox"
                checked={value.enabled}
                onChange={(e) =>
                  change({ ...value, enabled: e.target.checked })
                }
              />
              Answer incoming calls
            </label>
            {!ready?.business_configured && (
              <p className="field-help">
                Add business details in Settings before enabling reception.
              </p>
            )}
            <label>
              Personality
              <select
                value={value.profile_id}
                onChange={(e) =>
                  change({ ...value, profile_id: e.target.value })
                }
              >
                {session.setup.profiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Reception instructions
              <textarea
                rows={8}
                value={value.instructions}
                onChange={(e) =>
                  change({ ...value, instructions: e.target.value })
                }
              />
            </label>
            <label>
              Greeting and disclosure
              <textarea
                rows={5}
                value={value.greeting}
                onChange={(e) => change({ ...value, greeting: e.target.value })}
              />
            </label>
            <SettingsDisclosure
              title="Intake and limits"
              summary="Relevant questions, initial language, and duration"
            >
              <label>
                Intake questions
                <textarea
                  rows={5}
                  value={value.intake_questions.join("\n")}
                  onChange={(e) =>
                    change({
                      ...value,
                      intake_questions: lines(e.target.value),
                    })
                  }
                />
              </label>
              <label>
                Initial language
                <select
                  value={value.language}
                  onChange={(e) =>
                    change({
                      ...value,
                      language: e.target.value as InboundSettings["language"],
                    })
                  }
                >
                  {Object.entries(languages).map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <div className="form-grid">
                <label>
                  Maximum call length (seconds)
                  <input
                    type="number"
                    min={60}
                    max={7200}
                    value={value.max_call_seconds}
                    onChange={(e) =>
                      change({
                        ...value,
                        max_call_seconds: Number(e.target.value),
                      })
                    }
                  />
                </label>
                <label>
                  Silence limit (seconds)
                  <input
                    type="number"
                    min={15}
                    max={120}
                    value={value.silence_seconds}
                    onChange={(e) =>
                      change({
                        ...value,
                        silence_seconds: Number(e.target.value),
                      })
                    }
                  />
                </label>
              </div>
            </SettingsDisclosure>
            <div className="save-row">
              <button
                onClick={() =>
                  onTest({
                    profileId: value.profile_id,
                    direction: "inbound",
                    inbound: value,
                  })
                }
              >
                Try in Playground
              </button>
            </div>
          </div>
        )}
      </RevisionForm>
    </div>
  );
}
