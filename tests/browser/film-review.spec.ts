import { test, expect, type Locator, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { filmAsset, mockRecast } from "./recast-fixtures";

// ADR 0017 U3d: a film's monitors, Properties cards and Project panel, on the mocked recast. Part 1: blindness,
// the monitors' ranges, the wipe and compare keys, and accessibility in both themes.

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

const program = (page: Page) =>
  page.getByRole("region", { name: "Program monitor" });

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

test("Program compares the current shot blind; W and \\ switch the wipe and the original", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("button", { name: "Shot 002", exact: true }).click();
  await expect(program(page).locator(".monitor-heading")).toContainText(
    "Shot 002 · A",
  );
  const wipe = program(page).getByLabel("Before/after wipe position");
  await expect(wipe).toBeVisible();
  await page.keyboard.press("w");
  await expect(wipe).toHaveCount(0);
  await page.keyboard.press("w");
  await expect(wipe).toBeVisible();
  await page.keyboard.press("\\");
  await expect(program(page).locator(".monitor-heading")).toContainText(
    "Shot 002 · Original",
  );
  await page.keyboard.press("\\");
  await expect(program(page).locator(".monitor-heading")).toContainText(
    "Shot 002 · A",
  );
  await expectBlind(page);
});

test("Source plays only the current shot at the film's rate; the comparison follows the playhead", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("button", { name: "Shot 003", exact: true }).click();
  const fps = mock.value.film.fps_num / mock.value.film.fps_den;
  const shot = mock.value.shots[2];
  const source = page
    .getByRole("region", { name: "Source monitor" })
    .locator("video");
  await expect
    .poll(() => source.evaluate((v: HTMLVideoElement) => v.readyState))
    .toBeGreaterThan(0);
  await expect
    .poll(() => source.evaluate((v: HTMLVideoElement) => v.currentTime))
    .toBeCloseTo(shot.start / fps, 1);
  await expect(
    page.getByRole("region", { name: "Source monitor" }),
  ).toContainText("00:00:04:00");
  // Playing the Source stops at the shot's end; the playhead stays in the shot.
  await page
    .getByRole("region", { name: "Source monitor" })
    .getByRole("button", { name: "Play preview" })
    .click();
  await expect
    .poll(() => source.evaluate((v: HTMLVideoElement) => v.paused), {
      timeout: 8000,
    })
    .toBe(true);
  const stopped = await source.evaluate((v: HTMLVideoElement) => v.currentTime);
  expect(stopped).toBeGreaterThan(shot.start / fps + 1);
  expect(stopped).toBeLessThan(shot.end / fps);
  const playhead = Number(
    await page
      .getByRole("slider", { name: "Playhead" })
      .getAttribute("aria-valuenow"),
  );
  expect(playhead).toBeGreaterThanOrEqual(shot.start);
  expect(playhead).toBeLessThan(shot.end);
  await expect(page.locator(".workspace-breadcrumb")).toContainText("Shot 003");
  // Program follows the film's playhead; stepping the comparison moves the playhead.
  const head = page.getByRole("slider", { name: "Playhead" });
  await head.focus();
  await page.keyboard.press("Home");
  await page.getByRole("button", { name: "Shot 003", exact: true }).click();
  await head.focus();
  for (let i = 0; i < 10; i++) await page.keyboard.press("ArrowRight");
  await expect(head).toHaveAttribute("aria-valuenow", String(shot.start + 10));
  const next = program(page).getByRole("button", {
    name: "Next frame of the comparison",
  });
  await expect(next).toBeEnabled();
  await expect(program(page).locator(".timecode")).toHaveText("00:00:04:10");
  await next.click();
  await expect(head).toHaveAttribute("aria-valuenow", String(shot.start + 11));
  await expect(program(page).locator(".timecode")).toHaveText("00:00:04:11");
});

