import type { Page } from "@playwright/test";
import { installFixture } from "./fixture";

export async function installReceptionFixture(page: Page) {
  const state = await installFixture(page);
  const contact = {
    contact_id: "contact_maria",
    revision: 1,
    name: "María",
    background: "Runs a cafe",
    private_note: "Owner-only planning note",
    numbers: [{ phone: "+12025550150", source: "owner", confirmed: true }],
    memory: {
      revision: 1,
      facts: [
        {
          key: "opening",
          text: "Prefers calls in the afternoon",
          evidence_turn_ids: ["t1"],
          source_call_id: "call_demo_1",
          owner_protected: false,
        },
      ],
      removed_keys: [],
    },
    calls: [
      {
        call_id: "call_demo_1",
        state: "completed",
        direction: "inbound",
        created_at: "2026-10-25T10:00:00Z",
        summary: {
          text: "Discussed the cafe's booking needs. A follow-up needs an approved quote.",
          outcome: "incomplete",
        },
        review_state: "done",
      },
    ],
    next_offset: null,
    callbacks: [],
  };
  const extra = {
    contact,
    contacts: null as any[] | null,
    contactsWritten: 0,
    memoryWrites: 0,
    schedules: [] as any[],
    scheduleRequests: [] as any[],
    acceptance: { passed: false, window_until: null as string | null },
  };
  await page.route("**/admin/api/inbound/**", (route) =>
    route.fulfill({ json: extra.acceptance }),
  );
  await page.route("**/admin/api/contacts**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON();
      if (path.endsWith("/memory")) {
        if (body.expected_revision !== extra.contact.memory.revision)
          return route.fulfill({
            status: 409,
            json: {
              detail: "Contact memory changed. Reload it before saving.",
            },
          });
        extra.memoryWrites++;
        extra.contact.memory = {
          ...extra.contact.memory,
          revision: extra.contact.memory.revision + 1,
          facts: body.facts,
        };
        return route.fulfill({ json: extra.contact.memory });
      }
      extra.contactsWritten++;
      extra.contact = {
        ...extra.contact,
        ...body,
        revision: extra.contact.revision + 1,
        numbers: body.numbers.map((phone: string) => ({
          phone,
          source: "owner",
          confirmed: true,
        })),
      };
      return route.fulfill({ json: extra.contact });
    }
    return route.fulfill({
      json: path.endsWith("/contacts")
        ? { contacts: extra.contacts || [extra.contact], next_offset: null }
        : extra.contact,
    });
  });
  await page.route("**/admin/api/callbacks**", async (route) => {
    if (route.request().method() === "PUT") {
      const row = extra.schedules[0],
        body = route.request().postDataJSON();
      row.state = body.action === "cancel" ? "cancelled" : "scheduled";
      if (body.action === "resolve_information")
        row.value = {
          ...row.value,
          needed_information: body.needed_information,
          new_facts: body.new_facts,
        };
      row.revision++;
      return route.fulfill({ json: row });
    }
    return route.fulfill({
      json: { callbacks: extra.schedules, next_offset: null },
    });
  });
  await page.route("**/admin/api/calls/*/callbacks", async (route) => {
    const value = route.request().postDataJSON();
    extra.scheduleRequests.push(value);
    const row = {
      schedule_id: "schedule_demo",
      source_call_id: "call_demo_1",
      revision: 1,
      state: "scheduled",
      destination: value.destination,
      window_start: value.window_start,
      window_end: value.window_end,
      value,
      call_id: null,
      dial_claimed: 0,
      reason: null,
    };
    extra.schedules.push(row);
    return route.fulfill({ json: row });
  });
  return { state, extra };
}
