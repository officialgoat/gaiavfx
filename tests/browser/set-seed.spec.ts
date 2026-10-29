import {
  expect,
  test,
  type Locator,
  type Page,
  type TestInfo,
} from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { seedProduction } from "./production-fixture";
import { openMovie } from "./direct-fixture";

// ADR 0019 §4: a set built from one shot of a scene's take. The browser service's fake ComfyUI answers the
// take-geometry graph with a synthetic room whose camera moves through it for a first shot and stands still for a
// later shot; the real worldbuilder worker runs the gate and the atlas, and a fake splat fit stands in for gsplat.

// The Shot select; a label match would also find the region "This shot's last build".
const shotOf = (dialog: Locator) =>
  dialog.getByRole("combobox", { name: "Shot", exact: true });

// Arrow navigation is deliberate: focusing/clicking the menu item directly would bypass keyboard regressions.
async function openWithKeyboard(page: Page, trigger: Locator) {
  await trigger.focus();
  await page.keyboard.press("Shift+F10");
  const item = page.getByRole("menuitem", {
    name: /Build a stage from this take/,
  });
  await expect(item).toBeVisible();
  await expect(item).toBeEnabled();
  await page.keyboard.press("Home");
  const count = await page.getByRole("menuitem").count();
  for (let i = 0; i < count; i++) {
    if (await item.evaluate((el) => el === document.activeElement)) break;
    await page.keyboard.press("ArrowDown");
  }
  await expect(item).toBeFocused();
  await page.keyboard.press("Enter");
  return page.getByRole("dialog", { name: "Build a stage from this take" });
}

/** Opens the dialog by keyboard and reaches its Shot select with Tab only. */
async function buildWithKeyboard(page: Page, trigger: Locator) {
  const dialog = await openWithKeyboard(page, trigger);
  const shot = shotOf(dialog);
  await expect(shot).toBeVisible();
  // The close button may receive initial focus while the preview loads. Reach Shot with Tab only.
  for (let i = 0; i < 8; i++) {
    if (await shot.evaluate((el) => el === document.activeElement)) break;
    expect(
      await dialog.evaluate((el) => el.contains(document.activeElement)),
    ).toBe(true);
    await page.keyboard.press("Tab");
  }
  await expect(shot).toBeFocused();
  return dialog;
}

async function tabTo(page: Page, control: Locator) {
  for (let i = 0; i < 10; i++) {
    if (await control.evaluate((el) => el === document.activeElement)) break;
    await page.keyboard.press("Tab");
  }
  await expect(control).toBeFocused();
}

async function auditDialog(
  page: Page,
  info: TestInfo,
  state: string,
  theme: string,
) {
  const file = `${theme}-${state}.png`;
  const screenshot = process.env.GAIAVFX_REVIEW_DIR
    ? path.join(process.env.GAIAVFX_REVIEW_DIR, file)
    : info.outputPath(file);
  await page.screenshot({ path: screenshot });
  await info.attach(file, { path: screenshot, contentType: "image/png" });
  const results = await new AxeBuilder({ page })
    .include('[role="dialog"]')
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(results.violations).toEqual([]);
}

async function withSelectedTake(request: Parameters<typeof seedProduction>[0]) {
  const data = await seedProduction(request, true);
  await data.post(`/api/production/reviews/${data.review!.id}/select`, {
    candidate_id: data.review!.members[0].candidate_id,
    expected_revision: 0,
  });
  return data;
}

async function waitForFilling(
  data: Awaited<ReturnType<typeof withSelectedTake>>,
) {
  await expect
    .poll(
      async () => {
        const preview = await data.get<{ seed: { label: string } | null }>(
          `/api/direction/segments/${data.second.id}/build-set?shot=0&fill_unseen=true`,
        );
        return preview.seed?.label;
      },
      { timeout: 120_000 },
    )
    .toMatch(/Filling unseen space · round [12] of 2/);
}

