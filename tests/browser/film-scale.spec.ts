import type { Page } from "@playwright/test";
import {
  card,
  expect,
  expectBlind,
  openFilm,
  sharedRecast,
  test,
} from "./recast-world";
import { setZoom, viewCommand, visibleEnd } from "./timeline-fixture";

// A long film (ADR 0017 U5a): its timeline draws a bounded number of items, the status strip stands for the shots and
// reaches the last one, the ruler's ticks stay few, the trackbar's counts fold before a step is clipped, and opening,
// polling and changing shots stay within budget. The recast is the service's own, seeded once for this file and only
// read (tests/browser/recast-world.ts sharedRecast): every command the page sends is refused.

test.use({ reducedMotion: "reduce" });

/** Mara and Elias (the user's pictures) and The courier take 2000 shots of 2 s in turn at 25 fps, 01:06:40 in all.
 * Mara's and Elias's shots among the first six were rendered twice; Shots 008 and 011 have a gap. */
const share = sharedRecast({
  shots: 2000,
  length: 50,
  rate: [25, 1],
  characters: ["Mara", "Elias", "The courier"],
  targets: ["confirmed", "confirmed", null],
  rendered: 6,
  takes: 2,
  states: { 7: "gap", 10: "gap" },
});
const FRAMES = 100_000;

async function open(page: Page) {
  const film = await share(page);
  await page.goto("/");
  await openFilm(page, film);
  return film;
}

const trackbar = (page: Page) => page.getByRole("toolbar", { name: "Recast" });
const step = (page: Page, name: string) =>
  trackbar(page).getByRole("button", { name: new RegExp(`^${name}: `) });
const breadcrumb = (page: Page) => page.locator(".workspace-breadcrumb");
const strip = (page: Page) =>
  page.getByRole("slider", { name: "Film position" });
const ticks = (page: Page) =>
  page
    .locator(".ruler-scale > span")
    .evaluateAll((els) => els.map((el) => el.textContent ?? ""));
const itemsPerRow = (page: Page) =>
  page
    .locator(".timeline-canvas .track-row")
    .evaluateAll((rows) =>
      rows.map((row) => row.querySelectorAll(".track-item").length),
    );

/** A film key, once the last Recast action has saved (a saving film disables its steps with "Saving…"). */
async function keys(page: Page, key: string) {
  await expect(step(page, "Cast")).toBeEnabled();
  await page.keyboard.press(key);
}

/** The zoom at which one 50-frame shot is `pixels` wide. */
async function zoomForShotWidth(page: Page, pixels: number) {
  const available = await page
    .locator(".timeline-scroll")
    .evaluate((el) => Math.max(240, el.clientWidth - 220 - 20));
  return (pixels * FRAMES) / (50 * available);
}

test("a long film draws strips, not thousands of items, when zoomed out", async ({
  page,
}) => {
  await open(page);
  await expect(page.locator(".status-strip")).toHaveCount(1);
  await expect(page.locator(".timeline-pinned .status-cell")).toHaveCount(0);
  for (const row of ["shots", "takes"])
    await expect(
      page.locator(`.track-row[data-row="${row}"] .span-strip`),
    ).toHaveCount(1);
  expect(
    await page.locator(".timeline-canvas .track-item").count(),
  ).toBeLessThan(200);
  await expect(
    trackbar(page).getByRole("button", {
      name: /^Render: \d+ of \d+ renders used$/,
    }),
  ).toBeVisible();
  await expectBlind(page);
});

test("a long film draws a bounded number of items, reaches the last shot, and a refused choice stays readable", async ({
  page,
}) => {
  await open(page);
  await page.getByRole("tab", { name: "Properties" }).click();
  // Items per row stand in for the page's sheet rows: drawn only near the viewport, whatever the shot count.
  // Zoomed out, the rows are strips.
  for (const count of await itemsPerRow(page)) expect(count).toBeLessThan(30);
  // The last shot.
  await strip(page).press("End");
  await expect(strip(page)).toHaveAttribute("aria-valuenow", "2000");
  await expect(
    card(page).getByRole("heading", { name: "Shot 2000", exact: true }),
  ).toBeVisible();
  // Zoomed in to single shots at the end of the film, the rows draw only what is near the viewport.
  await setZoom(page, 400);
  expect(await visibleEnd(page)).toBeLessThan((2000 * 50) / 300);
  await page.locator(".timeline-scroll").evaluate((el) => {
    el.scrollLeft = el.scrollWidth;
  });
  const last = page.getByRole("button", { name: "Shot 2000", exact: true });
  await expect(last).toBeVisible();
  await page.getByRole("button", { name: "Shot 001", exact: true }).waitFor({
    state: "detached",
  });
  await last.click();
  await expect(
    card(page).getByRole("heading", { name: "Shot 2000", exact: true }),
  ).toBeVisible();
  const counts = await itemsPerRow(page);
  expect(Math.max(...counts)).toBeGreaterThan(0);
  for (const count of counts) expect(count).toBeLessThan(30);
  await expectBlind(page);
  // Back at the start, Accept saves the shown version of Shot 001; the service's refusal stays readable.
  await page.locator(".timeline-scroll").evaluate((el) => {
    el.scrollLeft = 0;
  });
  await page.getByRole("button", { name: "Shot 001", exact: true }).click();
  await expect(breadcrumb(page)).toContainText("Shot 001");
  await keys(page, "Enter");
  await expect(page.locator(".film-message[role='alert']")).toContainText(
    "Refresh and try again",
  );
});

