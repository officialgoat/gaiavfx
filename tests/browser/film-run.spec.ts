import { test, expect, type Page } from "@playwright/test";
import { filmAsset, mockRecast } from "./recast-fixtures";
import { seedDirect } from "./direct-fixture";

// U5a: the run behaviours the Recast page's tests protected (recast.spec.ts), on the film document in the track
// workspace (ADR 0017; retained regression coverage rows 2, 3, 5, 18, 19, 20, 22 and 28). The run controls
// are registered commands shared by the trackbar's main action, its "Run settings" overflow and the Render menu.

test.use({ reducedMotion: "reduce" });

type Mock = Awaited<ReturnType<typeof mockRecast>>;

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
const main = (page: Page) => trackbar(page).locator(".trackbar-main button");
const mainReason = (page: Page) => trackbar(page).locator("#trackbar-reason");
const step = (page: Page, name: string) =>
  trackbar(page).getByRole("button", { name: new RegExp(`^${name}: `) });
const breadcrumb = (page: Page) => page.locator(".workspace-breadcrumb");
const card = (page: Page) => page.locator(".film-properties");
const program = (page: Page) =>
  page.getByRole("region", { name: "Program monitor" });
const renderMenu = (page: Page) =>
  page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Render", exact: true });

/** The trackbar's "Run settings" overflow, opened. */
async function runSettings(page: Page) {
  const more = trackbar(page).locator("details.trackbar-more");
  if (!(await more.evaluate((d) => (d as HTMLDetailsElement).open)))
    await more.getByLabel("Run settings", { exact: true }).click();
  await expect(more).toHaveAttribute("open", "");
  return more;
}

/** A film key, once the last Recast action has saved (a saving film disables its steps with "Saving…"). */
async function keys(page: Page, key: string) {
  await expect(step(page, "Cast")).toBeEnabled();
  await page.keyboard.press(key);
}

/** No version's asset name or operation reaches the page: text, titles, labels, alt text or values. */
async function expectBlind(page: Page) {
  const leaks = await page.evaluate(() => {
    const secrets = ["PRIVATE SEED ARM ROUND", "hidden-operation"];
    const found: string[] = [];
    const text = document.body.innerText;
    for (const secret of secrets)
      if (text.includes(secret)) found.push(`text: ${secret}`);
    for (const element of Array.from(document.querySelectorAll("*")))
      for (const attribute of Array.from(element.attributes))
        for (const secret of secrets)
          if (attribute.value.includes(secret))
            found.push(`${element.tagName} ${attribute.name}: ${secret}`);
    return found;
  });
  expect(leaks).toEqual([]);
}

const lastPath = (mock: Mock) => mock.calls.at(-1)?.path;

// Row 2: run progress, its names and its controls while a run is active.
function runningFixture(mock: Mock) {
  mock.value.run = {
    ...mock.value.run,
    state: "running",
    current_shot_id: "shot-5",
    shots_done: 1,
    shots_total: 5,
    renders_used: 4,
    render_budget: 7,
  };
  mock.value.shots.forEach((s, i) => {
    s.status = i < 4 ? "gap" : i === 4 ? "needs_review" : "rendering";
    s.included = true;
  });
}

test("run progress separates gaps, names controls and keeps Pause and Cancel available", async ({
  page,
}) => {
  const mock = await mockRecast(page, 9);
  mock.existing();
  runningFixture(mock);
  await page.goto("/");
  await openFilm(page);
  // (1) (2): every step is named with its count; Render counts renders by design, and the shots done stay in the
  // run status below.
  for (const name of [
    "Analyse: complete · 9 shots",
    "Cast: 1 of 3 mapped",
    "Plan: 9 shots",
    "Render: 4 of 7 renders used",
    "Review: 1 waiting",
    "Conform: 0 films",
  ])
    await expect(
      trackbar(page).getByRole("button", { name, exact: true }),
    ).toBeVisible();
  // (5): while running, the main action is Pause; Start is not offered at all.
  await expect(main(page)).toHaveText("Pause");
  await expect(main(page)).toBeEnabled();
  await expect(
    trackbar(page).getByRole("button", { name: "Start", exact: true }),
  ).toHaveCount(0);
  // (3) (4) (6)
  const settings = await runSettings(page);
  await expect(
    settings.getByRole("combobox", { name: "Mode", exact: true }),
  ).toBeDisabled();
  const status = settings.getByRole("status");
  await expect(status).toHaveText(
    "Running · shot 6 · 1 of 5 shots · 4 of 7 renders used",
  );
  await expect(
    settings.getByRole("button", { name: "Cancel run", exact: true }),
  ).toBeEnabled();
  // (7), in the film's words: the chip names the gaps and goes to the first of them.
  await page.getByRole("button", { name: "Shot 005", exact: true }).click();
  await expect(breadcrumb(page)).toContainText("Shot 005");
  await trackbar(page)
    .getByRole("button", {
      name: "4 need attention: show them",
      exact: true,
    })
    .click();
  await expect(breadcrumb(page)).toContainText("Shot 001");
  await expectBlind(page);
  // (8) (9)
  await main(page).click();
  await expect.poll(() => lastPath(mock)).toBe("/recast-1/run/pause");
  await expect(status).toHaveText(
    "Paused · shot 6 · 1 of 5 shots · 4 of 7 renders used",
  );
  await expect(main(page)).toHaveText("Resume");
  await settings
    .getByRole("button", { name: "Cancel run", exact: true })
    .click();
  await expect.poll(() => mock.value.run.state).toBe("finished");
  await expect(main(page)).toHaveText("Start");
});

