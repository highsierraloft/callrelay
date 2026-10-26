import { test, expect, type Page, type TestInfo } from "@playwright/test";
import { installReceptionFixture } from "./reception-fixture";

const pageErrors = new WeakMap<Page, string[]>();
async function setup(page: Page, info: TestInfo) {
  const errors: string[] = [];
  pageErrors.set(page, errors);
  page.on("pageerror", (error) => errors.push(error.message));
  const fixture = await installReceptionFixture(page);
  await page.setViewportSize(
    info.project.name === "phone"
      ? { width: 390, height: 844 }
      : { width: 1728, height: 1084 },
  );
  return fixture;
}
async function nav(page: Page, name: string) {
  await page
    .locator(".sidebar")
    .getByRole("link", { name, exact: true })
    .click();
}
async function capture(page: Page, info: TestInfo, name: string) {
  await expect(page.locator(".workspace > .app-toolbar h1")).toBeVisible();
  await expect(
    page.getByText("Loading settings…", { exact: true }),
  ).toHaveCount(0);
  expect(pageErrors.get(page), name).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    name,
  ).toBeTruthy();
  await page.screenshot({
    path: info.outputPath(name + ".png"),
    animations: "disabled",
  });
}

test("contact toolbar and every contact form keep aligned controls and insets", async ({
  page,
}, info) => {
  test.setTimeout(60000);
  const { extra } = await setup(page, info);
  await page.goto("/contacts");
  await expect(page.locator(".contact-rows a")).toHaveCount(1);
  const search = await page
    .getByRole("textbox", { name: "Search contacts" })
    .boundingBox();
  const add = await page
    .getByRole("button", { name: "New contact", exact: true })
    .boundingBox();
  expect(Math.abs(search!.y - add!.y)).toBeLessThanOrEqual(1);
  expect(Math.abs(search!.height - add!.height)).toBeLessThanOrEqual(1);
  await capture(page, info, "contacts-empty");
  await page.getByRole("button", { name: "New contact", exact: true }).click();
  const form = page.locator(".contact-form");
  const assertInset = async () => {
    const box = await form.boundingBox();
    const first = await form.locator("label").first().boundingBox();
    const title = await page.locator(".contact-heading h2").boundingBox();
    expect(first!.x - box!.x).toBeGreaterThanOrEqual(16);
    expect(first!.y - box!.y).toBeGreaterThanOrEqual(16);
    expect(Math.abs(title!.x - first!.x)).toBeLessThanOrEqual(1);
  };
  await expect(
    page.getByRole("textbox", { name: "Name", exact: true }),
  ).toBeVisible();
  await assertInset();
  await capture(page, info, "contact-new");
  await nav(page, "Contacts");
  await page.locator('.contact-rows a[href="/contacts/contact_maria"]').click();
  await page.getByRole("tab", { name: "Memory", exact: true }).click();
  await expect(page.locator(".memory-fact")).toHaveCount(1);
  await assertInset();
  await capture(page, info, "contact-memory");
  await page.getByRole("tab", { name: "Details", exact: true }).click();
  await assertInset();
  await page.locator(".contact-form .settings-disclosure > summary").click();
  await capture(page, info, "contact-details");
  expect(extra.contactsWritten).toBe(0);
  expect(extra.memoryWrites).toBe(0);
});

test("costs use one aligned column and avoid redundant scope and missing-usage labels", async ({
  page,
}, info) => {
  await setup(page, info);
  let missing = true;
  await page.route("**/admin/api/costs?**", async (route) => {
    if (!missing) return route.fallback();
    await route.fulfill({
      json: {
        month: "2026-10",
        timezone: "Europe/London",
        currency: "USD",
        scope:
          "Telephone calls only; excludes practice, configuration checks, carrier charges, taxes, and plan fees.",
        call_count: 1,
        coverage: { complete: 0, partial: 0, unavailable: 1 },
        known_subtotal_usd: 0,
        providers: [],
        next_offset: null,
        calls: [
          {
            call_id: "call_demo_1",
            target: "Maria",
            created_at: "2026-10-25T10:00:00Z",
            state: "completed",
            duration_seconds: 60,
            cost: {
              coverage: "unavailable",
              total_usd: null,
              known_subtotal_usd: null,
              providers: [],
              note: "Not recorded",
            },
          },
        ],
      },
    });
  });
  await page.goto("/costs");
  await expect(page.locator(".cost-calls .cost-call")).toHaveCount(1);
  await expect(
    page.getByText(
      /Telephone calls only|Calls this month|Missing usage is not zero/,
    ),
  ).toHaveCount(0);
  await expect(
    page.locator(".cost-call").getByText("Not recorded", { exact: true }),
  ).toHaveCount(1);
  const column = await page.locator(".workflow-content").boundingBox();
  const pageBox = await page.locator(".workflow-page").boundingBox();
  const overview = await page.locator(".cost-summary").boundingBox();
  const rows = await page.locator(".cost-calls").boundingBox();
  expect(column!.x - pageBox!.x).toBeGreaterThanOrEqual(16);
  expect(column!.x - pageBox!.x).toBeLessThanOrEqual(28);
  expect(Math.abs(overview!.x - rows!.x)).toBeLessThanOrEqual(1);
  await capture(page, info, "costs-missing");
  missing = false;
  await page.getByLabel("Month", { exact: true }).fill("2026-11");
  await expect(page.locator(".provider-totals")).toBeVisible();
  await capture(page, info, "costs-recorded");
  await page.locator(".settings-disclosure > summary").click();
  await page
    .getByRole("button", { name: "Save Pricing", exact: true })
    .scrollIntoViewIfNeeded();
  await capture(page, info, "costs-pricing");
});

