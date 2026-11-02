import type { Page } from "@playwright/test";
import { seedDirect } from "./direct-fixture";
import {
  card,
  expect,
  expectBlind,
  notice,
  openFilm,
  readAs,
  recastCommands,
  reopen,
  test,
  type RecastWorld,
  type Scene,
} from "./recast-world";

// U5a: the run behaviours the Recast page's tests protected (recast.spec.ts), on the film document in the track
// workspace (ADR 0017; retained regression coverage rows 2, 3, 5, 18, 19, 20, 22 and 28). The run controls
// are registered commands shared by the trackbar's main action, its "Run settings" overflow and the Render menu. The
// recast is the service's own (tests/browser_recast.py): its run, counts, estimates and refusals are Python's, and its
// renders wait until a spec settles them. The 135- and 600-shot cases are in film-scale.spec.ts.

test.use({ reducedMotion: "reduce" });

const trackbar = (page: Page) => page.getByRole("toolbar", { name: "Recast" });
const main = (page: Page) => trackbar(page).locator(".trackbar-main button");
const mainReason = (page: Page) => trackbar(page).locator("#trackbar-reason");
const step = (page: Page, name: string) =>
  trackbar(page).getByRole("button", { name: new RegExp(`^${name}: `) });
const breadcrumb = (page: Page) => page.locator(".workspace-breadcrumb");
const program = (page: Page) =>
  page.getByRole("region", { name: "Program monitor" });
const renderMenu = (page: Page) =>
  page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Render", exact: true });
const workArea = (page: Page) =>
  trackbar(page).getByRole("group", { name: "Work area" });
const plural = (count: number, word: string) =>
  `${count} ${count === 1 ? word : `${word}s`}`;

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

/** The run's status line in Run settings, from the service's read of the run. */
async function runStatus(world: RecastWorld, state: "Running" | "Paused") {
  const read = await world.read();
  const run = read.run;
  const current = read.shots.find((s) => s.id === run.current_shot_id);
  return (
    `${state}${current ? ` · shot ${current.index + 1}` : ""} · ${run.shots_done} of ${plural(run.shots_total, "shot")}` +
    ` · ${run.renders_used} of ${plural(run.render_budget, "render")} used`
  );
}

async function start(page: Page, world: RecastWorld) {
  await page.goto("/");
  await openFilm(page, world);
}