test("the needs-attention chip lists exactly the shots that need attention", async ({
  page,
}) => {
  const mock = await mockRecast(page, 9);
  mock.existing();
  runningFixture(mock);
  await page.goto("/");
  await openFilm(page);
  await trackbar(page)
    .getByRole("button", { name: /^4 need attention/ })
    .click();
  // The review queue (the Project panel's Review bin) opens on its Need attention filter, at the first of them.
  await expect(
    page.locator(".film-project .project-bins").getByRole("button", {
      name: /^Review/,
    }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    page
      .getByRole("group", { name: "Review filter" })
      .getByRole("button", { name: /^Attention 4$/ }),
  ).toHaveAttribute("aria-pressed", "true");
  const list = page.getByRole("list", { name: "Shots needing attention" });
  await expect(list.getByRole("listitem")).toHaveCount(4);
  for (const [index, shot] of ["001", "002", "003", "004"].entries()) {
    await expect(list.getByRole("listitem").nth(index)).toContainText(
      `Shot ${shot}`,
    );
    // Each says why: the per-shot gap of the Recast page's sheet.
    await expect(list.getByRole("listitem").nth(index)).toContainText(
      "The plan cannot cover this shot",
    );
  }
  await expect(breadcrumb(page)).toContainText("Shot 001");
  await expectBlind(page);
});

test("reload restores the film, its shot and the saved run settings; a movie opened instead stays open", async ({
  page,
  request,
}) => {
  const data = await seedDirect(request);
  const mock = await mockRecast(page, 9);
  mock.existing();
  mock.value.run = {
    ...mock.value.run,
    state: "paused",
    mode: "final",
    range: { start: 50, end: 400 },
    render_budget: 37,
    renders_used: 4,
  };
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.getByRole("button", { name: "Shot 007", exact: true }).click();
  await expect(
    card(page).getByRole("heading", { name: "Shot 007", exact: true }),
  ).toBeVisible();
  await page.reload();
  // (1) (2)
  await expect(page.locator(".timeline-heading")).toContainText(filmAsset.name);
  await expect(breadcrumb(page)).toContainText(`${filmAsset.name} · Shot 007`);
  await page.getByRole("tab", { name: "Properties" }).click();
  await expect(
    card(page).getByRole("heading", { name: "Shot 007", exact: true }),
  ).toBeVisible();
  // (3) (4)
  const settings = await runSettings(page);
  await expect(
    settings.getByRole("combobox", { name: "Mode", exact: true }),
  ).toHaveValue("final");
  await expect(settings.getByLabel("Render budget")).toHaveValue("37");
  await expectBlind(page);
  // (5): a film closes by opening a movie; after a reload the movie, not the film, is open.
  await page.getByRole("button", { name: "File", exact: true }).click();
  await page.getByRole("menuitem", { name: /Open movie/ }).click();
  await page
    .getByRole("dialog", { name: "Open movie", exact: true })
    .getByRole("button", { name: data.movie.title, exact: true })
    .click();
  await expect(
    page.locator(".cut-item").filter({ hasText: "f36" }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.locator(".cut-item").filter({ hasText: "f36" }),
  ).toBeVisible();
  await expect(breadcrumb(page)).toContainText(data.movie.title);
  await expect(breadcrumb(page)).not.toContainText(filmAsset.name);
  await expect(page.locator(".timeline-heading")).not.toContainText(
    filmAsset.name,
  );
  await expect(trackbar(page)).toHaveCount(0);
});

test("a retake announces its reserved work and keeps its status after reload", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  mock.existing();
  mock.value.shots[0].plan!.windows.push({
    ...mock.value.shots[0].plan!.windows[0],
    index: 1,
  });
  await page.goto("/");
  await openFilm(page);
  // (1): Final mode, Q goes to the first waiting shot (Shot 001), R retakes it.
  const settings = await runSettings(page);
  await settings
    .getByRole("combobox", { name: "Mode", exact: true })
    .selectOption("final");
  await expect(page.locator(".timeline-pinned .tool-header")).toContainText(
    "Final",
  );
  await page.getByRole("button", { name: "Shot 003", exact: true }).click();
  await expect(breadcrumb(page)).toContainText("Shot 003");
  await keys(page, "q");
  await expect(breadcrumb(page)).toContainText("Shot 001");
  await keys(page, "r");
  // Usability round 2 (C9): Retake asks first, naming what it reserves; Enter confirms.
  const confirm = page.getByRole("dialog", { name: "Retake" });
  await expect(confirm).toContainText(
    "Render Shot 001 again in Final mode: 2 windows, up to 2 renders of the 20 left.",
  );
  expect(mock.calls.some((c) => c.path.endsWith("/retake"))).toBe(false);
  await page.keyboard.press("Enter");
  // (2)
  const notice = page.locator(".film-message.notice");
  await expect(notice).toHaveText(
    /^Shot 001: retake queued in Final mode · 2 windows · up to 8 renders\s*Dismiss$/,
  );
  expect(mock.calls.at(-1)?.body).toMatchObject({ mode: "final" });
  // (4)
  await notice.getByRole("button", { name: "Dismiss", exact: true }).click();
  await expect(notice).toHaveCount(0);
  // (5) (6)
  const status = page.getByRole("button", {
    name: "Shot 001: Rendering (retake)",
    exact: true,
  });
  await expect(status).toBeVisible();
  await page.reload();
  await expect(status).toBeVisible();
  // (7)
  expect(mock.calls.filter((c) => c.path.endsWith("/retake"))).toHaveLength(1);
  await expectBlind(page);
});

test("Q enters a visible review queue and leaves it after a retake", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  // The review queue is the Project panel's Review bin (ADR 0017 §8); Q opens it and returns to the bin before.
  const bins = page.locator(".film-project .project-bins");
  const queue = bins.getByRole("button", { name: /^Review/ });
  await expect(queue).toHaveAttribute("aria-pressed", "false");
  await keys(page, "q");
  await expect(queue).toHaveAttribute("aria-pressed", "true");
  const list = page.getByRole("list", { name: "Shots waiting for review" });
  await expect(list.getByRole("listitem").first()).toContainText("Shot 001");
  await expect(breadcrumb(page)).toContainText("Shot 001");
  await keys(page, "r");
  await page
    .getByRole("dialog", { name: "Retake" })
    .getByRole("button", { name: "Retake", exact: true })
    .click();
  await expect(page.locator(".film-message.notice")).toContainText(
    "Shot 001: retake queued",
  );
  // The retaken shot leaves the waiting shots but keeps its row while the list is open, with its new state.
  await expect(list.getByRole("listitem").first()).toContainText("Shot 001");
  await expect(list.getByRole("listitem").first()).toContainText("Rendering");
  await expect(queue).toContainText("1");
  await keys(page, "q");
  await expect(queue).toHaveAttribute("aria-pressed", "false");
  await expect(bins.getByRole("button", { name: /^Shots/ })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(
    page.getByRole("button", {
      name: "Shot 001: Rendering (retake)",
      exact: true,
    }),
  ).toBeVisible();
  await expectBlind(page);
});

test("analysis holds Start until it completes; tracks move and characters merge from the cast", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  mock.value.analysis = {
    operation_id: "analysis",
    state: "running",
    stage: "faces",
    done: 12,
    total: 40,
    error: null,
  };
  await page.goto("/");
  await openFilm(page);
  // (1) (2), and row 2's opacity check on a disabled main action.
  await expect(page.locator("#film-analysis")).toContainText("faces 12 of 40");
  await expect(main(page)).toHaveText("Start");
  await expect(main(page)).toBeDisabled();
  await expect(main(page)).toHaveAttribute(
    "title",
    "Wait for analysis to finish.",
  );
  await expect(mainReason(page)).toHaveText("Wait for analysis to finish.");
  await expect(main(page)).toHaveCSS("opacity", "0.5");
  // (3): once analysis succeeds, the banner gives way to the Analyse step.
  mock.value.analysis = {
    ...mock.value.analysis,
    state: "succeeded",
    stage: "done",
    done: 40,
  };
  await expect(page.locator("#film-analysis")).toHaveCount(0);
  const analyse = trackbar(page).getByRole("button", {
    name: "Analyse: complete · 40 shots",
    exact: true,
  });
  // Once complete, the step counts shots, not the analysis's stages (usability round 1, P3 T1-F7).
  await expect(analyse).toContainText("40 shots");
  await expect(analyse.locator("svg")).toHaveCount(1);
  await expect(analyse).toHaveAttribute("title", "Analysis is complete.");
  await expect(main(page)).toBeEnabled();
  // (7): Mara's face tracks; the first moves to Elias.
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.locator('[data-row="cast-character-0"] .cast-header').click();
  await card(page).getByRole("button", { name: "Face tracks…" }).click();
  const drawer = page.getByRole("dialog", { name: "Mara · tracks" });
  await expect(drawer.getByAltText("Face track").first()).toBeVisible();
  await drawer.getByRole("checkbox").first().check();
  await drawer.getByLabel("Move to").selectOption("character-1");
  await drawer.getByRole("button", { name: "Move 1 track" }).click();
  await expect
    .poll(
      () =>
        mock.calls.find((c) => c.path.endsWith("/tracks/move"))?.body.track_ids,
    )
    .toEqual(["track-0-0"]);
  expect(mock.calls.find((c) => c.path.endsWith("/tracks/move"))?.body.to).toBe(
    "character-1",
  );
  await expect(drawer).toHaveCount(0);
  // (8): The courier's lane menu › Merge with… › Merge into Elias, then confirm.
  await page
    .locator('[data-row="cast-character-2"] .cast-header')
    .click({ button: "right" });
  await page.getByRole("menuitem", { name: /^Merge with…/ }).click();
  const sheet = page.getByRole("dialog", { name: "Merge characters" });
  await sheet
    .getByRole("group", { name: "Merge into" })
    .getByRole("button", { name: "Merge into Elias", exact: true })
    .click();
  await expect(sheet).toContainText("Merge The courier into Elias?");
  expect(mock.calls.some((c) => c.path.endsWith("/merge"))).toBe(false);
  await sheet
    .getByRole("button", { name: "Merge characters", exact: true })
    .click();
  await expect.poll(() => lastPath(mock)).toBe("/recast-1/characters/merge");
  expect(mock.calls.at(-1)?.body).toMatchObject({
    sources: ["character-2"],
    into: "character-1",
  });
  await expect(page.locator('[data-row="cast-character-2"]')).toHaveCount(0);
  await expectBlind(page);
});

