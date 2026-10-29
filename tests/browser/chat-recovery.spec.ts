import { test, expect } from "@playwright/test";
import { referenceReading, seedChat } from "./chat-fixture";
import { openMovie } from "./direct-fixture";
import type { components } from "../../frontend/src/shared/api.generated";
import {
  mergeState,
  pollDelay,
  turnCursor,
} from "../../frontend/src/features/chat/client";
type Schema = components["schemas"];

test("poll cursor re-reads requested turns and merges completed updates", () => {
  const pending: Schema["TurnDTO"] = {
    id: "turn",
    ordinal: 2,
    role: "agent",
    state: "requested",
    action: null,
    result: null,
    receipts: [],
    provenance: null,
    created_at: "1",
    updated_at: "1",
  };
  expect(turnCursor([pending])).toBe(1);
  const accepted = { ...pending, state: "accepted", updated_at: "2" };
  const session = { id: "s", revision: 3 } as Schema["HarnessSessionDTO"];
  const merged = mergeState(
    { session, turns: [accepted] },
    { session: { ...session, revision: 2 }, turns: [pending] },
  );
  expect(merged.turns).toEqual([accepted]);
  expect(merged.session.revision).toBe(3);
  expect(turnCursor(merged.turns)).toBe(2);
  expect(pollDelay("running")).toBe(1000);
  expect(pollDelay("waiting_human")).toBe(10_000);
});