// Row 2: run progress, its names and its controls while a run is active.
test.describe("a whole-film run renders while four shots have gaps", () => {
  /** Mara (the user's picture), Elias and The courier are seen in all nine shots. The first four have a gap, Shot 005
   * was rendered once, and a whole-film run renders the rest. */
  test.use({
    scene: {
      shots: 9,
      characters: ["Mara", "Elias", "The courier"],
      targets: ["confirmed", null, null],
      ensemble: 9,
      rendered: 5,
      states: { 0: "gap", 1: "gap", 2: "gap", 3: "gap" },
      run: { state: "running" },
    },
  });

  test("run progress separates gaps, names controls and keeps Pause and Cancel available", async ({
    page,
    world,
  }) => {
    const calls = recastCommands(page);
    await start(page, world);
    const { run } = await world.read();
    // The run covers the five shots without a gap, one of them rendered.
    expect([run.shots_done, run.shots_total]).toEqual([1, 5]);
    // (1) (2): every step is named with its count; Render counts renders by design, and the shots done stay in the
    // run status below.
    for (const name of [
      "Analyse: complete · 9 shots",
      "Cast: 1 of 3 mapped",
      "Plan: 9 shots",
      `Render: ${run.renders_used} of ${plural(run.render_budget, "render")} used`,
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
    await expect(status).toHaveText(await runStatus(world, "Running"));
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
    await expect.poll(() => calls.at(-1)?.path).toBe(`/${world.id}/run/pause`);
    await expect(main(page)).toHaveText("Resume");
    await expect(status).toHaveText(await runStatus(world, "Paused"));
    await settings
      .getByRole("button", { name: "Cancel run", exact: true })
      .click();
    await expect.poll(async () => (await world.read()).run.state).toBe("idle");
    await expect(main(page)).toHaveText("Start");
  });

  test("the needs-attention chip lists exactly the shots that need attention", async ({
    page,
    world,
  }) => {
    await start(page, world);
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
      // Each says why: the shot's gap, in the planner's words.
      await expect(list.getByRole("listitem").nth(index)).toContainText(
        "The plan cannot cover this shot: Confirm who this person is before rendering.",
      );
    }
    await expect(breadcrumb(page)).toContainText("Shot 001");
    await expectBlind(page);
  });
});

test.describe("a paused Final run over Shots 002-008", () => {
  test.use({
    scene: {
      shots: 9,
      run: {
        state: "paused",
        mode: "final",
        range: { start: 48, end: 384 },
        budget: 37,
      },
    },
  });

  test("reload restores the film, its shot and the saved run settings; a movie opened instead stays open", async ({
    page,
    request,
    world,
  }) => {
    const data = await seedDirect(request);
    await start(page, world);
    await page.getByRole("tab", { name: "Properties" }).click();
    await page.getByRole("button", { name: "Shot 007", exact: true }).click();
    await expect(
      card(page).getByRole("heading", { name: "Shot 007", exact: true }),
    ).toBeVisible();
    await page.reload();
    // (1) (2)
    await expect(page.locator(".timeline-heading")).toContainText(world.title);
    await expect(breadcrumb(page)).toContainText(
      `${world.film.name} · Shot 007`,
    );
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
    await expect(breadcrumb(page)).not.toContainText(world.film.name);
    await expect(page.locator(".timeline-heading")).not.toContainText(
      world.title,
    );
    await expect(trackbar(page)).toHaveCount(0);
  });
});

test.describe("Mara's 124-frame shots have two windows each", () => {
  /** The first two of four shots were rendered once. */
  test.use({ scene: { shots: 4, length: 124, rendered: 2 } });

  test("a retake announces its reserved work and keeps its status after reload", async ({
    page,
    world,
  }) => {
    const calls = recastCommands(page);
    const retakes = () => calls.filter((c) => c.path.endsWith("/retake"));
    await start(page, world);
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
    // Usability round 2 (C9): Retake asks first, naming what it reserves from the budget left; Enter confirms.
    const before = await world.read();
    const plan = before.shots[0].plan!;
    expect(plan.windows).toHaveLength(2);
    const confirm = page.getByRole("dialog", { name: "Retake" });
    await expect(confirm).toContainText(
      `Render Shot 001 again in Final mode: 2 windows, up to ${2 * plan.renders_per_window} renders of the ` +
        `${before.run.render_budget - before.run.renders_used} left.`,
    );
    expect(retakes()).toEqual([]);
    await page.keyboard.press("Enter");
    // (2): the notice counts the renders the service reserved for the retake in Final mode.
    await expect(notice(page)).toContainText("Shot 001: retake queued");
    expect(retakes()).toHaveLength(1);
    expect(retakes()[0].body).toMatchObject({ mode: "final" });
    const queued = (await world.read()).shots[0].plan!;
    await expect(notice(page)).toHaveText(
      new RegExp(
        `^Shot 001: retake queued in Final mode · 2 windows · up to ${2 * queued.renders_per_window} renders\\s*Dismiss$`,
      ),
    );
    // (4)
    await notice(page)
      .getByRole("button", { name: "Dismiss", exact: true })
      .click();
    await expect(notice(page)).toHaveCount(0);
    // (5) (6)
    const status = page.getByRole("button", {
      name: "Shot 001: Rendering (retake)",
      exact: true,
    });
    await expect(status).toBeVisible();
    await reopen(page, world);
    await expect(status).toBeVisible();
    // (7)
    expect(retakes()).toHaveLength(1);
    await expectBlind(page);
  });
});

test.describe("Mara's first two of four shots are rendered", () => {
  test.use({ scene: { shots: 4, rendered: 2 } });

  test("Q enters a visible review queue and leaves it after a retake", async ({
    page,
    world,
  }) => {
    await start(page, world);
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
    await expect(notice(page)).toContainText("Shot 001: retake queued");
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

  test("at a few shots, each status cell selects its shot", async ({
    page,
    world,
  }) => {
    await start(page, world);
    await expect(
      page.getByRole("slider", { name: "Film position" }),
    ).toHaveCount(0);
    await page
      .getByRole("button", { name: "Shot 004: Planned", exact: true })
      .click();
    await expect(breadcrumb(page)).toContainText("Shot 004");
    await page
      .getByRole("button", {
        name: "Shot 002: Needs review · 1 version",
        exact: true,
      })
      .click();
    await expect(breadcrumb(page)).toContainText("Shot 002");
    await expectBlind(page);
  });

  // Usability round 2 (T5-F7, T1-F6): the Cast step opens the Cast bin; Retake is not offered on a shot without
  // versions.
  test("the Cast step opens the Cast bin, and a shot without versions cannot be retaken", async ({
    page,
    world,
  }) => {
    await start(page, world);
    await page.getByRole("tab", { name: "Library" }).first().click();
    await step(page, "Cast").click();
    await expect(
      page
        .locator(".film-project .project-bins")
        .getByRole("button", { name: /^Cast/ }),
    ).toHaveAttribute("aria-pressed", "true");
    // Shot 003 was planned and never rendered.
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
});

test.describe("a film still being analysed", () => {
  test.use({ scene: { analysed: false } });

  test("analysis holds Start until it completes", async ({ page, world }) => {
    // The fixture analysis has no progress of its own to show: the read says how far it got while it runs.
    let running = true;
    await readAs(page, world, (read) => {
      if (running)
        read.analysis = {
          ...read.analysis,
          state: "running",
          stage: "faces",
          done: 12,
          total: 40,
        };
    });
    await start(page, world);
    // (1) (2), and row 2's opacity check on a disabled main action.
    await expect(page.locator("#film-analysis")).toContainText(
      "faces 12 of 40",
    );
    await expect(main(page)).toHaveText("Start");
    await expect(main(page)).toBeDisabled();
    await expect(main(page)).toHaveAttribute(
      "title",
      "Wait for analysis to finish.",
    );
    await expect(mainReason(page)).toHaveText("Wait for analysis to finish.");
    await expect(main(page)).toHaveCSS("opacity", "0.5");
    // (3): once analysis succeeds, the banner gives way to the Analyse step.
    running = false;
    await world.settle("film_analysis");
    await expect(page.locator("#film-analysis")).toHaveCount(0);
    const analyse = trackbar(page).getByRole("button", {
      name: "Analyse: complete · 8 shots",
      exact: true,
    });
    // Once complete, the step counts shots, not the analysis's stages (usability round 1, P3 T1-F7).
    await expect(analyse).toContainText("8 shots");
    await expect(analyse.locator("svg")).toHaveCount(1);
    await expect(analyse).toHaveAttribute("title", "Analysis is complete.");
    // Start now waits for the cast instead.
    await expect(mainReason(page)).toHaveText(
      "Confirm a character mapping first.",
    );
    await expectBlind(page);
  });
});

test.describe("Mara (the user's picture), Elias and The courier take the shots in turn", () => {
  test.use({ scene: { characters: ["Mara", "Elias", "The courier"] } });

  test("tracks move and characters merge from the cast", async ({
    page,
    world,
  }) => {
    const calls = recastCommands(page);
    const [mara, elias, courier] = world.characters;
    const tracks = (await world.read()).characters[0].tracks.map((t) => t.id);
    await start(page, world);
    // (7): Mara's face tracks; the first moves to Elias.
    await page.getByRole("tab", { name: "Properties" }).click();
    await page.locator(`[data-row="cast-${mara}"] .cast-header`).click();
    await card(page).getByRole("button", { name: "Face tracks…" }).click();
    const drawer = page.getByRole("dialog", { name: "Mara · tracks" });
    await expect(drawer.getByAltText("Face track").first()).toBeVisible();
    await drawer.getByRole("checkbox").first().check();
    await drawer.getByLabel("Move to").selectOption(elias);
    await drawer.getByRole("button", { name: "Move 1 track" }).click();
    const move = () => calls.find((c) => c.path.endsWith("/tracks/move"));
    await expect.poll(() => move()?.body.to).toBe(elias);
    const moved = move()!.body.track_ids as string[];
    expect(moved).toHaveLength(1);
    expect(tracks).toContain(moved[0]);
    await expect(drawer).toHaveCount(0);
    expect(
      (await world.read()).characters[1].tracks.map((t) => t.id),
    ).toContain(moved[0]);
    // (8): The courier's lane menu › Merge with… › Merge into Elias, then confirm.
    await page
      .locator(`[data-row="cast-${courier}"] .cast-header`)
      .click({ button: "right" });
    await page.getByRole("menuitem", { name: /^Merge with…/ }).click();
    const sheet = page.getByRole("dialog", { name: "Merge characters" });
    await sheet
      .getByRole("group", { name: "Merge into" })
      .getByRole("button", { name: "Merge into Elias", exact: true })
      .click();
    await expect(sheet).toContainText("Merge The courier into Elias?");
    expect(calls.some((c) => c.path.endsWith("/merge"))).toBe(false);
    await sheet
      .getByRole("button", { name: "Merge characters", exact: true })
      .click();
    await expect
      .poll(() => calls.at(-1)?.path)
      .toBe(`/${world.id}/characters/merge`);
    expect(calls.at(-1)?.body).toMatchObject({
      sources: [courier],
      into: elias,
    });
    await expect(page.locator(`[data-row="cast-${courier}"]`)).toHaveCount(0);
    await expectBlind(page);
  });
});

test.describe("Mara and Elias (the user's pictures) and The courier take 2 s shots in turn", () => {
  /** The courier has no picture, so Shot 003 is excluded; Shots 001 and 002 were rendered once. */
  test.use({
    scene: {
      shots: 8,
      length: 50,
      rate: [25, 1],
      characters: ["Mara", "Elias", "The courier"],
      targets: ["confirmed", "confirmed", null],
      rendered: 2,
    },
  });

  test("work area, estimate, run controls, review keys and a conform of the work area", async ({
    page,
    world,
  }) => {
    test.slow();
    const calls = recastCommands(page);
    await start(page, world);
    await page.getByRole("tab", { name: "Properties" }).click();
    // (1): I on Shot 002, O on Shot 004; the work area holds Shot 003, which is excluded (its item says so).
    await expect(
      page.getByRole("button", { name: "Shot 003 (excluded)", exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Shot 002", exact: true }).click();
    await keys(page, "i");
    await page.getByRole("button", { name: "Shot 004", exact: true }).click();
    await keys(page, "o");
    await expect(workArea(page).locator("summary")).toHaveAttribute(
      "title",
      "In 00:00:02:00 · Out 00:00:08:00",
    );
    // A changed work area asks to plan again before a run.
    await expect(main(page)).toHaveText("Update plan");
    // (2): Plan, then the estimate in Run settings.
    await step(page, "Plan").click();
    await expect
      .poll(() => calls.find((c) => c.path.endsWith("/plan"))?.body.range)
      .toEqual({ start: 50, end: 200 });
    const settings = await runSettings(page);
    // Usability round 1 (B12, T3-F3): the estimate is for what Start would run, the work area in the chosen mode,
    // and states its renders: Shot 004, the one shot in it not rendered yet.
    const estimate = settings.locator(".recast-estimate");
    await expect(estimate).toContainText(
      /Work area, Draft: 1 shot · \d+ renders? · ≈ \d+\.\d–\d+\.\d GPU h · \d+\.\d GB/,
    );
    await settings.getByLabel("Mode").selectOption("final");
    await expect(estimate).toContainText(
      /Work area, Final: 1 shot · \d+ renders/,
    );
    await settings.getByLabel("Mode").selectOption("draft");
    // (3)
    await expect(main(page)).toHaveText("Start");
    await main(page).click();
    await expect
      .poll(() => calls.find((c) => c.path.endsWith("/run"))?.body.range)
      .toEqual({ start: 50, end: 200 });
    // (4): Pause, Resume (a new run request), Cancel.
    await expect(main(page)).toHaveText("Pause");
    await main(page).click();
    await expect.poll(() => calls.at(-1)?.path).toBe(`/${world.id}/run/pause`);
    await expect(main(page)).toHaveText("Resume");
    await expect(main(page)).toBeEnabled();
    await main(page).click();
    await expect.poll(() => calls.at(-1)?.path).toBe(`/${world.id}/run`);
    await expect(main(page)).toHaveText("Pause");
    await settings
      .getByRole("button", { name: "Cancel run", exact: true })
      .click();
    await expect.poll(() => calls.at(-1)?.path).toBe(`/${world.id}/run/cancel`);
    await expect.poll(async () => (await world.read()).run.state).toBe("idle");
    // (6), as the user decided at CP1 (preview first; the Recast page's keys chose at once): 1 shows A and Enter
    // chooses it; Mod+Z takes it back; 0 shows the original and Enter keeps it; Mod+Z takes that back too.
    const read = await world.read();
    const first = read.revision;
    const a = read.shots[0].versions.find((v) => v.letter === "A")!.id;
    const choices = () =>
      calls
        .filter((c) => c.path.endsWith("/choice"))
        .map((c) => [c.path, c.method, c.body]);
    const choice = (version: string, step: number) => [
      `/${world.id}/shots/${world.shots[0]}/choice`,
      "POST",
      { expected_revision: first + step, version },
    ];
    const chosen = async () => (await world.read()).shots[0].choice;
    const heading = program(page).locator(".monitor-heading");
    await page.getByRole("button", { name: "Shot 001", exact: true }).click();
    await expect(breadcrumb(page)).toContainText("Shot 001");
    await keys(page, "1");
    await expect(heading).toContainText("Shot 001 · A · Unchosen");
    expect(choices()).toEqual([]);
    await keys(page, "Enter");
    await expect.poll(async () => (await chosen())?.version).toBe(a);
    await expect(breadcrumb(page)).toContainText("Shot 002");
    await keys(page, "Control+z");
    await expect.poll(chosen).toBeNull();
    await expect(breadcrumb(page)).toContainText("Shot 001");
    await expect(heading).toContainText("Shot 001 · A · Unchosen");
    await keys(page, "0");
    await expect(heading).toContainText("Shot 001 · Original · Unchosen");
    expect(choices()).toEqual([choice(a, 0), choice("unchosen", 1)]);
    await keys(page, "Enter");
    await expect.poll(async () => (await chosen())?.version).toBe("original");
    await keys(page, "Control+z");
    await expect.poll(chosen).toBeNull();
    expect(choices()).toEqual([
      choice(a, 0),
      choice("unchosen", 1),
      choice("original", 2),
      choice("unchosen", 3),
    ]);
    // (7): Enter accepts what Program shows (still the original) and moves to the next waiting shot.
    await expect(heading).toContainText("Shot 001 · Original · Unchosen");
    await keys(page, "Enter");
    await expect.poll(async () => (await chosen())?.version).toBe("original");
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
    await expect(heading).toContainText("Shot 002 · Original");
    await keys(page, "\\");
    await expect(heading).toContainText("Shot 002 · A");
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
    const conform = dialog.getByRole("button", {
      name: "Conform film",
      exact: true,
    });
    await conform.click();
    await expect.poll(() => calls.at(-1)?.path).toBe(`/${world.id}/conform`);
    expect(calls.at(-1)?.body).toMatchObject({
      mode: "preview_unreviewed",
      codec: "prores",
      range: { start: 50, end: 200 },
    });
    // Once the service has started it, the conform finishes with a film.
    await expect.poll(async () => (await world.read()).conforms.length).toBe(1);
    await world.settle("recast_conform");
    // B15: each conform row says what it covered and when.
    const row = dialog.locator(".recast-conform-progress").first();
    await expect(row).toContainText("Unreviewed preview · 00:00:02–00:00:08 ·");
    await expect(row).toContainText("· ProRes · succeeded · 100%");
    // A failed conform says why, in the service's words (B15).
    await conform.click();
    await expect.poll(async () => (await world.read()).conforms.length).toBe(2);
    await world.settle("recast_conform", "failed");
    await expect(dialog).toContainText(
      "Encoding failed. Conform again; if it fails again, the app log has the details.",
    );
    await dialog
      .getByRole("button", { name: "Open result in Library" })
      .first()
      .click();
    const result = page.getByRole("dialog", {
      name: world.film.name,
      exact: true,
    });
    await expect(result).toBeVisible();
    // Round 2 (C13): the conformed film plays here, and its player is named so it can be found.
    await expect(result.locator("video")).toHaveAttribute(
      "aria-label",
      `Play ${world.film.name}`,
    );
    await result.getByRole("button", { name: "Close panel" }).click();
    await expect(
      page.getByRole("region", { name: "Creative library" }),
    ).toBeVisible();
  });
});

test.describe("Mara and Elias take four shots in turn, neither mapped", () => {
  /** Their shots' window analyses wait until the spec settles them. */
  test.use({
    scene: {
      shots: 4,
      characters: ["Mara", "Elias"],
      targets: [null, null],
      pending_windows: true,
    },
  });

  test("the run bar explains Plan states, pending estimates and budget coverage", async ({
    page,
    world,
  }) => {
    // Each state waits for the film's next poll.
    test.slow();
    const calls = recastCommands(page);
    await start(page, world);
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
    // (2): Mara is mapped, with her two shots to plan.
    await world.target(0, "confirmed");
    await expect(main(page)).toHaveText("Plan 2 shots");
    await expect(main(page)).toBeEnabled();
    await main(page).click();
    // (3): her shots' windows are still being analysed.
    await expect(mainReason(page)).toHaveText(
      "Wait for window analyses to finish.",
    );
    await expect(main(page)).toHaveText("Start");
    await expect(main(page)).toBeDisabled();
    const settings = await runSettings(page);
    const estimate = settings.locator(".recast-estimate");
    const budget = settings.getByLabel("Render budget");
    const pending = (await world.read()).estimate!;
    expect(pending.windows_pending).toBeGreaterThan(0);
    await expect(estimate).toContainText(
      `Estimating… ${pending.windows - pending.windows_pending!} of ${pending.windows} windows analysed`,
    );
    await expect(budget).toHaveValue("20");
    // (4): planned; the budget is prefilled from the estimate.
    await world.settle("person_analysis");
    await expect(mainReason(page)).toHaveCount(0);
    const planned = (await world.read()).estimate!;
    expect(planned.shots).toBe(2);
    await expect(budget).toHaveValue(String(planned.renders));
    await expect(plan).toBeDisabled();
    await expect(plan).toHaveAttribute("title", "All mapped shots are planned");
    await renderMenu(page).click();
    await expect(planShots).toHaveAttribute("aria-disabled", "true");
    await expect(planShots).toContainText("All mapped shots are planned");
    await page.keyboard.press("Escape");
    await expect(main(page)).toBeEnabled();
    // (5): a budget for one shot says what it covers, and is kept when the estimate grows (Elias is mapped too).
    const one = planned.renders / planned.shots;
    await budget.fill(String(one));
    await expect(estimate).toContainText(
      `Budget covers ${one} of ${planned.renders} renders`,
    );
    await world.target(1, "confirmed");
    await expect(main(page)).toHaveText("Plan 2 shots");
    await main(page).click();
    await expect(mainReason(page)).toHaveText(
      "Wait for window analyses to finish.",
    );
    await world.settle("person_analysis");
    await expect(mainReason(page)).toHaveCount(0);
    const grown = (await world.read()).estimate!;
    expect(grown.renders).toBe(2 * planned.renders);
    await runSettings(page);
    await expect(estimate).toContainText(
      `Budget covers ${one} of ${grown.renders} renders`,
    );
    await expect(budget).toHaveValue(String(one));
    // (6): the budget is cumulative: once the run has reserved it, the words say it is used.
    await main(page).click();
    await expect
      .poll(() => calls.find((c) => c.path.endsWith("/run"))?.body)
      .toMatchObject({ render_budget: one });
    await world.feed();
    await expect
      .poll(async () => (await world.read()).run.renders_used)
      .toBe(one);
    await runSettings(page);
    await expect(estimate).toContainText(
      `All ${one} budget renders are used; raise the budget to render more`,
    );
    await expect(estimate).not.toContainText("Budget covers");
    await expectBlind(page);
  });
});

// Replaces film-timeline.spec.ts › "the trackbar's main action and reasons match the Recast page": the expected
// words are literal for each state, so the check no longer needs the Recast page.

test.describe("Mara (the user's picture), Elias and The courier are seen in all six shots", () => {
  /** The first two shots were rendered once. */
  test.use({
    scene: {
      shots: 6,
      characters: ["Mara", "Elias", "The courier"],
      targets: ["confirmed", null, null],
      ensemble: 6,
      rendered: 2,
    },
  });

  test("the trackbar names its steps with their counts", async ({
    page,
    world,
  }) => {
    await start(page, world);
    const { run } = await world.read();
    for (const name of [
      "Analyse: complete · 6 shots",
      "Cast: 1 of 3 mapped",
      "Plan: 6 shots",
      `Render: ${run.renders_used} of ${plural(run.render_budget, "render")} used`,
      "Review: 2 waiting",
      "Conform: 0 films",
    ])
      await expect(
        trackbar(page).getByRole("button", { name, exact: true }),
      ).toBeVisible();
    await expectBlind(page);
  });
});

const mainStates: {
  state: string;
  scene: Scene;
  label: string;
  reason?: string;
}[] = [
  { state: "a mapped, planned film", scene: { planned: true }, label: "Start" },
  {
    state: "nothing mapped",
    scene: { targets: [null] },
    label: "Start",
    reason: "Confirm a character mapping first.",
  },
  {
    state: "analysis running",
    scene: { analysed: false },
    label: "Start",
    reason: "Wait for analysis to finish.",
  },
  {
    // Both planned shots have a gap: a run would cover nothing, so there is no estimate.
    state: "no estimate yet",
    scene: { shots: 2, planned: true, states: { 0: "gap", 1: "gap" } },
    label: "Start",
    reason: "Plan the film to get an estimate first.",
  },
  {
    state: "window analyses pending",
    scene: { planned: true, pending_windows: true },
    label: "Start",
    reason: "Wait for window analyses to finish.",
  },
  {
    state: "an unsupported frame rate",
    scene: { rate: [50, 1], planned: true },
    label: "Start",
    reason: "This film's frame rate is unsupported.",
  },
  {
    state: "two shots to plan",
    scene: { shots: 4, characters: ["Mara", "Elias"] },
    label: "Plan 2 shots",
  },
  {
    state: "one shot to plan",
    scene: { shots: 2, characters: ["Mara", "Elias"] },
    label: "Plan 1 shot",
  },
  {
    state: "a running run",
    scene: { run: { state: "running" } },
    label: "Pause",
  },
  {
    state: "a paused run",
    scene: { run: { state: "paused" } },
    label: "Resume",
  },
];

for (const { state, scene, label, reason } of mainStates)
  test.describe(state, () => {
    test.use({ scene });
    test(`the trackbar's main action and reason: ${state}`, async ({
      page,
      world,
    }) => {
      await start(page, world);
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
  });

test.describe("Mara's eight shots are planned", () => {
  test.use({ scene: { planned: true } });

  test("the trackbar's main action asks to plan again after the work area changes", async ({
    page,
    world,
  }) => {
    const calls = recastCommands(page);
    await start(page, world);
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
      .poll(() => calls.find((c) => c.path.endsWith("/plan"))?.body.range)
      .toEqual({ start: 96, end: 384 });
    await expect(main(page)).toHaveText("Start");
    await expectBlind(page);
  });

  // Usability round 1 (B4): with ComfyUI down, face checks, analysis and renders wait without an error of their own;
  // the film names the cause and points to Settings, and says nothing while ComfyUI answers.
  test("the film says when ComfyUI is not reachable and opens Settings", async ({
    page,
    world,
  }) => {
    // The test service has no ComfyUI to ask; its status is whichever this case needs.
    let reachable = false;
    await page.route("**/api/v1/settings/comfy", (route) =>
      route.fulfill({
        json: reachable
          ? { connected: true, version: "0.35" }
          : { connected: false, error: "Connection refused" },
      }),
    );
    await start(page, world);
    const banner = page.locator(".comfy-offline");
    await expect(banner).toContainText(
      "ComfyUI is not reachable, so face checks, analysis and renders wait until it is.",
    );
    await banner.getByRole("button", { name: "Open Settings" }).click();
    await expect(page.getByRole("dialog", { name: "Settings" })).toBeVisible();
    await page.keyboard.press("Escape");
    reachable = true;
    await reopen(page, world);
    await expect(main(page)).toHaveText("Start");
    await expect(banner).toHaveCount(0);
    await expectBlind(page);
  });

  // CP3 (ADR 0017 amendment 11) replaces usability round 1's P3 note: with no movie open, the film's Chat tab holds the
  // film's own conversation, and a film that reopens onto Chat stays there (film-chat.spec.ts covers the
  // conversation).
  test("with no movie open, the film's Chat tab holds the film's own conversation", async ({
    page,
    world,
  }) => {
    // No movie in this library (the shared test backend keeps other tests' movies, and the first one opens).
    await page.route("**/api/direction/movies", (route) =>
      route.fulfill({ json: [] }),
    );
    await start(page, world);
    await page.getByRole("tab", { name: "Chat" }).click();
    await expect(
      page.getByRole("region", { name: "Film conversation" }),
    ).toBeVisible();
    await expect(page.locator(".film-chat-note")).toHaveCount(0);
    await expect(page.getByLabel("Message to Gaia")).toBeEnabled();
    await reopen(page, world);
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
  test("the Recast badge shows Properties", async ({ page, world }) => {
    await start(page, world);
    await page.getByRole("tab", { name: "Chat" }).click();
    await page
      .getByRole("button", { name: /^Recast\s*(Draft|Final)$/ })
      .click();
    await expect(page.getByRole("tab", { name: "Properties" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expectBlind(page);
  });
});

// Usability round 2 (C2): a run that renders nothing says so.
test.describe("all four of Mara's shots are rendered", () => {
  test.use({ scene: { shots: 4, rendered: 4 } });

  test("a run without renders says so", async ({ page, world }) => {
    await start(page, world);
    await expect(main(page)).toHaveText("Start");
    await main(page).click();
    await expect(main(page)).toHaveText("Pause");
    // The feed finds nothing to render in the run's range.
    await world.feed();
    await expect(notice(page)).toContainText(
      "The run finished without rendering: every planned shot in its range already has versions.",
    );
    await expect(main(page)).toHaveText("Start");
  });
});

// Usability round 2 (C3): with ComfyUI down, a busy shot waits for it.
test.describe("Shot 002 is still rendering", () => {
  test.use({ scene: { shots: 4, rendered: 2, states: { 1: "rendering" } } });

  test("with ComfyUI not reachable, the rendering shot waits for it", async ({
    page,
    world,
  }) => {
    await page.route("**/api/v1/settings/comfy", (route) =>
      route.fulfill({
        json: { connected: false, error: "Connection refused" },
      }),
    );
    await start(page, world);
    await page.getByRole("tab", { name: "Properties" }).click();
    await page.getByRole("button", { name: "Shot 002", exact: true }).click();
    await expect(card(page)).toContainText(
      "Waiting for ComfyUI: Waiting for ComfyUI, which is not reachable",
      { timeout: 25_000 },
    );
    await expectBlind(page);
  });
});

test.describe("Mara's eight 2 s shots are mapped and not planned", () => {
  test.use({ scene: { shots: 8, length: 50, rate: [25, 1] } });

  // Usability round 2 (C10, C5): Plan's reason names the work area and the shots outside it; a work area set back
  // to the paused run's range keeps Resume.
  test("Plan's reason names the work area, and a restored work area keeps Resume", async ({
    page,
    world,
  }) => {
    const calls = recastCommands(page);
    await start(page, world);
    const workAreaOnFirstTwo = async () => {
      await page.getByRole("button", { name: "Shot 001", exact: true }).click();
      await keys(page, "i");
      await page.getByRole("button", { name: "Shot 002", exact: true }).click();
      await keys(page, "o");
    };
    // C10: the work area is planned and holds only planned shots; the reason names it and the shots outside.
    await workAreaOnFirstTwo();
    await expect(main(page)).toHaveText("Plan 2 shots");
    await main(page).click();
    await expect
      .poll(() => calls.find((c) => c.path.endsWith("/plan"))?.body.range)
      .toEqual({ start: 0, end: 100 });
    await expect(step(page, "Plan")).toHaveAttribute(
      "title",
      "Every mapped shot in the work area is planned; 6 more shots outside it. Clear the work area to plan them.",
    );
    // C5: a run over the work area, paused; the work area cleared and set back to the run's range keeps Resume.
    await expect(main(page)).toHaveText("Start");
    await main(page).click();
    await expect(main(page)).toHaveText("Pause");
    await main(page).click();
    await expect(main(page)).toHaveText("Resume");
    const summary = workArea(page).locator("summary");
    await summary.click();
    await page.getByRole("button", { name: "Clear the work area" }).click();
    // Closed again, so it covers no shot.
    await summary.click();
    await expect(workArea(page)).not.toHaveAttribute("open", "");
    await workAreaOnFirstTwo();
    await expect(main(page)).toHaveText("Resume");
    await expect(main(page)).toBeEnabled();
    await expectBlind(page);
  });
});

test.describe("nothing is mapped", () => {
  test.use({ scene: { targets: [null] } });

  // D2 (ADR 0017, CP3): a trackbar step that cannot run stays focusable and says why under the bar, by keyboard or
  // click; it runs nothing.
  test("a disabled trackbar step says why when focused or clicked", async ({
    page,
    world,
  }) => {
    const calls = recastCommands(page);
    await start(page, world);
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
    expect(calls.some((c) => c.path.endsWith("/plan"))).toBe(false);
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
});

test.describe("a run over Shots 003-008 is paused", () => {
  /** Mara's eight 2 s shots; the first two were rendered before the run. */
  test.use({
    scene: {
      shots: 8,
      length: 50,
      rate: [25, 1],
      rendered: 2,
      run: { state: "paused", range: { start: 100, end: 400 } },
    },
  });

  // Usability round 1 (B10, B11, B13, B14): the run's words name the state they refer to; a budget below the renders
  // used is refused at the field; the work area survives a reload and says it follows shot boundaries.
  test("run reasons name a paused run, the budget is checked at its field, and the work area survives a reload", async ({
    page,
    world,
  }) => {
    await start(page, world);
    await page.getByRole("button", { name: "Shot 003", exact: true }).click();
    await keys(page, "i");
    await workArea(page).locator("summary").click();
    await expect(
      workArea(page).getByRole("button", {
        name: /^Set In at the start of this shot/,
      }),
    ).toBeVisible();
    await expect(
      workArea(page).getByRole("button", {
        name: /^Set Out at the end of this shot/,
      }),
    ).toBeVisible();
    await expect(workArea(page)).toContainText(
      "The work area follows shot boundaries.",
    );
    await expect(workArea(page).locator("summary")).toHaveAttribute(
      "title",
      "In 00:00:04:00 · Out 00:00:16:00",
    );
    // B13: kept per film in this browser.
    await reopen(page, world);
    await expect(workArea(page).locator("summary")).toHaveAttribute(
      "title",
      "In 00:00:04:00 · Out 00:00:16:00",
    );
    // B10: a paused run says so on Start and Pause.
    await expect(main(page)).toHaveText("Resume");
    const settings = await runSettings(page);
    await expect(settings.getByRole("status")).toContainText(
      "the run's range 00:00:04–00:00:16",
    );
    await renderMenu(page).click();
    await expect(
      page.getByRole("menuitem", { name: /^Start run/ }),
    ).toContainText(
      "The run is paused: Resume continues it, and Cancel run ends it.",
    );
    await page.keyboard.press("Escape");
    // B11: a budget below the renders used is refused beside the field, and Resume says why it cannot go on.
    const used = (await world.read()).run.renders_used;
    expect(used).toBeGreaterThan(1);
    const budget = settings.getByLabel("Render budget");
    await budget.fill(String(used - 1));
    await expect(budget).toHaveAttribute("aria-invalid", "true");
    await expect(settings.getByRole("alert")).toHaveText(
      `The budget counts every render of this recast so far; it cannot be below the ${used} already used.`,
    );
    await expect(main(page)).toBeDisabled();
    await expect(mainReason(page)).toHaveText(
      `Raise the render budget to at least ${used}, the renders already used.`,
    );
    await budget.fill(String(used + 6));
    await expect(settings.getByRole("alert")).toHaveCount(0);
    await expect(main(page)).toBeEnabled();
    // A cleared work area (the whole film) is kept too, over the paused run's range (Astra R5).
    await settings.getByLabel("Run settings", { exact: true }).click();
    await workArea(page).locator("summary").click();
    await page.getByRole("button", { name: "Clear the work area" }).click();
    await reopen(page, world);
    await expect(workArea(page).locator("summary")).toContainText("Whole film");
    await expectBlind(page);
  });
});

test.describe("a run over Shots 001-002 is paused", () => {
  test.use({
    scene: {
      shots: 8,
      length: 50,
      rate: [25, 1],
      run: { state: "paused", range: { start: 0, end: 100 } },
    },
  });

  // B16 (ADR 0017, CP3): one work area; when it differs from a paused run's range, the ruler also draws the run's
  // range, and Resume says how to continue.
  test("a paused run's own range shows on the ruler when the work area differs", async ({
    page,
    world,
  }) => {
    await start(page, world);
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
});

test.describe("a whole-film run is paused", () => {
  test.use({ scene: { run: { state: "paused" } } });

  // Astra R-A: a whole-film paused run and a work area written as the film's full span are the same range: no span,
  // and Resume works.
  test("a whole-film paused run matches a work area set to the whole film", async ({
    page,
    world,
  }) => {
    await start(page, world);
    await expect(main(page)).toHaveText("Resume");
    // I on the first shot keeps Out at the film's end: the whole film, now written as a span instead of "whole".
    await page.getByRole("button", { name: "Shot 001", exact: true }).click();
    await keys(page, "i");
    const frames = (await world.read()).film.frame_count;
    await expect
      .poll(() =>
        page.evaluate(
          (id) => localStorage.getItem(`gaiavfx:recast-range:${id}`),
          world.id,
        ),
      )
      .toBe(JSON.stringify({ start: 0, end: frames }));
    await expect(page.locator(".ruler-span")).toHaveCount(0);
    await expect(main(page)).toHaveText("Resume");
    await expect(main(page)).toBeEnabled();
  });
});