test("work area, estimate, run controls, review keys and a conform of the work area", async ({
  page,
}) => {
  test.slow();
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("tab", { name: "Properties" }).click();
  // (1): I on Shot 002, O on Shot 004 (the fixture excludes it; its item says so).
  await page.getByRole("button", { name: "Shot 002", exact: true }).click();
  await keys(page, "i");
  await page
    .getByRole("button", { name: "Shot 004 (excluded)", exact: true })
    .click();
  await keys(page, "o");
  const range = trackbar(page).getByRole("group", { name: "Work area" });
  await expect(range.locator("summary")).toHaveAttribute(
    "title",
    "In 00:00:02:00 · Out 00:00:08:00",
  );
  // A changed work area asks to plan again before a run.
  await expect(main(page)).toHaveText("Update plan");
  // (2): Plan, then the estimate in Run settings.
  await step(page, "Plan").click();
  await expect
    .poll(() => mock.calls.find((c) => c.path.endsWith("/plan"))?.body.range)
    .toEqual({ start: 50, end: 200 });
  const settings = await runSettings(page);
  // Usability round 1 (B12, T3-F3): the estimate is for what Start would run, the work area in the chosen mode,
  // and states its renders.
  await expect(settings.locator(".recast-estimate")).toContainText(
    "Work area, Draft: 2 shots · 2 renders · ≈ 1.7–3.1 GPU h · 2.1 GB",
  );
  await settings.getByLabel("Mode").selectOption("final");
  await expect(settings.locator(".recast-estimate")).toContainText(
    "Work area, Final: 2 shots · 4 renders",
  );
  await settings.getByLabel("Mode").selectOption("draft");
  // (3)
  await expect(main(page)).toHaveText("Start");
  await main(page).click();
  await expect
    .poll(() => mock.calls.find((c) => c.path.endsWith("/run"))?.body.range)
    .toEqual({ start: 50, end: 200 });
  // (4): Pause, Resume (a new run request), Cancel.
  await expect(main(page)).toHaveText("Pause");
  await main(page).click();
  await expect.poll(() => lastPath(mock)).toBe("/recast-1/run/pause");
  await expect(main(page)).toHaveText("Resume");
  await expect(main(page)).toBeEnabled();
  await main(page).click();
  await expect.poll(() => lastPath(mock)).toBe("/recast-1/run");
  await expect(main(page)).toHaveText("Pause");
  await settings
    .getByRole("button", { name: "Cancel run", exact: true })
    .click();
  await expect.poll(() => lastPath(mock)).toBe("/recast-1/run/cancel");
  await expect.poll(() => mock.value.run.state).toBe("finished");
  // (6), as the user decided at CP1 (preview first; the Recast page's keys chose at once): 1 shows A and Enter
  // chooses it; Mod+Z takes it back; 0 shows the original and Enter keeps it; Mod+Z takes that back too.
  const choices = () =>
    mock.calls
      .filter((c) => c.path.endsWith("/choice"))
      .map((c) => [c.path, c.method, c.body]);
  const first = mock.value.revision;
  const choice = (version: string, step: number) => [
    "/recast-1/shots/shot-0/choice",
    "POST",
    { expected_revision: first + step, version },
  ];
  const heading = program(page).locator(".monitor-heading");
  await page.getByRole("button", { name: "Shot 001", exact: true }).click();
  await expect(breadcrumb(page)).toContainText("Shot 001");
  await keys(page, "1");
  await expect(heading).toContainText("Shot 001 · A · Unchosen");
  expect(choices()).toEqual([]);
  await keys(page, "Enter");
  await expect
    .poll(() => mock.value.shots[0].choice?.version)
    .toBe("version-0-A");
  await expect(breadcrumb(page)).toContainText("Shot 002");
  await keys(page, "Control+z");
  await expect.poll(() => mock.value.shots[0].choice).toBeNull();
  await expect(breadcrumb(page)).toContainText("Shot 001");
  await expect(heading).toContainText("Shot 001 · A · Unchosen");
  await keys(page, "0");
  await expect(heading).toContainText("Shot 001 · Original · Unchosen");
  expect(choices()).toEqual([choice("version-0-A", 0), choice("unchosen", 1)]);
  await keys(page, "Enter");
  await expect.poll(() => mock.value.shots[0].choice?.version).toBe("original");
  await keys(page, "Control+z");
  await expect.poll(() => mock.value.shots[0].choice).toBeNull();
  expect(choices()).toEqual([
    choice("version-0-A", 0),
    choice("unchosen", 1),
    choice("original", 2),
    choice("unchosen", 3),
  ]);
  // (7): Enter accepts what Program shows (still the original) and moves to the next waiting shot.
  await expect(heading).toContainText("Shot 001 · Original · Unchosen");
  await keys(page, "Enter");
  await expect.poll(() => mock.value.shots[0].choice?.version).toBe("original");
  await expect(breadcrumb(page)).toContainText("Shot 002");
  await expect(
    card(page).getByRole("heading", { name: "Shot 002", exact: true }),
  ).toBeVisible();
  // (8)
  const wipe = program(page).getByLabel("Before/after wipe position");
  await keys(page, "w");
  await expect(wipe).toHaveCount(0);
  await keys(page, "w");
  await expect(wipe).toBeVisible();
  await keys(page, "\\");
  await expect(program(page).locator(".monitor-heading")).toContainText(
    "Shot 002 · Original",
  );
  await keys(page, "\\");
  await expect(program(page).locator(".monitor-heading")).toContainText(
    "Shot 002 · A",
  );
  // (9)
  await expectBlind(page);
  // (10): the uncalibrated measurement is in its row's title; the unsupported check reads so.
  const rows = card(page).locator(".check-rows li");
  const identity = rows.filter({ hasText: "Identity" });
  await expect(identity).toContainText("Not calibrated");
  await expect(identity).toHaveAttribute(
    "title",
    "Measured 0.87. Identity scoring is not calibrated.",
  );
  await expect(rows.filter({ hasText: "Seam" })).toContainText("Unsupported");
  // (11): conform the work area as an unreviewed ProRes preview, then open the result in Library.
  await renderMenu(page).click();
  await page.getByRole("menuitem", { name: /^Conform film…/ }).click();
  const dialog = page.getByRole("dialog", { name: "Conform film" });
  await dialog.getByLabel("Versions").selectOption("preview_unreviewed");
  await expect(dialog).toContainText("without approving them");
  // B14: the range offers the work area with its times, and starts there.
  const rangeField = dialog.getByLabel("Range", { exact: true });
  await expect(rangeField).toHaveValue("run");
  await expect(rangeField.locator("option[value=run]")).toHaveText(
    "Work area, 00:00:02–00:00:08",
  );
  await dialog.getByLabel("Codec").selectOption("prores");
  await dialog
    .getByRole("button", { name: "Conform film", exact: true })
    .click();
  await expect.poll(() => lastPath(mock)).toBe("/recast-1/conform");
  expect(mock.calls.at(-1)?.body).toMatchObject({
    mode: "preview_unreviewed",
    codec: "prores",
    range: { start: 50, end: 200 },
  });
  await expect(dialog.getByRole("progressbar")).toHaveAttribute("value", "0.4");
  mock.value.conforms[0] = {
    ...mock.value.conforms[0],
    state: "succeeded",
    progress: 1,
    asset_id: "conform-result",
  };
  // B15: each conform row says what it covered and when.
  await expect(
    dialog.locator(".recast-conform-progress").first(),
  ).toContainText("Unreviewed preview · 00:00:02–00:00:08 ·");
  await expect(
    dialog.locator(".recast-conform-progress").first(),
  ).toContainText("· ProRes · succeeded · 100%");
  // A failed conform says why (B15).
  mock.value.conforms.push({
    ...mock.value.conforms[0],
    operation_id: "conform-2",
    state: "failed",
    asset_id: null,
    error:
      "Encoding failed. Conform again; if it fails again, the app log has the details.",
  });
  await expect(dialog).toContainText(
    "Encoding failed. Conform again; if it fails again, the app log has the details.",
  );
  await dialog
    .getByRole("button", { name: "Open result in Library" })
    .first()
    .click();
  const result = page.getByRole("dialog", {
    name: filmAsset.name,
    exact: true,
  });
  await expect(result).toBeVisible();
  // Round 2 (C13): the conformed film plays here, and its player is named so it can be found.
  await expect(result.locator("video")).toHaveAttribute(
    "aria-label",
    `Play ${filmAsset.name}`,
  );
  await result.getByRole("button", { name: "Close panel" }).click();
  await expect(
    page.getByRole("region", { name: "Creative library" }),
  ).toBeVisible();
});