test("Properties shows the shot, the character and the Recast tool from what is selected", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.getByRole("button", { name: "Shot 002", exact: true }).click();
  const card = page.locator(".film-properties");
  await expect(
    card.getByRole("heading", { name: "Shot 002", exact: true }),
  ).toBeVisible();
  await expect(card).toContainText("00:00:02:00 – 00:00:04:00");
  await expect(
    card.getByRole("button", { name: "A 1", exact: true }),
  ).toBeVisible();
  await expect(card.getByRole("button", { name: /^Original/ })).toBeVisible();
  const rows = card.locator(".check-rows li");
  await expect(rows.first()).toContainText(
    /^[A-Z][a-z ]+(Passed|Failed|Not calibrated|Unsupported)/,
  );
  // Each row carries its reason; the reasons are also listed under Shot details.
  await expect(rows.first()).toHaveAttribute("title", /\S/);
  await card.getByText("Shot details").click();
  await expect(card).toContainText("Identity scoring is not calibrated.");

  const character = mock.value.characters.find((c) => c.target)!;
  await page.locator(`[data-row="cast-${character.id}"] .cast-header`).click();
  await expect(
    card.getByRole("heading", { name: character.label, exact: true }),
  ).toBeVisible();
  // A mapped target's label is its editable name field.
  await expect(
    card.getByRole("textbox", { name: `Target label for ${character.label}` }),
  ).toHaveValue(character.target!.label);

  await page.locator(".timeline-pinned .tool-header").click();
  await expect(card.getByRole("heading", { name: /^Recast · / })).toBeVisible();
  await expect(card).toContainText("25 fps");
  await expectBlind(page);
});

test("the Project panel lists the film's shots, cast and pictures", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  const panel = page.locator(".film-project");
  await expect(panel.getByRole("button", { name: /^Shot 001/ })).toBeVisible();
  await panel.getByRole("button", { name: /^Shot 004/ }).click();
  await expect(page.locator(".workspace-breadcrumb")).toContainText("Shot 004");
  await panel.getByRole("button", { name: /^Cast/ }).click();
  const visible = mock.value.characters.filter((c) => !c.hidden);
  // One record per visible character, then the face tracks no character holds yet.
  await expect(panel.locator(".project-record")).toHaveCount(
    visible.length + 1,
  );
  await expect(panel.locator(".project-record").last()).toContainText(
    "Unknown faces",
  );
  await panel.getByRole("button", { name: /^Assets/ }).click();
  // The film's own source leads; Library pictures follow, ready to drag onto a character.
  await expect(panel.locator(".film-asset").first()).toContainText(
    filmAsset.name,
  );
  await expect(
    panel.locator('.film-asset[draggable="true"]').first(),
  ).toBeVisible();
  await expectBlind(page);
});

