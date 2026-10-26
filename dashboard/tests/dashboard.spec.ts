import { test, expect, type Page } from "@playwright/test";
import { installFixture } from "./fixture";

async function action(page: Page, name: string, menu = "More") {
  const scope = page.locator(
    ".configuration-editor:visible, .practice-setup:visible",
  );
  await scope.waitFor({ state: "visible" });
  await scope
    .getByRole("button", { name, exact: true, includeHidden: true })
    .waitFor({ state: "attached" });
  const button = scope.getByRole("button", { name, exact: true });
  if (!(await button.isVisible()))
    await scope
      .locator(".action-menu > summary")
      .filter({ hasText: menu })
      .click();
  await button.click();
}
async function navigation(page: Page, label: string) {
  await page.locator(".sidebar").waitFor({ state: "attached" });
  await page.getByRole("link", { name: label, exact: true }).click();
}
async function globalDefaults(page: Page) {
  await navigation(page, "Settings");
  await page
    .getByRole("tablist", { name: "Workspace settings", exact: true })
    .getByRole("tab", { name: "Voice & Models", exact: true })
    .click();
  await expect(
    page.getByRole("combobox", { name: "OpenRouter model", exact: true }),
  ).toBeVisible();
}
async function voiceTab(page: Page, scope = "Personality settings") {
  if (
    await page
      .getByRole("tablist", { name: "Voice settings", exact: true })
      .isVisible()
  )
    return;
  await page
    .getByRole("tablist", { name: scope, exact: true })
    .getByRole("tab", { name: "Voice & Model", exact: true })
    .click();
}

test("AI input inspection separates call instructions, runtime tools, and Hermes", async ({
  page,
}) => {
  await installFixture(page);
  await page.goto("/personalities");
  await action(page, "Inspect AI Inputs", "Inspect");
  const inspector = page.getByRole("region", { name: "Call AI inputs" });
  await expect(
    inspector.getByText("google/gemini-3.1-flash-lite", { exact: true }),
  ).toBeVisible();
  await expect(
    inspector.getByText("Global Instructions", { exact: false }),
  ).toBeVisible();
  await inspector.getByRole("tab", { name: "Model & Voice" }).click();
  await expect(
    inspector.getByText('"max_tokens": 512', { exact: false }),
  ).toBeVisible();
  await inspector.getByRole("tab", { name: "Runtime & Tools" }).click();
  await inspector.getByText("finish_call", { exact: true }).click();
  await expect(
    inspector.getByText("Close the current conversation.", { exact: true }),
  ).toBeVisible();
  await inspector.getByRole("tab", { name: "Latest Request" }).click();
  await expect(
    inspector.getByText(/No request has been captured/),
  ).toBeVisible();
  await inspector.getByRole("button", { name: "Close Preview" }).click();
  await navigation(page, "Integrations");
  await expect(
    page.getByRole("heading", { name: "Integrations", exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Connection" }).click();
  await expect(
    page.getByText(/The calling guide goes to your MCP client/),
  ).toBeVisible();
  await page.getByRole("tab", { name: "MCP Tools" }).click();
  await expect(
    page.getByText("prepare_phone_call", { exact: true }),
  ).toBeVisible();
});

test("practice shows captured model inputs without dialing", async ({
  page,
}) => {
  const state = await installFixture(page);
  await page.goto("/playground");
  await page.getByRole("button", { name: "Text", exact: true }).click();
  await page
    .getByRole("button", { name: "Start Text Practice", exact: true })
    .click();
  await page
    .getByLabel("Practice message", { exact: true })
    .fill("What did the model receive?");
  await page
    .getByRole("button", { name: "Send practice message", exact: true })
    .click();
  await expect(
    page.getByText("What would you like to talk about?", { exact: true }),
  ).toBeVisible();
  await action(page, "Inspect AI Inputs", "Inspect");
  const inspector = page.getByRole("region", { name: "Call AI inputs" });
  await inspector.getByRole("tab", { name: "Latest Request" }).click();
  await expect(inspector.locator("pre")).toContainText(
    "What did the model receive?",
  );
  expect(state.started).toBe(0);
  await page.getByRole("button", { name: "End Practice", exact: true }).click();
});

test("reading older calls is not reset by automatic refresh", async ({
  page,
}) => {
  const state = await installFixture(page);
  state.paginate = true;
  await page.clock.install();
  await page.goto("/calls");
  await page.getByRole("button", { name: "Load More", exact: true }).click();
  await expect(page.getByText("Bookshop", { exact: true })).toBeVisible();
  await page.clock.fastForward(16000);
  await expect(page.getByText("Bookshop", { exact: true })).toBeVisible();
  await page.locator(".compact-details > summary").click();
  await page
    .getByRole("button", { name: "Refresh calls", exact: true })
    .click();
  await expect(page.getByText("Bookshop", { exact: true })).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "Load More", exact: true }),
  ).toBeVisible();
});