test("600 shots draw a bounded number of items, reach the last shot, and a refused choice stays readable", async ({
  page,
}) => {
  await mockRecast(page, 600).then((mock) => mock.existing());
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("tab", { name: "Properties" }).click();
  // Items per row stand in for the page's sheet rows: drawn only near the viewport, whatever the shot count.
  const perRow = () =>
    page
      .locator(".timeline-canvas .track-row")
      .evaluateAll((rows) =>
        rows.map((row) => row.querySelectorAll(".track-item").length),
      );
  // (1): zoomed out, the rows are strips.
  for (const count of await perRow()) expect(count).toBeLessThan(30);
  // (2): the last shot.
  const strip = page.getByRole("slider", { name: "Film position" });
  await strip.press("End");
  await expect(strip).toHaveAttribute("aria-valuenow", "600");
  await expect(
    card(page).getByRole("heading", { name: "Shot 600", exact: true }),
  ).toBeVisible();
  // (3): zoomed in to single shots at the end of the film, the rows draw only what is near the viewport.
  await page.getByLabel("Timeline zoom").press("End");
  await expect(page.getByLabel("Timeline zoom")).toHaveValue("400");
  await page.locator(".timeline-scroll").evaluate((el) => {
    el.scrollLeft = el.scrollWidth;
  });
  const last = page.getByRole("button", { name: "Shot 600", exact: true });
  await expect(last).toBeVisible();
  await page.getByRole("button", { name: "Shot 001", exact: true }).waitFor({
    state: "detached",
  });
  await last.click();
  await expect(
    card(page).getByRole("heading", { name: "Shot 600", exact: true }),
  ).toBeVisible();
  const counts = await perRow();
  expect(Math.max(...counts)).toBeGreaterThan(0);
  for (const count of counts) expect(count).toBeLessThan(30);
  await expectBlind(page);
  // (4)
  await page.route("**/api/recasts/recast-1/shots/*/choice", (route) =>
    route.fulfill({
      status: 409,
      json: { detail: "The recast changed. Refresh and try again." },
    }),
  );
  // Accept saves the shown version; a refused save stays readable.
  await keys(page, "Enter");
  await expect(page.locator(".film-message[role='alert']")).toContainText(
    "Refresh and try again",
  );
});

