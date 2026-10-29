import { test, expect } from "@playwright/test";
import { filmAsset, mockRecast } from "./recast-fixtures";
import {
  compMediaReady,
  compPlayhead,
  compShot,
  mockCompRecast,
} from "./fixtures/one-track-comp";

// Screenshots of the film view for design review (ADR 0017); run with GAIAVFX_CAPTURE=1. Captures go to
// .cache/film-capture/ and are not test evidence.
test.skip(!process.env.GAIAVFX_CAPTURE, "set GAIAVFX_CAPTURE=1 to capture");

for (const theme of ["dark", "light"] as const)
  test(`film view, ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width: 1586, height: 992 });
    await page.emulateMedia({ colorScheme: theme });
    const mock = await mockRecast(page, 135);
    mock.existing();
    await page.addInitScript((value) => {
      localStorage.setItem(
        "gaiavfx-preferences",
        JSON.stringify({ state: { theme: value }, version: 1 }),
      );
    }, theme);
    await page.goto("/");
    await page.getByRole("button", { name: "File", exact: true }).click();
    await page.getByRole("menuitem", { name: /Open film/ }).click();
    await page
      .getByRole("dialog", { name: "Open film" })
      .getByRole("button", { name: new RegExp(filmAsset.name) })
      .first()
      .click();
    await expect(page.locator(".timeline-heading")).toContainText(
      filmAsset.name,
    );
    await page.getByRole("button", { name: "Zoom in" }).click();
    await page.getByRole("button", { name: "Zoom in" }).click();
    await page.getByRole("button", { name: "Zoom in" }).click();
    await page.getByRole("button", { name: "Recast lanes" }).click();
    await page.waitForTimeout(500);
    await page.screenshot({ path: `.cache/film-capture/${theme}.png` });
  });

// The hero capture for the Impeccable gate: the comp state framed like the comp. The comp is the app's 1440x900
// screenshot rendered at 110%, so the capture is 1442x902 CSS px at a 1.1 device scale (1586x992 pixels). The playhead
// is on the comp's frame (04:33:00, shot 047) about 300 px into the lanes at 0.75 px a frame, the Recast lanes open.
test.describe("comp state", () => {
  test.use({ viewport: { width: 1442, height: 902 }, deviceScaleFactor: 1.1 });
  test("film view, comp state", async ({ page }) => {
    test.skip(!compMediaReady(), "run one_track_comp_media.py first");
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
    const mock = await mockCompRecast(page);
    await page.goto("/");
    await page.getByRole("button", { name: "File", exact: true }).click();
    await page.getByRole("menuitem", { name: /Open film/ }).click();
    await page
      .getByRole("dialog", { name: "Open film" })
      .getByRole("button", { name: /Tears of Steel|tears_of_steel/ })
      .first()
      .click();
    await expect(page.locator(".timeline-heading")).toContainText(
      "Tears of Steel",
    );
    await page.getByRole("tab", { name: "Properties" }).click();
    const shot = mock.value.shots[compShot];
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
      .fill(String(((perFrame * 17620) / available).toFixed(1)));
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
    const mock = await mockRecast(page, 135);
    mock.existing();
    mock.comfy.connected = false;
    mock.value.run.range = { start: 0, end: 3000 };
    await page.addInitScript((value) => {
      localStorage.setItem(
        "gaiavfx-preferences",
        JSON.stringify({ state: { theme: value }, version: 1 }),
      );
    }, theme);
    await page.goto("/");
    await page.getByRole("button", { name: "File", exact: true }).click();
    await page.getByRole("menuitem", { name: /Open film/ }).click();
    await page
      .getByRole("dialog", { name: "Open film" })
      .getByRole("button", { name: new RegExp(filmAsset.name) })
      .first()
      .click();
    await expect(page.locator(".timeline-heading")).toContainText(
      filmAsset.name,
    );
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
