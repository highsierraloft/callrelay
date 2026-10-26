import type { Page } from "@playwright/test";

export const voice = {
  conversation_languages: ["en", "es", "ru", "uk"],
  dialogue_model: "google/gemini-3.1-flash-lite",
  dialogue_provider: "google-ai-studio",
  dialogue_fallback_providers: ["google-vertex"],
  reasoning_effort: "none",
  dialogue_temperature: 0.4,
  dialogue_max_tokens: 512,
  soniox_model: "stt-rt-v5",
  soniox_turn_detection: "semantic",
  soniox_endpoint_delay_ms: 500,
  soniox_endpoint_latency_level: 2,
  soniox_endpoint_sensitivity: 0.3,
  inworld_model: "inworld-tts-2",
  inworld_voice: "Wade",
  inworld_delivery_mode: "CREATIVE",
  default_call_seconds: 1800,
  max_call_seconds: 7200,
};
export const globals = {
  prompt_contract_version: 2,
  voice,
  prompts: {
    conversation: "Speak naturally and leave room for the other person.",
    opening: "Give a short greeting and explain the purpose.",
  },
};
export const profile = (name = "Warm") => ({
  name,
  description:
    name === "Warm"
      ? "Friendly, attentive conversations"
      : "A starting personality",
  personality: name.toLowerCase(),
  prompt: "Be friendly and attentive. Ask one useful question.",
  voice_overrides: {},
  archived: false,
});
export async function installFixture(
  page: Page,
  sessionOverrides: Record<string, unknown> = {},
) {
  const records: Record<string, any> = {
    global: {
      entity: "global",
      revision: 1,
      created_at: "2026-10-24T08:00:00Z",
      value: structuredClone(globals),
    },
    guide: {
      entity: "guide",
      revision: 1,
      created_at: "2026-10-24T08:00:00Z",
      value: {
        text: "Read the setup, prepare the call, and return its recording and transcript.",
      },
    },
  };
  records.pricing = {
    entity: "pricing",
    revision: 1,
    created_at: "2026-10-25T08:00:00Z",
    value: {
      soniox_usd_per_hour: 0.12,
      inworld_tts2_usd_per_million_characters: 25,
      inworld_flash_usd_per_million_characters: 15,
      note: "On-demand assumptions. Carrier charges excluded.",
    },
  };
  for (const name of ["Warm", "Professional", "Salesperson", "Playful"])
    records["profile:" + name.toLowerCase()] = {
      entity: "profile:" + name.toLowerCase(),
      revision: 1,
      created_at: "2026-10-24T08:00:00Z",
      value: profile(name),
    };
  for (const [entity, value] of Object.entries({
    business: {
      name: "Example Business",
      description: "Reception test business",
      approved_facts: ["We discuss customer needs"],
      timezone: "Europe/London",
      memory_review_instructions:
        "Use transcript evidence and protect owner corrections.",
    },
    inbound: {
      enabled: false,
      instructions: "Understand the caller's need.",
      greeting: "Name the business and identify the AI assistant.",
      intake_questions: ["Name", "Problem and next step"],
      profile_id: "warm",
      language: "en",
      max_call_seconds: 1800,
      silence_seconds: 45,
    },
    callback_policy: {
      enabled: true,
      timezone: "Europe/London",
      weekdays: [0, 1, 2, 3, 4],
      opens: "09:00",
      closes: "18:00",
      default_window_minutes: 15,
      max_window_minutes: 60,
      allow_alternate_number: true,
      attempts: 1,
    },
  }))
    records[entity] = {
      entity,
      revision: 1,
      created_at: "2026-10-25T08:00:00Z",
      value,
    };
  const histories: Record<string, any[]> = {};
  for (const [key, value] of Object.entries(records))
    histories[key] = [structuredClone(value)];
  const state = {
    records,
    histories,
    prepared: 0,
    prepareRequest: null as any,
    assessments: {} as Record<string, any>,
    followups: [] as any[],
    check: null as any,
    checkStarts: 0,
    checkRequest: null as any,
    checkAutoFinish: true,
    checkFails: false,

    loseStartResponse: false,
    previewFetches: 0,
    previewUnavailable: false,
    started: 0,
    recordingAvailable: false,
    ended: 0,
    paginate: false,
    practiceStarts: 0,
    practiceRequest: null as any,
    previewRequest: null as any,
    practiceStops: 0,
    onPracticeStop: null as null | (() => void),
    practice: null as any,
    active: null as string | null,
    brief: null as any,
    annotation: { note: "", tags: [] as string[] },
  };
  const setup = () => ({
    guide: records.guide.value.text,
    guide_revision: records.guide.revision,
    owner: { display_name: "Owner", timezone: "Europe/London" },
    profiles: Object.entries(records)
      .filter(([k, v]) => k.startsWith("profile:") && !v.value.archived)
      .map(([k, v]) => ({
        id: k.slice(8),
        revision: v.revision,
        name: v.value.name,
        personality: v.value.personality,
        description: v.value.description,
      })),
    defaults: records.global.value.voice,
    source_line: "Selected SIM",
    reception: {
      business: records.business.value,
      inbound: records.inbound.value,
      callback_policy: records.callback_policy.value,
    },
  });
  const brief = {
    owner: setup().owner,
    target: { name: "Alex Morgan", phone: "+12025550110" },
    objective: "Arrange a time for a weekend walk.",
    language: "en",
    intention: "task",
    personality: "warm",
    profile_id: "warm",
    max_duration_seconds: 1800,
    relevant_facts: ["Available on Saturday afternoon."],
    allowed_commitments: ["Agree on a meeting time."],
    hard_constraints: ["Do not change other plans."],
    escalation: { mode: "end_call" },
  };
  const snapshot = {
    profile_id: "warm",
    profile_name: "Warm",
    profile_revision: 1,
    configuration_revision: 1,
    personality_prompt: records["profile:warm"].value.prompt,
    prompts: globals.prompts,
    voice,
    unsaved: false,
  };
  const inspect = (prompt: string, mode = "voice") => ({
    configured: {
      version: 1,
      mode,
      system_prompt: prompt,
      prompt_sections: [
        {
          id: "global",
          label: "Global Instructions",
          source: "Global Defaults",
          editable: true,
          text: globals.prompts.conversation,
        },
        {
          id: "rules",
          label: "Authority and runtime rules",
          source: "Runtime rules",
          editable: false,
          text: "Act only within the approved brief.",
        },
        {
          id: "personality",
          label: "Personality",
          source: "Selected personality",
          editable: true,
          text: prompt,
        },
      ],
      model_options: {
        model: voice.dialogue_model,
        temperature: 0.4,
        max_tokens: 512,
        reasoning: { effort: "none" },
      },
      tools: [
        {
          type: "function",
          function: {
            name: "finish_call",
            description: "Close the current conversation.",
            parameters: {},
          },
        },
      ],
      initial_request_example: {
        model: voice.dialogue_model,
        messages: [{ role: "system", content: prompt }],
      },
      runtime_messages: {
        connection_event: "Internal connection event",
        history: "Recipient transcripts and heard speech",
      },
      speech_settings: {
        enabled: mode === "voice",
        transcription_provider: "soniox",
        speech_provider: "inworld",
        voice: "Wade",
      },
      limits: {
        call_budget_seconds: 1800,
        model_timeout_seconds: mode === "voice" ? 10 : 30,
      },
      scope: "Application-owned inputs.",
    },
    last_request: state.practice?.messages.length
      ? {
          recorded_at: "2026-10-24T14:00:00Z",
          bytes: 1000,
          body: {
            model: voice.dialogue_model,
            messages: state.practice.messages.map((m: any) => ({
              role: m.speaker === "callee" ? "user" : "assistant",
              content: m.text,
            })),
          },
          omitted: false,
          scope: "Latest outgoing request body; headers are excluded.",
        }
      : null,
  });
  const calls = [
    {
      call_id: "call_demo_1",
      state: "completed",
      created_at: "2026-10-24T08:20:00Z",
      answered_at: "2026-10-24T08:20:12Z",
      ended_at: "2026-10-24T08:22:12Z",
      duration_seconds: 120,
      termination_reason: "assistant_finished",
      target: brief.target,
      objective: brief.objective,
      language: "en",
      profile_id: "warm",
      personality: "warm",
    },
    {
      call_id: "call_demo_2",
      state: "completed",
      created_at: "2026-10-23T13:00:00Z",
      answered_at: "2026-10-23T13:00:12Z",
      ended_at: "2026-10-23T13:01:00Z",
      duration_seconds: 48,
      termination_reason: "callee_hangup",
      target: { name: "Bookshop", phone: "+12025550151" },
      objective: "Ask about tomorrow’s opening hours.",
      language: "es",
      profile_id: "professional",
      personality: "professional",
    },
  ];
  await page.route("**/admin/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname.replace("/admin/api", ""),
      method = request.method(),
      body = request.postDataJSON?.();
    const respond = (json: any, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(json),
      });
    if (path === "/session")
      return respond({
        csrf: "local-test-csrf",
        owner_login: "owner@example.test",
        timezone: "Europe/London",
        hermes_url: "https://example.ts.net:8443",
        setup: setup(),
        ...sessionOverrides,
      });
    if (path === "/status")
      return respond({
        ready: true,
        sip_registered: true,
        source_line: "Selected SIM",
        phone: { phase: "gateway_ready", call_ready: true },
        active_call: state.active,
        practice_active:
          !!state.practice &&
          !["stopped", "failed"].includes(state.practice.state),
        check_active: state.check?.state === "running",
        check_id: state.check?.state === "running" ? state.check.id : null,
        last_hermes_request: null,
        inbound: {
          answering_on: records.inbound.value.enabled,
          availability: "Ready",
          reason: null,
          business_configured: true,
        },
      });
    if (path === "/contacts")
      return respond({ contacts: [], next_offset: null });
    if (path === "/callbacks")
      return respond({ callbacks: [], next_offset: null });
    if (path === "/catalog")
      return respond({
        voices: [
          {
            id: "Wade",
            name: "Wade",
            description: "Casual and relaxed",
            language: "en-US",
          },
          {
            id: "voice-custom-ashley",
            name: "Ashley",
            description: "Warm and bright",
            language: "en-US",
          },
        ],
        models: [
          {
            id: "google/gemini-3.1-flash-lite",
            name: "Google: Gemini 3.1 Flash Lite",
            supported_parameters: [
              "tools",
              "tool_choice",
              "temperature",
              "reasoning",
            ],
            reasoning: {
              mandatory: false,
              supported_efforts: ["none", "minimal", "low", "medium", "high"],
            },
          },
          {
            id: "openai/gpt-6-luna",
            name: "OpenAI: GPT-6 Luna",
            supported_parameters: ["tools", "tool_choice", "reasoning"],
            reasoning: {
              mandatory: false,
              supported_efforts: [
                "none",
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
              ],
            },
          },
          {
            id: "anthropic/claude-haiku-5.5",
            name: "Anthropic: Claude Haiku 5.5",
            supported_parameters: ["tools", "tool_choice", "reasoning"],
            reasoning: {
              mandatory: false,
              supported_efforts: ["none", "low", "medium", "high"],
            },
          },
          {
            id: "google/gemini-3.8-flash",
            name: "Google: Gemini 3.8 Flash",
            supported_parameters: [
              "tools",
              "tool_choice",
              "temperature",
              "reasoning",
            ],
            reasoning: {
              mandatory: true,
              supported_efforts: ["low", "medium", "high"],
            },
          },
        ],
        stt_models: [
          {
            id: "stt-rt-v5",
            name: "Speech-to-Text Real-time v5",
            max_latency_level: 3,
          },
          {
            id: "stt-rt-v4",
            name: "Real-time v4",
            aliased_model_id: "stt-rt-v5",
            max_latency_level: 3,
          },
        ],
        tts_models: [
          {
            id: "inworld-tts-2",
            name: "Inworld TTS-2 · expressive",
            supports_delivery: true,
          },
          {
            id: "inworld-tts-2-flash",
            name: "Inworld TTS-2 Flash · faster",
            supports_delivery: false,
          },
        ],
        errors: [],
      });
    if (path === "/profiles")
      return respond(
        Object.entries(records)
          .filter(([k]) => k.startsWith("profile:"))
          .map(([, v]) => v),
      );
    if (path.startsWith("/configuration/")) {
      const parts = path.split("/");
      const requestedEntity = decodeURIComponent(parts[2]);
      const scope =
        requestedEntity === "outbound"
          ? "prompts"
          : requestedEntity === "voice_defaults"
            ? "voice"
            : null;
      const entity = scope ? "global" : requestedEntity;
      const project = (record: any) =>
        scope
          ? { ...record, entity: requestedEntity, value: record.value[scope] }
          : record;
      if (parts[3] === "history")
        return respond((histories[entity] || []).map(project));
      if (method === "GET")
        return records[entity]
          ? respond(project(records[entity]))
          : respond({ detail: "Not found" }, 404);
      if (method === "PUT") {
        if ((records[entity]?.revision || 0) !== body.expected_revision)
          return respond(
            {
              detail:
                "This configuration changed in another window. Reload it before saving.",
            },
            409,
          );
        records[entity] = {
          entity,
          revision: body.expected_revision + 1,
          value: scope
            ? { ...records.global.value, [scope]: body.value }
            : body.value,
          actor: "owner@example.test",
          created_at: "2026-10-24T10:00:00Z",
        };
        (histories[entity] ??= []).unshift(structuredClone(records[entity]));
        return respond(project(records[entity]));
      }
    }
    if (path === "/checks" && method === "GET")
      return respond({
        checks: state.check ? [state.check] : [],
        active_id: state.check?.state === "running" ? state.check.id : null,
      });
    if (path === "/checks" && method === "POST") {
      state.checkStarts++;
      state.checkRequest = body;
      state.check = {
        id: "check_fixture",
        state: "running",
        configuration: {
          ...snapshot,
          voice: { ...voice, ...body.overrides?.voice },
          unsaved: !!body.overrides,
          check_fingerprint: "captured-settings",
        },
        created_at: "2026-10-25T08:00:00Z",
        updated_at: "2026-10-25T08:00:00Z",
        scenario_names: [
          "corrected_details",
          "missing_facts",
          "unauthorized_commitment",
          "refusal",
          "language_switching",
          "premature_ending",
        ],
        scenario_count: 6,
        judge_model: "openai/gpt-6-luna",
        scope: "Synthetic conversations; no calls.",
        error: null,
        results: [],
      };
      return respond(state.check);
    }
    if (path.startsWith("/checks/")) {
      if (method === "DELETE") {
        state.check.state = "cancelled";
        return route.fulfill({ status: 204 });
      }
      if (state.check?.state === "running" && state.checkAutoFinish) {
        state.check.state = state.checkFails ? "failed" : "passed";
        state.check.results = state.check.scenario_names.map(
          (name: string, i: number) => ({
            name,
            status: state.checkFails && i === 1 ? "failed" : "passed",
            reason:
              state.checkFails && i === 1
                ? "The response invented a price."
                : "Expected behavior observed.",
            responses: [
              {
                type: "llm_response",
                text: "I will check the missing price and call back.",
              },
            ],
            objective: "Answer from the approved facts.",
            messages: ["How much does it cost?"],
            duration_ms: 2100,
          }),
        );
      }
      return respond(state.check);
    }
    if (path === "/followups")
      return respond({
        followups: state.followups.filter(
          (f) =>
            url.searchParams.get("status") === "all" ||
            !["completed", "cancelled"].includes(f.status),
        ),
        next_offset: null,
        automatic_dialing: false,
      });
    if (path.startsWith("/followups/")) {
      const id = path.split("/")[2];
      const item = state.followups.find((f) => f.followup_id === id);
      if (path.endsWith("/brief"))
        return respond({
          followup_id: id,
          context: {
            ...brief,
            objective: item.value.objective,
            relevant_facts: [
              ...brief.relevant_facts,
              ...(item.value.new_facts || []),
            ],
          },
          needed_information: item.value.needed_information,
          dialed: false,
        });
      if (method === "PUT") {
        if (body.expected_revision !== item.revision)
          return respond(
            { detail: "This follow-up changed in another window." },
            409,
          );
        item.revision++;
        item.status = body.status;
        item.value = { ...item.value, ...body };
        item.updated_at = new Date().toISOString();
        return respond(item);
      }
    }
    if (path === "/costs")
      return respond({
        month: url.searchParams.get("month"),
        timezone: "Europe/London",
        currency: "USD",
        scope:
          "Telephone calls only. Provider estimates exclude carrier charges and taxes.",
        call_count: 2,
        coverage: { complete: 1, partial: 0, unavailable: 1 },
        known_subtotal_usd: 0.0274,
        providers: [
          { provider: "OpenRouter", usd: 0.0004 },
          { provider: "Soniox", usd: 0.002 },
          { provider: "Inworld", usd: 0.025 },
        ],
        next_offset: null,
        calls: [
          {
            call_id: "call_demo_1",
            created_at: "2026-10-24T08:20:00Z",
            target: "Alex Morgan",
            state: "completed",
            duration_seconds: 60,
            cost: {
              coverage: "complete",
              currency: "USD",
              total_usd: 0.0274,
              known_subtotal_usd: 0.0274,
              providers: [],
              note: "Estimate",
            },
          },
          {
            call_id: "call_demo_2",
            created_at: "2026-10-23T13:00:00Z",
            target: "Bookshop",
            state: "completed",
            duration_seconds: 48,
            cost: {
              coverage: "unavailable",
              currency: "USD",
              total_usd: null,
              known_subtotal_usd: null,
              providers: [],
              note: "Not recorded",
            },
          },
        ],
      });
    if (path === "/prompt-preview") {
      state.previewRequest = body;
      return respond({
        ...inspect(
          body.overrides?.profile?.prompt ||
            records["profile:warm"].value.prompt,
          body.mode,
        ),
        prompt:
          "Your identity is an AI calling assistant.\n" +
          (body.overrides?.profile?.prompt ||
            records["profile:warm"].value.prompt) +
          "\nApproved brief: " +
          body.context.objective,
        configuration: snapshot,
      });
    }
    if (path === "/integration")
      return respond({
        setup: setup(),
        tools: [
          {
            name: "get_calling_setup",
            description: "Read the current guide and available profiles.",
            parameters: { type: "object" },
          },
          {
            name: "prepare_phone_call",
            description: "Prepare a call without dialing.",
            parameters: { type: "object" },
          },
          {
            name: "start_phone_call",
            description: "Start an approved call.",
            parameters: { type: "object" },
          },
        ],
      });
    if (path === "/calls" && method === "GET") {
      const q = (url.searchParams.get("q") || "").toLowerCase(),
        filter = url.searchParams.get("state"),
        lang = url.searchParams.get("language");
      return respond({
        calls: calls
          .filter(
            (c) =>
              (!q || JSON.stringify(c).toLowerCase().includes(q)) &&
              (!filter || filter === c.state) &&
              (!lang || lang === c.language),
          )
          .slice(
            state.paginate && url.searchParams.has("cursor") ? 1 : 0,
            state.paginate && !url.searchParams.has("cursor") ? 1 : undefined,
          ),
        next_cursor:
          state.paginate && !url.searchParams.has("cursor")
            ? "next-page"
            : null,
      });
    }
    if (path === "/calls/prepare") {
      state.prepared++;
      state.prepareRequest = body;
      state.brief = body.context;
      return respond({
        plan_id: "plan_local_test",
        confirmation_summary:
          "Call " + body.context.target.name + " to: " + body.context.objective,
        missing_fields: [],
      });
    }
    if (path === "/voice-preview") {
      state.previewFetches++;
      return state.previewUnavailable
        ? route.fulfill({
            status: 429,
            json: {
              detail: "Provider samples are rate limited. Try again shortly.",
            },
          })
        : route.fulfill({
            contentType: "audio/mpeg",
            body: Buffer.from("ID3sample"),
          });
    }
    if (path.startsWith("/plans/"))
      return respond({
        plan_id: "plan_local_test",
        call_id: state.active,
        state: state.active ? "active" : "prepared",
      });
    if (path === "/calls/start") {
      state.started++;
      state.active = "call_started";
      if (state.loseStartResponse) return route.abort("failed");
      return respond({ call_id: state.active, state: "active" });
    }
    if (path.startsWith("/calls/")) {
      const parts = path.split("/"),
        id = parts[2],
        call = calls.find((c) => c.call_id === id) || {
          ...calls[0],
          call_id: id,
          state: state.active ? "active" : "completed",
        };
      if (parts[3] === "outcome") {
        if (method === "PUT") {
          const current = state.assessments[id];
          if (body.expected_revision !== (current?.revision || 0))
            return respond(
              { detail: "The outcome changed in another window." },
              409,
            );
          state.assessments[id] = {
            call_id: id,
            revision: (current?.revision || 0) + 1,
            actor: "owner",
            updated_at: new Date().toISOString(),
            value: {
              status: body.status,
              explanation: body.explanation,
              evidence_turn_ids: body.evidence_turn_ids,
              facts: body.facts,
            },
          };
          for (const cb of body.callbacks || []) {
            if (
              !state.followups.some((f) => f.value.source_key === cb.source_key)
            )
              state.followups.push({
                followup_id: "followup_" + cb.source_key,
                source_call_id: id,
                revision: 1,
                status: cb.needed_information.length
                  ? "needs_information"
                  : "ready",
                value: {
                  ...cb,
                  new_facts: [],
                  note: "",
                  completion_call_id: null,
                },
                actor: "owner",
                created_at: new Date().toISOString(),
                updated_at: new Date().toISOString(),
                target: brief.target,
                recipient_opted_out: false,
                attempts: [],
              });
          }
          return respond({
            call_id: id,
            revision: state.assessments[id].revision,
            followup_ids: state.followups.map((f) => f.followup_id),
          });
        }
        return respond({
          assessment: state.assessments[id] || null,
          followups: state.followups.filter((f) => f.source_call_id === id),
        });
      }
      if (parts[3] === "ai-inputs")
        return respond(
          id === "call_demo_2"
            ? {
                configured: null,
                last_request: null,
                saved_system_prompt: null,
              }
            : inspect("Recorded prompt for this call."),
        );
      if (parts[3] === "end") {
        state.ended++;
        state.active = null;
        return respond({ ended: true });
      }
      if (parts[3] === "annotation") {
        state.annotation = body;
        return route.fulfill({ status: 204 });
      }
      if (parts[3] === "files") {
        return route.fulfill({
          status: 200,
          contentType: "application/octet-stream",
          headers: {
            "content-disposition": 'attachment; filename="transcript.md"',
          },
          body: "# Call transcript\n\nThe full conversation.",
        });
      }
      return respond({
        call_id: id,
        state: call.state,
        brief: id === "call_started" ? state.brief : brief,
        configuration: id === "call_demo_2" ? null : snapshot,
        prompt: "Saved prompt for this call.",
        annotation: state.annotation,
        assessment: state.assessments[id] || null,
        followups: state.followups.filter((f) => f.source_call_id === id),
        timing: call,
        latency: [
          {
            seconds: 1.14,
            measured_from: "server",
            parts: [
              { stage: "transcription", seconds: 0.4 },
              { stage: "model", seconds: 0.32 },
              { stage: "speech", seconds: 0.42 },
            ],
          },
        ],
        artifacts: {
          audio_status: state.recordingAvailable ? "available" : "unavailable",
          files: state.recordingAvailable
            ? [{ name: "recording.mp3" }, { name: "transcript.md" }]
            : [{ name: "transcript.md" }],
        },
        next_transcript_cursor: null,
        transcript: [
          {
            sequence_number: 1,
            turn_id: "turn_1",
            speaker: "assistant",
            text: "Hi Alex, I’m calling for the owner about the weekend walk.",
            created_at: "2026-10-24T08:20:16Z",
          },
          {
            sequence_number: 2,
            turn_id: "turn_2",
            speaker: "callee",
            text: "Saturday afternoon works. Can we meet by the park?",
            created_at: "2026-10-24T08:20:20Z",
          },
          {
            sequence_number: 3,
            turn_id: "turn_3",
            speaker: "assistant",
            text: "That works. What time would suit you?",
            created_at: "2026-10-24T08:20:24Z",
          },
        ],
      });
    }
    if (path === "/practice" && method === "POST") {
      state.practiceStarts++;
      state.practiceRequest = body;
      state.practice = {
        id: "practice_local",
        mode: body.mode,
        state: "waiting",
        messages: [],
        metrics: [],
        error: null,
        configuration: { ...snapshot, unsaved: !!body.overrides },
      };
      return respond(state.practice);
    }
    if (path === "/practice/practice_local/ai-inputs")
      return respond(inspect("Saved practice prompt.", state.practice.mode));
    if (path === "/practice/practice_local/text") {
      state.practice.messages.push(
        { speaker: "callee", text: body.text },
        { speaker: "assistant", text: "What would you like to talk about?" },
      );
      state.practice.state = "ready";
      state.practice.metrics.push({ seconds: 0.96, measured_from: "text" });
      return respond(state.practice);
    }
    if (path === "/practice/practice_local" && method === "DELETE") {
      state.practiceStops++;
      state.practice.state = "stopped";
      state.onPracticeStop?.();
      return route.fulfill({ status: 204 });
    }
    if (path === "/practice/practice_local") return respond(state.practice);
    return respond({ detail: "Fixture route not found: " + path }, 404);
  });
  return state;
}