test("the status strip selects shots by keyboard and pointer", async ({
  page,
}) => {
  await mockRecast(page, 600).then((mock) => mock.existing());
  await page.goto("/");
  await openFilm(page);
  const strip = page.getByRole("slider", { name: "Film position" });
  // (1) (2)
  await strip.press("End");
  await expect(strip).toHaveAttribute("aria-valuenow", "600");
  await expect(breadcrumb(page)).toContainText("Shot 600");
  await strip.press("Home");
  await expect(strip).toHaveAttribute("aria-valuenow", "1");
  await expect(breadcrumb(page)).toContainText("Shot 001");
  // (3): a click selects the shot under the pointer. Zoom in while the strip still stands for the shots, so one
  // shot is several pixels wide.
  const width = async () => (await strip.boundingBox())!.width;
  const shot = (await width()) / 600;
  const steps = Math.floor(Math.log(7.5 / shot) / Math.log(1.25));
  for (let i = 0; i < steps; i++)
    await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  const zoomed = (await width()) / 600;
  expect(zoomed).toBeGreaterThan(3);
  expect(zoomed).toBeLessThan(8);
  await expect(strip).toBeVisible();
  const box = (await strip.boundingBox())!;
  // The middle of Shot 002 (frames 50 to 100 of 30000); the strip starts in view, wider than the timeline.
  await page.mouse.click(
    box.x + (75 / 30000) * box.width,
    box.y + box.height / 2,
  );
  await expect(strip).toHaveAttribute("aria-valuenow", "2");
  await expect(breadcrumb(page)).toContainText("Shot 002");
  // (4)
  await expect(strip).toHaveAttribute(
    "aria-valuetext",
    "Shot 002: Needs review · 2 versions",
  );
  // Arrows step one shot.
  await strip.press("ArrowRight");
  await expect(strip).toHaveAttribute("aria-valuenow", "3");
  await expect(breadcrumb(page)).toContainText("Shot 003");
  await strip.press("ArrowLeft");
  await expect(strip).toHaveAttribute("aria-valuenow", "2");
  await expect(breadcrumb(page)).toContainText("Shot 002");
  await expectBlind(page);
});

test("at a few shots, each status cell selects its shot", async ({ page }) => {
  await mockRecast(page, 6).then((mock) => mock.existing());
  await page.goto("/");
  await openFilm(page);
  await expect(page.getByRole("slider", { name: "Film position" })).toHaveCount(
    0,
  );
  await page
    .getByRole("button", { name: "Shot 006: Planned", exact: true })
    .click();
  await expect(breadcrumb(page)).toContainText("Shot 006");
  await page
    .getByRole("button", {
      name: "Shot 002: Needs review · 2 versions",
      exact: true,
    })
    .click();
  await expect(breadcrumb(page)).toContainText("Shot 002");
  await expectBlind(page);
});

test("the run bar explains Plan states, pending estimates and budget coverage", async ({
  page,
}) => {
  // Each state waits for the film's next poll.
  test.slow();
  const mock = await mockRecast(page, 8);
  mock.existing();
  const target = mock.value.characters[0].target;
  mock.value.characters.forEach((c) => {
    c.target = null;
  });
  mock.value.estimate = null;
  mock.value.shots.forEach((s) => {
    s.status = "absent";
    s.plan = null;
    s.versions = [];
  });
  await page.goto("/");
  await openFilm(page);
  const plan = step(page, "Plan");
  const planShots = page.getByRole("menuitem", { name: /^Plan shots/ });
  // (1): nothing mapped.
  await expect(plan).toBeDisabled();
  await expect(plan).toHaveAttribute("title", "Map a character first");
  await renderMenu(page).click();
  await expect(planShots).toHaveAttribute("aria-disabled", "true");
  await expect(planShots).toContainText("Map a character first");
  await page.keyboard.press("Escape");
  await expect(main(page)).toHaveText("Start");
  await expect(main(page)).toBeDisabled();
  await expect(mainReason(page)).toHaveText(
    "Confirm a character mapping first.",
  );
  // (2): mapped, with two shots to plan.
  mock.value.characters[0].target = target;
  mock.value.shots[0].status = mock.value.shots[1].status = "unplanned";
  await expect(main(page)).toHaveText("Plan 2 shots");
  await expect(main(page)).toBeEnabled();
  await main(page).click();
  await expect.poll(() => lastPath(mock)).toBe("/recast-1/plan");
  mock.value.estimate = {
    shots: 2,
    windows: 28,
    windows_pending: 24,
    renders: 4,
    gpu_seconds_low: 0,
    gpu_seconds_high: 0,
    storage_bytes: 0,
    basis: "seed",
  };
  // (3): windows still analysing.
  const settings = await runSettings(page);
  const estimate = settings.locator(".recast-estimate");
  const budget = settings.getByLabel("Render budget");
  await expect(estimate).toContainText("Estimating… 4 of 28 windows analysed");
  await expect(budget).toHaveValue("20");
  await expect(main(page)).toHaveText("Start");
  await expect(main(page)).toBeDisabled();
  await expect(mainReason(page)).toHaveText(
    "Wait for window analyses to finish.",
  );
  // (4): planned; the budget is prefilled from the estimate.
  mock.value.shots[0].status = mock.value.shots[1].status = "planned";
  mock.value.estimate = {
    ...mock.value.estimate,
    windows_pending: 0,
    renders: 28,
  };
  await expect(budget).toHaveValue("28");
  await expect(plan).toBeDisabled();
  await expect(plan).toHaveAttribute("title", "All mapped shots are planned");
  await renderMenu(page).click();
  await expect(planShots).toHaveAttribute("aria-disabled", "true");
  await expect(planShots).toContainText("All mapped shots are planned");
  await page.keyboard.press("Escape");
  await expect(main(page)).toBeEnabled();
  // (5): a budget below the estimate says what it covers, and is kept when the estimate grows.
  await budget.fill("12");
  await expect(estimate).toContainText("Budget covers 12 of 28 renders");
  mock.value.estimate.renders = 32;
  await expect(estimate).toContainText("Budget covers 12 of 32 renders");
  await expect(budget).toHaveValue("12");
  // (6): the budget is cumulative: once renders are used, the words count what is left.
  mock.value.run.renders_used = 12;
  mock.value.run.state = "finished";
  await expect(estimate).toContainText(
    "All 12 budget renders are used; raise the budget to render more",
  );
  await expect(estimate).not.toContainText("Budget covers");
  await expectBlind(page);
});

