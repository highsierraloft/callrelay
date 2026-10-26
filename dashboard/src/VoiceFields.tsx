import { useEffect, useId, useState } from "react";
import VoicePreview from "./VoicePreview";
import { api, message } from "./api";
import { SettingsDisclosure } from "./Controls";
import { languages, type Language, type Voice } from "./types";

interface Choice {
  id: string;
  name: string;
}
interface Model extends Choice {
  supported_parameters: string[];
  reasoning: {
    mandatory?: boolean;
    supported_efforts?: string[];
    default_effort?: string;
  };
  pricing?: { prompt?: string; completion?: string };
}
interface Catalog {
  voices: (Choice & { description: string; language: string })[];
  models: Model[];
  stt_models: (Choice & {
    aliased_model_id?: string;
    max_latency_level: number;
  })[];
  tts_models: (Choice & { supports_delivery: boolean })[];
  errors: string[];
}
const suggested = [
  "openai/gpt-6-luna",
  "anthropic/claude-haiku-5.5",
  "google/gemini-3.1-flash-lite",
  "google/gemini-3.5-flash-lite",
  "google/gemini-3.8-flash",
  "openai/gpt-4.1-mini",
  "openai/gpt-6-sol",
];
const efforts = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];
let cached: { value: Catalog; at: number } | undefined;
let pending: Promise<Catalog> | undefined;
function choices(refresh = false): Promise<Catalog> {
  if (!refresh && cached && Date.now() - cached.at < 3600000)
    return Promise.resolve(cached.value);
  return (pending ??= api<Catalog>("/catalog")
    .then((value) => {
      if (!value.errors.length) cached = { value, at: Date.now() };
      return value;
    })
    .finally(() => {
      pending = undefined;
    }));
}
function price(model?: Model) {
  const input = Number(model?.pricing?.prompt),
    output = Number(model?.pricing?.completion);
  if (!Number.isFinite(input) || !Number.isFinite(output)) return "";
  const usd = (n: number) =>
    new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 2,
    }).format(n * 1000000);
  return `${usd(input)} input / ${usd(output)} output per million tokens. Provider rates can vary.`;
}

