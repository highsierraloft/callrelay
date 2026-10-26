import { test, expect, type Page } from "@playwright/test";
import { installReceptionFixture as fixture } from "./reception-fixture";

test.use({ timezoneId: "Europe/Madrid" });

async function nav(page: Page, label: string) {
  await page.locator(".sidebar").waitFor({ state: "attached" });
  await page.getByRole("link", { name: label, exact: true }).click();
}

test("sidebar remains visible, remembers its width, and dismisses its compact expansion", async ({
  page,
}) => {
  await fixture(page);
  await page.setViewportSize({ width: 712, height: 989 });
  await page.goto("/calls");
  const sidebar = page.locator(".sidebar");
  await expect(sidebar).toBeVisible();
  await expect(sidebar.getByRole("link")).toHaveCount(10);
  await expect(
    page.getByRole("button", { name: "Menu", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Expand sidebar", exact: true })
    .click();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Collapse sidebar", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Collapse sidebar", exact: true })
    .click();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Expand sidebar", exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("button", { name: "Expand sidebar", exact: true })
    .click();
  const expanded = page.getByRole("dialog", {
    name: "Navigation",
    exact: true,
  });
  await expect(expanded).toBeVisible();
  await expect(expanded.getByRole("link")).toHaveCount(10);
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "Expand sidebar", exact: true }),
  ).toBeFocused();
  await nav(page, "Contacts");
  await expect(
    page.getByRole("heading", { name: "Contacts", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("business and outbound editing preserve voice settings and show conflicts", async ({
  page,
}) => {
  const { state } = await fixture(page);
  const original = structuredClone(state.records.global.value.voice);
  await page.goto("/settings");
  await page
    .getByLabel("Business name", { exact: true })
    .fill("My reception business");
  await page.getByRole("button", { name: "Save Changes", exact: true }).click();
  await expect
    .poll(() => state.records.business.value.name)
    .toBe("My reception business");
  await nav(page, "Outbound");
  await page
    .getByRole("textbox", { name: "Conversation instructions", exact: true })
    .fill("Ask one useful question and follow the approved task.");
  state.records.global.revision++;
  await page.getByRole("button", { name: "Save Changes", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("changed");
  await expect(
    page.getByRole("textbox", {
      name: "Conversation instructions",
      exact: true,
    }),
  ).toHaveValue("Ask one useful question and follow the approved task.");
  expect(state.records.global.value.voice).toEqual(original);
});

test("inbound drafts enter practice without enabling reception or changing contacts", async ({
  page,
}, info) => {
  const { state, extra } = await fixture(page);
  await page.goto("/inbound");
  await expect(page.getByText("Answering Off", { exact: true })).toBeVisible();
  await page
    .getByRole("textbox", { name: "Reception instructions", exact: true })
    .fill("Ask what the caller needs and learn the useful missing details.");
  await page.screenshot({
    path: info.outputPath("inbound.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page
    .getByRole("button", { name: "Try in Playground", exact: true })
    .click();
  await expect(
    page.getByRole("combobox", { name: "Call direction", exact: true }),
  ).toHaveValue("inbound");
  await page
    .getByRole("combobox", { name: "Caller context", exact: true })
    .selectOption("contact_maria");
  await page.getByRole("button", { name: "Text", exact: true }).click();
  await page
    .getByRole("button", { name: "Start Text Practice", exact: true })
    .click();
  expect(state.practiceRequest.context.direction).toBe("inbound");
  expect(state.practiceRequest.overrides.inbound.instructions).toContain(
    "useful missing details",
  );
  expect(state.practiceRequest.overrides.simulation_contact_id).toBe(
    "contact_maria",
  );
  expect(state.records.inbound.value.enabled).toBe(false);
  expect(extra.contactsWritten).toBe(0);
  expect(extra.memoryWrites).toBe(0);
  expect(state.started).toBe(0);
});

test("contact history keeps recordings linked and memory conflicts retain edits", async ({
  page,
}, info) => {
  const { extra } = await fixture(page);
  await page.goto("/contacts/contact_maria");
  await expect(
    page.getByText(/Discussed the cafe's booking needs/),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: /Open incoming call from/ }),
  ).toBeVisible();
  await expect(
    page.getByText("Loading contacts…", { exact: true }),
  ).not.toBeVisible();
  await page.screenshot({
    path: info.outputPath("contact-history.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("tab", { name: "Memory", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Fact 1", exact: true })
    .fill("Prefers mornings now.");
  extra.contact.memory.revision = 2;
  await page.getByRole("button", { name: "Save Memory", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("memory changed");
  await expect(
    page.getByRole("textbox", { name: "Fact 1", exact: true }),
  ).toHaveValue("Prefers mornings now.");
  expect(extra.memoryWrites).toBe(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("callback creation requires review and sends the device timezone's actual offset", async ({
  page,
}, info) => {
  const { state, extra } = await fixture(page);
  await page.goto("/callbacks");
  await page
    .getByRole("button", { name: "Schedule Callback", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "Source conversation", exact: true })
    .selectOption("call_demo_1");
  await page
    .getByRole("textbox", { name: "Callback task", exact: true })
    .fill("Discuss the approved quote");
  await page
    .getByLabel("Confirmed number", { exact: true })
    .fill("+12025550150");
  await page
    .getByLabel("Window starts", { exact: true })
    .fill("2026-11-02T10:00");
  await page
    .getByLabel("Window ends", { exact: true })
    .fill("2026-11-02T10:15");
  expect(extra.scheduleRequests).toHaveLength(0);
  await page
    .getByRole("button", { name: "Review Schedule", exact: true })
    .click();
  await expect(
    page.getByText("One automatic dialing attempt.", { exact: false }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Confirm Schedule", exact: true })
    .click();
  await expect.poll(() => extra.scheduleRequests.length).toBe(1);
  expect(extra.scheduleRequests[0].window_start).toBe(
    "2026-11-02T10:00:00+01:00",
  );
  expect(extra.scheduleRequests[0].timezone).toBe("Europe/Madrid");
  expect(state.started).toBe(0);
  await expect(
    page.getByRole("button", { name: "Cancel Callback", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("callbacks.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page
    .getByRole("button", { name: "Cancel Callback", exact: true })
    .click();
  await expect.poll(() => extra.schedules[0].state).toBe("cancelled");
});

test("the full-text editor rejects invalid shapes before opening guided controls", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/settings");
  await page.getByLabel("Business name", { exact: true }).waitFor();
  await page.locator(".revision-form .action-menu > summary").click();
  await page
    .getByRole("button", { name: "Full Text Editor", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Configuration JSON", exact: true })
    .fill("[]");
  await page.locator(".revision-form .action-menu > summary").click();
  await page
    .getByRole("button", { name: "Guided Editor", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("Correct the JSON");
  await expect(
    page.getByRole("textbox", { name: "Configuration JSON", exact: true }),
  ).toHaveValue("[]");
});

test("saving memory preserves the separate unsaved identity draft", async ({
  page,
}) => {
  const { extra } = await fixture(page);
  await page.goto("/contacts/contact_maria");
  await page.getByRole("tab", { name: "Details", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Name", exact: true })
    .fill("María draft");
  await page.getByRole("tab", { name: "Memory", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Fact 1", exact: true })
    .fill("Prefers weekday mornings");
  await page.getByRole("button", { name: "Save Memory", exact: true }).click();
  await expect.poll(() => extra.memoryWrites).toBe(1);
  await page.getByRole("tab", { name: "Details", exact: true }).click();
  await expect(
    page.getByRole("textbox", { name: "Name", exact: true }),
  ).toHaveValue("María draft");
  expect(extra.contactsWritten).toBe(0);
});

test("blocked callbacks require verified answers before release", async ({
  page,
}) => {
  const { extra } = await fixture(page);
  extra.schedules.push({
    schedule_id: "schedule_blocked",
    source_call_id: "call_demo_1",
    revision: 1,
    state: "needs_information",
    destination: "+12025550150",
    window_start: "2026-11-02T10:00:00Z",
    window_end: "2026-11-02T10:15:00Z",
    value: {
      objective: "Discuss the approved quote",
      needed_information: ["Confirm the approved price"],
    },
    dial_claimed: 0,
  });
  await page.goto("/callbacks");
  await expect(
    page.getByRole("button", { name: "Call Now", exact: true }),
  ).toBeDisabled();
  await page
    .getByText("Waiting for required information", { exact: true })
    .click();
  const release = page.getByRole("button", {
    name: "Confirm Answers and Release Callback",
    exact: true,
  });
  await expect(release).toBeDisabled();
  await page
    .getByRole("textbox", { name: "Verified answers", exact: true })
    .fill("The approved price is 20 EUR monthly.");
  await release.click();
  await expect.poll(() => extra.schedules[0].state).toBe("scheduled");
  await expect(
    page.getByRole("button", { name: "Call Now", exact: true }),
  ).toBeEnabled();
});

test("contact drafts survive cancelled navigation and browser back", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/calls");
  await nav(page, "Contacts");
  await page.getByRole("link", { name: /María/ }).click();
  await page.getByRole("tab", { name: "Details", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Name", exact: true })
    .fill("Unsaved María");
  page.once("dialog", (dialog) => dialog.dismiss());
  await nav(page, "Calls");
  await expect(page).toHaveURL(/contacts\/contact_maria$/);
  await expect(
    page.getByRole("textbox", { name: "Name", exact: true }),
  ).toHaveValue("Unsaved María");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.goBack();
  await expect(page).toHaveURL(/contacts\/contact_maria$/);
  page.once("dialog", (dialog) => dialog.accept());
  await nav(page, "Calls");
  await expect(page).toHaveURL(/\/calls$/);
});

test("contacts stay dense and left aligned at the reported window width", async ({
  page,
}, info) => {
  const { extra, state } = await fixture(page);
  extra.contacts = Array.from({ length: 24 }, (_, i) => ({
    ...extra.contact,
    contact_id: i ? "contact_" + i : extra.contact.contact_id,
    name: i ? "Customer with a deliberately long name " + i : "Maria",
    numbers: [
      {
        phone: "+34614000" + String(i).padStart(3, "0"),
        source: "owner",
        confirmed: true,
      },
    ],
  }));
  extra.contact.calls = Array.from({ length: 6 }, (_, i) => ({
    ...extra.contact.calls[0],
    call_id: "history_" + i,
    direction: "outbound",
    summary: null,
    review_state: null,
    state: i === 4 ? "failed" : "completed",
  })) as any;
  await page.setViewportSize({ width: 712, height: 989 });
  await page.goto("/contacts/contact_maria");
  await expect(page.locator(".contact-rows a")).toHaveCount(24);
  await expect(page.locator(".timeline-call")).toHaveCount(6);
  const metrics = await page
    .locator(".contact-rows a")
    .first()
    .evaluate((element) => {
      const row = element.getBoundingClientRect(),
        name = element.querySelector("strong")!.getBoundingClientRect();
      return {
        height: row.height,
        leftInset: name.left - row.left,
        align: getComputedStyle(element).alignItems,
      };
    });
  expect(metrics.height).toBeLessThanOrEqual(64);
  expect(metrics.leftInset).toBeGreaterThanOrEqual(8);
  expect(metrics.leftInset).toBeLessThanOrEqual(16);
  expect(metrics.align).not.toBe("center");
  await expect(
    page.getByText("No summary recorded.", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText(/Original recordings and full transcripts stay/),
  ).toHaveCount(0);
  expect(
    await page
      .locator(".contact-rows")
      .evaluate((el) => el.scrollHeight > el.clientHeight),
  ).toBeTruthy();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: info.outputPath("contacts-compact.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page.locator(".timeline-call .action-menu > summary").first().click();
  await expect(
    page.getByRole("button", { name: "Queue review", exact: true }),
  ).toBeVisible();
  expect(state.started).toBe(0);
});

test("empty contact state has spacing without tutorial copy and reduced motion stays still", async ({
  page,
}, info) => {
  await fixture(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/contacts");
  await expect(page.locator(".empty-detail")).toHaveText("Select a contact");
  expect(
    await page
      .locator(".empty-detail")
      .evaluate((el) => parseFloat(getComputedStyle(el).paddingLeft)),
  ).toBeGreaterThanOrEqual(24);
  const collapse = page.getByRole("button", {
    name: "Collapse sidebar",
    exact: true,
  });
  if (await collapse.isVisible()) await collapse.click();
  expect(
    await page
      .locator(".app-shell")
      .evaluate((el) => getComputedStyle(el).transitionDuration),
  ).toBe("0s");
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Expand sidebar", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".contact-rows a")).toHaveCount(1);
  await page.screenshot({
    path: info.outputPath("contacts-empty.png"),
    fullPage: true,
    animations: "disabled",
  });
});

test("navigation and forms fit a compact workspace and enlarged text", async ({
  page,
}, info) => {
  test.setTimeout(60000);
  const { state } = await fixture(page);
  await page.setViewportSize({ width: 712, height: 989 });
  await page.goto("/settings");
  const destinations: Record<string, string> = {
    settings: "Settings",
    outbound: "Outbound",
    inbound: "Inbound",
    personalities: "Personalities",
    playground: "Playground",
    integration: "Integrations",
    callbacks: "Callbacks",
    costs: "Costs",
  };
  for (const path of [
    "settings",
    "outbound",
    "inbound",
    "personalities",
    "playground",
    "integration",
    "callbacks",
    "costs",
  ]) {
    await nav(page, destinations[path]);
    await expect(page.locator(".workspace > .app-toolbar h1")).toHaveText(
      path === "integration" ? "Integrations" : destinations[path],
    );
    await expect(
      page.getByText("Opening your private workspace…", { exact: true }),
    ).toHaveCount(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      path,
    ).toBeTruthy();
    const field: Record<string, string> = {
      settings: "Business name",
      inbound: "Reception instructions",
      personalities: "Personality prompt",
      playground: "Task",
    };
    if (field[path]) {
      await expect(
        page.getByRole("textbox", { name: field[path], exact: true }),
      ).toBeVisible();
      await page.screenshot({
        path: info.outputPath(path + "-compact.png"),
        fullPage: true,
        animations: "disabled",
      });
    }
  }
  await nav(page, "Contacts");
  await page.locator('.contact-rows a[href="/contacts/contact_maria"]').click();
  await expect(page.locator(".contact-heading h2")).toHaveText("María");
  await page.addStyleTag({ content: "html { font-size: 175%; }" });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await expect(
    page.getByRole("button", { name: "Contacts", exact: true }),
  ).toBeVisible();
  expect(state.started).toBe(0);
});

test("contact detail and persistent rail fit a phone without filler", async ({
  page,
}, info) => {
  await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/contacts/contact_maria");
  await expect(page.locator(".contact-heading h2")).toHaveText("María");
  await expect(
    page.getByRole("button", { name: "Expand sidebar", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Contacts", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".contact-list")).not.toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: info.outputPath("contacts-phone.png"),
    fullPage: true,
    animations: "disabled",
  });
});
