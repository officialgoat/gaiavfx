import { test, expect, type Page } from "@playwright/test";
import { filmAsset, mockRecast, type FilmRate } from "./recast-fixtures";
import { seedDirect, openMovie } from "./direct-fixture";

// ADR 0017 U3b (Fable review #3, P2-4): what the timeline renderer adds for a film document. Ticks are windowed at any
// length and zoom, the ruler counts the film's rational rate, the Recast tool track stays in view, lanes twirl by
// keyboard, items draw by their width, and every item opens its own menu from the keyboard. A movie has none of it.

async function openFilm(page: Page) {
  await page.getByRole("button", { name: "File", exact: true }).click();
  await page.getByRole("menuitem", { name: /Open film/ }).click();
  await page
    .getByRole("dialog", { name: "Open film" })
    .getByRole("button", { name: new RegExp(filmAsset.name) })
    .first()
    .click();
  await expect(page.locator(".timeline-heading")).toContainText(filmAsset.name);
}

async function film(page: Page, count = 40, rate?: FilmRate) {
  const mock = await mockRecast(page, count, rate);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  return mock;
}

const ticks = (page: Page) =>
  page
    .locator(".ruler-scale > span")
    .evaluateAll((els) => els.map((el) => el.textContent ?? ""));

/** Sets the zoom slider as a pointer drag would (the slider's own input event). */
async function setZoom(page: Page, zoom: number) {
  await page
    .getByRole("slider", { name: "Timeline zoom" })
    .evaluate((element: HTMLInputElement, value) => {
      Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )!.set!.call(element, String(value));
      element.dispatchEvent(new Event("input", { bubbles: true }));
    }, zoom);
}

/** The zoom at which one 50-frame fixture shot is `pixels` wide in a film of `frames` frames. */
async function zoomForShotWidth(page: Page, pixels: number, frames: number) {
  const available = await page
    .locator(".timeline-scroll")
    .evaluate((el) => Math.max(240, el.clientWidth - 220 - 20));
  return (pixels * frames) / (50 * available);
}

test("ruler ticks stay few at every zoom and scroll of a 2000-shot film", async ({
  page,
}) => {
  await film(page, 2000);
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

for (const [rate, words] of [
  [[24000, 1001], "23.976 fps"],
  [[30000, 1001], "29.97 fps"],
] as [FilmRate, string][])
  test(`the ruler counts whole seconds at ${words}`, async ({ page }) => {
    await film(page, 40, rate);
    await expect(page.locator(".timeline-fps")).toHaveText(words);
    await page.getByRole("tab", { name: "Properties" }).click();
    await page.getByRole("button", { name: "Shot 002", exact: true }).click();
    await expect(page.locator(".film-properties")).toContainText(words);
    for (const zoom of [1, 6, 40]) {
      await setZoom(page, zoom);
      const labels = await ticks(page);
      expect(labels.length).toBeGreaterThan(2);
      // Every tick sits on a whole second of the rational timebase, in ascending order.
      expect(labels.every((t) => /^\d\d:\d\d:\d\d:00$/.test(t))).toBe(true);
      const seconds = labels.map((t) => {
        const [h, m, s] = t.split(":").map(Number);
        return h * 3600 + m * 60 + s;
      });
      expect(seconds).toEqual([...seconds].sort((a, b) => a - b));
      expect(new Set(seconds).size).toBe(seconds.length);
    }
  });

test("the Recast tool track stays in view while the tracks scroll", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 600 });
  await film(page);
  await page.getByRole("button", { name: "Recast lanes" }).click();
  const scroller = page.locator(".timeline-scroll");
  const range = await scroller.evaluate(
    (el) => el.scrollHeight - el.clientHeight,
  );
  expect(range).toBeGreaterThan(20);
  const pinned = page.locator(".timeline-pinned");
  const shots = page.locator('.track-row[data-row="shots"]');
  const before = [
    (await pinned.boundingBox())!.y,
    (await shots.boundingBox())!.y,
  ];
  await scroller.evaluate((el) => (el.scrollTop = el.scrollHeight));
  await expect
    .poll(async () => (await shots.boundingBox())!.y)
    .toBeLessThan(before[1] - range + 2);
  expect(Math.abs((await pinned.boundingBox())!.y - before[0])).toBeLessThan(1);
});