test("Build a set from this take says what it builds, then the References row and the workbench show the set", async ({
  page,
  request,
}) => {
  test.setTimeout(360_000);
  const data = await withSelectedTake(request);
  await openMovie(page, data.movie.title);
  const segment = page.locator(".segment-item").nth(1);
  await segment.click({ button: "right" });
  await page
    .getByRole("menuitem", { name: /Build a stage from this take/ })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Build a stage from this take",
  });
  await expect(shotOf(dialog)).toHaveValue("0");
  const builds = dialog.getByRole("region", { name: "What it builds" });
  await expect(builds).toContainText("from 12 of its frames");
  // Filling the unseen space is on unless the user turns it off (ADR 0020), and Python says what it adds.
  await expect(
    dialog.getByRole("checkbox", { name: "Fill in unseen space" }),
  ).toBeChecked();
  await expect(builds).toContainText(
    "Up to 6 renders, about 15 to 25 minutes of GPU work",
  );
  await expect(builds).toContainText(
    "stands still or only turns builds this way too",
  );
  await expect(dialog.getByLabel("Name")).toHaveValue(/ from a take$/);
  await expect(
    dialog.getByRole("checkbox", { name: /as this stage/ }),
  ).toBeChecked();
  const results = await new AxeBuilder({ page })
    .include('[role="dialog"]')
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(results.violations).toEqual([]);
  await dialog.getByLabel("Name").fill("Courtyard from its take");
  await dialog.getByRole("button", { name: "Build stage" }).click();
  await expect(dialog).toBeHidden();

  // The new set has a chip of its own; the place's chip keeps the first shot's own state.
  const chip = page.getByRole("button", {
    name: "New stage from this take · Courtyard from its take · v1 to review",
  });
  await expect(chip).toBeVisible({ timeout: 240_000 });
  await expect(chip).toHaveClass(/seed-done/);
  await expect(
    page.getByRole("button", {
      name: /^First-shot references · .*v1 to review/,
    }),
  ).toHaveCount(0);
  // Its dialog leads with the build that waits, and opening it is the primary action.
  await chip.click({ button: "right" });
  await page
    .getByRole("menuitem", { name: /Build a stage from this take/ })
    .click();
  const last = dialog.getByRole("region", { name: "This shot's last build" });
  await expect(last).toContainText(
    "Courtyard from its take is built and filled its unseen space in 2 rounds; its v1 waits for you in the stage workbench.",
  );
  await expect(
    dialog.getByRole("button", { name: "Build another stage" }),
  ).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Open the new stage" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  // The chip's menu opens it too.
  await chip.click({ button: "right" });
  await page.getByRole("menuitem", { name: /Open the new stage/ }).click();
  const workbench = page.getByRole("main", {
    name: "Stage Courtyard from its take",
  });
  await expect(workbench).toBeVisible();
  await expect(
    workbench.getByRole("heading", {
      name: "Courtyard from its take",
      level: 1,
    }),
  ).toBeVisible();
});