test("the status strip selects shots by keyboard and pointer", async ({
  page,
}) => {
  await open(page);
  // Keys.
  await strip(page).press("End");
  await expect(strip(page)).toHaveAttribute("aria-valuenow", "2000");
  await expect(breadcrumb(page)).toContainText("Shot 2000");
  await strip(page).press("Home");
  await expect(strip(page)).toHaveAttribute("aria-valuenow", "1");
  await expect(breadcrumb(page)).toContainText("Shot 001");
  // A click selects the shot under the pointer. Zoom in while the strip still stands for the shots, so one shot is
  // several pixels wide.
  const width = async () => (await strip(page).boundingBox())!.width;
  const shot = (await width()) / 2000;
  const steps = Math.floor(Math.log(7.5 / shot) / Math.log(1.25));
  for (let i = 0; i < steps; i++) await viewCommand(page, "Zoom in");
  const zoomed = (await width()) / 2000;
  expect(zoomed).toBeGreaterThan(3);
  expect(zoomed).toBeLessThan(8);
  await expect(strip(page)).toBeVisible();
  const box = (await strip(page).boundingBox())!;
  // The middle of Shot 002 (frames 50 to 100); the strip starts in view, wider than the timeline.
  await page.mouse.click(
    box.x + (75 / FRAMES) * box.width,
    box.y + box.height / 2,
  );
  await expect(strip(page)).toHaveAttribute("aria-valuenow", "2");
  await expect(breadcrumb(page)).toContainText("Shot 002");
  await expect(strip(page)).toHaveAttribute(
    "aria-valuetext",
    "Shot 002: Needs review · 2 versions",
  );
  // Arrows step one shot.
  await strip(page).press("ArrowRight");
  await expect(strip(page)).toHaveAttribute("aria-valuenow", "3");
  await expect(breadcrumb(page)).toContainText("Shot 003");
  await strip(page).press("ArrowLeft");
  await expect(strip(page)).toHaveAttribute("aria-valuenow", "2");
  await expect(breadcrumb(page)).toContainText("Shot 002");
  await expectBlind(page);
});

test("ruler ticks stay few at every zoom and scroll of a long film", async ({
  page,
}) => {
  await open(page);
  expect((await ticks(page)).length).toBeLessThanOrEqual(45);
  await setZoom(page, 400);
  expect((await ticks(page)).length).toBeLessThanOrEqual(45);
  await page
    .locator(".timeline-scroll")
    .evaluate((el) => (el.scrollLeft = el.scrollWidth));
  // 2000 shots of 50 frames at 25 fps end at 01:06:40.
  await expect
    .poll(async () => (await ticks(page)).some((t) => t.startsWith("01:06:")))
    .toBe(true);
  const labels = await ticks(page);
  expect(labels.length).toBeLessThanOrEqual(45);
  expect(labels.every((t) => /^\d\d:\d\d:\d\d:00$/.test(t))).toBe(true);
});

test("items draw by their width: strips below 8 px, bare boxes below 24 px, posters above", async ({
  page,
}) => {
  await open(page);
  const shots = page.locator('.track-row[data-row="shots"]');
  await expect(shots.locator(".span-strip")).toHaveCount(1);
  await expect(shots.locator(".shot-item")).toHaveCount(0);
  await setZoom(page, await zoomForShotWidth(page, 14));
  await expect(shots.locator(".span-strip")).toHaveCount(0);
  await expect(shots.locator(".shot-item").first()).toBeVisible();
  await expect(shots.locator(".shot-item img")).toHaveCount(0);
  expect(
    await page.locator(".timeline-canvas .track-item").count(),
  ).toBeLessThan(600);
  await setZoom(page, await zoomForShotWidth(page, 60));
  await expect(shots.locator(".shot-item img").first()).toBeVisible();
  expect(
    await page.locator(".timeline-canvas .track-item").count(),
  ).toBeLessThan(300);
});