// Replaces film-timeline.spec.ts › "the trackbar's main action and reasons match the Recast page": the expected
// words are literal for each state, so the check no longer needs the Recast page.

test("the trackbar names its steps with their counts", async ({ page }) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  for (const name of [
    "Analyse: complete · 40 shots",
    "Cast: 1 of 3 mapped",
    "Plan: 40 shots",
    "Render: 0 of 20 renders used",
    "Review: 8 waiting",
    "Conform: 0 films",
  ])
    await expect(
      trackbar(page).getByRole("button", { name, exact: true }),
    ).toBeVisible();
  await expectBlind(page);
});

const mainStates: {
  state: string;
  set?: (mock: Mock) => void;
  label: string;
  reason?: string;
}[] = [
  { state: "a mapped, planned film", label: "Start" },
  {
    state: "nothing mapped",
    set: (mock) =>
      mock.value.characters.forEach((c) => {
        c.target = null;
      }),
    label: "Start",
    reason: "Confirm a character mapping first.",
  },
  {
    state: "analysis running",
    set: (mock) => {
      mock.value.analysis = {
        ...mock.value.analysis,
        state: "running",
        stage: "faces",
        done: 1,
      };
    },
    label: "Start",
    reason: "Wait for analysis to finish.",
  },
  {
    state: "no estimate yet",
    set: (mock) => {
      mock.value.estimate = null;
    },
    label: "Start",
    reason: "Plan the film to get an estimate first.",
  },
  {
    state: "window analyses pending",
    set: (mock) => {
      mock.value.shots[0].status = "analysing";
    },
    label: "Start",
    reason: "Wait for window analyses to finish.",
  },
  {
    state: "an unsupported frame rate",
    set: (mock) => {
      mock.value.film.fps_status = "unsupported";
    },
    label: "Start",
    reason: "This film's frame rate is unsupported.",
  },
  {
    state: "two shots to plan",
    set: (mock) => {
      mock.value.shots[0].status = mock.value.shots[1].status = "unplanned";
    },
    label: "Plan 2 shots",
  },
  {
    state: "one shot to plan",
    set: (mock) => {
      mock.value.shots[0].status = "unplanned";
    },
    label: "Plan 1 shot",
  },
  {
    state: "a running run",
    set: (mock) => {
      mock.value.run.state = "running";
    },
    label: "Pause",
  },
  {
    state: "a paused run",
    set: (mock) => {
      mock.value.run.state = "paused";
    },
    label: "Resume",
  },
];

for (const { state, set, label, reason } of mainStates)
  test(`the trackbar's main action and reason: ${state}`, async ({ page }) => {
    const mock = await mockRecast(page);
    mock.existing();
    set?.(mock);
    await page.goto("/");
    await openFilm(page);
    await expect(main(page)).toHaveText(label);
    if (reason) {
      await expect(main(page)).toBeDisabled();
      await expect(main(page)).toHaveAttribute("title", reason);
      await expect(mainReason(page)).toHaveText(reason);
    } else {
      await expect(main(page)).toBeEnabled();
      await expect(main(page)).not.toHaveAttribute("title");
      await expect(mainReason(page)).toHaveCount(0);
    }
    await expectBlind(page);
  });

test("the trackbar's main action asks to plan again after the work area changes", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  await expect(main(page)).toHaveText("Start");
  await page.getByRole("button", { name: "Shot 003", exact: true }).click();
  await keys(page, "i");
  await expect(main(page)).toHaveText("Update plan");
  await expect(main(page)).toBeEnabled();
  await expect(mainReason(page)).toHaveCount(0);
  const settings = await runSettings(page);
  await expect(settings.locator(".recast-estimate")).toContainText(
    "Range changed — plan again.",
  );
  await main(page).click();
  await expect
    .poll(() => mock.calls.find((c) => c.path.endsWith("/plan"))?.body.range)
    .toEqual({ start: 100, end: 2000 });
  await expect(main(page)).toHaveText("Start");
  await expectBlind(page);
});

// Usability round 1 (B10, B11, B13, B14): the run's words name the state they refer to; a budget below the renders
// used is refused at the field; the work area survives a reload and says it follows shot boundaries.
test("run reasons name a paused run, the budget is checked at its field, and the work area survives a reload", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("button", { name: "Shot 003", exact: true }).click();
  await keys(page, "i");
  const range = trackbar(page).getByRole("group", { name: "Work area" });
  await range.locator("summary").click();
  await expect(
    range.getByRole("button", { name: /^Set In at the start of this shot/ }),
  ).toBeVisible();
  await expect(
    range.getByRole("button", { name: /^Set Out at the end of this shot/ }),
  ).toBeVisible();
  await expect(range).toContainText("The work area follows shot boundaries.");
  await expect(range.locator("summary")).toHaveAttribute(
    "title",
    "In 00:00:04:00 · Out 00:01:20:00",
  );
  // B13: kept per film in this browser.
  await page.reload();
  await openFilm(page);
  await expect(
    trackbar(page).getByRole("group", { name: "Work area" }).locator("summary"),
  ).toHaveAttribute("title", "In 00:00:04:00 · Out 00:01:20:00");
  // B10: a paused run says so on Start and Pause.
  mock.value.run = {
    ...mock.value.run,
    state: "paused",
    range: { start: 100, end: 2000 },
    renders_used: 6,
  };
  await expect(main(page)).toHaveText("Resume");
  const settings = await runSettings(page);
  await expect(settings.getByRole("status")).toContainText(
    "the run's range 00:00:04–00:01:20",
  );
  await renderMenu(page).click();
  await expect(
    page.getByRole("menuitem", { name: /^Start run/ }),
  ).toContainText(
    "The run is paused: Resume continues it, and Cancel run ends it.",
  );
  await page.keyboard.press("Escape");
  // B11: a budget below the renders used is refused beside the field, and Resume says why it cannot go on.
  const budget = settings.getByLabel("Render budget");
  await budget.fill("4");
  await expect(budget).toHaveAttribute("aria-invalid", "true");
  await expect(settings.getByRole("alert")).toHaveText(
    "The budget counts every render of this recast so far; it cannot be below the 6 already used.",
  );
  await expect(main(page)).toBeDisabled();
  await expect(mainReason(page)).toHaveText(
    "Raise the render budget to at least 6, the renders already used.",
  );
  await budget.fill("12");
  await expect(settings.getByRole("alert")).toHaveCount(0);
  await expect(main(page)).toBeEnabled();
  // A cleared work area (the whole film) is kept too, over the paused run's range (Astra R5).
  await settings.getByLabel("Run settings", { exact: true }).click();
  await trackbar(page)
    .getByRole("group", { name: "Work area" })
    .locator("summary")
    .click();
  await page.getByRole("button", { name: "Clear the work area" }).click();
  await page.reload();
  await openFilm(page);
  await expect(
    trackbar(page).getByRole("group", { name: "Work area" }).locator("summary"),
  ).toContainText("Whole film");
  await expectBlind(page);
});