test("a still shot is refused without filling, the refusal holds for that setting, and filling lifts it", async ({
  page,
  request,
}) => {
  test.setTimeout(240_000);
  const data = await withSelectedTake(request);
  await openMovie(page, data.movie.title);
  await page.locator(".segment-item").nth(1).click({ button: "right" });
  await page
    .getByRole("menuitem", { name: /Build a stage from this take/ })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Build a stage from this take",
  });
  await shotOf(dialog).selectOption("1");
  await expect(shotOf(dialog)).toContainText("Cut 1");
  const fill = dialog.getByRole("checkbox", { name: "Fill in unseen space" });
  await fill.uncheck();
  await expect(
    dialog.getByRole("region", { name: "What it builds" }),
  ).toContainText("stands still or only turns is refused");
  await dialog.getByRole("button", { name: "Build stage" }).click();
  await expect(dialog).toBeHidden();
  const refused =
    "The camera stands still in this take: without filling its unseen space, building a stage needs the camera to move through the place.";
  const chip = page.getByRole("button", {
    name: /^New stage from this take · .* · Refused: the camera stands still$/,
  });
  await expect(chip).toBeVisible({ timeout: 90_000 });
  await expect(chip).toHaveClass(/seed-refused/);

  // Its chip opens the dialog on the shot it names, filling on as always: that is another build, so it may run.
  await chip.click({ button: "right" });
  await page
    .getByRole("menuitem", { name: /Build a stage from this take/ })
    .click();
  await expect(shotOf(dialog)).toHaveValue("1");
  const last = dialog.getByRole("region", { name: "This shot's last build" });
  await expect(last).toContainText(refused);
  await expect(last.getByRole("button", { name: "Try again" })).toHaveCount(0);
  await expect(fill).toBeChecked();
  await expect(
    dialog.getByRole("button", { name: "Build stage" }),
  ).toBeEnabled();
  // Without filling, the same build would measure the same thing, so it is not offered again; the toggle stays
  // in view beside the refusal, as the one thing that changes the answer.
  await fill.uncheck();
  await expect(dialog.getByRole("alert")).toContainText(
    "would give the same answer. Fill in its unseen space",
  );
  await expect(dialog.getByLabel("Name")).toHaveCount(0);
  await expect(
    dialog.getByRole("button", { name: "Build stage" }),
  ).toBeDisabled();
  await fill.check();
  await expect(dialog.getByLabel("Name")).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Build stage" }),
  ).toBeEnabled();
});

test("Build set is unavailable, in Python's words, for a take made before the scene changed", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  const data = await withSelectedTake(request);
  const current = await data.get<{ revision: number }>(
    `/api/direction/segments/${data.second.id}`,
  );
  await data.patch(`/api/direction/segments/${data.second.id}`, {
    expected_revision: current.revision,
    delivered_frames: 103,
  });
  const preview = await data.get<{ refusal: string }>(
    `/api/direction/segments/${data.second.id}/build-set`,
  );
  expect(preview.refusal).toContain("made before this scene changed");
  await openMovie(page, data.movie.title);
  await page.locator(".segment-item").nth(1).focus();
  await page.keyboard.press("Shift+F10");
  const menu = page.getByRole("menuitem", {
    name: /Build a stage from this take/,
  });
  await expect(menu).toBeDisabled();
  await expect(menu).toContainText("made before this scene changed");
});