// Finish verdict (22 October): at 1440x900 with the gaps chip showing, the trackbar's steps fold their counts before
// any step label is clipped.
test("at 1440x900 no trackbar step is clipped", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page);
  await expect(
    trackbar(page).getByRole("button", { name: /^2 need attention/ }),
  ).toBeVisible();
  const steps = trackbar(page).locator(".trackbar-steps");
  const clipped = await steps.evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(clipped).toBeLessThanOrEqual(0);
  await expect(step(page, "Conform")).toContainText("Conform");
});

// U5a performance budgets for a long film, with the recast polled every 2 s, in the development build the suite runs.
// Measured on this workstation on 22 October (on the TypeScript mock's film): open about 1.9 s; no long task while
// polling; a shot change 45-75 ms; 51 items in view at the deepest zoom. On 2 November, on the service's own read:
// open about 1.5 s; no long task while polling; a shot change 24-80 ms; 33 items in view at the deepest zoom. Before
// the Shots bin's rows and the current-shot marks stopped redrawing everything, a shot change took about 330 ms and
// each poll blocked for about 250 ms. The budgets sit between the two, so they catch a regression in kind, not noise.
test.describe("performance budgets", () => {
  const BUDGET = {
    openMs: 4000,
    longestTaskMs: 150,
    blockingPerPollMs: 100,
    shotChangeMs: 200,
    itemsPerView: 300,
  };

  async function openTimed(page: Page) {
    const film = await share(page);
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
      .getByRole("button", { name: new RegExp(film.title) })
      .first();
    const started = Date.now();
    await entry.click();
    await expect(page.locator(".timeline-heading")).toContainText(film.title);
    await expect(page.locator(".status-strip")).toBeVisible();
    return { film, openMs: Date.now() - started };
  }

  const longTasks = (page: Page) =>
    page.evaluate(() => [
      ...(window as unknown as { longTasks: number[] }).longTasks,
    ]);

  /** The time from a key on the status strip to the second frame after it, five times, timed inside the page so the
   * test runner's own work is not counted. */
  async function shotChanges(page: Page) {
    await strip(page).focus();
    const times: number[] = [];
    for (let n = 2; n <= 6; n++) {
      times.push(
        await strip(page).evaluate(async (element) => {
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
      await expect(breadcrumb(page)).toContainText(
        `Shot ${String(n).padStart(3, "0")}`,
      );
    }
    return times;
  }

  test("a long film opens within budget and polling stays light", async ({
    page,
  }) => {
    const { film, openMs } = await openTimed(page);
    console.log(`open ${openMs} ms`);
    expect(openMs).toBeLessThan(BUDGET.openMs);
    // At least three polls with the film idle, counted as the recast's GET responses: each returns the whole
    // recast, as the service does. They are counted from the first poll after opening, which also waits for
    // opening's last render (a task of about 190 ms on the service's 2000-shot read, 2 November; in a probe, none
    // of the nine polls after it made one).
    let polls = 0;
    page.on("response", (response) => {
      if (
        response.request().method() === "GET" &&
        new URL(response.url()).pathname === `/api/recasts/${film.id}`
      )
        polls += 1;
    });
    await expect
      .poll(() => polls, { timeout: 10000 })
      .toBeGreaterThanOrEqual(1);
    polls = 0;
    const before = (await longTasks(page)).length;
    const writes = film.calls.length;
    await expect
      .poll(() => polls, { timeout: 10000 })
      .toBeGreaterThanOrEqual(3);
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
    expect(film.calls.length).toBe(writes);
  });

  test("in a long film the next shot is selected quickly and the last shot is reachable", async ({
    page,
  }) => {
    await openTimed(page);
    const times = await shotChanges(page);
    console.log(`shot change: ${times.map((t) => t.toFixed(0)).join(", ")} ms`);
    expect(Math.max(...times)).toBeLessThan(BUDGET.shotChangeMs);
    await page.keyboard.press("End");
    await expect(strip(page)).toHaveAttribute("aria-valuenow", "2000");
    await expect(breadcrumb(page)).toContainText("Shot 2000");
    // At the deepest zoom, at the end of the film, the timeline still draws a viewport's worth of items.
    await setZoom(page, 400);
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

  test("with the review queue open, a long film still changes shots within budget", async ({
    page,
  }) => {
    await openTimed(page);
    await page.keyboard.press("q");
    await expect(
      page.getByRole("list", { name: "Shots waiting for review" }),
    ).toBeVisible();
    const times = await shotChanges(page);
    console.log(
      `shot change, queue open: ${times.map((t) => t.toFixed(0)).join(", ")} ms`,
    );
    expect(Math.max(...times)).toBeLessThan(BUDGET.shotChangeMs);
  });
});
