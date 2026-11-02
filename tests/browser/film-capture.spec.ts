import type { Page } from "@playwright/test";
import { expect, openFilm, sharedRecast, test } from "./recast-world";
import {
  compFrames,
  compPlayhead,
  compScene,
  compShot,
} from "./fixtures/one-track-comp";

// Screenshots of the film view for design review (ADR 0017); run with GAIAVFX_CAPTURE=1. Captures go to
// .cache/film-capture/ and are not test evidence. The film is the service's own recast, seeded once for this file
// (tests/browser/recast-world.ts sharedRecast).
test.skip(!process.env.GAIAVFX_CAPTURE, "set GAIAVFX_CAPTURE=1 to capture");

/** Mara and Elias (the user's and Gaia's pictures) and The courier, who looks like Mara, take 135 shots of 2 s in turn
 * at 25 fps; two faces belong to no one. Mara's shots among the first twelve were rendered twice, her Shots 013 and
 * 022 have a gap, and a run over the first two minutes is paused. */
const share = sharedRecast({
  shots: 135,
  length: 50,
  rate: [25, 1],
  characters: ["Mara", "Elias", "The courier"],
  targets: ["confirmed", "proposed", null],
  similar: [[0, 2]],
  unknown: 2,
  rendered: 12,
  takes: 2,
  states: { 12: "gap", 21: "gap" },
  run: { state: "paused", range: { start: 0, end: 3000 } },
});

/** The shared film, open in `theme`. */
async function open(page: Page, theme: "dark" | "light") {
  const film = await share(page);
  await page.addInitScript((value) => {
    localStorage.setItem(
      "gaiavfx-preferences",
      JSON.stringify({ state: { theme: value }, version: 1 }),
    );
  }, theme);
  await page.goto("/");
  await openFilm(page, film);
}

for (const theme of ["dark", "light"] as const)
  test(`film view, ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width: 1586, height: 992 });
    await page.emulateMedia({ colorScheme: theme });
    await open(page, theme);
    await page.getByRole("button", { name: "Zoom in" }).click();
    await page.getByRole("button", { name: "Zoom in" }).click();
    await page.getByRole("button", { name: "Zoom in" }).click();
    await page.getByRole("button", { name: "Recast lanes" }).click();
    await page.waitForTimeout(500);
    await page.screenshot({ path: `.cache/film-capture/${theme}.png` });
  });

// The hero capture for the Impeccable gate: the comp state framed like the comp, on the Recast test world's film. The
// comp is the app's 1440x900 screenshot rendered at 110%, so the capture is 1442x902 CSS px at a 1.1 device scale
// (1586x992 pixels). The playhead is on the comp's frame (04:33:00, shot 047) about 300 px into the lanes at 0.75 px a
// frame, the Recast lanes open.
test.describe("comp state", () => {
  const shareComp = sharedRecast(compScene);
  test.use({ viewport: { width: 1442, height: 902 }, deviceScaleFactor: 1.1 });
  test("film view, comp state", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
    await page.addInitScript(() => {
      localStorage.setItem(
        "gaiavfx-preferences",
        // The comp's inspector is 375 px, as if the user had dragged the seam; the product default stays 420 px.
        JSON.stringify({
          state: {
            theme: "dark",
            layouts: {
              direct: {
                mode: "default",
                left: 218,
                right: 375,
                monitor: 245,
                rightTab: "properties",
                leftTab: "project",
              },
            },
          },
          version: 1,
        }),
      );
    });
    const film = await shareComp(page);
    await page.goto("/");
    await openFilm(page, film);
    await page.getByRole("tab", { name: "Properties" }).click();
    const shot = film.seeded.shots[compShot];
    await page
      .locator(".film-project")
      .getByRole("button", {
        name: new RegExp(`^Shot ${String(compShot + 1).padStart(3, "0")}`),
      })
      .click();
    const playhead = page.getByRole("slider", { name: "Playhead" });
    await playhead.focus();
    for (let i = 0; i < compPlayhead - shot.start; i++)
      await page.keyboard.press("ArrowRight");
    await expect(playhead).toHaveAttribute(
      "aria-valuenow",
      String(compPlayhead),
    );
    const lanes = page.getByRole("button", { name: "Recast lanes" });
    if ((await lanes.getAttribute("aria-expanded")) !== "true")
      await lanes.click();
    const perFrame = 0.75 / 1.1;
    const scroll = page.locator(".timeline-scroll");
    const available = await scroll.evaluate((el) => el.clientWidth - 220 - 20);
    await page
      .getByLabel("Timeline zoom")
      .fill(String(((perFrame * compFrames) / available).toFixed(1)));
    await scroll.evaluate(
      (el, left) => {
        el.scrollLeft = left;
      },
      (compPlayhead - 300 / 0.75) * perFrame,
    );
    await page
      .locator(".film-project")
      .getByRole("button", { name: /^Assets|^Pictures/ })
      .click();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: ".impeccable/review/hero-repro.png" });
  });
});

// The surfaces added after CP1 and usability round 1, for the Impeccable finish review: the Review bin with the
// queue, the Cast bin with its "Same person?" list, the shot card and the ComfyUI banner, in both themes.
for (const theme of ["dark", "light"] as const)
  test(`film view surfaces after round 1, ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    // The test service has no ComfyUI to ask: the capture shows the film waiting for it.
    await page.route("**/api/v1/settings/comfy", (route) =>
      route.fulfill({
        json: { connected: false, error: "Connection refused" },
      }),
    );
    await open(page, theme);
    await page.getByRole("tab", { name: "Properties" }).click();
    await page.keyboard.press("q");
    await page.keyboard.press("2");
    await page.waitForTimeout(500);
    await page.screenshot({ path: `.cache/film-capture/review-${theme}.png` });
    await page
      .locator(".film-project")
      .getByRole("button", { name: /^Attention/ })
      .click();
    await page.waitForTimeout(300);
    await page.screenshot({
      path: `.cache/film-capture/attention-${theme}.png`,
    });
    await page.keyboard.press("q");
    await page
      .locator(".film-project")
      .getByRole("button", { name: /^Cast/ })
      .click();
    await page
      .locator(".film-project")
      .getByRole("button", { name: /^Mara/ })
      .first()
      .click();
    await page.waitForTimeout(500);
    await page.screenshot({ path: `.cache/film-capture/cast-${theme}.png` });
  });
