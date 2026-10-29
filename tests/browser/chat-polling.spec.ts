import { test, expect } from "@playwright/test";
import { seedChat } from "./chat-fixture";
import { openMovie } from "./direct-fixture";

test("delayed reply survives a background poll failure and settles without reload", async ({
  page,
  request,
}) => {
  const data = await seedChat(request);
  let failedPoll = false;
  let background = false;
  let terminalFullRefresh = false;
  try {
    await data.script(
      [
        {
          tool: "say",
          thought: "",
          input: { text: "The delayed reply arrived." },
        },
      ],
      4500,
    );
    await page.route("**/api/agent/sessions/*?after=*", async (route) => {
      if (background && !failedPoll) {
        failedPoll = true;
        // Simulate hiding the window while a poll is in flight. Both interval
        // polling and React Query's retry loop consult document visibility.
        await page.evaluate(() => {
          Object.defineProperty(document, "visibilityState", {
            configurable: true,
            get: () => "hidden",
          });
          window.dispatchEvent(new Event("visibilitychange"));
        });
        await route.fulfill({
          status: 500,
          json: { detail: "Transient poll failure" },
        });
        return;
      }
      const response = await route.fetch();
      const body = await response.json();
      if (
        background &&
        body.session.state === "idle" &&
        new URL(route.request().url()).searchParams.get("after") === "-1"
      )
        terminalFullRefresh = true;
      await route.fulfill({ response });
    });
    await openMovie(page, data.movie.title);
    await page.getByLabel("Message to Gaia").fill("Take time to answer");
    await page.getByLabel("Message to Gaia").press("Enter");
    await expect(page.locator(".chat-activity")).toContainText("thinking");
    background = true;
    await expect.poll(() => failedPoll).toBe(true);
    await expect
      .poll(async () => (await data.session()).state, { timeout: 15_000 })
      .toBe("idle");
    await expect(page.locator(".chat-assistant")).toContainText(
      "The delayed reply arrived.",
      { timeout: 12_000 },
    );
    await expect(page.locator(".chat-activity")).toHaveText("idle");
    await page.getByLabel("Message to Gaia").fill("A follow-up is ready");
    await expect(
      page.getByRole("button", { name: "Send message", exact: true }),
    ).toBeEnabled();
    await expect(page.getByRole("alert")).toHaveCount(0);
    expect(failedPoll).toBe(true);
    await expect.poll(() => terminalFullRefresh).toBe(true);
  } finally {
    await page.unrouteAll({ behavior: "wait" });
    await data.restore();
  }
});

test("terminal reconciliation retries with backoff and recovers omitted turns once", async ({
  page,
  request,
}) => {
  test.setTimeout(45_000);
  const data = await seedChat(request);
  const fullAttempts: number[] = [];
  let reconciled = false;
  let settledPolls = 0;
  try {
    await data.script(
      [
        {
          tool: "say",
          thought: "",
          input: { text: "Recovered by the full refresh." },
        },
      ],
      3500,
    );
    await page.route("**/api/agent/sessions/*?after=*", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      const hasReply = body.turns.some(
        (turn: { role: string; state: string }) =>
          turn.role === "agent" && turn.state === "accepted",
      );
      const full =
        new URL(route.request().url()).searchParams.get("after") === "-1";
      if (body.session.state === "idle" && full && hasReply) {
        fullAttempts.push(Date.now());
        if (fullAttempts.length <= 2) {
          await route.fulfill({
            status: 500,
            json: { detail: "Final refresh temporarily unavailable" },
          });
          return;
        }
        reconciled = true;
      } else if (body.session.state === "idle" && !full) {
        if (reconciled) settledPolls++;
        // A delta alone must not be the last word on a completed run.
        // The cursor-free request below still reads the real persisted turns.
        body.turns = [];
      }
      await route.fulfill({ json: body });
    });
    await openMovie(page, data.movie.title);
    await page.getByLabel("Message to Gaia").fill("Reconcile the final reply");
    await page.getByLabel("Message to Gaia").press("Enter");
    await expect(page.locator(".chat-activity")).toHaveText("thinking");
    await expect(page.getByRole("alert")).toContainText(
      "Final refresh temporarily unavailable",
      { timeout: 12_000 },
    );
    await expect(page.locator(".chat-human")).toContainText(
      "Reconcile the final reply",
    );
    await expect(page.locator(".chat-assistant")).toContainText(
      "Recovered by the full refresh.",
      { timeout: 12_000 },
    );
    await expect(page.locator(".chat-activity")).toHaveText("idle");
    await expect(page.getByRole("alert")).toHaveCount(0);
    // Check actual network retry spacing, allowing a little timer jitter.
    expect(fullAttempts[1] - fullAttempts[0]).toBeGreaterThanOrEqual(900);
    expect(fullAttempts[2] - fullAttempts[1]).toBeGreaterThanOrEqual(1900);
    await expect
      .poll(() => settledPolls, { timeout: 12_000 })
      .toBeGreaterThan(0);
    expect(fullAttempts).toHaveLength(3);
    await expect(page.locator(".chat-assistant")).toHaveCount(1);
  } finally {
    await page.unrouteAll({ behavior: "wait" });
    await data.restore();
  }
});
