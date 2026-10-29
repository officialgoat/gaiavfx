import { test, expect, type Page } from "@playwright/test";
import { filmAsset, mockRecast } from "./recast-fixtures";
import { seedDirect, openMovie } from "./direct-fixture";
import type { components } from "../../frontend/src/shared/api.generated";

// ADR 0017 U4c: a film conforms from its trackbar and from the Export workspace; the movie behind it is never
// exported from a film.

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

test("the trackbar's Conform step conforms the film's choices", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  await page
    .getByRole("toolbar", { name: "Recast" })
    .getByRole("button", { name: /^Conform: / })
    .click();
  const sheet = page.getByRole("dialog", { name: "Conform film" });
  await expect(sheet.getByLabel("Range", { exact: true })).toBeVisible();
  await sheet.getByLabel("Versions").selectOption("preview_unreviewed");
  await expect(sheet).toContainText("without approving them");
  await sheet.getByLabel("Codec").selectOption("prores");
  await sheet
    .getByRole("button", { name: "Conform film", exact: true })
    .click();
  await expect.poll(() => mock.calls.at(-1)?.path).toBe("/recast-1/conform");
  expect(mock.calls.at(-1)?.body).toMatchObject({
    mode: "preview_unreviewed",
    codec: "prores",
    range: null,
  });
  await expect(sheet.getByRole("progressbar")).toHaveAttribute("value", "0.4");
});

test("Export with a film open conforms the film, never the movie", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  const exports: string[] = [];
  page.on("request", (request) => {
    if (/\/api\/v1\/(movies|exports)\/.*export/.test(request.url()))
      exports.push(request.url());
  });
  await page.goto("/");
  await openFilm(page);
  await page
    .getByRole("navigation", { name: "Workspaces" })
    .getByRole("button", { name: "Export", exact: true })
    .click();
  const main = page.locator(".film-export");
  await expect(main.getByRole("heading")).toContainText(filmAsset.name);
  await main.getByRole("button", { name: "Conform film", exact: true }).click();
  await expect.poll(() => mock.calls.at(-1)?.path).toBe("/recast-1/conform");
  expect(mock.calls.at(-1)?.body).toMatchObject({
    mode: "chosen",
    codec: "h264",
  });
  mock.value.conforms[0] = {
    ...mock.value.conforms[0],
    state: "succeeded",
    progress: 1,
    asset_id: "conformed-film",
  };
  await expect(
    main.getByRole("button", { name: "Open result in Library" }),
  ).toBeVisible({ timeout: 8000 });
  expect(exports).toEqual([]);
});

// Moved from recast.spec.ts (U5a): Prepare's "Open in Recast" now opens the film document at the shot.
test("Prepare opens the film as the track workspace's document at the shot", async ({
  page,
  request,
}) => {
  const data = await seedDirect(request);
  const mock = await mockRecast(page);
  mock.existing();
  const analysis: components["schemas"]["ReplacementPeopleAnalysisDTO"] = {
    operation_id: "recast-entry-analysis",
    state: "succeeded",
    report: {
      native: [1920, 800],
      shot_start: 100,
      shot_end: 150,
      fps: 25,
      tracks: [],
    },
  };
  await page.route("**/api/production/people-analyses?*", (route) =>
    route.fulfill({ json: [analysis] }),
  );
  await openMovie(page, data.movie.title);
  await page
    .getByRole("navigation", { name: "Workspaces" })
    .getByRole("button", { name: "Prepare", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Replace character…", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByLabel("Source clip")
    .selectOption(filmAsset.id);
  await page
    .getByRole("dialog")
    .getByLabel("Analysed shot")
    .selectOption(analysis.operation_id);
  await page
    .getByRole("button", { name: "Open in Recast", exact: true })
    .click();
  await expect(page.locator(".timeline-heading")).toContainText(filmAsset.name);
  await expect(
    page
      .getByRole("navigation", { name: "Workspaces" })
      .getByRole("button", { name: "Direct", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  await expect(page.locator(".workspace-breadcrumb")).toContainText("Shot 003");
  expect(mock.calls.filter((c) => c.path === "")).toHaveLength(0);
});

// ADR 0017 §1: Prepare's "Open in Recast" on a film that has no recast asks before its analysis, too.
test("Prepare's Open in Recast asks before analysing a new film", async ({
  page,
  request,
}) => {
  const data = await seedDirect(request);
  const mock = await mockRecast(page);
  const analysis: components["schemas"]["ReplacementPeopleAnalysisDTO"] = {
    operation_id: "recast-entry-analysis",
    state: "succeeded",
    report: {
      native: [1920, 800],
      shot_start: 100,
      shot_end: 150,
      fps: 25,
      tracks: [],
    },
  };
  await page.route("**/api/production/people-analyses?*", (route) =>
    route.fulfill({ json: [analysis] }),
  );
  await openMovie(page, data.movie.title);
  await page
    .getByRole("navigation", { name: "Workspaces" })
    .getByRole("button", { name: "Prepare", exact: true })
    .click();
  const openInRecast = async () => {
    await page
      .getByRole("button", { name: "Replace character…", exact: true })
      .click();
    await page
      .getByRole("dialog")
      .getByLabel("Source clip")
      .selectOption(filmAsset.id);
    await page
      .getByRole("dialog")
      .getByLabel("Analysed shot")
      .selectOption(analysis.operation_id);
    await page
      .getByRole("button", { name: "Open in Recast", exact: true })
      .click();
  };
  await openInRecast();
  const ask = page.getByRole("dialog", { name: "Analyse this film?" });
  await ask.getByRole("button", { name: "Cancel", exact: true }).click();
  // Cancel leaves the movie where it was.
  await expect(ask).toHaveCount(0);
  expect(mock.calls.filter((c) => c.path === "")).toHaveLength(0);
  await expect(page.locator(".timeline-heading")).toContainText(
    data.movie.title,
  );
  await openInRecast();
  await ask.getByRole("button", { name: "Analyse film" }).click();
  await expect(page.locator(".timeline-heading")).toContainText(filmAsset.name);
  const creates = mock.calls.filter((c) => c.path === "");
  expect(creates).toHaveLength(1);
  expect(creates[0].method).toBe("POST");
  expect(creates[0].body).toEqual({
    source_asset_id: filmAsset.id,
    request_key: expect.any(String),
  });
  // The film opens at the analysed shot (frame 100 lies in Shot 003).
  await expect(page.locator(".workspace-breadcrumb")).toContainText("Shot 003");
});