test("remote model label and keyboard mention picker focus", async ({
  page,
  request,
}) => {
  const data = await seedChat(request);
  try {
    await page.route("**/api/v1/settings/connections", async (route) => {
      const response = await route.fetch();
      const settings = await response.json();
      settings.assistant_connections[0].base_url = "https://example.invalid/v1";
      settings.assistant_connections[0].name = "Remote studio";
      await route.fulfill({ json: settings });
    });
    await openMovie(page, data.movie.title);
    // The state depends on whether an earlier test already called the scripted model.
    await expect(page.locator(".chat-model-line")).toContainText(
      /Remote studio · scripted-gaia · (unknown|ready)/,
    );
    await page
      .getByRole("button", { name: "Mention cast, places or segments" })
      .click();
    await expect(page.getByRole("button", { name: "Arin cast" })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(page.locator(".chat-mention-option").nth(1)).toBeFocused();
    await page.keyboard.press("ArrowUp");
    await expect(page.getByRole("button", { name: "Arin cast" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.getByLabel("Message to Gaia")).toBeFocused();
    await expect(
      page.getByRole("region", { name: "Mention an item" }),
    ).toHaveCount(0);
  } finally {
    await data.restore();
  }
});

test("a completed send preserves edits made while its response was pending", async ({
  page,
  request,
}) => {
  const data = await seedChat(request);
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  try {
    await data.script([
      { tool: "say", thought: "", input: { text: "First message received." } },
    ]);
    await page.route("**/api/agent/sessions/*/messages", async (route) => {
      const response = await route.fetch();
      await held;
      await route.fulfill({ response });
    });
    await openMovie(page, data.movie.title);
    await page.getByLabel("Message to Gaia").fill("First message");
    const posted = page.waitForRequest((r) => r.url().endsWith("/messages"));
    await page.getByLabel("Message to Gaia").press("Enter");
    await posted;
    await page
      .getByLabel("Message to Gaia")
      .fill("A thought for the next message");
    release();
    await expect(page.locator(".chat-human")).toContainText("First message");
    await expect(page.getByLabel("Message to Gaia")).toHaveValue(
      "A thought for the next message",
    );
    // A saved human message does not mean the worker has consumed its script yet.
    // Keep this fixture installed until its model turn completes.
    await expect(page.locator(".chat-assistant")).toContainText(
      "First message received.",
      { timeout: 20_000 },
    );
  } finally {
    release();
    await data.restore();
  }
});

test("a real revision conflict refreshes the plan and never silently retries approval", async ({
  page,
  request,
}) => {
  const data = await seedChat(request);
  try {
    await data.script([data.proposal()]);
    await openMovie(page, data.movie.title);
    await page.getByLabel("Message to Gaia").fill("Plan this shot");
    await page.getByLabel("Message to Gaia").press("Enter");
    const plan = page.getByRole("region", { name: "Plan revision 1" });
    await expect(plan).toBeVisible({ timeout: 20_000 });
    let decisions = 0;
    await page.route("**/api/agent/sessions/*/plan/approve", async (route) => {
      decisions++;
      const current = await data.session();
      const mode = await request.put(
        `/api/agent/sessions/${current.id}/plan-mode`,
        {
          headers: data.headers,
          data: { expected_revision: current.revision, enabled: true },
        },
      );
      expect(mode.ok(), await mode.text()).toBeTruthy();
      await route.continue();
    });
    await plan.getByRole("button", { name: "Approve", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText(
      "The session changed; review the plan again",
    );
    expect(decisions).toBe(1);
    expect((await data.session()).plan.state).toBe("proposed");
    await page.getByRole("button", { name: "Refresh conversation" }).click();
    await expect(page.getByRole("alert")).toHaveCount(0);
  } finally {
    await data.restore();
  }
});

test("running session disables mode; Stop cancels the scripted request", async ({
  page,
}) => {
  const data = await seedChat(page.request);
  try {
    await data.script(
      [{ tool: "say", thought: "", input: { text: "Slow reply" } }],
      8000,
    );
    await openMovie(page, data.movie.title);
    await page.getByLabel("Message to Gaia").fill("Take your time");
    await page.getByLabel("Message to Gaia").press("Enter");
    await expect(
      page.getByRole("button", { name: /Plan mode Shift/ }),
    ).toBeDisabled();
    await expect(
      page.getByRole("button", { name: /Plan mode Shift/ }),
    ).toHaveAttribute("title", /running/);
    // Exercise cancellation during the scripted request, after the worker's
    // queued-to-running claim has finished updating the session revision.
    await expect
      .poll(async () => {
        const response = await page.request.get(
          `${process.env.GAIAVFX_SCRIPTED_AGENT_URL}/requests`,
        );
        return (await response.json()).length;
      })
      .toBe(1);
    await page.getByRole("button", { name: "Stop", exact: true }).click();
    await expect
      .poll(async () => (await data.session()).state, { timeout: 20_000 })
      .toBe("stopped");
  } finally {
    await data.restore();
  }
});

test("unranked review tool cards never expose candidate operation, seed or technique", async ({
  page,
  request,
}) => {
  const data = await seedChat(request);
  try {
    const opened = await request.post("/api/agent/sessions", {
      headers: data.headers,
      data: { movie_id: data.movie.id },
    });
    const session = await opened.json();
    const turn: Schema["TurnDTO"] = {
      id: "blind-tool",
      ordinal: 0,
      role: "agent",
      state: "accepted",
      created_at: "2026-10-02",
      updated_at: "2026-10-02",
      action: {
        tool: "read_candidates",
        input: {
          operation_id: "secret-operation-321",
          candidate_id: "secret-candidate-456",
          seed: 7654321,
          technique: "secret-technique",
        },
      },
      result: {
        payload: {
          review: {
            id: "unranked-review",
            ranking: null,
            revealed: false,
            members: [
              {
                letter: "A",
                candidate_id: "secret-candidate-456",
                operation_id: "secret-operation-321",
                seed: 7654321,
                technique: "secret-technique",
              },
            ],
          },
        },
      },
      receipts: [
        { kind: "review_set", id: "unranked-review" },
        { kind: "operation", id: "secret-operation-321" },
      ],
      provenance: { seed: 7654321 },
    };
    await page.route(`**/api/agent/sessions/${session.id}?*`, (route) =>
      route.fulfill({
        json: {
          session,
          turns: [
            turn,
            {
              ...turn,
              id: "rejected",
              ordinal: 1,
              state: "rejected",
              action: { tool: "render_segment", input: {} },
            },
          ],
        },
      }),
    );
    await openMovie(page, data.movie.title);
    const card = page.locator(".chat-tool");
    await expect(card).toHaveCount(1);
    await card.locator("summary").click();
    await expect(
      page.getByRole("button", { name: "Open in Review", exact: true }),
    ).toBeEnabled();
    await expect(page.locator(".chat-panel")).not.toContainText(
      /secret-operation|secret-candidate|7654321|secret-technique/,
    );
    await card.locator("summary").focus();
    await page.keyboard.press("Shift+F10");
    await expect(
      page.getByRole("menuitem", { name: "Open in Review", exact: true }),
    ).not.toHaveAttribute("aria-disabled", "true");
    await page.keyboard.press("Escape");
    await page.getByRole("checkbox", { name: "Debug" }).check();
    await expect(page.locator(".chat-tool")).toHaveCount(2);
    await expect(page.locator(".chat-panel")).not.toContainText(
      /secret-operation|secret-candidate|7654321|secret-technique/,
    );
  } finally {
    await data.restore();
  }
});

test("Project drag attachments are unique and a failed send preserves the draft", async ({
  page,
  request,
}) => {
  const data = await seedChat(request);
  try {
    await openMovie(page, data.movie.title);
    const reference = page.getByRole("button", {
      name: "Arin reference.jpg image",
    });
    await page.getByLabel("Search project").fill("Arin reference.jpg");
    await reference.dragTo(page.locator(".chat-composer"));
    await expect(page.locator(".chat-composer .chat-attachment")).toHaveCount(
      1,
    );
    await reference.dragTo(page.locator(".chat-composer"));
    await expect(page.locator(".chat-composer .chat-attachment")).toHaveCount(
      1,
    );
    await page.route("**/api/agent/sessions/*/messages", (route) =>
      route.fulfill({ status: 503, json: { detail: "Offline fixture" } }),
    );
    await page.getByLabel("Message to Gaia").fill("Keep my draft");
    await page.getByLabel("Message to Gaia").press("Shift+Enter");
    await page.getByLabel("Message to Gaia").press("Enter");
    await expect(page.getByRole("alert")).toContainText("Offline fixture");
    await expect(page.getByLabel("Message to Gaia")).toHaveValue(
      "Keep my draft\n",
    );
    await expect(page.locator(".chat-composer .chat-attachment")).toHaveCount(
      1,
    );
    await data.script([
      ...referenceReading(data.asset.id),
      {
        tool: "say",
        thought: "",
        input: { text: "Recovered the conversation." },
      },
    ]);
    await page.unroute("**/api/agent/sessions/*/messages");
    await page.getByLabel("Message to Gaia").press("Enter");
    await expect(page.locator(".chat-markdown")).toHaveText(
      "Recovered the conversation.",
      { timeout: 20_000 },
    );
    expect(
      (
        await (
          await request.get(`/api/agent/sessions?movie_id=${data.movie.id}`, {
            headers: data.headers,
          })
        ).json()
      ).length,
    ).toBe(1);
  } finally {
    await data.restore();
  }
});