// Usability round 1 (B4): with ComfyUI down, face checks, analysis and renders wait without an error of their own;
// the film names the cause and points to Settings, and says nothing while ComfyUI answers.
test("the film says when ComfyUI is not reachable and opens Settings", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  mock.comfy.connected = false;
  await page.goto("/");
  await openFilm(page);
  const banner = page.locator(".comfy-offline");
  await expect(banner).toContainText(
    "ComfyUI is not reachable, so face checks, analysis and renders wait until it is.",
  );
  await banner.getByRole("button", { name: "Open Settings" }).click();
  await expect(page.getByRole("dialog", { name: "Settings" })).toBeVisible();
  await page.keyboard.press("Escape");
  mock.comfy.connected = true;
  await page.reload();
  await openFilm(page);
  await expect(main(page)).toHaveText("Start");
  await expect(banner).toHaveCount(0);
  await expectBlind(page);
});

// CP3 (ADR 0017 amendment 11) replaces usability round 1's P3 note: with no movie open, the film's Chat tab holds the
// film's own conversation, and a film that reopens onto Chat stays there (film-chat.spec.ts covers the conversation).
test("with no movie open, the film's Chat tab holds the film's own conversation", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  // No movie in this library (the shared test backend keeps other tests' movies, and the first one opens).
  await page.route("**/api/direction/movies", (route) =>
    route.fulfill({ json: [] }),
  );
  // The recast is mocked, so its conversation list is too: no conversation yet.
  await page.route(
    `**/api/agent/sessions?recast_id=${mock.value.id}`,
    (route) => route.fulfill({ json: [] }),
  );
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("tab", { name: "Chat" }).click();
  await expect(
    page.getByRole("region", { name: "Film conversation" }),
  ).toBeVisible();
  await expect(page.locator(".film-chat-note")).toHaveCount(0);
  await expect(page.getByLabel("Message to Gaia")).toBeEnabled();
  await page.reload();
  await openFilm(page);
  await expect(page.getByRole("tab", { name: "Chat" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(
    page.getByRole("region", { name: "Film conversation" }),
  ).toBeVisible();
  await expectBlind(page);
});

// Usability round 1 (T3-F8): the Recast lane badge brings its settings forward in Properties.
test("the Recast badge shows Properties", async ({ page }) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("tab", { name: "Chat" }).click();
  await page.getByRole("button", { name: /^Recast\s*(Draft|Final)$/ }).click();
  await expect(page.getByRole("tab", { name: "Properties" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expectBlind(page);
});

// Finish verdict (22 October): at 1440x900 with the gaps chip showing, the trackbar's steps fold their counts before
// any step label is clipped.
test("at 1440x900 no trackbar step is clipped", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const mock = await mockRecast(page, 135);
  mock.existing();
  mock.value.run.range = { start: 0, end: 3000 };
  await page.goto("/");
  await openFilm(page);
  await expect(
    trackbar(page).getByRole("button", { name: /need attention/ }),
  ).toBeVisible();
  const steps = trackbar(page).locator(".trackbar-steps");
  const clipped = await steps.evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(clipped).toBeLessThanOrEqual(0);
  await expect(
    trackbar(page).getByRole("button", { name: /^Conform/ }),
  ).toContainText("Conform");
});

// Usability round 2 (C2, C3, C5, C10): a run that renders nothing says so; with ComfyUI down a busy shot waits for it;
// a work area set back to the run's range keeps Resume; Plan's reason names the work area.
test("a run without renders says so, busy shots wait for ComfyUI, and a restored work area keeps Resume", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  // C2: the run goes from running to finished with no new renders.
  mock.value.run = { ...mock.value.run, state: "running", renders_used: 4 };
  await expect(main(page)).toHaveText("Pause");
  mock.value.run = { ...mock.value.run, state: "finished" };
  await expect(page.locator(".film-message.notice")).toContainText(
    "The run finished without rendering: every planned shot in its range already has versions.",
  );
  // C3: with ComfyUI down, the rendering shot (Shot 008) reads as waiting for it.
  mock.comfy.connected = false;
  await page.reload();
  await openFilm(page);
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.getByRole("button", { name: "Shot 008", exact: true }).click();
  await expect(card(page)).toContainText(
    "Waiting for ComfyUI: Waiting for ComfyUI, which is not reachable",
    { timeout: 25_000 },
  );
  // C10: the work area is the planned range and holds only planned shots; the reason names it and the shots outside.
  mock.value.shots.forEach((s, i) => {
    if (i < 2) s.status = "planned";
    else if (s.status !== "excluded") s.status = "unplanned";
  });
  mock.value.run = { ...mock.value.run, range: { start: 0, end: 100 } };
  // A finished run does not poll: the reload reads the new statuses.
  await page.reload();
  await openFilm(page);
  await page.getByRole("button", { name: "Shot 001", exact: true }).click();
  await keys(page, "i");
  await page.getByRole("button", { name: "Shot 002", exact: true }).click();
  await keys(page, "o");
  await expect(step(page, "Plan")).toHaveAttribute(
    "title",
    /^Every mapped shot in the work area is planned; \d+ more shots outside it\. Clear the work area to plan them\.$/,
  );
  // C5: the work area set back to the paused run's range keeps Resume.
  mock.value.run = {
    ...mock.value.run,
    state: "paused",
    range: { start: 0, end: 100 },
    renders_used: 4,
  };
  await expect(main(page)).toHaveText("Resume");
  await trackbar(page)
    .getByRole("group", { name: "Work area" })
    .locator("summary")
    .click();
  await page.getByRole("button", { name: "Clear the work area" }).click();
  await page.getByRole("button", { name: "Shot 001", exact: true }).click();
  await keys(page, "i");
  await page.getByRole("button", { name: "Shot 002", exact: true }).click();
  await keys(page, "o");
  await expect(main(page)).toHaveText("Resume");
  await expect(main(page)).toBeEnabled();
  await expectBlind(page);
});

// Usability round 2 (T5-F7, T1-F6): the Cast step opens the Cast bin; Retake is not offered on a shot without versions.
test("the Cast step opens the Cast bin, and a shot without versions cannot be retaken", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("tab", { name: "Library" }).first().click();
  await step(page, "Cast").click();
  await expect(
    page
      .locator(".film-project .project-bins")
      .getByRole("button", { name: /^Cast/ }),
  ).toHaveAttribute("aria-pressed", "true");
  // Shot 003 never had versions.
  mock.value.shots[2].versions = [];
  mock.value.shots[2].status = "unplanned";
  await page.getByRole("button", { name: "Shot 003", exact: true }).click();
  await expect(breadcrumb(page)).toContainText("Shot 003");
  await page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Review", exact: true })
    .click();
  const retake = page.getByRole("menuitem", { name: /^Retake/ });
  await expect(retake).toHaveAttribute("aria-disabled", "true");
  await expect(retake).toContainText(
    "This shot has no versions yet: plan and render it first.",
  );
  await page.keyboard.press("Escape");
});

// D2 (ADR 0017, CP3): a trackbar step that cannot run stays focusable and says why under the bar, by keyboard or
// click; it runs nothing.
test("a disabled trackbar step says why when focused or clicked", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  mock.existing();
  mock.value.characters.forEach((c) => {
    c.target = null;
  });
  mock.value.shots.forEach((s) => {
    s.status = "absent";
    s.plan = null;
    s.versions = [];
  });
  await page.goto("/");
  await openFilm(page);
  const why = page.locator("#trackbar-step-reason");
  const plan = step(page, "Plan");
  await expect(plan).toBeDisabled();
  await plan.focus();
  await expect(why).toHaveText("Plan: Map a character first");
  await expect(plan).toHaveAttribute(
    "aria-describedby",
    "trackbar-step-reason",
  );
  // It paints on top: the element at its centre is the reason itself (Astra R-A: the ruler covered it). The line
  // ignores the pointer, so hit-testing needs it switched on for the check.
  expect(
    await why.evaluate((el: HTMLElement) => {
      el.style.pointerEvents = "auto";
      const box = el.getBoundingClientRect();
      const top = document.elementFromPoint(
        box.left + box.width / 2,
        box.top + box.height / 2,
      );
      el.style.pointerEvents = "";
      return !!top && el.contains(top);
    }),
  ).toBe(true);
  // A real mouse click (forced past the disabled check) shows the reason and runs nothing.
  await page.keyboard.press("Escape");
  await expect(why).toHaveText("");
  await plan.click({ force: true });
  await expect(why).toHaveText("Plan: Map a character first");
  expect(mock.calls.some((c) => c.path.endsWith("/plan"))).toBe(false);
  await plan.focus();
  await page.keyboard.press("Tab");
  await expect(step(page, "Render")).toBeFocused();
  await expect(why).toHaveText("Render: No shot is rendering.");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  await expect(why).toHaveText("Review: No shot is waiting for review.");
  // Nothing ran: the review queue stays closed.
  await expect(
    page
      .locator(".film-project .project-bins")
      .getByRole("button", { name: /^Review/ }),
  ).toHaveAttribute("aria-pressed", "false");
  await page.keyboard.press("Escape");
  await expect(why).toHaveText("");
  // An enabled step shows no reason.
  await step(page, "Plan").focus();
  await expect(why).not.toHaveText("");
  await step(page, "Cast").focus();
  await expect(why).toHaveText("");
  await expectBlind(page);
});