for (const theme of ["dark", "light"] as const) {
  test(`keyboard Build set and References menu; ${theme} dialog states have no axe violations`, async ({
    page,
    request,
  }, info) => {
    test.setTimeout(420_000);
    await page.emulateMedia({ colorScheme: theme });
    await page.addInitScript((value) => {
      localStorage.setItem(
        "gaiavfx-preferences",
        JSON.stringify({ state: { theme: value }, version: 1 }),
      );
    }, theme);
    const data = await withSelectedTake(request);
    await openMovie(page, data.movie.title);
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    const segment = page.locator(".segment-item").nth(1);
    const dialog = await buildWithKeyboard(page, segment);
    const shot = shotOf(dialog);
    await auditDialog(page, info, "fill-on", theme);
    // Every form control is reachable with Tab, the primary last.
    for (const control of [
      dialog.getByRole("checkbox", { name: "Fill in unseen space" }),
      dialog.getByLabel("Name", { exact: true }),
      dialog.getByRole("checkbox", { name: /as this stage/ }),
      dialog.getByRole("button", { name: "Build stage", exact: true }),
    ]) {
      await page.keyboard.press("Tab");
      await expect(control).toBeFocused();
    }
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(segment).toBeFocused();

    // The place's chip has the same keyboard menu. Build the second shot, which the gate refuses.
    const place = page
      .getByRole("button", { name: /First-shot references/ })
      .last();
    await buildWithKeyboard(page, place);
    await page.keyboard.press("ArrowDown");
    await expect(shot).toHaveValue("1");
    // Without filling, so the gate refuses it.
    const fill = dialog.getByRole("checkbox", { name: "Fill in unseen space" });
    await tabTo(page, fill);
    await page.keyboard.press("Space");
    await expect(fill).not.toBeChecked();
    await expect(
      dialog.getByRole("region", { name: "What it builds" }),
    ).toContainText("About 3 minutes of GPU work");
    await auditDialog(page, info, "fill-off", theme);
    await tabTo(
      page,
      dialog.getByRole("button", { name: "Build stage", exact: true }),
    );
    await page.keyboard.press("Enter");
    await expect(dialog).toBeHidden();
    const seed = page.getByRole("button", {
      name: /^New stage from this take/,
    });
    await expect(seed).toContainText("Refused: the camera stands still", {
      timeout: 90_000,
    });
    // The new set's chip reopens the dialog on the shot it names.
    await buildWithKeyboard(page, seed);
    await expect(shot).toHaveValue("1");
    await expect(
      dialog.getByRole("region", { name: "This shot's last build" }),
    ).toContainText("The camera stands still");
    await auditDialog(page, info, "refused", theme);
    // Reopening defaults to filling on. Space restores the measured refusal, and Space lifts it again.
    await tabTo(page, fill);
    await expect(fill).toBeChecked();
    await page.keyboard.press("Space");
    await expect(dialog.getByRole("alert")).toContainText(
      "would give the same answer",
    );
    await expect(
      dialog.getByRole("button", { name: "Build stage", exact: true }),
    ).toBeDisabled();
    await auditDialog(page, info, "refused-fill-off", theme);
    await page.keyboard.press("Space");
    await expect(fill).toBeChecked();
    await expect(
      dialog.getByRole("button", { name: "Build stage", exact: true }),
    ).toBeEnabled();
    await page.keyboard.press("Escape");
    await expect(seed).toBeFocused();

    // Build the first shot through the keyboard as well.
    await buildWithKeyboard(page, seed);
    await page.keyboard.press("ArrowUp");
    await expect(shot).toHaveValue("0");
    await tabTo(
      page,
      dialog.getByRole("button", { name: "Build stage", exact: true }),
    );
    await page.keyboard.press("Enter");
    await expect(dialog).toBeHidden();
    await waitForFilling(data);
    await expect(seed).toContainText(/Filling unseen space · round [12] of 2/);
    const runningFile = process.env.GAIAVFX_REVIEW_DIR
      ? path.join(
          process.env.GAIAVFX_REVIEW_DIR,
          `${theme}-fill-running.png`,
        )
      : info.outputPath(`${theme}-fill-running.png`);
    await page.screenshot({ path: runningFile });
    await info.attach("fill-running", {
      path: runningFile,
      contentType: "image/png",
    });
    await expect(seed).toContainText("v1 to review", { timeout: 240_000 });
    await expect(seed).toHaveClass(/seed-done/);
    await buildWithKeyboard(page, seed);
    await expect(
      dialog.getByRole("button", { name: "Open the new stage" }),
    ).toBeVisible();
    await auditDialog(page, info, "done", theme);
    await page.keyboard.press("Escape");
    await expect(seed).toBeFocused();

    // No take is normally refused by the menu. Replay Python's genuine no-take preview from the first segment to
    // model the selection disappearing between opening the menu and loading the dialog.
    const noTake = await data.get<{ refusal: string }>(
      `/api/direction/segments/${data.first.id}/build-set`,
    );
    expect(noTake.refusal).toBe("Select a take of this scene first.");
    await page.route(
      `**/api/direction/segments/${data.second.id}/build-set?*`,
      (route) => route.fulfill({ json: noTake }),
    );
    // A fresh page avoids the preceding done preview's query-cache lifetime.
    await openMovie(page, data.movie.title);
    await openWithKeyboard(page, place);
    await expect(dialog.getByRole("alert")).toHaveText(
      "Select a take of this scene first.",
    );
    await expect(dialog.getByLabel("Name")).toHaveCount(0);
    await expect(
      dialog.getByRole("button", { name: "Build stage", exact: true }),
    ).toBeDisabled();
    await auditDialog(page, info, "no-take", theme);
    await page.keyboard.press("Escape");
    await expect(place).toBeFocused();
  });
}