test("callback empty views and editors share a compact aligned toolbar", async ({
  page,
}, info) => {
  test.setTimeout(60000);
  await setup(page, info);
  await page.route("**/admin/api/followups?**", (route) =>
    route.fulfill({ json: { followups: [], next_offset: null } }),
  );
  await page.goto("/callbacks");
  await expect(page.getByText("No callbacks", { exact: true })).toBeVisible();
  const toolbar = page.locator(".workflow-toolbar");
  const box = await toolbar.boundingBox();
  const workspace = await page.locator(".callbacks-workspace").boundingBox();
  const panel = await page
    .locator(".callbacks-workspace > .settings-panel")
    .boundingBox();
  expect(box!.x - workspace!.x).toBeGreaterThanOrEqual(16);
  expect(box!.x - workspace!.x).toBeLessThanOrEqual(28);
  expect(box!.y - panel!.y).toBeGreaterThanOrEqual(16);
  expect(box!.y - panel!.y).toBeLessThanOrEqual(28);
  await expect(
    page.getByRole("heading", { name: "Scheduled callbacks", exact: true }),
  ).toHaveCount(0);
  await capture(page, info, "callbacks-empty");
  await page
    .getByRole("button", { name: "Schedule Callback", exact: true })
    .click();
  await expect(page.locator(".schedule-editor")).toBeVisible();
  await capture(page, info, "callback-editor");
  await page.getByRole("tab", { name: "Follow-ups", exact: true }).click();
  await expect(page.getByText("No follow-ups", { exact: true })).toBeVisible();
  expect(
    await page
      .locator(".followup-list")
      .evaluate((el) => getComputedStyle(el).borderTopWidth),
  ).toBe("0px");
  await expect(
    page.getByRole("heading", { name: "Follow-ups", exact: true }),
  ).toHaveCount(0);
  await capture(page, info, "followups-empty");
});

