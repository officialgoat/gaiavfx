import { test, expect, type Page } from "@playwright/test";
import { filmAsset, mockRecast } from "./recast-fixtures";

// ADR 0017 U5a performance budgets for a long film (2000 shots, about 67 minutes at 25 fps), with the recast polled
// every 2 s, in the development build the suite runs. Measured on this workstation on 22 October: open about 1.9 s;
// no long task while polling; a shot change 45-75 ms; 51 items in view at the deepest zoom. Before the Shots bin's
// rows and the current-shot marks stopped redrawing everything, a shot change took about 330 ms and each poll blocked
// for about 250 ms. The budgets sit between the two, so they catch a regression in kind, not noise.

const BUDGET = {
  openMs: 4000,
  longestTaskMs: 150,
  blockingPerPollMs: 100,
  shotChangeMs: 200,
  itemsPerView: 300,
};

async function openLongFilm(page: Page) {
  const mock = await mockRecast(page, 2000);
  mock.existing();
  await page.addInitScript(() => {
    const tasks: number[] = [];
    (window as unknown as { longTasks: number[] }).longTasks = tasks;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) tasks.push(entry.duration);
    }).observe({ type: "longtask", buffered: true });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "File", exact: true }).click();
  await page.getByRole("menuitem", { name: /Open film/ }).click();
  const entry = page
    .getByRole("dialog", { name: "Open film" })
    .getByRole("button", { name: new RegExp(filmAsset.name) })
    .first();
  const started = Date.now();
  await entry.click();
  await expect(page.locator(".timeline-heading")).toContainText(filmAsset.name);
  await expect(page.locator(".status-strip")).toBeVisible();
  return { mock, openMs: Date.now() - started };
}

const longTasks = (page: Page) =>
  page.evaluate(() => [
    ...(window as unknown as { longTasks: number[] }).longTasks,
  ]);

test("a 2000-shot film opens within budget and polling stays light", async ({
  page,
}) => {
  const { mock, openMs } = await openLongFilm(page);
  console.log(`open ${openMs} ms`);
  expect(openMs).toBeLessThan(BUDGET.openMs);
  // At least three polls with the film idle, counted as the recast's GET responses: each returns the whole
  // recast, as the service does.
  let polls = 0;
  page.on("response", (response) => {
    if (
      response.request().method() === "GET" &&
      new URL(response.url()).pathname === "/api/recasts/recast-1"
    )
      polls += 1;
  });
  const before = (await longTasks(page)).length;
  const writes = mock.calls.length;
  await expect.poll(() => polls, { timeout: 10000 }).toBeGreaterThanOrEqual(3);
  // The last poll's render settles before the long tasks are read.
  await page.evaluate(() => new Promise((done) => setTimeout(done, 300)));
  const during = (await longTasks(page)).slice(before);
  const blocking = during.reduce((sum, d) => sum + Math.max(0, d - 50), 0);
  console.log(
    `polling: ${polls} polls, ${during.length} long tasks, longest ${Math.max(0, ...during).toFixed(0)} ms, blocking ${blocking.toFixed(0)} ms`,
  );
  expect(Math.max(0, ...during)).toBeLessThan(BUDGET.longestTaskMs);
  expect(blocking / polls).toBeLessThan(BUDGET.blockingPerPollMs);
  // Polling only reads.
  expect(mock.calls.length).toBe(writes);
});

test("in a 2000-shot film the next shot is selected quickly and the last shot is reachable", async ({
  page,
}) => {
  await openLongFilm(page);
  const strip = page.getByRole("slider", { name: "Film position" });
  await strip.focus();
  // Timed inside the page, from the key to the second frame after it, so the test runner's own work is not counted.
  const times: number[] = [];
  for (let n = 2; n <= 6; n++) {
    times.push(
      await strip.evaluate(async (element) => {
        const started = performance.now();
        element.dispatchEvent(
          new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }),
        );
        await new Promise((done) =>
          requestAnimationFrame(() => requestAnimationFrame(done)),
        );
        return performance.now() - started;
      }),
    );
    await expect(page.locator(".workspace-breadcrumb")).toContainText(
      `Shot ${String(n).padStart(3, "0")}`,
    );
  }
  console.log(`shot change: ${times.map((t) => t.toFixed(0)).join(", ")} ms`);
  expect(Math.max(...times)).toBeLessThan(BUDGET.shotChangeMs);
  await page.keyboard.press("End");
  await expect(strip).toHaveAttribute("aria-valuenow", "2000");
  await expect(page.locator(".workspace-breadcrumb")).toContainText(
    "Shot 2000",
  );
  // At the deepest zoom, at the end of the film, the timeline still draws a viewport's worth of items.
  await page
    .getByRole("slider", { name: "Timeline zoom" })
    .evaluate((element: HTMLInputElement) => {
      Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )!.set!.call(element, "400");
      element.dispatchEvent(new Event("input", { bubbles: true }));
    });
  await page
    .locator(".timeline-scroll")
    .evaluate((el) => (el.scrollLeft = el.scrollWidth));
  await expect(
    page.getByRole("button", { name: "Shot 2000", exact: true }),
  ).toBeVisible();
  const items = await page.locator(".timeline-canvas .track-item").count();
  console.log(`items at the end, zoom 400: ${items}`);
  expect(items).toBeLessThan(BUDGET.itemsPerView);
});

test("with the review queue open, a 2000-shot film still changes shots within budget", async ({
  page,
}) => {
  await openLongFilm(page);
  await page.keyboard.press("q");
  await expect(
    page.getByRole("list", { name: "Shots waiting for review" }),
  ).toBeVisible();
  const strip = page.getByRole("slider", { name: "Film position" });
  await strip.focus();
  const times: number[] = [];
  for (let n = 2; n <= 6; n++) {
    times.push(
      await strip.evaluate(async (element) => {
        const started = performance.now();
        element.dispatchEvent(
          new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }),
        );
        await new Promise((done) =>
          requestAnimationFrame(() => requestAnimationFrame(done)),
        );
        return performance.now() - started;
      }),
    );
    await expect(page.locator(".workspace-breadcrumb")).toContainText(
      `Shot ${String(n).padStart(3, "0")}`,
    );
  }
  console.log(
    `shot change, queue open: ${times.map((t) => t.toFixed(0)).join(", ")} ms`,
  );
  expect(Math.max(...times)).toBeLessThan(BUDGET.shotChangeMs);
});