export default function VoiceFields({
  value,
  base,
  onChange,
  overrides = false,
  practice = false,
  section = "all",
}: {
  value: Partial<Voice>;
  base: Voice;
  onChange: (value: Partial<Voice>) => void;
  overrides?: boolean;
  practice?: boolean;
  section?: "voice" | "call" | "all";
}) {
  const temperatureId = useId();
  const [catalog, setCatalog] = useState<Catalog | null>(cached?.value ?? null);
  const [loading, setLoading] = useState(!cached),
    [error, setError] = useState("");
  const [voiceSearch, setVoiceSearch] = useState("");
  useEffect(() => {
    let alive = true;
    choices()
      .then((v) => {
        if (alive) setCatalog(v);
      })
      .catch((e) => {
        if (alive) setError(message(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);
  async function loadChoices() {
    setLoading(true);
    setError("");
    try {
      setCatalog(await choices(true));
    } catch (e) {
      setError(message(e));
    } finally {
      setLoading(false);
    }
  }
  const effective = { ...base, ...value };
  const model = catalog?.models.find((m) => m.id === effective.dialogue_model);
  const supported = model?.reasoning.supported_efforts ?? [];
  const reasoningSupported = model?.supported_parameters.includes("reasoning");
  const temperatureSupported =
    !model || model.supported_parameters.includes("temperature");
  const canDisableReasoning =
    !model ||
    (!model.reasoning.mandatory &&
      (!reasoningSupported || supported.includes("none")));
  const invalidReasoning =
    !!model &&
    effective.reasoning_effort !== null &&
    (effective.reasoning_effort === "none"
      ? !canDisableReasoning
      : !supported.includes(effective.reasoning_effort));
  const voice = catalog?.voices.find((v) => v.id === effective.inworld_voice);
  const voices =
    catalog?.voices.filter((v) =>
      `${v.name} ${v.description} ${v.language}`
        .toLowerCase()
        .includes(voiceSearch.toLowerCase()),
    ) ?? [];
  const tts = catalog?.tts_models.find((m) => m.id === effective.inworld_model);
  const supportsDelivery =
    tts?.supports_delivery ?? effective.inworld_model === "inworld-tts-2";
  const stt = catalog?.stt_models.find((m) => m.id === effective.soniox_model);
  const change = <K extends keyof Voice>(key: K, next: Voice[K]) =>
    onChange({ ...value, [key]: next });
  function reset(key: keyof Voice) {
    return overrides && Object.hasOwn(value, key) ? (
      <button
        className="text-button"
        type="button"
        onClick={() => {
          const next = { ...value };
          delete next[key];
          onChange(next);
        }}
      >
        Use Global Default
      </button>
    ) : null;
  }
  const modelOption = (m: Model) => (
    <option key={m.id} value={m.id}>
      {m.name}
      {m.reasoning.mandatory ? " · reasoning required" : ""}
    </option>
  );
  return (
    <div className="voice-fields">
      {error && (
        <p role="alert" className="error">
          {error} Your current selections are retained. Try Reload Choices.
        </p>
      )}
      {catalog?.errors.map((text) => (
        <p key={text} className="muted">
          {text}
        </p>
      ))}
      {(section === "voice" || section === "all") && (
        <div className="voice-model-section">
          <h4>Conversation model</h4>
          <label>
            OpenRouter model
            <select
              aria-label="OpenRouter model"
              value={effective.dialogue_model}
              onChange={(e) => {
                const next = catalog?.models.find(
                  (m) => m.id === e.target.value,
                );
                onChange({
                  ...value,
                  dialogue_model: e.target.value,
                  dialogue_provider: null,
                  dialogue_fallback_providers: [],
                  reasoning_effort: "none",
                  ...(!next?.supported_parameters.includes("temperature")
                    ? { dialogue_temperature: null }
                    : {}),
                });
              }}
            >
              {!model && (
                <option value={effective.dialogue_model}>
                  {effective.dialogue_model} · current selection
                </option>
              )}
              <optgroup label="Models to compare for calls">
                {suggested
                  .map((id) => catalog?.models.find((m) => m.id === id))
                  .filter((m): m is Model => !!m)
                  .map(modelOption)}
              </optgroup>
              <optgroup label="Other models with tool support">
                {catalog?.models
                  .filter((m) => !suggested.includes(m.id))
                  .map(modelOption)}
              </optgroup>
            </select>
            {reset("dialogue_model")}
            <small>
              Model changes keep reasoning Off and clear provider routing.{" "}
              {price(model)}
            </small>
          </label>
          <SettingsDisclosure
            title="Response Controls"
            summary={`Reasoning ${effective.reasoning_effort === "none" ? "off" : effective.reasoning_effort || "provider default"} · Temperature ${temperatureSupported && effective.dialogue_temperature !== null ? effective.dialogue_temperature : "provider default"}`}
            forceOpen={invalidReasoning}
          >
            <div className="two-fields">
              <label>
                Reasoning
                <select
                  aria-label="Reasoning"
                  value={effective.reasoning_effort ?? ""}
                  onChange={(e) =>
                    change("reasoning_effort", e.target.value || null)
                  }
                >
                  <option value="" disabled={model && !reasoningSupported}>
                    Provider default
                    {model?.reasoning.default_effort
                      ? ` (${model.reasoning.default_effort})`
                      : ""}
                  </option>
                  {efforts
                    .filter(
                      (e) =>
                        e === "none" ||
                        supported.includes(e) ||
                        e === effective.reasoning_effort,
                    )
                    .map((e) => (
                      <option
                        key={e}
                        value={e}
                        disabled={
                          e === "none"
                            ? !canDisableReasoning
                            : !!model && !supported.includes(e)
                        }
                      >
                        {e === "none"
                          ? "Off"
                          : e === "xhigh"
                            ? "Extra high"
                            : e[0].toUpperCase() + e.slice(1)}
                      </option>
                    ))}
                </select>
                {reset("reasoning_effort")}
                <small>
                  {model?.reasoning.mandatory
                    ? "This model requires reasoning. Choose a level explicitly, or select a model with Off."
                    : reasoningSupported === false
                      ? "This model does not use reasoning."
                      : "Extra reasoning can increase the pause before replies."}
                </small>
              </label>
              <div className="temperature-field">
                <div className="temperature-heading">
                  <label htmlFor={temperatureId}>Temperature</label>
                  <label className="check">
                    <input
                      type="checkbox"
                      aria-label="Apply temperature"
                      disabled={!temperatureSupported}
                      checked={
                        temperatureSupported &&
                        effective.dialogue_temperature !== null
                      }
                      onChange={(e) =>
                        change(
                          "dialogue_temperature",
                          e.target.checked ? 0.4 : null,
                        )
                      }
                    />
                    Apply
                  </label>
                </div>
                <input
                  id={temperatureId}
                  aria-label="Temperature"
                  type="number"
                  min="0"
                  max="2"
                  step="0.1"
                  disabled={
                    !temperatureSupported ||
                    effective.dialogue_temperature === null
                  }
                  value={effective.dialogue_temperature ?? ""}
                  onChange={(e) =>
                    change(
                      "dialogue_temperature",
                      e.target.value === "" ? null : Number(e.target.value),
                    )
                  }
                />
                {reset("dialogue_temperature")}
                <small>
                  {!temperatureSupported
                    ? "Not supported by this model. Omitted from requests."
                    : "Off omits temperature and uses the provider default."}
                </small>
              </div>
            </div>
            {invalidReasoning && (
              <p className="error" role="alert">
                The selected reasoning level is not supported. Choose a
                supported level before saving or starting practice.
              </p>
            )}
            <label>
              Maximum output tokens
              <input
                aria-label="Maximum output tokens"
                type="number"
                min="64"
                max="16384"
                step="64"
                value={effective.dialogue_max_tokens ?? 512}
                onChange={(e) =>
                  change("dialogue_max_tokens", Number(e.target.value))
                }
              />
              {reset("dialogue_max_tokens")}
              <small>
                This budget includes reasoning tokens. Reasoning models may need
                a larger budget to produce a spoken reply.
              </small>
            </label>
            <h4>Provider routing</h4>
            <div className="two-fields">
              <label>
                Preferred provider
                <input
                  value={effective.dialogue_provider || ""}
                  placeholder="Automatic routing"
                  onChange={(e) =>
                    change("dialogue_provider", e.target.value || null)
                  }
                />
                {reset("dialogue_provider")}
              </label>
              <label>
                Fallback providers
                <input
                  value={effective.dialogue_fallback_providers.join(", ")}
                  onChange={(e) =>
                    change(
                      "dialogue_fallback_providers",
                      e.target.value
                        .split(",")
                        .map((x) => x.trim())
                        .filter(Boolean),
                    )
                  }
                />
                {reset("dialogue_fallback_providers")}
              </label>
            </div>
          </SettingsDisclosure>
          <h4>Speaking voice</h4>
          <label>
            Find a voice
            <input
              type="search"
              value={voiceSearch}
              placeholder="Search name, language, or style"
              onChange={(e) => setVoiceSearch(e.target.value)}
            />
          </label>
          <div className="two-fields">
            <label>
              Voice
              <select
                aria-label="Voice"
                value={effective.inworld_voice}
                onChange={(e) => change("inworld_voice", e.target.value)}
              >
                {!voices.some((v) => v.id === effective.inworld_voice) && (
                  <option value={effective.inworld_voice}>
                    {voice?.name || effective.inworld_voice} · selected
                  </option>
                )}
                {voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                    {v.language ? ` · ${v.language}` : ""}
                  </option>
                ))}
              </select>
              {reset("inworld_voice")}
              <small>
                {voice?.description ||
                  "Select a named Inworld voice, then try it in Playground."}
              </small>
              {voiceSearch && !voices.length && (
                <small role="status">
                  No voices match. Clear the search to see all voices.
                </small>
              )}
            </label>
            <label>
              Voice model
              <select
                aria-label="Voice model"
                value={effective.inworld_model}
                onChange={(e) => change("inworld_model", e.target.value)}
              >
                {(
                  catalog?.tts_models ?? [
                    { id: "inworld-tts-2", name: "Inworld TTS-2 · expressive" },
                    {
                      id: "inworld-tts-2-flash",
                      name: "Inworld TTS-2 Flash · faster",
                    },
                  ]
                ).map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
              {reset("inworld_model")}
            </label>
          </div>
          <VoicePreview
            voice={effective.inworld_voice}
            model={effective.inworld_model}
          />
          <SettingsDisclosure
            title="Speech Expression"
            summary={
              supportsDelivery
                ? effective.inworld_delivery_mode === "CREATIVE"
                  ? "Expressive"
                  : (effective.inworld_delivery_mode === "STABLE"
                      ? "Consistent"
                      : effective.inworld_delivery_mode === "BALANCED"
                        ? "Balanced"
                        : null) || "Provider default"
                : "Automatic for TTS-2 Flash"
            }
          >
            <label>
              Speech Expression
              <select
                aria-label="Speech Expression"
                disabled={!supportsDelivery}
                value={effective.inworld_delivery_mode || ""}
                onChange={(e) =>
                  change("inworld_delivery_mode", e.target.value || null)
                }
              >
                <option value="">Provider default</option>
                <option value="STABLE">Consistent</option>
                <option value="BALANCED">Balanced</option>
                <option value="CREATIVE">Expressive</option>
              </select>
              {reset("inworld_delivery_mode")}
              <small>Changes audio variation and emotional range.</small>
              {!supportsDelivery && (
                <small>
                  TTS-2 Flash ignores Speech Expression. Your TTS-2 preference
                  is retained.
                </small>
              )}
            </label>
          </SettingsDisclosure>
        </div>
      )}
      {(section === "call" || section === "all") && (
        <div className="call-settings-section">
          <fieldset className="language-options">
            <legend>Allowed languages</legend>
            {Object.entries(languages).map(([k, v]) => (
              <label className="check" key={k}>
                <input
                  type="checkbox"
                  checked={effective.conversation_languages.includes(
                    k as Language,
                  )}
                  onChange={(e) =>
                    change(
                      "conversation_languages",
                      e.target.checked
                        ? [...effective.conversation_languages, k as Language]
                        : effective.conversation_languages.filter(
                            (l) => l !== k,
                          ),
                    )
                  }
                />
                {v}
              </label>
            ))}
            {reset("conversation_languages")}
          </fieldset>
          <h4>Speech recognition</h4>
          <label>
            Soniox model
            <select
              aria-label="Soniox model"
              value={effective.soniox_model}
              onChange={(e) => change("soniox_model", e.target.value)}
            >
              {!catalog?.stt_models.some(
                (m) => m.id === effective.soniox_model,
              ) && (
                <option value={effective.soniox_model}>
                  {effective.soniox_model} · current selection
                </option>
              )}
              {catalog?.stt_models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                  {m.aliased_model_id
                    ? ` · alias of ${m.aliased_model_id}`
                    : ""}
                </option>
              ))}
            </select>
            {reset("soniox_model")}
            <small>
              Compatible real-time models only. Aliases use the same underlying
              engine.
            </small>
          </label>
          <SettingsDisclosure
            title="Turn Detection & Timing"
            summary={
              effective.soniox_turn_detection === "semantic"
                ? "Meaning and speech"
                : "Speech activity"
            }
          >
            <label>
              Turn detection
              <select
                value={effective.soniox_turn_detection}
                onChange={(e) =>
                  change(
                    "soniox_turn_detection",
                    e.target.value as Voice["soniox_turn_detection"],
                  )
                }
              >
                <option value="semantic">Meaning and speech</option>
                <option value="vad">Speech activity</option>
              </select>
              {reset("soniox_turn_detection")}
            </label>
            <div className="three-fields">
              <label>
                Endpoint delay (ms)
                <input
                  type="number"
                  min="500"
                  max="3000"
                  step="100"
                  value={effective.soniox_endpoint_delay_ms}
                  onChange={(e) =>
                    change("soniox_endpoint_delay_ms", Number(e.target.value))
                  }
                />
                {reset("soniox_endpoint_delay_ms")}
              </label>
              <label>
                Latency level
                <input
                  type="number"
                  min="0"
                  max={stt?.max_latency_level ?? 3}
                  step="1"
                  value={effective.soniox_endpoint_latency_level}
                  onChange={(e) =>
                    change(
                      "soniox_endpoint_latency_level",
                      Number(e.target.value),
                    )
                  }
                />
                {reset("soniox_endpoint_latency_level")}
              </label>
              <label>
                Sensitivity
                <input
                  type="number"
                  min="-1"
                  max="1"
                  step="0.1"
                  value={effective.soniox_endpoint_sensitivity}
                  onChange={(e) =>
                    change(
                      "soniox_endpoint_sensitivity",
                      Number(e.target.value),
                    )
                  }
                />
                {reset("soniox_endpoint_sensitivity")}
              </label>
            </div>
          </SettingsDisclosure>
          {!practice && (
            <>
              <h4>Call duration</h4>
              <div className="two-fields">
                <label>
                  Default minutes
                  <input
                    type="number"
                    min="0.5"
                    max="120"
                    step="0.5"
                    value={effective.default_call_seconds / 60}
                    onChange={(e) =>
                      change(
                        "default_call_seconds",
                        Number(e.target.value) * 60,
                      )
                    }
                  />
                  {reset("default_call_seconds")}
                </label>
                <label>
                  Maximum minutes
                  <input
                    type="number"
                    min="0.5"
                    max="120"
                    step="0.5"
                    value={effective.max_call_seconds / 60}
                    onChange={(e) =>
                      change("max_call_seconds", Number(e.target.value) * 60)
                    }
                  />
                  {reset("max_call_seconds")}
                </label>
              </div>
            </>
          )}
        </div>
      )}
      <div className="catalog-footer">
        {loading && (
          <span className="muted" role="status">
            Loading provider choices…
          </span>
        )}
        <button
          className="quiet-button"
          type="button"
          onClick={loadChoices}
          disabled={loading}
        >
          {loading ? "Loading…" : "Reload Choices"}
        </button>
      </div>
    </div>
  );
}