test("agent and workspace settings remain readable in every editor section", async ({
  page,
}, info) => {
  test.setTimeout(120000);
  const { state } = await setup(page, info);
  await page.goto("/settings");
  await expect(
    page.getByRole("textbox", { name: "Business name", exact: true }),
  ).toBeVisible();
  await capture(page, info, "settings-business");
  await page
    .getByRole("tablist", { name: "Workspace settings" })
    .getByRole("tab", { name: "Memory", exact: true })
    .click();
  await capture(page, info, "settings-memory");
  await page
    .getByRole("tablist", { name: "Workspace settings" })
    .getByRole("tab", { name: "Voice & Models", exact: true })
    .click();
  await expect(
    page.getByRole("combobox", { name: "OpenRouter model", exact: true }),
  ).toBeVisible();
  await capture(page, info, "settings-voice");
  const responseControls = page
    .locator(".settings-disclosure > summary")
    .filter({ hasText: "Response Controls" });
  await responseControls.click();
  await capture(page, info, "settings-response-controls");
  await responseControls.click();
  await page
    .getByRole("tablist", { name: "Voice settings" })
    .getByRole("tab", { name: "Languages & Timing", exact: true })
    .click();
  await capture(page, info, "settings-languages");
  const timing = page
    .locator(".settings-disclosure > summary")
    .filter({ hasText: "Turn Detection & Timing" });
  await timing.click();
  await capture(page, info, "settings-turn-timing");
  await nav(page, "Outbound");
  await expect(
    page.getByRole("textbox", {
      name: "Conversation instructions",
      exact: true,
    }),
  ).toBeVisible();
  await capture(page, info, "outbound-instructions");
  await page
    .getByRole("tab", { name: "Automatic Callbacks", exact: true })
    .click();
  await expect(
    page.getByRole("checkbox", { name: "Monday", exact: true }),
  ).toBeVisible();
  const weekday = await page
    .locator(".day-picker label")
    .first()
    .evaluate((label) => {
      const input = label.querySelector("input")!.getBoundingClientRect();
      const text = [...label.childNodes].find(
        (node) => node.nodeType === Node.TEXT_NODE && node.textContent?.trim(),
      )!;
      const range = document.createRange();
      range.selectNodeContents(text);
      const caption = range.getBoundingClientRect();
      return {
        gap: caption.left - input.right,
        centers: Math.abs(
          caption.y + caption.height / 2 - input.y - input.height / 2,
        ),
      };
    });
  expect(weekday.gap).toBeGreaterThanOrEqual(6);
  expect(weekday.centers).toBeLessThanOrEqual(2);
  await capture(page, info, "outbound-callback-policy");
  await page.getByRole("tab", { name: "Instructions", exact: true }).click();
  await expect(
    page.getByRole("textbox", {
      name: "Conversation instructions",
      exact: true,
    }),
  ).toBeVisible();
  await nav(page, "Inbound");
  await expect(
    page.getByRole("textbox", { name: "Reception instructions", exact: true }),
  ).toBeVisible();
  await capture(page, info, "inbound-instructions");
  await page
    .locator(".settings-disclosure > summary")
    .filter({ hasText: "Intake and limits" })
    .click();
  await page
    .getByRole("button", { name: "Try in Playground", exact: true })
    .scrollIntoViewIfNeeded();
  await capture(page, info, "inbound-limits");
  await nav(page, "Personalities");
  await expect(
    page.getByRole("textbox", { name: "Personality prompt", exact: true }),
  ).toBeVisible();
  await capture(page, info, "personality-instructions");
  await page
    .locator(".settings-disclosure > summary")
    .filter({ hasText: "Personality Details" })
    .click();
  await capture(page, info, "personality-details");
  await page.getByRole("tab", { name: "Voice & Model", exact: true }).click();
  await capture(page, info, "personality-voice");
  await page.getByRole("tab", { name: "Call Settings", exact: true }).click();
  await capture(page, info, "personality-call-settings");
  await nav(page, "Playground");
  await expect(
    page.getByRole("textbox", { name: "Task", exact: true }),
  ).toBeVisible();
  await capture(page, info, "playground");
  await page.getByRole("button", { name: "New Call", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await capture(page, info, "new-call");
  await page
    .getByRole("button", { name: "Review Call", exact: true })
    .scrollIntoViewIfNeeded();
  await capture(page, info, "new-call-bottom");
  await page
    .getByRole("button", { name: "Close call form", exact: true })
    .click();
  await nav(page, "Integrations");
  await capture(page, info, "hermes-guide");
  await page.getByRole("tab", { name: "MCP Tools", exact: true }).click();
  await capture(page, info, "hermes-tools");
  await page.getByRole("tab", { name: "Connection", exact: true }).click();
  await capture(page, info, "hermes-connection");
  expect(state.started).toBe(0);
});

test("call workspace keeps readable spacing across every detail tab", async ({
  page,
}, info) => {
  test.setTimeout(60000);
  await setup(page, info);
  await page.goto("/calls/call_demo_1");
  await expect(page.locator(".detail-pane")).toBeVisible();
  for (const name of ["Transcript", "Outcome", "Brief", "Details", "Notes"]) {
    await page.getByRole("tab", { name, exact: true }).click();
    await capture(page, info, "call-" + name.toLowerCase());
  }
});

test("phone editors stack headings and keep switches and picker buttons aligned", async ({
  page,
}, info) => {
  test.setTimeout(60000);
  await setup(page, info);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/settings");
  await page
    .getByRole("tablist", { name: "Workspace settings" })
    .getByRole("tab", { name: "Voice & Models", exact: true })
    .click();
  await expect(
    page.getByRole("combobox", { name: "OpenRouter model", exact: true }),
  ).toBeVisible();
  const heading = await page
    .locator(".revision-form .editor-title")
    .boundingBox();
  const save = await page.locator(".revision-form .editor-save").boundingBox();
  expect(save!.y - heading!.y - heading!.height).toBeGreaterThanOrEqual(8);
  await capture(page, info, "phone-editor-heading");
  await page
    .locator(".settings-disclosure > summary")
    .filter({ hasText: "Response Controls" })
    .click();
  await page
    .getByRole("spinbutton", { name: "Temperature", exact: true })
    .scrollIntoViewIfNeeded();
  const toggle = await page
    .getByRole("checkbox", { name: "Apply temperature", exact: true })
    .boundingBox();
  const input = await page
    .getByRole("spinbutton", { name: "Temperature", exact: true })
    .boundingBox();
  expect(input!.y - toggle!.y - toggle!.height).toBeGreaterThanOrEqual(6);
  await capture(page, info, "phone-temperature");
  await nav(page, "Personalities");
  await expect(page.locator(".profile-picker select")).toBeVisible();
  const aligned = await page.locator(".profile-list").evaluate((el) => {
    const picker = el
      .querySelector(".profile-picker select")!
      .getBoundingClientRect();
    const add = el.querySelector(".icon-button")!.getBoundingClientRect();
    return {
      top: Math.abs(picker.y - add.y),
      height: Math.abs(picker.height - add.height),
    };
  });
  expect(aligned.top).toBeLessThanOrEqual(1);
  expect(aligned.height).toBeLessThanOrEqual(1);
  await capture(page, info, "phone-profile-picker");
});