test("changing fill waits for the matching estimate before allowing a build", async ({
  page,
  request,
}, info) => {
  test.setTimeout(90_000);
  const data = await withSelectedTake(request);
  await openMovie(page, data.movie.title);
  const dialog = await buildWithKeyboard(
    page,
    page.locator(".segment-item").nth(1),
  );
  const fill = dialog.getByRole("checkbox", { name: "Fill in unseen space" });
  const estimate = dialog.getByRole("region", { name: "What it builds" });
  await expect(estimate).toContainText("about 15 to 25 minutes");
  // Hold the new preview, reproducing an ordinary slow service response without starting any build.
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(
    `**/api/direction/segments/${data.second.id}/build-set?*fill_unseen=false`,
    async (route) => {
      await pending;
      await route.continue();
    },
  );
  try {
    await tabTo(page, fill);
    await page.keyboard.press("Space");
    await expect(fill).not.toBeChecked();
    // The previous answer stays in view, marked busy, but never its cost, and nothing builds on it.
    await expect(estimate).toHaveAttribute("aria-busy", "true");
    await expect(estimate).toContainText(
      "Reading the estimate for this choice",
    );
    await expect(estimate).not.toContainText("about 15 to 25 minutes");
    await auditDialog(page, info, "pending-estimate", "dark");
    await expect(
      dialog.getByRole("button", { name: "Build stage", exact: true }),
    ).toBeDisabled();
  } finally {
    release();
  }
  await expect(estimate).toContainText("About 3 minutes of GPU work");
  await expect(estimate).not.toHaveAttribute("aria-busy");
  await expect(
    dialog.getByRole("button", { name: "Build stage", exact: true }),
  ).toBeEnabled();
});

test("References updates to filling progress without reopening the movie", async ({
  page,
  request,
}, info) => {
  test.setTimeout(180_000);
  const data = await withSelectedTake(request);
  await openMovie(page, data.movie.title);
  const dialog = await buildWithKeyboard(
    page,
    page.locator(".segment-item").nth(1),
  );
  await tabTo(
    page,
    dialog.getByRole("button", { name: "Build stage", exact: true }),
  );
  await page.keyboard.press("Enter");
  await expect(dialog).toBeHidden();
  await waitForFilling(data);
  const chip = page.getByRole("button", { name: /^New stage from this take/ });
  await expect(chip).toBeVisible();
  const live = await data.get<{
    seed: { operation_id: string; label: string };
  }>(
    `/api/direction/segments/${data.second.id}/build-set?shot=0&fill_unseen=true`,
  );
  await info.attach("backend-fill-state", {
    body: JSON.stringify(live.seed),
    contentType: "application/json",
  });
  try {
    // The chip reads the build's words again while it builds; no operation settles in between.
    await expect(chip).toContainText(/Filling unseen space · round [12] of 2/);
    const screenshot = process.env.GAIAVFX_REVIEW_DIR
      ? path.join(process.env.GAIAVFX_REVIEW_DIR, "dark-fill-progress.png")
      : info.outputPath("dark-fill-progress.png");
    await page.screenshot({ path: screenshot });
    await info.attach("fill-progress", {
      path: screenshot,
      contentType: "image/png",
    });
  } finally {
    await data.post(
      `/api/production/operations/${live.seed.operation_id}/cancel`,
      {},
    );
  }
});
