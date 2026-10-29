import { test, expect, type Page } from "@playwright/test";
import { filmAsset, mockRecast } from "./recast-fixtures";

// ADR 0017 U3c: the film's rows, the Recast tool track and the trackbar, on the mocked recast.

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

const trackbar = (page: Page) => page.getByRole("toolbar", { name: "Recast" });

test("status words, takes and cast lanes come from the recast", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  const pinned = page.locator(".timeline-pinned");
  const cells = pinned.locator(".status-cell");
  await expect(cells).toHaveCount(mock.value.shots.length);
  for (const [index, shot] of mock.value.shots.slice(0, 11).entries()) {
    const label = await cells.nth(index).getAttribute("aria-label");
    expect(label).toMatch(
      new RegExp(`^Shot ${String(shot.index + 1).padStart(3, "0")}: \\S`),
    );
    await expect(cells.nth(index)).toHaveAttribute("title", label!);
  }
  await expect(
    page.locator('[data-row^="cast-"] .target-chip').first(),
  ).toContainText(/Mapped|Proposed|No target/);
  await expect(page.locator(".target-chip.proposed")).toContainText("Proposed");
  await expect(page.locator(".take-item").first()).toBeVisible();
  // Versions are letters only: no asset name of a version appears anywhere.
  await expect(page.locator("body")).not.toContainText(
    "PRIVATE SEED ARM ROUND",
  );
});

test("selecting a shot moves the playhead; seeking selects the shot under it", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("button", { name: "Shot 002", exact: true }).click();
  const playhead = page.getByRole("slider", { name: "Playhead" });
  await expect(playhead).toHaveAttribute("aria-valuenow", "50");
  await expect(page.locator(".workspace-breadcrumb")).toContainText("Shot 002");
  await playhead.focus();
  await page.keyboard.press("End");
  await expect(page.locator(".workspace-breadcrumb")).toContainText(
    `Shot ${String(mock.value.shots.length).padStart(3, "0")}`,
  );
});

test("the work area follows I, O and Clear", async ({ page }) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("button", { name: "Shot 003", exact: true }).click();
  await page.keyboard.press("i");
  const range = trackbar(page).getByRole("group", { name: "Work area" });
  await expect(range.locator("summary")).toHaveAttribute(
    "title",
    "In 00:00:04:00 · Out 00:01:20:00",
  );
  await expect(range.locator("summary")).toContainText("00:00:04");
  await expect(page.locator(".work-area")).toHaveCount(1);
  await range.locator("summary").click();
  await range.getByRole("button", { name: "Clear the work area" }).click();
  await expect(range).toContainText("Whole film");
  await expect(page.locator(".work-area")).toHaveCount(0);
});

test("a failed analysis shows its banner and a running render shows in the status bar", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  mock.value.analysis = {
    ...mock.value.analysis,
    state: "failed",
    error: "Face analysis stopped.",
  };
  mock.value.run = {
    ...mock.value.run,
    state: "running",
    renders_used: 3,
    render_budget: 10,
  };
  await page.goto("/");
  await openFilm(page);
  await expect(page.locator("#film-analysis")).toContainText(
    "Face analysis stopped.",
  );
  await expect(
    page.getByRole("button", { name: "Retry analysis" }),
  ).toBeVisible();
  await expect(page.locator(".status-task")).toContainText(
    "Recast · Running · 3/10 renders",
  );
  await expect(trackbar(page).locator(".trackbar-main button")).toHaveText(
    "Pause",
  );
});

test("opening a film from Library shows the track workspace", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await page.getByRole("button", { name: "Library", exact: true }).click();
  await openFilm(page);
  await expect(
    page.getByRole("button", { name: "Shot 001", exact: true }),
  ).toBeVisible();
});

test("600 shots: one status strip with a slider reaches the last shot", async ({
  page,
}) => {
  const mock = await mockRecast(page, 600);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  const strip = page.getByRole("slider", { name: "Film position" });
  await expect(strip).toBeVisible();
  await expect(page.locator(".timeline-pinned .status-cell")).toHaveCount(0);
  await strip.focus();
  await page.keyboard.press("End");
  await expect(strip).toHaveAttribute("aria-valuenow", "600");
  await expect(page.locator(".workspace-breadcrumb")).toContainText("Shot 600");
});

test("the run is driven by commands: trackbar and Render menu share them", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  const main = trackbar(page).locator(".trackbar-main button");
  await expect(main).toHaveText("Start");
  await main.click();
  await expect.poll(() => mock.calls.at(-1)?.path).toBe("/recast-1/run");
  expect(mock.calls.at(-1)?.body).toMatchObject({ mode: "draft" });
  await expect(main).toHaveText("Pause");
  const menu = page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Render", exact: true });
  await menu.click();
  await expect(
    page.getByRole("menuitem", { name: /^Start run/ }),
  ).toHaveAttribute("aria-disabled", "true");
  await page.getByRole("menuitem", { name: /^Pause run/ }).click();
  await expect.poll(() => mock.calls.at(-1)?.path).toBe("/recast-1/run/pause");
  await expect(main).toHaveText("Resume");
  await menu.click();
  await page.getByRole("menuitem", { name: /^Cancel run/ }).click();
  await expect.poll(() => mock.calls.at(-1)?.path).toBe("/recast-1/run/cancel");
  await expect(main).toHaveText("Start");
});