test("a track's lanes twirl open and closed by keyboard, without acting on the shot", async ({
  page,
}) => {
  const mock = await film(page);
  const twirl = page.getByRole("button", { name: "Recast lanes" });
  await expect(twirl).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator(".sub-row")).toHaveCount(0);
  const before = mock.calls.length;
  await twirl.focus();
  await page.keyboard.press("Enter");
  await expect(twirl).toHaveAttribute("aria-expanded", "true");
  await expect(twirl).toBeFocused();
  const lanes = await page
    .locator(".sub-row .track-label")
    .evaluateAll((els) => els.map((el) => el.textContent?.trim() ?? ""));
  expect(lanes.length).toBeGreaterThan(0);
  expect(lanes.every((name) => name.startsWith("Mara · "))).toBe(true);
  await page.keyboard.press("Space");
  await expect(twirl).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator(".sub-row")).toHaveCount(0);
  // Enter on a focused button presses that button; it is not the film's Accept & next.
  expect(mock.calls.slice(before)).toEqual([]);
});

test("Shift+F10 opens the menu of the focused shot or cast item", async ({
  page,
}) => {
  await film(page);
  const shot = page.getByRole("button", { name: "Shot 002", exact: true });
  await shot.focus();
  await page.keyboard.press("Shift+F10");
  await expect(
    page.getByRole("menuitem", { name: /^Accept and go to next/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("menuitem", { name: /^Exclude shot/ }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu")).toHaveCount(0);
  // The closed menu returns focus to the shot; moving on before that would let the return close the next menu.
  await expect(shot).toBeFocused();
  const appearance = page
    .locator('[data-row="cast-character-1"] .cast-item')
    .first();
  await appearance.focus();
  await page.keyboard.press("Shift+F10");
  for (const name of ["Rename…", "Merge with…", "Hide character"])
    await expect(
      page.getByRole("menuitem", { name: new RegExp(`^${name}`) }),
    ).toBeVisible();
  await page.keyboard.press("Escape");
});

test("items draw by their width: strips below 8 px, bare boxes below 24 px, posters above", async ({
  page,
}) => {
  await film(page, 2000);
  const shots = page.locator('.track-row[data-row="shots"]');
  await expect(shots.locator(".span-strip")).toHaveCount(1);
  await expect(shots.locator(".shot-item")).toHaveCount(0);
  await setZoom(page, await zoomForShotWidth(page, 14, 100000));
  await expect(shots.locator(".span-strip")).toHaveCount(0);
  await expect(shots.locator(".shot-item").first()).toBeVisible();
  await expect(shots.locator(".shot-item img")).toHaveCount(0);
  expect(
    await page.locator(".timeline-canvas .track-item").count(),
  ).toBeLessThan(600);
  await setZoom(page, await zoomForShotWidth(page, 60, 100000));
  await expect(shots.locator(".shot-item img").first()).toBeVisible();
  expect(
    await page.locator(".timeline-canvas .track-item").count(),
  ).toBeLessThan(300);
});

test("a movie draws none of the film's timeline parts", async ({
  page,
  request,
}) => {
  const seeded = await seedDirect(request);
  await openMovie(page, seeded.movie.title);
  await expect(page.getByRole("button", { name: "Go to start" })).toBeVisible();
  for (const part of [
    ".timeline-bar",
    ".timeline-pinned",
    ".work-area",
    ".work-area-band",
    ".twirl",
    ".span-strip",
    ".status-strip",
    ".navigator-zoom",
  ])
    await expect(page.locator(part)).toHaveCount(0);
  await expect(page.locator(".timeline-ruler .track-label")).toContainText(
    "Frames",
  );
});