test("call history, readable transcript, notes, and full Markdown download", async ({
  page,
}, info) => {
  await installFixture(page);
  await page.goto("/calls");
  await page.getByRole("button", { name: /Alex Morgan/ }).click();
  await expect(
    page.getByRole("heading", { name: "Alex Morgan" }),
  ).toBeVisible();
  await expect(
    page.getByText("Saturday afternoon works. Can we meet by the park?"),
  ).toBeVisible();
  await expect(page.getByText(/Recording unavailable/)).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "Full Markdown" }).click();
  expect((await download).suggestedFilename()).toBe("transcript.md");
  await page.getByRole("tab", { name: "Notes", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Notes", exact: true })
    .fill("Natural conversation.");
  await page.getByLabel("Quality tags").fill("Clear voice");
  await page.getByRole("button", { name: "Save Notes" }).click();
  await expect(page.getByRole("status")).toContainText("Saved");
  await page.getByRole("tab", { name: "Transcript", exact: true }).click();
  await page.screenshot({ path: info.outputPath("calls.png"), fullPage: true });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("personalities share guided and full-text values and preserve edits during practice", async ({
  page,
}) => {
  const state = await installFixture(page);
  await page.goto("/personalities");
  await page
    .getByLabel("Personality prompt")
    .fill("Be thoughtful. Ask about the next chess game.");
  await action(page, "Full Text Editor");
  const editor = page.getByLabel("Configuration", { exact: true });
  const json = JSON.parse(await editor.inputValue());
  expect(json.prompt).toContain("chess");
  json.description = "A chess partner";
  await editor.fill(JSON.stringify(json, null, 2));
  await action(page, "Guided Editor");
  await page.getByText("Personality Details", { exact: true }).click();
  await expect(page.getByLabel("Description", { exact: true })).toHaveValue(
    "A chess partner",
  );
  await page.getByRole("button", { name: "Try in Playground" }).click();
  await expect(page.getByText("Testing unsaved changes")).toBeVisible();
  await navigation(page, "Personalities");
  await expect(page.getByLabel("Personality prompt")).toHaveValue(
    "Be thoughtful. Ask about the next chess game.",
  );
  await page.getByRole("button", { name: "Save Changes", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Saved.");
  expect(state.records["profile:warm"].revision).toBe(2);
  expect(state.started).toBe(0);
  await action(page, "History");
  await page
    .locator(".history-panel summary")
    .filter({ hasText: "Version 1" })
    .click();
  await page
    .getByRole("button", { name: "Restore This Version" })
    .last()
    .click();
  await expect.poll(() => state.records["profile:warm"].revision).toBe(3);
});

test("a conflicting save keeps edits and requires an explicit choice", async ({
  page,
}) => {
  const state = await installFixture(page);
  await page.goto("/personalities");
  await page.getByLabel("Personality prompt").fill("My revised tone.");
  state.records["profile:warm"].revision = 2;
  await page.getByRole("button", { name: "Save Changes", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "A newer version is available" }),
  ).toBeVisible();
  await expect(page.getByLabel("Personality prompt")).toHaveValue(
    "My revised tone.",
  );
  await page.getByRole("button", { name: "Keep My Edits" }).click();
  await page.getByRole("button", { name: "Save Changes", exact: true }).click();
  await expect.poll(() => state.records["profile:warm"].revision).toBe(3);
});

test("new calls require review and start only on the Call action", async ({
  page,
}) => {
  const state = await installFixture(page);
  await page.goto("/calls");
  await page.getByRole("button", { name: "New Call", exact: true }).click();
  await page.getByLabel("Recipient", { exact: true }).fill("Casey");
  await page.getByLabel("Phone number").fill("+1 202 555 0111");
  await page
    .getByRole("textbox", { name: "Task", exact: true })
    .fill("Invite Casey to play chess.");
  await page.getByRole("button", { name: "Review Call", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Review Call" }),
  ).toBeVisible();
  expect(state.prepared).toBe(1);
  expect(state.started).toBe(0);
  await page.getByRole("button", { name: "Call Casey", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "End Call", exact: true }),
  ).toBeVisible();
  expect(state.started).toBe(1);
  await page.getByRole("button", { name: "End Call", exact: true }).click();
  await expect.poll(() => state.ended).toBe(1);
});

test("text practice stays separate from telephone calls and stops cleanly", async ({
  page,
}) => {
  const state = await installFixture(page);
  await page.goto("/playground");
  await page.getByRole("button", { name: "Text", exact: true }).click();
  await page.getByRole("button", { name: "Start Text Practice" }).click();
  await page.getByLabel("Practice message", { exact: true }).fill("Hello");
  await page.getByRole("button", { name: "Send practice message" }).click();
  await expect(
    page.getByText("What would you like to talk about?"),
  ).toBeVisible();
  await page.getByRole("button", { name: "End Practice", exact: true }).click();
  await expect.poll(() => state.practiceStops).toBeGreaterThan(0);
  expect(state.started).toBe(0);
});

test("integration saves calling guidance and shows the actual tool contract", async ({
  page,
}, info) => {
  const state = await installFixture(page);
  await page.goto("/integration");
  await page
    .getByLabel("Instructions", { exact: true })
    .fill("Ask only for missing facts. Return both files.");
  await page.getByRole("button", { name: "Save Changes", exact: true }).click();
  await expect.poll(() => state.records.guide.revision).toBe(2);
  await page.getByRole("tab", { name: "MCP Tools" }).click();
  await page.getByText("get_calling_setup", { exact: true }).click();
  await expect(
    page.getByText("Read the current guide and available profiles."),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("integration.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("compact layout and keyboard focus remain usable at a narrow zoom-equivalent width", async ({
  page,
}) => {
  await installFixture(page);
  await page.setViewportSize({ width: 640, height: 900 });
  await page.goto("/calls");
  await expect(
    page.getByRole("button", { name: "Expand sidebar", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.locator(":focus")).toBeVisible();
  await page.getByRole("button", { name: /Alex Morgan/ }).click();
  await expect(
    page.getByRole("heading", { name: "Alex Morgan" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.getByRole("button", { name: "Calls", exact: true }).click();
  await expect(
    page.getByPlaceholder("Search calls and transcripts"),
  ).toBeVisible();
});

test("microphone denial does not reserve practice capacity or dial", async ({
  page,
}) => {
  const state = await installFixture(page);
  await page.addInitScript(() => {
    (window as any).__micRequests = 0;
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: async () => {
          (window as any).__micRequests++;
          throw new DOMException(
            "Microphone permission denied for this test",
            "NotAllowedError",
          );
        },
      },
    });
  });
  await page.goto("/playground");
  await page
    .getByRole("button", { name: "Start Voice Practice", exact: true })
    .click();
  await expect(page.getByRole("alert")).toBeVisible();
  expect(state.practiceStarts).toBe(0);
  expect(
    await page.evaluate(() => (window as any).__micRequests),
  ).toBeGreaterThan(0);
  expect(state.started).toBe(0);
  await expect(page.getByRole("alert")).toBeVisible();
});

test("voice practice connects with local audio and releases its microphone", async ({
  page,
}) => {
  const state = await installFixture(page);
  const remoteRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().startsWith("https://"))
      remoteRequests.push(request.url());
  });
  await page.addInitScript(() => {
    const tracks: MediaStreamTrack[] = [];
    (window as any).__practiceTracks = tracks;
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        addEventListener() {},
        removeEventListener() {},
        getSupportedConstraints: () => ({}),
        enumerateDevices: async () => [
          {
            kind: "audioinput",
            deviceId: "synthetic",
            groupId: "test",
            label: "Synthetic microphone",
            toJSON() {
              return {};
            },
          },
        ],
        getUserMedia: async () => {
          const context = new AudioContext({ sampleRate: 16000 });
          const stream = context.createMediaStreamDestination().stream;
          for (const track of stream.getTracks()) {
            tracks.push(track);
            const stop = track.stop.bind(track);
            track.stop = () => {
              stop();
              void context.close();
            };
          }
          return stream;
        },
      },
    });
  });
  const { ProtobufFrameSerializer } =
    await import("@pipecat-ai/websocket-transport");
  const serializer = new ProtobufFrameSerializer();
  let connected = false;
  await page.routeWebSocket("**/admin/api/practice/*/audio", (socket) => {
    connected = true;
    state.onPracticeStop = () => socket.close({ code: 1000 });
    socket.onMessage(() => {
      state.practice.state = "connected";
      socket.send(
        Buffer.from(
          serializer.serializeMessage({
            label: "rtvi-ai",
            type: "bot-ready",
            id: "test",
            data: { version: "2.0.0" },
          }),
        ),
      );
    });
  });
  await page.goto("/playground");
  await page
    .getByRole("button", { name: "Start Voice Practice", exact: true })
    .click();
  // Cold WebKit audio-worklet setup can exceed the default five-second assertion
  // budget. Keep this below the application's existing 20-second connection limit.
  await expect.poll(() => connected, { timeout: 15000 }).toBe(true);
  await expect(
    page.getByRole("button", { name: "End Practice", exact: true }),
  ).toBeEnabled();
  expect(remoteRequests).toEqual([]);
  await page.getByRole("button", { name: "End Practice", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Start Voice Practice", exact: true }),
  ).toBeEnabled();
  expect(
    await page.evaluate(() =>
      (window as any).__practiceTracks.every(
        (t: MediaStreamTrack) => t.readyState === "ended",
      ),
    ),
  ).toBe(true);
  expect(state.started).toBe(0);
  expect(state.practiceStops).toBeGreaterThan(0);
  await expect(page.getByRole("alert")).not.toBeVisible();
});

test("provider dropdowns respect model capabilities and keep reasoning off on model changes", async ({
  page,
}) => {
  const state = await installFixture(page);
  await page.goto("/personalities");
  await globalDefaults(page);
  await voiceTab(page);
  const model = page.getByRole("combobox", {
    name: "OpenRouter model",
    exact: true,
  });
  await expect(
    model.getByRole("option", { name: "OpenAI: GPT-6 Luna", exact: true }),
  ).toHaveCount(1);
  await model.selectOption("openai/gpt-6-luna");
  await page.getByText("Response Controls", { exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "Reasoning", exact: true }),
  ).toHaveValue("none");
  await expect(
    page.getByRole("checkbox", { name: "Apply temperature", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("spinbutton", { name: "Temperature", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Save Changes", exact: true }).click();
  await expect
    .poll(() => state.records.global.value.voice.dialogue_model)
    .toBe("openai/gpt-6-luna");
  expect(state.records.global.value.voice.dialogue_temperature).toBeNull();
  await model.selectOption("google/gemini-3.8-flash");
  await expect(
    page.getByRole("combobox", { name: "Reasoning", exact: true }),
  ).toHaveValue("none");
  await expect(
    page.getByText("The selected reasoning level is not supported.", {
      exact: false,
    }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "Reasoning", exact: true })
    .selectOption("low");
  await expect(
    page.getByText("The selected reasoning level is not supported.", {
      exact: false,
    }),
  ).toHaveCount(0);
  await model.selectOption("google/gemini-3.1-flash-lite");
  await page
    .getByRole("checkbox", { name: "Apply temperature", exact: true })
    .check();
  await page
    .getByRole("spinbutton", { name: "Temperature", exact: true })
    .fill("0.2");
  await page
    .getByRole("checkbox", { name: "Apply temperature", exact: true })
    .uncheck();
  await expect(
    page.getByRole("spinbutton", { name: "Temperature", exact: true }),
  ).toBeDisabled();
});

test("named voice and speech choices can be tried directly in Playground without saving", async ({
  page,
}) => {
  test.slow(); // WebKit runs this long settings flow under CPU-limited emulation.
  const state = await installFixture(page);
  await page.goto("/playground");
  await page.getByText("Voice and model choices", { exact: true }).click();
  await page
    .getByRole("combobox", { name: "OpenRouter model", exact: true })
    .selectOption("anthropic/claude-haiku-5.5");
  await page
    .getByRole("searchbox", { name: "Find a voice", exact: true })
    .fill("bright");
  await page
    .getByRole("combobox", { name: "Voice", exact: true })
    .selectOption({ label: "Ashley · en-US" });
  await page
    .getByRole("combobox", { name: "Voice model", exact: true })
    .selectOption("inworld-tts-2-flash");
  await page.getByText("Speech Expression", { exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "Speech Expression", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("tablist", { name: "Practice settings" })
    .getByRole("tab", { name: "Call Settings", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "Soniox model", exact: true })
    .selectOption("stt-rt-v4");
  await action(page, "Inspect AI Inputs", "Inspect");
  await expect
    .poll(() => state.previewRequest?.overrides?.voice?.inworld_voice)
    .toBe("voice-custom-ashley");
  await page
    .getByRole("button", { name: "Close Preview", exact: true })
    .click();
  await page.getByRole("button", { name: "Text", exact: true }).click();
  await page
    .getByRole("button", { name: "Start Text Practice", exact: true })
    .click();
  await expect.poll(() => state.practiceStarts).toBe(1);
  expect(state.practiceRequest.overrides.voice).toMatchObject({
    dialogue_model: "anthropic/claude-haiku-5.5",
    dialogue_temperature: null,
    reasoning_effort: "none",
    inworld_voice: "voice-custom-ashley",
    inworld_model: "inworld-tts-2-flash",
    soniox_model: "stt-rt-v4",
  });
  expect(state.records.global.revision).toBe(1);
  expect(state.started).toBe(0);
  await expect(
    page.getByRole("combobox", { name: "Soniox model", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "End Practice", exact: true }).click();
  await page
    .getByRole("button", { name: "Reset Practice Choices", exact: true })
    .click();
  await voiceTab(page, "Practice settings");
  await expect(
    page.getByRole("combobox", { name: "OpenRouter model", exact: true }),
  ).toHaveValue("google/gemini-3.1-flash-lite");
  await page
    .getByRole("combobox", { name: "OpenRouter model", exact: true })
    .selectOption("openai/gpt-6-luna");
  await action(page, "Inspect AI Inputs", "Inspect");
  await expect
    .poll(() => state.previewRequest?.overrides?.voice?.dialogue_model)
    .toBe("openai/gpt-6-luna");
  await page
    .getByRole("button", { name: "Close Preview", exact: true })
    .click();
  await action(page, "Inspect Last Session", "Inspect");
  await expect(
    page.getByRole("region", { name: "Call AI inputs" }),
  ).toBeVisible();
});

test("focused settings preserve edits across sections and keyboard navigation", async ({
  page,
}) => {
  const state = await installFixture(page);
  await page.goto("/personalities");
  await page
    .getByRole("textbox", { name: "Personality prompt", exact: true })
    .fill("Keep replies brief and warm.");
  const tabs = page.getByRole("tablist", { name: "Personality settings" });
  await expect(
    page.getByRole("combobox", { name: "OpenRouter model", exact: true }),
  ).toHaveCount(0);
  await tabs.getByRole("tab", { name: "Instructions", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(
    tabs.getByRole("tab", { name: "Voice & Model", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page
    .getByRole("combobox", { name: "OpenRouter model", exact: true })
    .selectOption("openai/gpt-6-luna");
  await tabs.getByRole("tab", { name: "Call Settings", exact: true }).click();
  await page
    .getByRole("spinbutton", { name: "Default minutes", exact: true })
    .fill("45");
  await tabs.getByRole("tab", { name: "Instructions", exact: true }).click();
  await expect(
    page.getByRole("textbox", { name: "Personality prompt", exact: true }),
  ).toHaveValue("Keep replies brief and warm.");
  await page.locator(".configuration-editor .action-menu > summary").click();
  await expect(
    page.getByRole("button", { name: "Duplicate", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "Duplicate", exact: true }),
  ).not.toBeVisible();
  await expect(
    page.locator(".configuration-editor .action-menu > summary"),
  ).toBeFocused();
  let warned = false;
  page.once("dialog", async (dialog) => {
    warned = true;
    await dialog.dismiss();
  });
  const picker = page.getByRole("combobox", { name: "Editing", exact: true });
  if (await picker.isVisible())
    await picker.selectOption("profile:professional");
  else
    await page
      .locator(".profile-row")
      .filter({ hasText: "Professional" })
      .click();
  expect(warned).toBe(true);
  await expect(
    page.getByRole("textbox", { name: "Personality prompt", exact: true }),
  ).toHaveValue("Keep replies brief and warm.");
  await page.getByRole("button", { name: "Save Changes", exact: true }).click();
  await expect.poll(() => state.records["profile:warm"].revision).toBe(2);
  expect(state.records["profile:warm"].value).toMatchObject({
    prompt: "Keep replies brief and warm.",
    voice_overrides: {
      dialogue_model: "openai/gpt-6-luna",
      default_call_seconds: 2700,
    },
  });
});

test("practice keeps its end action reachable with expanded settings", async ({
  page,
}) => {
  const state = await installFixture(page);
  await page.goto("/playground");
  await page.getByText("Voice and model choices", { exact: true }).click();
  await page.getByText("Response Controls", { exact: true }).click();
  await page.getByRole("button", { name: "Text", exact: true }).click();
  await page
    .getByRole("button", { name: "Start Text Practice", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Reload Choices", exact: true })
    .scrollIntoViewIfNeeded();
  await expect(
    page.getByRole("button", { name: "End Practice", exact: true }),
  ).toBeInViewport();
  await page.getByRole("button", { name: "End Practice", exact: true }).click();
  await expect.poll(() => state.practiceStops).toBeGreaterThan(0);
  expect(state.started).toBe(0);
});

test("call details keep original settings and diagnostics discoverable", async ({
  page,
}) => {
  await installFixture(page);
  await page.goto("/calls/call_demo_1");
  await page.getByRole("tab", { name: "Details", exact: true }).click();
  await expect(
    page.getByText("google/gemini-3.1-flash-lite", { exact: true }),
  ).toBeVisible();
  await page.getByText("Diagnostics", { exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Server reply delays" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Inspect AI Inputs", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Call AI inputs" }),
  ).toBeVisible();
  await page.goto("/calls/call_demo_2");
  await page.getByRole("tab", { name: "Details", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Configuration not recorded" }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Brief", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Approved brief" }),
  ).toBeVisible();
});

test("recipient and dynamic task share the call and practice flow", async ({
  page,
}, info) => {
  const state = await installFixture(page);
  await page.goto("/playground");
  await expect(
    page.getByText("Conversation Purpose", { exact: true }),
  ).toHaveCount(0);
  await page.getByLabel("Recipient", { exact: true }).fill("Sam");
  await page
    .getByLabel("About this person", { exact: true })
    .fill("Runs a cafe; met the owner last week.");
  await page
    .getByLabel("Task", { exact: true })
    .fill("Ask about opening on Sunday.");
  await page.getByText("Task Details", { exact: true }).click();
  await page
    .getByLabel("Done when", { exact: true })
    .fill("The Sunday opening time is confirmed.");
  await page.getByRole("button", { name: "Text", exact: true }).click();
  await page.getByRole("button", { name: "Start Text Practice" }).click();
  expect(state.practiceRequest.context.target.background).toContain(
    "Runs a cafe",
  );
  expect(state.practiceRequest.context.completion_criteria).toContain("Sunday");
  expect(state.practiceRequest.context.intention).toBeUndefined();
  await page.screenshot({
    path: info.outputPath("dynamic-brief.png"),
    fullPage: true,
    animations: "disabled",
  });
  expect(state.started).toBe(0);
});

test("an uncertain start recovers the original call after one dial", async ({
  page,
}) => {
  const state = await installFixture(page);
  state.loseStartResponse = true;
  await page.goto("/calls");
  await page.getByRole("button", { name: "New Call", exact: true }).click();
  await page.getByLabel("Recipient", { exact: true }).fill("Sam");
  await page.getByLabel("Phone number", { exact: true }).fill("+12025550120");
  await page.getByLabel("Task", { exact: true }).fill("Ask about Sunday.");
  await page.getByRole("button", { name: "Review Call", exact: true }).click();
  await page.getByRole("button", { name: "Call Sam", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "End Call", exact: true }),
  ).toBeVisible();
  expect(state.prepared).toBe(1);
  expect(state.started).toBe(1);
  expect(
    await page.evaluate(() => sessionStorage.getItem("calling-pending-start")),
  ).toBeNull();
});

test("provider samples load on click, stop on selection changes, and show retry errors", async ({
  page,
}) => {
  const state = await installFixture(page);
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = async function () {
      this.dataset.playing = "true";
    };
    HTMLMediaElement.prototype.pause = function () {
      this.dataset.playing = "false";
    };
    HTMLMediaElement.prototype.load = function () {};
    Object.defineProperty(HTMLMediaElement.prototype, "src", {
      configurable: true,
      get() {
        return "";
      },
      set() {},
    });
  });
  await page.goto("/personalities");
  await voiceTab(page);
  expect(state.previewFetches).toBe(0);
  const play = page.getByRole("button", { name: "Play Sample", exact: true });
  await play.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Stop Sample" })).toBeVisible();
  expect(state.previewFetches).toBe(1);
  await page
    .getByRole("combobox", { name: "Voice model", exact: true })
    .selectOption("inworld-tts-2-flash");
  await expect(play).toBeVisible();
  state.previewUnavailable = true;
  await play.click();
  await expect(page.getByRole("alert")).toContainText("rate limited");
  await expect(play).toBeVisible();
  expect(state.started).toBe(0);
});

test("starting microphone practice stops a provider sample before asking permission", async ({
  page,
}) => {
  await installFixture(page);
  await page.addInitScript(() => {
    let playing = false;
    HTMLMediaElement.prototype.play = async function () {
      playing = true;
    };
    HTMLMediaElement.prototype.pause = function () {
      playing = false;
    };
    HTMLMediaElement.prototype.load = function () {};
    Object.defineProperty(HTMLMediaElement.prototype, "src", {
      configurable: true,
      get() {
        return "";
      },
      set() {},
    });
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: async () => {
          (window as any).sampleWasPlayingAtMicRequest = playing;
          throw new Error("Permission denied");
        },
      },
    });
  });
  await page.goto("/playground");
  await page.getByText("Voice and model choices", { exact: true }).click();
  await page.getByRole("button", { name: "Play Sample", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Stop Sample", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Start Voice Practice", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("Permission denied");
  expect(
    await page.evaluate(() => (window as any).sampleWasPlayingAtMicRequest),
  ).toBe(false);
  await expect(
    page.getByRole("button", { name: "Play Sample", exact: true }),
  ).toBeVisible();
});

test("outcomes keep evidence separate and callbacks become reviewable work", async ({
  page,
}, info) => {
  const state = await installFixture(page);
  await page.goto("/calls/call_demo_1");
  await page.getByRole("tab", { name: "Outcome", exact: true }).click();
  await page.getByRole("button", { name: "Add Outcome", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Outcome", exact: true })
    .selectOption("incomplete");
  await page
    .getByRole("textbox", { name: "What happened?", exact: true })
    .fill("We need the owner’s approved price before calling back.");
  await page.getByLabel("Confirmed facts").fill("Saturday afternoon works.");
  await page.getByText("Supporting transcript lines", { exact: true }).click();
  await page.locator(".evidence-picker input").nth(1).check();
  await page.getByLabel("Add a callback", { exact: true }).check();
  await page
    .getByLabel("Callback task", { exact: true })
    .fill("Call back with the approved price.");
  await page.getByLabel("Information needed first").fill("Approved price");
  await page.getByRole("button", { name: "Save Outcome", exact: true }).click();
  await expect(page.getByText(/Your assessment/)).toBeVisible();
  expect(
    state.assessments.call_demo_1.value.facts[0].evidence_turn_ids,
  ).toEqual(["turn_2"]);
  expect(state.followups).toHaveLength(1);
  expect(state.started).toBe(0);
  await page.getByRole("button", { name: "Manage Follow-ups" }).click();
  await page.getByRole("button", { name: "Review", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Information still needed", exact: true })
    .fill("");
  await page
    .getByLabel("Facts for the callback")
    .fill("The owner approved twenty euros.");
  await page
    .getByRole("combobox", { name: "Status", exact: true })
    .selectOption("ready");
  await page
    .getByRole("button", { name: "Save Follow-up", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Prepare Callback", exact: true })
    .click();
  await expect(page.getByLabel("Phone number", { exact: true })).toHaveValue(
    "+12025550110",
  );
  await page.getByRole("button", { name: "Review Call", exact: true }).click();
  expect(state.prepareRequest.followup_id).toBe(state.followups[0].followup_id);
  expect(state.prepareRequest.context.relevant_facts).toContain(
    "The owner approved twenty euros.",
  );
  expect(state.started).toBe(0);
  await page.getByRole("button", { name: "Close call form" }).click();
  await page.screenshot({
    path: info.outputPath("followups.png"),
    fullPage: true,
    animations: "disabled",
  });
});

test("outcome conflicts preserve the owner draft", async ({ page }) => {
  const state = await installFixture(page);
  await page.goto("/calls/call_demo_1");
  await page.getByRole("tab", { name: "Outcome", exact: true }).click();
  await page.getByRole("button", { name: "Add Outcome" }).click();
  await page
    .getByRole("textbox", { name: "What happened?", exact: true })
    .fill("My unsaved review.");
  state.assessments.call_demo_1 = {
    call_id: "call_demo_1",
    revision: 1,
    actor: "hermes",
    updated_at: new Date().toISOString(),
    value: {
      status: "achieved",
      explanation: "Another saved assessment.",
      facts: [],
      evidence_turn_ids: ["turn_2"],
    },
  };
  await page.getByRole("button", { name: "Save Outcome", exact: true }).click();
  await expect(
    page.getByRole("textbox", { name: "What happened?", exact: true }),
  ).toHaveValue("My unsaved review.");
  await page
    .getByRole("button", { name: "Load Saved Outcome", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", { name: "What happened?", exact: true }),
  ).toHaveValue("Another saved assessment.");
});

test("costs distinguish missing usage and changing rates preserves earlier estimates", async ({
  page,
}, info) => {
  const state = await installFixture(page);
  await page.goto("/calls/costs");
  await expect(page.locator(".cost-total")).toContainText("$0.0274");
  await expect(
    page
      .locator(".cost-call")
      .filter({ hasText: "Bookshop" })
      .getByText("Not recorded", { exact: true }),
  ).toBeVisible();
  await page.getByText("Pricing assumptions", { exact: true }).click();
  await page
    .getByLabel("Soniox · USD per streaming hour", { exact: true })
    .fill("0.2");
  await page.getByRole("button", { name: "Save Pricing", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Pricing saved");
  expect(state.records.pricing.value.soniox_usd_per_hour).toBe(0.2);
  await expect(page.locator(".cost-total")).toContainText("$0.0274");
  await page.screenshot({
    path: info.outputPath("costs.png"),
    fullPage: true,
    animations: "disabled",
  });
});

test("configuration check uses temporary settings, reports failures, and never dials", async ({
  page,
}, info) => {
  const state = await installFixture(page);
  state.checkFails = true;
  await page.goto("/playground");
  await page.getByText("Voice and model choices", { exact: true }).click();
  await page
    .getByRole("combobox", { name: "OpenRouter model", exact: true })
    .selectOption("openai/gpt-6-luna");
  await page
    .getByRole("button", { name: "Check Configuration", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Check failed", exact: true }),
  ).toBeVisible();
  expect(state.checkRequest.overrides.voice.dialogue_model).toBe(
    "openai/gpt-6-luna",
  );
  expect(state.started).toBe(0);
  expect(state.practiceStarts).toBe(0);
  await page.getByText("Missing facts and callbacks", { exact: true }).click();
  await expect(
    page.getByText("The response invented a price.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Voice quality and telephone delay need a listening test.", {
      exact: false,
    }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("configuration-check.png"),
    fullPage: true,
    animations: "disabled",
  });
});

test("stopping a configuration check restores the practice controls", async ({
  page,
}) => {
  const state = await installFixture(page);
  state.checkAutoFinish = false;
  await page.goto("/playground");
  await page
    .getByRole("button", { name: "Check Configuration", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Start Voice Practice", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("region", { name: "Configuration testing", exact: true })
    .getByRole("button", { name: "Stop Check", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Start Voice Practice", exact: true }),
  ).toBeEnabled();
  expect(state.check.state).toBe("cancelled");
  expect(state.started).toBe(0);
});