// B16 (ADR 0017, CP3): one work area; when it differs from a paused run's range, the ruler also draws the run's range,
// and Resume says how to continue.
test("a paused run's own range shows on the ruler when the work area differs", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  mock.value.run = {
    ...mock.value.run,
    state: "paused",
    range: { start: 0, end: 100 },
    renders_used: 4,
  };
  await page.goto("/");
  await openFilm(page);
  const span = page.locator(".ruler-span");
  // The work area starts on the run's range: nothing extra on the ruler.
  await expect(main(page)).toHaveText("Resume");
  await expect(span).toHaveCount(0);
  await page.getByRole("button", { name: "Shot 003", exact: true }).click();
  await keys(page, "i");
  await keys(page, "o");
  await expect(span).toHaveText("Paused run");
  await expect(span).toHaveAttribute(
    "title",
    "The paused run's range: In 00:00:00:00 · Out 00:00:04:00",
  );
  await expect(main(page)).toBeDisabled();
  await expect(mainReason(page)).toHaveText(
    "The work area differs from the paused run's range (In 00:00:00 · Out 00:00:04, shown on the ruler). Set it back to resume, or plan the work area.",
  );
  // Back on the run's range: the span goes and Resume works.
  await page.getByRole("button", { name: "Shot 001", exact: true }).click();
  await keys(page, "i");
  await page.getByRole("button", { name: "Shot 002", exact: true }).click();
  await keys(page, "o");
  await expect(span).toHaveCount(0);
  await expect(main(page)).toBeEnabled();
  await expectBlind(page);
});

// Astra R-A: a whole-film paused run and a work area written as the film's full span are the same range: no span,
// and Resume works.
test("a whole-film paused run matches a work area set to the whole film", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  mock.value.run = {
    ...mock.value.run,
    state: "paused",
    range: null,
    renders_used: 4,
  };
  await page.goto("/");
  await openFilm(page);
  await expect(main(page)).toHaveText("Resume");
  // I on the first shot keeps Out at the film's end: the whole film, now written as a span instead of "whole".
  await page.getByRole("button", { name: "Shot 001", exact: true }).click();
  await keys(page, "i");
  await expect
    .poll(() =>
      page.evaluate(() =>
        localStorage.getItem("gaiavfx:recast-range:recast-1"),
      ),
    )
    .toBe(JSON.stringify({ start: 0, end: mock.value.film.frame_count }));
  await expect(page.locator(".ruler-span")).toHaveCount(0);
  await expect(main(page)).toHaveText("Resume");
  await expect(main(page)).toBeEnabled();
});
