import { SettingsDisclosure } from "./Controls";
import type { Brief } from "./types";

export default function BriefFields({
  brief,
  onChange,
  phone = false,
}: {
  brief: Brief;
  onChange: (value: Brief) => void;
  phone?: boolean;
}) {
  const update = <K extends keyof Brief>(key: K, value: Brief[K]) =>
    onChange({ ...brief, [key]: value });
  return (
    <>
      <div className={phone ? "two-fields" : undefined}>
        <label>
          Recipient
          <input
            required
            maxLength={160}
            value={brief.target.name}
            onChange={(e) =>
              update("target", { ...brief.target, name: e.target.value })
            }
          />
        </label>
        {phone && (
          <label>
            Phone number
            <input
              required
              type="tel"
              aria-label="Phone number"
              placeholder="+34 …"
              value={brief.target.phone}
              onChange={(e) =>
                update("target", { ...brief.target, phone: e.target.value })
              }
            />
            <small>Include the country code.</small>
          </label>
        )}
      </div>
      <label>
        About this person
        <textarea
          aria-label="About this person"
          rows={2}
          maxLength={4000}
          value={brief.target.background || ""}
          placeholder="Known role, company, relationship, or background"
          onChange={(e) =>
            update("target", {
              ...brief.target,
              background: e.target.value || undefined,
            })
          }
        />
        <small>Use known facts. Leave unknown details out.</small>
      </label>
      <label>
        Task
        <textarea
          required
          aria-label="Task"
          rows={3}
          maxLength={4000}
          value={brief.objective}
          placeholder="What should the agent do in this conversation?"
          onChange={(e) => update("objective", e.target.value)}
        />
      </label>
      <SettingsDisclosure
        title="Task Details"
        summary="Completion, facts, and limits"
      >
        <label>
          Done when
          <textarea
            aria-label="Done when"
            rows={2}
            maxLength={2000}
            value={brief.completion_criteria || ""}
            placeholder="Optional: what would show that the task is complete?"
            onChange={(e) =>
              update("completion_criteria", e.target.value || undefined)
            }
          />
          <small>
            A refusal or missing information can still end the call.
          </small>
        </label>
        {(
          ["relevant_facts", "allowed_commitments", "hard_constraints"] as const
        ).map((key) => (
          <label key={key}>
            {
              {
                relevant_facts: "Relevant facts",
                allowed_commitments: "Allowed commitments",
                hard_constraints: "Limits",
              }[key]
            }
            <textarea
              rows={3}
              value={brief[key].join("\n")}
              onChange={(e) => update(key, e.target.value.split("\n"))}
            />
            <small>One item per line.</small>
          </label>
        ))}
        <label className="check">
          <input
            type="checkbox"
            checked={!!brief.business}
            onChange={(e) =>
              update(
                "business",
                e.target.checked
                  ? {
                      name: "",
                      description: "",
                      offer: "",
                      desired_next_step: "",
                      contact_permission: "",
                    }
                  : undefined,
              )
            }
          />
          This is a commercial offer
        </label>
        {brief.business && (
          <fieldset>
            <legend>Commercial offer</legend>
            {(
              ["name", "description", "offer", "desired_next_step"] as const
            ).map((key) => (
              <label key={key}>
                {
                  {
                    name: "Business name",
                    description: "What the business does",
                    offer: "Offer",
                    desired_next_step: "Desired next step",
                  }[key]
                }
                <input
                  required
                  value={brief.business![key]}
                  onChange={(e) =>
                    update("business", {
                      ...brief.business!,
                      [key]: e.target.value,
                    })
                  }
                />
              </label>
            ))}
            <label>
              Contact permission
              <select
                required
                value={brief.business.contact_permission}
                onChange={(e) =>
                  update("business", {
                    ...brief.business!,
                    contact_permission: e.target.value,
                  })
                }
              >
                <option value="">Choose the actual permission</option>
                <option value="recipient_requested">
                  Recipient requested contact
                </option>
                <option value="prior_consent">Prior consent</option>
              </select>
            </label>
          </fieldset>
        )}
      </SettingsDisclosure>
    </>
  );
}
