import { test, expect } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { installFixture } from "./fixture";

test("MCP clients do not require a Hermes dashboard link", async ({ page }) => {
  await installFixture(page, { hermes_url: null });
  await page.goto("/integration");
  await expect(
    page.getByRole("heading", { name: "Integrations", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "Open Hermes" })).toHaveCount(0);
  await page.getByRole("tab", { name: "MCP Tools", exact: true }).click();
  await expect(
    page.locator("code").filter({ hasText: "prepare_phone_call" }).first(),
  ).toBeVisible();
});

test("documentation preview contains only fictional data", async ({
  page,
}, info) => {
  test.skip(!["chromium", "dark"].includes(info.project.name));
  const state = await installFixture(page);
  state.recordingAvailable = true;
  // The preview uses a generated silent WAV, never a recorded conversation.
  const samples = 8000 * 18;
  const wav = Buffer.alloc(44 + samples * 2);
  wav.write("RIFF", 0);
  wav.writeUInt32LE(wav.length - 8, 4);
  wav.write("WAVEfmt ", 8);
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(8000, 24);
  wav.writeUInt32LE(16000, 28);
  wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34);
  wav.write("data", 36);
  wav.writeUInt32LE(samples * 2, 40);
  await page.route("**/admin/api/calls/*/files/recording.mp3", (route) =>
    route.fulfill({ contentType: "audio/wav", body: wav }),
  );
  await page.goto("/calls");
  await page.getByRole("button", { name: /Alex Morgan/ }).click();
  await expect(
    page.getByRole("heading", { name: "Alex Morgan" }),
  ).toBeVisible();
  await expect(page.locator("audio")).toBeVisible();
  await page.waitForFunction(
    () => (document.querySelector("audio")?.readyState ?? 0) >= 1,
  );
  mkdirSync("../docs/images", { recursive: true });
  await page.screenshot({
    path: `../docs/images/workspace-${info.project.name === "dark" ? "dark" : "light"}.png`,
    fullPage: false,
  });
  expect(state.started).toBe(0);
});
