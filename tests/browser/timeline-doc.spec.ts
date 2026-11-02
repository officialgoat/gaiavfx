import type { Page } from "@playwright/test";
import { seedDirect, openMovie } from "./direct-fixture";
import {
  expect,
  openFilm,
  recastCommands,
  test,
  type RecastWorld,
} from "./recast-world";

// ADR 0017 U3b (Fable review #3, P2-4): what the timeline renderer adds for a film document. Ticks are windowed at any
// length and zoom (film-scale.spec.ts covers the long film), the ruler counts the film's rational rate, the Recast
// tool track stays in view, lanes twirl by keyboard, and every item opens its own menu from the keyboard. A movie has
// none of it. The recast is the service's own (tests/browser_recast.py).

async function start(page: Page, world: RecastWorld) {
  await page.goto("/");
  await openFilm(page, world);
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

for (const [rate, words] of [
  [[24000, 1001], "23.976 fps"],
  [[30000, 1001], "29.97 fps"],
] as [[number, number], string][])
  test.describe(`Mara's forty shots at ${words}`, () => {
    test.use({ scene: { shots: 40, length: 50, rate } });

    test(`the ruler counts whole seconds at ${words}`, async ({
      page,
      world,
    }) => {
      await start(page, world);
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
  });

test.describe("Mara (the user's picture), Elias and The courier are seen in all forty shots", () => {
  /** The first two shots were rendered once. */
  test.use({
    scene: {
      shots: 40,
      length: 50,
      rate: [25, 1],
      characters: ["Mara", "Elias", "The courier"],
      targets: ["confirmed", null, null],
      ensemble: 40,
      rendered: 2,
    },
  });

  test("the Recast tool track stays in view while the tracks scroll", async ({
    page,
    world,
  }) => {
    await page.setViewportSize({ width: 1440, height: 600 });
    await start(page, world);
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
    expect(Math.abs((await pinned.boundingBox())!.y - before[0])).toBeLessThan(
      1,
    );
  });

  test("a track's lanes twirl open and closed by keyboard, without acting on the shot", async ({
    page,
    world,
  }) => {
    const calls = recastCommands(page);
    await start(page, world);
    const twirl = page.getByRole("button", { name: "Recast lanes" });
    await expect(twirl).toHaveAttribute("aria-expanded", "false");
    await expect(page.locator(".sub-row")).toHaveCount(0);
    await twirl.focus();
    await page.keyboard.press("Enter");
    await expect(twirl).toHaveAttribute("aria-expanded", "true");
    await expect(twirl).toBeFocused();
    // One lane per mapped character's part: Mara is the only one mapped.
    const lanes = await page
      .locator(".sub-row .track-label")
      .evaluateAll((els) => els.map((el) => el.textContent?.trim() ?? ""));
    expect(lanes.length).toBeGreaterThan(0);
    expect(lanes.every((name) => name.startsWith("Mara · "))).toBe(true);
    await page.keyboard.press("Space");
    await expect(twirl).toHaveAttribute("aria-expanded", "false");
    await expect(page.locator(".sub-row")).toHaveCount(0);
    // Enter on a focused button presses that button; it is not the film's Accept & next.
    expect(calls).toEqual([]);
  });

  test("Shift+F10 opens the menu of the focused shot or cast item", async ({
    page,
    world,
  }) => {
    await start(page, world);
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
    // Elias's appearance in a shot.
    const appearance = page
      .locator(`[data-row="cast-${world.characters[1]}"] .cast-item`)
      .first();
    await appearance.focus();
    await page.keyboard.press("Shift+F10");
    for (const name of ["Rename…", "Merge with…", "Hide character"])
      await expect(
        page.getByRole("menuitem", { name: new RegExp(`^${name}`) }),
      ).toBeVisible();
    await page.keyboard.press("Escape");
  });
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