for (const theme of ["dark", "light"] as const)
  test(`the film view passes axe, ${theme}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: theme });
    await page.addInitScript((value) => {
      localStorage.setItem(
        "gaiavfx-preferences",
        JSON.stringify({ state: { theme: value }, version: 1 }),
      );
    }, theme);
    const mock = await mockRecast(page);
    mock.existing();
    await page.goto("/");
    await openFilm(page);
    await page.getByRole("button", { name: "Shot 002", exact: true }).click();
    for (const tab of ["Chat", "Properties"]) {
      await page.getByRole("tab", { name: tab }).click();
      const audit = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze();
      expect(
        audit.violations.map((v) => ({
          id: v.id,
          nodes: v.nodes.map((n) => n.target.join(" ")),
        })),
      ).toEqual([]);
    }
  });

// Fix pass after the pilot usability round and Astra R2 (21 October).

test("the film's Source monitor cannot open media in the movie's Source monitor", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  await page
    .getByRole("region", { name: "Source monitor" })
    .locator(".monitor-well")
    .click({ button: "right" });
  const item = page.getByRole("menuitem", { name: /Open in Source monitor/ });
  await expect(item).toHaveAttribute("aria-disabled", "true");
  await expect(item).toHaveAttribute(
    "title",
    "Works on movies; a film is open",
  );
});

// Preview first (the user at CP1): a tile or key shows a version in Program; Accept & next (Enter) chooses it.
const choiceCalls = (mock: Awaited<ReturnType<typeof mockRecast>>) =>
  mock.calls.filter((c) => c.path.endsWith("/choice"));

test("a tile shows a version without choosing; Accept chooses it, Undo takes it back", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.getByRole("button", { name: "Shot 002", exact: true }).click();
  const card = page.locator(".film-properties");
  const tile = card.getByRole("button", { name: "B 2", exact: true });
  await expect(tile).toHaveAttribute("title", "Show version B in Program (2)");
  await tile.click();
  await expect(tile).toHaveAttribute("aria-pressed", "true");
  await expect(program(page).locator(".monitor-heading")).toContainText(
    "Shot 002 · B · Unchosen",
  );
  expect(choiceCalls(mock)).toHaveLength(0);
  // Enter on the focused tile accepts what Program shows and moves to the next waiting shot.
  await page.keyboard.press("Enter");
  await expect
    .poll(() => mock.value.shots[1].choice?.version)
    .toBe("version-1-B");
  expect(choiceCalls(mock)).toHaveLength(1);
  expect(choiceCalls(mock)[0].body).toMatchObject({
    expected_revision: 1,
    version: "version-1-B",
  });
  await expect(page.locator(".workspace-breadcrumb")).toContainText("Shot 012");
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const undo = page.getByRole("menuitem", { name: /Undo choice/ });
  await expect(undo).not.toHaveAttribute("aria-disabled", "true");
  await undo.click();
  await expect.poll(() => mock.value.shots[1].choice).toBeNull();
  expect(choiceCalls(mock).at(-1)?.body.version).toBe("unchosen");
  // Undo returns to the shot, still showing the version it took back, ready to accept again.
  await expect(page.locator(".workspace-breadcrumb")).toContainText("Shot 002");
  await expect(program(page).locator(".monitor-heading")).toContainText(
    "Shot 002 · B · Unchosen",
  );
  await expect(
    card.getByRole("button", { name: /^Accept & next/ }),
  ).toBeEnabled();
  await expectBlind(page);
});

// Astra review of CP1: an undo that finishes after the user moved shows the restored choice on its own shot, and leaves
// nothing stale for the next shot.
test("an undo that finishes after moving on shows the restored choice on its shot", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  mock.value.shots[0].choice = {
    version: "version-0-A",
    chosen_at: "2026-10-08T10:00:00Z",
    revision: 1,
  };
  await page.goto("/");
  await openFilm(page);
  const heading = program(page).locator(".monitor-heading");
  await page.getByRole("button", { name: "Shot 001", exact: true }).click();
  await expect(heading).toContainText("Shot 001 · A · A chosen");
  // Show B and accept it: the choice moves to B, and the next waiting shot is current.
  await page.keyboard.press("2");
  await page.keyboard.press("Enter");
  await expect
    .poll(() => mock.value.shots[0].choice?.version)
    .toBe("version-0-B");
  await expect(page.locator(".workspace-breadcrumb")).toContainText("Shot 002");
  // The undo's request is slow; meanwhile the user returns to Shot 001.
  let release!: () => void;
  const held = new Promise<void>((resolve) => (release = resolve));
  await page.route(
    "**/api/recasts/recast-1/shots/shot-0/choice",
    async (route) => {
      await held;
      await route.fallback();
    },
  );
  await page.keyboard.press("Control+z");
  await page.getByRole("button", { name: "Shot 001", exact: true }).click();
  release();
  await expect
    .poll(() => mock.value.shots[0].choice?.version)
    .toBe("version-0-A");
  await expect(heading).toContainText("Shot 001 · A · A chosen");
  // Another shot shows its own first version, not a leftover from the undo.
  await page.getByRole("button", { name: "Shot 003", exact: true }).click();
  await expect(heading).toContainText("Shot 003 · A · Unchosen");
  await expectBlind(page);
});

test("keys 1-9 and 0 change only Program; the chosen mark and the take stay", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("tab", { name: "Properties" }).click();
  // Shot 009 has version B chosen.
  await page.getByRole("button", { name: "Shot 009", exact: true }).click();
  const card = page.locator(".film-properties");
  const heading = program(page).locator(".monitor-heading");
  await expect(heading).toContainText("Shot 009 · B · B chosen");
  await expect(
    card.getByRole("button", { name: "B 2 chosen", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  const takeLabel = await page
    .locator('[data-item-key="take-shot-8"]')
    .getAttribute("aria-label");
  await page.keyboard.press("1");
  await expect(heading).toContainText("Shot 009 · A · B chosen");
  await expect(
    card.getByRole("button", { name: "A 1", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("0");
  await expect(heading).toContainText("Shot 009 · Original · B chosen");
  await expect(
    card.getByRole("button", { name: "Original 0", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    card.getByRole("button", { name: "B 2 chosen", exact: true }),
  ).toHaveAttribute("aria-pressed", "false");
  expect(choiceCalls(mock)).toHaveLength(0);
  expect(
    await page
      .locator('[data-item-key="take-shot-8"]')
      .getAttribute("aria-label"),
  ).toBe(takeLabel);
  await expectBlind(page);
});

test("Accept chooses the shown version over an earlier choice, and moves on without a request when it is chosen", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  // Shot 009 (B chosen): showing B and accepting changes nothing and moves on.
  await page.getByRole("button", { name: "Shot 009", exact: true }).click();
  await page.keyboard.press("Enter");
  await expect(page.locator(".workspace-breadcrumb")).toContainText("Shot 012");
  expect(choiceCalls(mock)).toHaveLength(0);
  // Back to Shot 009: show A, accept, and A replaces B.
  await page.getByRole("button", { name: "Shot 009", exact: true }).click();
  await page.keyboard.press("1");
  await page.keyboard.press("Enter");
  await expect
    .poll(() => mock.value.shots[8].choice?.version)
    .toBe("version-8-A");
  expect(choiceCalls(mock)).toHaveLength(1);
  await expectBlind(page);
});

test("a failed action stays visible in the film until dismissed", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.route("**/api/recasts/*/shots/*/choice", (route) =>
    route.fulfill({
      status: 500,
      json: { detail: "Saving the choice failed." },
    }),
  );
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("button", { name: "Shot 002", exact: true }).click();
  // Accept (Enter) is what saves a choice now; its failure stays readable.
  await page.keyboard.press("Enter");
  const alert = page.locator(".film-message[role='alert']");
  await expect(alert).toContainText("Saving the choice failed.");
  await alert.getByRole("button", { name: "Dismiss" }).click();
  await expect(alert).toHaveCount(0);
});

test("a film that cannot load is asked for once, then waits for Retry", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  let asked = 0;
  await page.route("**/api/recasts/missing-recast", (route) => {
    asked++;
    return route.fulfill({
      status: 404,
      json: { detail: "Recast not found." },
    });
  });
  await page.addInitScript(() =>
    localStorage.setItem(
      "gaiavfx:document",
      JSON.stringify({ kind: "film", recastId: "missing-recast" }),
    ),
  );
  await page.goto("/");
  await expect(page.getByRole("alert").first()).toContainText(
    "Recast not found.",
  );
  await page.waitForTimeout(2500);
  expect(asked).toBe(1);
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect.poll(() => asked).toBe(2);
});

test("a long film draws strips, not thousands of items, when zoomed out", async ({
  page,
}) => {
  const mock = await mockRecast(page, 2000);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  await expect(page.locator(".status-strip")).toHaveCount(1);
  for (const row of ["shots", "takes"])
    await expect(
      page.locator(`.track-row[data-row="${row}"] .span-strip`),
    ).toHaveCount(1);
  expect(
    await page.locator(".timeline-canvas .track-item").count(),
  ).toBeLessThan(200);
  await expect(
    page.getByRole("toolbar", { name: "Recast" }).getByRole("button", {
      name: /^Render: \d+ of \d+ renders used$/,
    }),
  ).toBeVisible();
  await expectBlind(page);
});

test("the Review menu carries a film's review only while a film is open", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  const review = page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Review", exact: true });
  await review.click();
  await expect(
    page.getByRole("menuitem", { name: /^Accept and go to next/ }),
  ).toHaveCount(0);
  await expect(page.getByRole("menuitem", { name: /^Choose/ })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await openFilm(page);
  await review.click();
  await expect(
    page.getByRole("menuitem", { name: /^Accept and go to next/ }),
  ).toBeVisible();
  await expect(page.getByRole("menuitem", { name: /^Choose/ })).toBeVisible();
  await expect(
    page.getByRole("menuitem", { name: /^Undo choice/ }),
  ).toBeVisible();
});

// Parity row 4 (recast.spec "failed checks lead the blind-safe Stage list and summary"). The film keeps the measured
// order (decision D1, open for CP3), so the order assertion is the measured one; the summary and words are exact.
test("a failed check is announced, and every check reads in words in measured order", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  mock.existing();
  // Both versions measure the same checks (a check only one version has is left out; see the test below).
  const [a, b] = mock.value.shots[0].versions;
  a.checks.push(
    {
      check: "residual_source",
      verdict: "fail",
      calibrated: true,
      measured: 0.9,
      reason: "Source face remains visible.",
    },
    {
      check: "continuity",
      verdict: "pass",
      calibrated: true,
      measured: null,
      reason: "Continuity checked.",
    },
  );
  b.checks.push(
    {
      check: "residual_source",
      verdict: "pass",
      calibrated: true,
      measured: 0.2,
      reason: "No source face remains.",
    },
    {
      check: "continuity",
      verdict: "pass",
      calibrated: true,
      measured: null,
      reason: "Continuity checked.",
    },
  );
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.getByRole("button", { name: "Shot 001", exact: true }).click();
  const card = page.locator(".film-properties");
  await expect(
    card.getByRole("status").filter({ hasText: "failed" }),
  ).toHaveText("1 check failed: Original face showing");
  const rows = card.locator(".check-rows li");
  await expect(rows).toHaveCount(4);
  for (const [index, [name, word, title]] of [
    [
      "Identity",
      "Not calibrated",
      "Measured 0.87. Identity scoring is not calibrated.",
    ],
    ["Seams", "Unsupported", "Window seams have no qualified check."],
    [
      "Original face showing",
      "Failed",
      "Measured 0.9. Source face remains visible.",
    ],
    ["Continuity", "Passed", "Continuity checked."],
  ].entries()) {
    await expect(rows.nth(index).locator("span")).toHaveText(name);
    await expect(rows.nth(index).locator("strong")).toHaveText(word);
    await expect(rows.nth(index)).toHaveAttribute("title", title);
  }
  await expect(rows.nth(2)).toHaveClass(/check-fail/);
  await expectBlind(page);
});

// Fable #3 P3: the Show and Choose submenus list every version the shot has, past D, and none it lacks. Show carries
// the keys; Choose has none and records the choice.
test("a shot's Show and Choose menus list exactly its versions", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  mock.existing();
  const shot = mock.value.shots[1];
  shot.versions = "ABCDEF".split("").map((letter, i) => ({
    ...shot.versions[0],
    id: `version-1-${letter}`,
    letter,
    asset_id: `preview-${i}`,
  }));
  await page.goto("/");
  await openFilm(page);
  const items = async (name: string, submenu: string, first: RegExp) => {
    await page
      .getByRole("button", { name, exact: true })
      .click({ button: "right" });
    await page.getByRole("menuitem", { name: submenu, exact: true }).hover();
    await expect(page.getByRole("menuitem", { name: first })).toBeVisible();
    const texts = await page
      .getByRole("menu")
      .last()
      .getByRole("menuitem")
      .evaluateAll((els) => els.map((el) => el.textContent?.trim() ?? ""));
    await page.keyboard.press("Escape");
    await page.keyboard.press("Escape");
    return texts;
  };
  expect(await items("Shot 002", "Choose", /^Keep original/)).toEqual([
    "Keep original",
    "Choose version A",
    "Choose version B",
    "Choose version C",
    "Choose version D",
    "Choose version E",
    "Choose version F",
  ]);
  expect(await items("Shot 001", "Choose", /^Keep original/)).toEqual([
    "Keep original",
    "Choose version A",
    "Choose version B",
  ]);
  expect(await items("Shot 002", "Show", /^Show original/)).toEqual([
    "Show original0",
    "Show version A1",
    "Show version B2",
    "Show version C3",
    "Show version D4",
    "Show version E5",
    "Show version F6",
  ]);
  // A version the shot lacks answers its key with the reason, not with another shot's version.
  await page.getByRole("button", { name: "Shot 001", exact: true }).click();
  await page.keyboard.press("5");
  await expect(program(page).locator(".monitor-heading")).toContainText(
    "Shot 001 · A",
  );
  expect(choiceCalls(mock)).toHaveLength(0);
  // Choose records the choice from the menu.
  await page
    .getByRole("button", { name: "Shot 002", exact: true })
    .click({ button: "right" });
  await page.getByRole("menuitem", { name: "Choose", exact: true }).hover();
  await page.getByRole("menuitem", { name: "Choose version C" }).click();
  await expect
    .poll(() => mock.value.shots[1].choice?.version)
    .toBe("version-1-C");
  expect(choiceCalls(mock)).toHaveLength(1);
  await expectBlind(page);
});

// Fable #3 P3-8: the film's Jobs tab is a blindness surface. A recast window renders as a replacement operation
// whose automatic decisions name the arm; in a film those details stay hidden, and the job still shows its state.
test("a film's Jobs tab and Jobs sheet list recast renders without their arms or errors", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  const operation = {
    id: "op-window-1",
    kind: "character_replacement",
    state: "succeeded",
    awaits_review: false,
    request: {
      first_plan: {
        arm: {
          treatment: "PRIVATE SEED ARM ROUND",
          framing: "hidden-operation",
        },
        reasons: ["PRIVATE SEED ARM ROUND chosen first"],
      },
    },
    budget_reserved: 1,
    budget_released_at: null,
    cancel_requested: false,
    review: [
      {
        decided_by: "advancer",
        round: 1,
        arm: { treatment: "PRIVATE SEED ARM ROUND", framing: "tight" },
        reasons: ["PRIVATE SEED ARM ROUND kept"],
      },
    ],
    error: null,
    created_at: "2026-10-21T10:00:00Z",
    updated_at: "2026-10-21T10:05:00Z",
  };
  const failed = {
    ...operation,
    id: "op-window-2",
    state: "failed",
    review: null,
    error:
      "ComfyUI execution error: seed 81723 · PRIVATE SEED ARM ROUND · hidden-operation",
  };
  await page.route("**/api/production/operations?**", (route) =>
    route.fulfill({ json: [operation, failed] }),
  );
  await page.goto("/");
  await openFilm(page);
  const check = async (jobs: Locator) => {
    await expect(jobs).toHaveCount(2);
    await expect(jobs.first()).toContainText("Recast render");
    await expect(jobs.first()).toContainText("succeeded");
    await expect(jobs.nth(1)).toContainText(
      "This render failed. Its details stay hidden while a film is open",
    );
    await expectBlind(page);
  };
  // The dock's Jobs tab.
  await page.getByRole("tab", { name: "Jobs" }).click();
  await check(page.locator(".dock-scroll .task-item"));
  // The shell's Jobs sheet, from the workspace bar and from Render › Jobs.
  await page.getByRole("button", { name: "Jobs", exact: true }).click();
  const sheet = page.getByRole("dialog", { name: "Jobs" });
  await check(sheet.locator(".task-item"));
  await sheet.getByRole("button", { name: "Close panel" }).click();
  await page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Render", exact: true })
    .click();
  await page.getByRole("menuitem", { name: /^Open jobs/ }).click();
  await check(sheet.locator(".task-item"));
});

// Usability round 1 (A1, T1-F1): a check that only some versions of a shot have, or that versions measure
// differently (supported or not, calibrated or not), would hint at how a version was made. It is left out for every
// version, with a note; an uncalibrated score never reads "Failed".
test("only checks every version measured the same way are shown", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  mock.existing();
  const [a, b] = mock.value.shots[0].versions;
  // Shared and alike: identity (uncalibrated) and seam (unsupported) from the fixture, plus residual source below.
  a.checks.push(
    {
      check: "residual_source",
      verdict: "fail",
      calibrated: false,
      measured: 0.9,
      reason: "Source face may remain.",
    },
    // Only A has it.
    {
      check: "background_geometry",
      verdict: "pass",
      calibrated: true,
      measured: null,
      reason: "Passed in every window.",
    },
    // Measured on A, unsupported on B.
    {
      check: "letterbox_difference",
      verdict: "pass",
      calibrated: true,
      measured: 0.01,
      reason: "Letterbox matches.",
    },
  );
  b.checks.push(
    {
      check: "residual_source",
      verdict: "pass",
      calibrated: false,
      measured: 0.3,
      reason: "Source face may remain.",
    },
    {
      check: "letterbox_difference",
      verdict: "unsupported",
      calibrated: false,
      measured: null,
      reason: "Not measured in every window.",
    },
  );
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.getByRole("button", { name: "Shot 001", exact: true }).click();
  const card = page.locator(".film-properties");
  for (const key of ["1", "2"]) {
    await page.keyboard.press(key);
    const letter = key === "1" ? "A" : "B";
    await expect(card.getByRole("heading", { name: /^Checks/ })).toHaveText(
      `Checks · version ${letter}`,
    );
    const rows = card.locator(".check-rows li");
    expect(
      await rows.evaluateAll((els) =>
        els.map((el) => el.querySelector("span")?.textContent),
      ),
    ).toEqual(["Identity", "Seams", "Original face showing"]);
    // Uncalibrated reads the same whatever the score: no "Failed" for A, no "Passed" for B.
    await expect(rows.nth(2).locator("strong")).toHaveText("Not calibrated");
    await expect(card).toContainText(
      "Only checks measured the same way on every version of a shot are shown.",
    );
    await expect(card).not.toContainText("Background unchanged");
    await expect(card).not.toContainText("Letterbox match");
    await expect(
      card.getByRole("status").filter({ hasText: "failed" }),
    ).toHaveCount(0);
  }
  await expectBlind(page);
});

// Usability round 1 (B2, B3): the card says who the analysis saw in a shot, planned or not, and Shot details offers
// the Exclude its gap advice names.
test("the shot card names who is seen, and Shot details excludes and includes the shot", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  mock.existing();
  mock.value.shots[0].plan = null;
  mock.value.shots[0].status = "unplanned";
  mock.value.shots[0].appearances.push({
    character_id: null,
    track_ids: ["track-0-x"],
    frames_with_face: 4,
    coverage: 0.1,
    confirmed: false,
  });
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.getByRole("button", { name: "Shot 001", exact: true }).click();
  const card = page.locator(".film-properties");
  await expect(card.locator("dt", { hasText: "Seen" })).toBeVisible();
  await expect(card.locator("dt:has-text('Seen') + dd")).toHaveText(
    "Mara, Elias, 1 unknown face track",
  );
  await expect(card.getByText(/^Person links \(0\)/)).toBeVisible();
  await card.getByText("Shot details", { exact: true }).click();
  await card.getByRole("button", { name: "Exclude shot", exact: true }).click();
  await expect
    .poll(() => mock.calls.at(-1))
    .toMatchObject({
      path: "/recast-1/shots/shot-0",
      method: "PATCH",
      body: { included: false },
    });
  await expect(
    card.getByRole("button", { name: "Include shot", exact: true }),
  ).toBeVisible();
  await card.getByRole("button", { name: "Include shot", exact: true }).click();
  await expect
    .poll(() => mock.calls.at(-1)?.body)
    .toMatchObject({ included: true });
  await expectBlind(page);
});
