import type { Page } from "@playwright/test";
import {
  expect,
  expectBlind,
  openFilm,
  recastCommands,
  test,
} from "./recast-world";

// ADR 0017 U3c: the film's rows, the Recast tool track and the trackbar. The recast is the service's own
// (tests/browser_recast.py); the 600-shot status strip is in film-scale.spec.ts.

const trackbar = (page: Page) => page.getByRole("toolbar", { name: "Recast" });
const breadcrumb = (page: Page) => page.locator(".workspace-breadcrumb");

test.describe("Mara (the user's picture), Elias (Gaia's) and The courier take the shots in turn", () => {
  /** The first two shots were rendered once. */
  test.use({
    scene: {
      characters: ["Mara", "Elias", "The courier"],
      targets: ["confirmed", "proposed", null],
      rendered: 2,
    },
  });

  test("status words, takes and cast lanes come from the recast", async ({
    page,
    world,
  }) => {
    await page.goto("/");
    await openFilm(page, world);
    const pinned = page.locator(".timeline-pinned");
    const cells = pinned.locator(".status-cell");
    await expect(cells).toHaveCount(world.shots.length);
    for (let index = 0; index < world.shots.length; index++) {
      const label = await cells.nth(index).getAttribute("aria-label");
      expect(label).toMatch(
        new RegExp(`^Shot ${String(index + 1).padStart(3, "0")}: \\S`),
      );
      await expect(cells.nth(index)).toHaveAttribute("title", label!);
    }
    await expect(
      page.locator('[data-row^="cast-"] .target-chip').first(),
    ).toContainText(/Mapped|Proposed|No target/);
    await expect(page.locator(".target-chip.proposed")).toContainText(
      "Proposed",
    );
    await expect(page.locator(".take-item").first()).toBeVisible();
    // Versions are letters only: no asset name of a version appears anywhere.
    await expectBlind(page);
  });

  test("selecting a shot moves the playhead; seeking selects the shot under it", async ({
    page,
    world,
  }) => {
    const shots = (await world.read()).shots;
    await page.goto("/");
    await openFilm(page, world);
    // Shot 004 is Mara's; Elias's picture is only proposed, so his shots are excluded.
    await page.getByRole("button", { name: "Shot 004", exact: true }).click();
    const playhead = page.getByRole("slider", { name: "Playhead" });
    await expect(playhead).toHaveAttribute(
      "aria-valuenow",
      String(shots[3].start),
    );
    await expect(breadcrumb(page)).toContainText("Shot 004");
    await playhead.focus();
    await page.keyboard.press("End");
    await expect(breadcrumb(page)).toContainText(
      `Shot ${String(shots.length).padStart(3, "0")}`,
    );
  });

  test("opening a film from Library shows the track workspace", async ({
    page,
    world,
  }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Library", exact: true }).click();
    await openFilm(page, world);
    await expect(
      page.getByRole("button", { name: "Shot 001", exact: true }),
    ).toBeVisible();
  });
});

test.describe("Mara's eight 2 s shots", () => {
  test.use({ scene: { shots: 8, length: 50, rate: [25, 1] } });

  test("the work area follows I, O and Clear", async ({ page, world }) => {
    await page.goto("/");
    await openFilm(page, world);
    await page.getByRole("button", { name: "Shot 003", exact: true }).click();
    await page.keyboard.press("i");
    const range = trackbar(page).getByRole("group", { name: "Work area" });
    await expect(range.locator("summary")).toHaveAttribute(
      "title",
      "In 00:00:04:00 · Out 00:00:16:00",
    );
    await expect(range.locator("summary")).toContainText("00:00:04");
    await expect(page.locator(".work-area")).toHaveCount(1);
    await range.locator("summary").click();
    await range.getByRole("button", { name: "Clear the work area" }).click();
    await expect(range).toContainText("Whole film");
    await expect(page.locator(".work-area")).toHaveCount(0);
  });
});

test.describe("the film's analysis is still running", () => {
  test.use({ scene: { analysed: false } });

  test("a failed analysis shows its banner and offers a retry", async ({
    page,
    world,
  }) => {
    await page.goto("/");
    await openFilm(page, world);
    await world.settle("film_analysis", "failed");
    const { analysis } = await world.read();
    expect(analysis.state).toBe("failed");
    expect(analysis.error).toBeTruthy();
    await expect(page.locator("#film-analysis")).toContainText(analysis.error!);
    await expect(
      page.getByRole("button", { name: "Retry analysis" }),
    ).toBeVisible();
  });
});

test.describe("a whole-film run is rendering", () => {
  test.use({ scene: { run: { state: "running" } } });

  test("a running render shows in the status bar", async ({ page, world }) => {
    await page.goto("/");
    await openFilm(page, world);
    const { run } = await world.read();
    await expect(page.locator(".status-task")).toContainText(
      `Recast · Running · ${run.renders_used}/${run.render_budget} renders`,
    );
    await expect(trackbar(page).locator(".trackbar-main button")).toHaveText(
      "Pause",
    );
  });
});

test.describe("Mara's eight shots are planned", () => {
  test.use({ scene: { planned: true } });

  test("the run is driven by commands: trackbar and Render menu share them", async ({
    page,
    world,
  }) => {
    const calls = recastCommands(page);
    await page.goto("/");
    await openFilm(page, world);
    const main = trackbar(page).locator(".trackbar-main button");
    await expect(main).toHaveText("Start");
    await main.click();
    await expect.poll(() => calls.at(-1)?.path).toBe(`/${world.id}/run`);
    expect(calls.at(-1)?.body).toMatchObject({ mode: "draft" });
    await expect(main).toHaveText("Pause");
    const menu = page
      .getByRole("navigation", { name: "Application menus" })
      .getByRole("button", { name: "Render", exact: true });
    await menu.click();
    await expect(
      page.getByRole("menuitem", { name: /^Start run/ }),
    ).toHaveAttribute("aria-disabled", "true");
    await page.getByRole("menuitem", { name: /^Pause run/ }).click();
    await expect.poll(() => calls.at(-1)?.path).toBe(`/${world.id}/run/pause`);
    await expect(main).toHaveText("Resume");
    await menu.click();
    await page.getByRole("menuitem", { name: /^Cancel run/ }).click();
    await expect.poll(() => calls.at(-1)?.path).toBe(`/${world.id}/run/cancel`);
    await expect(main).toHaveText("Start");
  });
});
