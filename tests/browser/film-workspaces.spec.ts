import type { Page } from "@playwright/test";
import { seedDirect, openMovie } from "./direct-fixture";
import type { components } from "../../frontend/src/shared/api.generated";
import {
  expect,
  openFilm,
  recastCommands,
  test,
  type RecastWorld,
  type Scene,
} from "./recast-world";

// ADR 0017 U4c: a film conforms from its trackbar and from the Export workspace; the movie behind it is never
// exported from a film. The recast is the service's own (tests/browser_recast.py), whose conforms wait for the spec.

/** Mara (the user's picture) in every other 2 s shot at 25 fps; her first two shots are rendered. */
const FILM: Scene = {
  shots: 6,
  length: 50,
  rate: [25, 1],
  characters: ["Mara", "Elias"],
  targets: ["confirmed", null],
  rendered: 3,
};
test.use({ scene: FILM });

async function conformSheet(page: Page) {
  await page
    .getByRole("toolbar", { name: "Recast" })
    .getByRole("button", { name: /^Conform: / })
    .click();
  return page.getByRole("dialog", { name: "Conform film" });
}

test("the trackbar's Conform step conforms the film's unreviewed drafts", async ({
  page,
  world,
}) => {
  const calls = recastCommands(page);
  await page.goto("/");
  await openFilm(page, world);
  const sheet = await conformSheet(page);
  await expect(sheet.getByLabel("Range", { exact: true })).toBeVisible();
  await sheet.getByLabel("Versions").selectOption("preview_unreviewed");
  await expect(sheet).toContainText("without approving them");
  await sheet.getByLabel("Codec").selectOption("prores");
  await sheet
    .getByRole("button", { name: "Conform film", exact: true })
    .click();
  await expect.poll(() => calls.at(-1)?.path).toBe(`/${world.id}/conform`);
  // The whole film, or the run's range when a run has one, as the sheet's Range starts.
  expect(calls.at(-1)?.body).toMatchObject({
    mode: "preview_unreviewed",
    codec: "prores",
    range: (await world.read()).run.range,
  });
  // The service started the conform; it waits for the spec, so it is still at work.
  await expect(sheet.getByRole("progressbar")).toBeVisible();
  await expect
    .poll(async () => (await world.read()).conforms.map((c) => c.mode))
    .toEqual(["preview_unreviewed"]);
});

test.describe("Mara's first shot has a chosen version", () => {
  test("Export with a film open conforms the film, never the movie", async ({
    page,
    world,
  }) => {
    const shot = (await world.read()).shots[0];
    await world.elsewhere("POST", `/shots/${shot.id}/choice`, {
      version: shot.versions[0].id,
    });
    const calls = recastCommands(page);
    const exports: string[] = [];
    page.on("request", (request) => {
      if (/\/api\/v1\/(movies|exports)\/.*export/.test(request.url()))
        exports.push(request.url());
    });
    await page.goto("/");
    await openFilm(page, world);
    await page
      .getByRole("navigation", { name: "Workspaces" })
      .getByRole("button", { name: "Export", exact: true })
      .click();
    const main = page.locator(".film-export");
    await expect(main.getByRole("heading")).toContainText(world.film.name);
    await main
      .getByRole("button", { name: "Conform film", exact: true })
      .click();
    await expect.poll(() => calls.at(-1)?.path).toBe(`/${world.id}/conform`);
    expect(calls.at(-1)?.body).toMatchObject({
      mode: "chosen",
      codec: "h264",
    });
    // The page sent the conform; once the service has started it, it finishes with a film.
    await expect.poll(async () => (await world.read()).conforms.length).toBe(1);
    await world.settle("recast_conform");
    await expect(
      main.getByRole("button", { name: "Open result in Library" }),
    ).toBeVisible({ timeout: 8000 });
    expect(exports).toEqual([]);
  });
});

/** Prepare's Replace character on the world's film, at a shot analysed from frame 100 (in Shot 003). */
async function openInRecast(page: Page, world: RecastWorld) {
  await page
    .getByRole("button", { name: "Replace character…", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByLabel("Source clip")
    .selectOption(world.film.asset_id);
  await page
    .getByRole("dialog")
    .getByLabel("Analysed shot")
    .selectOption("recast-entry-analysis");
  await page
    .getByRole("button", { name: "Open in Recast", exact: true })
    .click();
}

async function prepare(page: Page, request: Parameters<typeof seedDirect>[0]) {
  const data = await seedDirect(request);
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
  return data;
}

// Moved from recast.spec.ts (U5a): Prepare's "Open in Recast" now opens the film document at the shot.
test("Prepare opens the film as the track workspace's document at the shot", async ({
  page,
  request,
  world,
}) => {
  const calls = recastCommands(page);
  await prepare(page, request);
  await openInRecast(page, world);
  await expect(page.locator(".timeline-heading")).toContainText(
    world.film.name,
  );
  await expect(
    page
      .getByRole("navigation", { name: "Workspaces" })
      .getByRole("button", { name: "Direct", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  await expect(page.locator(".workspace-breadcrumb")).toContainText("Shot 003");
  expect(calls.filter((c) => c.path === "")).toHaveLength(0);
});

test.describe("a Library film with no recast", () => {
  test.use({ scene: { ...FILM, recast: false } });

  // ADR 0017 §1: Prepare's "Open in Recast" on a film that has no recast asks before its analysis, too.
  test("Prepare's Open in Recast asks before analysing it", async ({
    page,
    request,
    world,
  }) => {
    const calls = recastCommands(page);
    const data = await prepare(page, request);
    await openInRecast(page, world);
    const ask = page.getByRole("dialog", { name: "Analyse this film?" });
    await ask.getByRole("button", { name: "Cancel", exact: true }).click();
    // Cancel leaves the movie where it was.
    await expect(ask).toHaveCount(0);
    expect(calls.filter((c) => c.path === "")).toHaveLength(0);
    await expect(page.locator(".timeline-heading")).toContainText(
      data.movie.title,
    );
    await openInRecast(page, world);
    const created = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname === "/api/recasts" &&
        r.request().method() === "POST",
    );
    await ask.getByRole("button", { name: "Analyse film" }).click();
    await expect(page.locator(".timeline-heading")).toContainText(
      world.film.name,
    );
    // The film's analysis finishes, so its shots are known.
    const id = ((await (await created).json()) as { id: string }).id;
    const settled = await page.request.post(
      `/api/test/recast-world/${id}/settle`,
      { headers: world.headers, data: { kind: "film_analysis" } },
    );
    expect(settled.ok(), await settled.text()).toBeTruthy();
    const creates = calls.filter((c) => c.path === "");
    expect(creates).toHaveLength(1);
    expect(creates[0].method).toBe("POST");
    expect(creates[0].body).toEqual({
      source_asset_id: world.film.asset_id,
      request_key: expect.any(String),
    });
    // The film opens at the analysed shot (frame 100 lies in Shot 003).
    await expect(page.locator(".workspace-breadcrumb")).toContainText(
      "Shot 003",
    );
  });
});
