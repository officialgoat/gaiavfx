import type { Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import {
  expect,
  expectBlind,
  openFilm,
  recastCommands,
  test,
  type RecastWorld,
} from "./recast-world";

// ADR 0017 §8 and §11 after CP1: the film's review queue is the Project panel's Review bin, with the shots waiting for
// a choice, those that need attention in the run's range, and the failed ones, at the trackbar's counts. Versions
// are shown first and Enter accepts (the user's preview-first decision). The recast is the service's own
// (tests/browser_recast.py): its statuses, counts and choices are Python's.

/** Mara and Elias (the user's pictures) take twelve 2 s shots in turn at 25 fps, all rendered twice, except: Shots
 * 005 and 011 have a gap, Shots 007 and 010 failed, and Shot 008 is still rendering. Seven shots wait for review. */
test.use({
  scene: {
    shots: 12,
    length: 50,
    rate: [25, 1],
    characters: ["Mara", "Elias"],
    targets: ["confirmed", "confirmed"],
    rendered: 12,
    takes: 2,
    states: { 4: "gap", 10: "gap", 6: "failed", 9: "failed", 7: "rendering" },
  },
});

const bins = (page: Page) => page.locator(".film-project .project-bins");
const filters = (page: Page) =>
  page.getByRole("group", { name: "Review filter" });
const trackbar = (page: Page) => page.getByRole("toolbar", { name: "Recast" });
const breadcrumb = (page: Page) => page.locator(".workspace-breadcrumb");
const shotsIn = (list: ReturnType<Page["getByRole"]>) =>
  list
    .getByRole("listitem")
    .evaluateAll((els) =>
      els.map((el) => /Shot \d{3}/.exec(el.textContent ?? "")?.[0]),
    );
const version = async (world: RecastWorld, index: number, letter: string) =>
  (await world.read()).shots[index].versions.find((v) => v.letter === letter)!
    .id;
const choice = async (world: RecastWorld, index: number) =>
  (await world.read()).shots[index].choice?.version ?? null;

const commands = new WeakMap<Page, ReturnType<typeof recastCommands>>();
test.beforeEach(async ({ page, world }) => {
  commands.set(page, recastCommands(page));
  await page.goto("/");
  await openFilm(page, world);
});
const choices = (page: Page) =>
  commands.get(page)!.filter((c) => c.path.endsWith("/choice"));

test("the Review step opens the waiting shots; Enter accepts and advances, and the accepted row keeps its mark", async ({
  page,
  world,
}) => {
  await trackbar(page)
    .getByRole("button", { name: /^Review: / })
    .click();
  await expect(
    bins(page).getByRole("button", { name: /^Review/ }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    filters(page).getByRole("button", { name: /^Waiting/ }),
  ).toHaveAttribute("aria-pressed", "true");
  const list = page.getByRole("list", { name: "Shots waiting for review" });
  const rows = list.getByRole("listitem");
  // The waiting shots, in film order.
  expect(await shotsIn(list)).toEqual([
    "Shot 001",
    "Shot 002",
    "Shot 003",
    "Shot 004",
    "Shot 006",
    "Shot 009",
    "Shot 012",
  ]);
  await expect(breadcrumb(page)).toContainText("Shot 001");
  await expect(rows.first().getByRole("button")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  // The focus moves to the current row, so the review keys (and Enter) act there, not on the trackbar.
  await expect(rows.first().getByRole("button")).toBeFocused();
  // Show B, accept: the choice is saved and the next waiting shot is current; the accepted row stays, marked.
  await page.keyboard.press("2");
  await page.keyboard.press("Enter");
  await expect.poll(() => choice(world, 0)).toBe(await version(world, 0, "B"));
  await expect(breadcrumb(page)).toContainText("Shot 002");
  await expect(rows.first()).toContainText("Shot 001");
  await expect(rows.first()).toContainText("B chosen");
  await expect(rows.nth(1).getByRole("button")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(rows.nth(1).getByRole("button")).toBeFocused();
  await expect(
    bins(page).getByRole("button", { name: /^Review/ }),
  ).toContainText("6");
  // A row selects its shot.
  await rows.nth(4).getByRole("button").click();
  await expect(breadcrumb(page)).toContainText("Shot 006");
  await expectBlind(page);
});

test("the queue's counts equal the trackbar's, and Failed lists the failed shots", async ({
  page,
  world,
}) => {
  await bins(page)
    .getByRole("button", { name: /^Review/ })
    .click();
  await expect(
    trackbar(page).getByRole("button", { name: /^Review: / }),
  ).toHaveAccessibleName("Review: 7 waiting");
  await expect(
    filters(page).getByRole("button", { name: "Waiting 7", exact: true }),
  ).toBeVisible();
  await expect(
    trackbar(page).getByRole("button", { name: /^2 need attention/ }),
  ).toBeVisible();
  await expect(
    filters(page).getByRole("button", {
      name: "Attention 2",
      exact: true,
    }),
  ).toBeVisible();
  await filters(page)
    .getByRole("button", { name: "Failed 2", exact: true })
    .click();
  const failed = page.getByRole("list", { name: "Failed shots" });
  expect(await shotsIn(failed)).toEqual(["Shot 007", "Shot 010"]);
  await expect(failed.getByRole("listitem").first()).toContainText("Failed");
  await expectBlind(page);
});

test("the Review workspace opens the queue in the Project panel", async ({
  page,
  world,
}) => {
  // The Library tab is in front; the Review workspace brings the Project panel's queue forward.
  await page.getByRole("tab", { name: "Library" }).click();
  await page
    .getByRole("navigation", { name: "Workspaces" })
    .getByRole("button", { name: "Review", exact: true })
    .click();
  await expect(page.getByRole("tab", { name: "Project" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(
    page.getByRole("list", { name: "Shots waiting for review" }),
  ).toBeVisible();
});

for (const theme of ["dark", "light"] as const)
  test(`the review queue passes axe's default rules, ${theme}`, async ({
    page,
    world,
  }) => {
    await page.emulateMedia({ colorScheme: theme });
    await page.addInitScript((value) => {
      localStorage.setItem(
        "gaiavfx-preferences",
        JSON.stringify({ state: { theme: value }, version: 1 }),
      );
    }, theme);
    // The film reopens from the stored document, now in this theme.
    await page.reload();
    await expect(page.locator(".timeline-heading")).toContainText(world.title);
    await page.keyboard.press("q");
    await expect(
      page.getByRole("list", { name: "Shots waiting for review" }),
    ).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    const audit = await new AxeBuilder({ page }).analyze();
    expect(
      audit.violations.map((v) => ({
        id: v.id,
        nodes: v.nodes.map((n) => n.target.join(" ")),
      })),
    ).toEqual([]);
    await expectBlind(page);
  });

// Astra review of the queue (22 October).

test("Enter accepts only on the current row or the shown tile; elsewhere it selects or shows", async ({
  page,
  world,
}) => {
  await trackbar(page)
    .getByRole("button", { name: /^Review: / })
    .click();
  const rows = page
    .getByRole("list", { name: "Shots waiting for review" })
    .getByRole("listitem");
  await expect(rows.first().getByRole("button")).toBeFocused();
  // Tab to Shot 002's row: Enter selects it, and nothing is chosen.
  await page.keyboard.press("Tab");
  await expect(rows.nth(1).getByRole("button")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(breadcrumb(page)).toContainText("Shot 002");
  expect(choices(page)).toHaveLength(0);
  // Now it is the current row: Enter accepts what Program shows for Shot 002.
  await page.keyboard.press("Enter");
  await expect.poll(() => choice(world, 1)).toBe(await version(world, 1, "A"));
  expect(choices(page)).toHaveLength(1);
  // A tile that is not shown: Enter shows it; only then does Enter accept.
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.getByRole("button", { name: "Shot 012", exact: true }).click();
  const card = page.locator(".film-properties");
  const tile = card.getByRole("button", { name: "B 2", exact: true });
  await tile.focus();
  await page.keyboard.press("Enter");
  await expect(tile).toHaveAttribute("aria-pressed", "true");
  expect(choices(page)).toHaveLength(1);
  await page.keyboard.press("Enter");
  await expect
    .poll(() => choice(world, 11))
    .toBe(await version(world, 11, "B"));
  await expectBlind(page);
});

test("opening the queue brings the Project panel forward, even when the bin is already Review", async ({
  page,
  world,
}) => {
  await page.keyboard.press("q");
  await expect(
    page.getByRole("list", { name: "Shots waiting for review" }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Library" }).click();
  await expect(
    page.getByRole("list", { name: "Shots waiting for review" }),
  ).toHaveCount(0);
  await trackbar(page)
    .getByRole("button", { name: /need attention/ })
    .click();
  await expect(page.getByRole("tab", { name: "Project" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  const list = page.getByRole("list", { name: "Shots needing attention" });
  await expect(list.getByRole("button", { pressed: true })).toBeFocused();
});

test("Q after a mouse-opened queue returns to the bin before and keeps the reviewer's shot", async ({
  page,
  world,
}) => {
  await bins(page)
    .getByRole("button", { name: /^Review/ })
    .click();
  await page
    .getByRole("list", { name: "Shots waiting for review" })
    .getByRole("listitem")
    .nth(1)
    .getByRole("button")
    .click();
  await expect(breadcrumb(page)).toContainText("Shot 002");
  await page.getByRole("button", { name: "Shot 002", exact: true }).focus();
  await page.keyboard.press("q");
  await expect(
    bins(page).getByRole("button", { name: /^Shots/ }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(breadcrumb(page)).toContainText("Shot 002");
});

test("Need attention lists every gap in the film, and shots that start waiting join an open list", async ({
  page,
  world,
}) => {
  // Gaps at Shots 005 and 011, both in the run's range (the whole film). Usability round 2 (C4): the list is
  // film-wide, and its note says how many are in the run's range.
  await expect(
    trackbar(page).getByRole("button", { name: /^2 need attention/ }),
  ).toBeVisible();
  await trackbar(page)
    .getByRole("button", { name: /need attention/ })
    .click();
  const attention = page.getByRole("list", { name: "Shots needing attention" });
  expect(await shotsIn(attention)).toEqual(["Shot 005", "Shot 011"]);
  await expect(page.locator(".queue-scope")).toHaveText(
    "Shots with a gap, in the whole film; 2 of them in the run's range, 00:00:00–00:00:24.",
  );
  await filters(page)
    .getByRole("button", { name: /^Waiting/ })
    .click();
  const waiting = page.getByRole("list", { name: "Shots waiting for review" });
  await expect(waiting.getByRole("listitem")).toHaveCount(7);
  // Shot 008 finishes rendering while the list is open: it joins the list in film order.
  await world.settle("character_replacement");
  await world.settle("shot_assembly");
  await expect(waiting.getByRole("listitem")).toHaveCount(8, {
    timeout: 8000,
  });
  await expect(waiting.getByRole("listitem").nth(5)).toContainText("Shot 008");
});

// Usability round 1 (B17, T4-F1, T7-F4, T7-F1): opening the queue from the Review menu puts the focus on the current
// row; a review key brings the focus back to where Enter accepts; each choice is announced.
test("the Review menu's queue focuses the current row; review keys return focus; choices are announced", async ({
  page,
  world,
}) => {
  await page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Review", exact: true })
    .click();
  await page.getByRole("menuitem", { name: /^Review queue/ }).click();
  const waiting = page.getByRole("list", { name: "Shots waiting for review" });
  await expect(waiting.getByRole("button", { pressed: true })).toBeFocused();
  // A click elsewhere moves the focus away; a review key brings it back to the current row.
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.keyboard.press("2");
  await expect(waiting.getByRole("button", { pressed: true })).toBeFocused();
  const live = page.locator("[aria-live=polite].sr-only");
  const shot = await waiting
    .getByRole("button", { pressed: true })
    .evaluate((el) => /Shot \d{3}/.exec(el.textContent ?? "")![0]);
  await page.keyboard.press("Enter");
  await expect(live).toHaveText(`${shot}: version B chosen.`);
  await page.keyboard.press("Control+z");
  await expect(live).toHaveText(`Undone: ${shot} is unchosen.`);
  // With the queue closed, a review key focuses the shown tile.
  await page.keyboard.press("q");
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.keyboard.press("1");
  await expect(
    page.locator(".version-tiles").getByRole("button", { pressed: true }),
  ).toBeFocused();
});

// Usability round 2 (C8): after a monitor button, the wipe slider or a closed menu, Enter still accepts and the review
// keys still show versions.
test("Enter and the review keys work after a monitor button, the wipe slider and a closed menu", async ({
  page,
  world,
}) => {
  await page.keyboard.press("q");
  const waiting = page.getByRole("list", { name: "Shots waiting for review" });
  const row = waiting.getByRole("button", { pressed: true });
  await expect(row).toBeFocused();
  const program = page.getByRole("region", { name: "Program monitor" });
  // A monitor button hands the focus back to the current row, and shows its state.
  const compare = program.getByRole("button", { name: "Original / version" });
  await compare.click();
  await expect(compare).toHaveAttribute("aria-pressed", "true");
  await expect(row).toBeFocused();
  await compare.click();
  // On the wipe slider (on by default), digits reach the review commands while the arrows stay with the slider.
  const wipe = program.getByLabel("Before/after wipe position");
  await wipe.focus();
  await page.keyboard.press("2");
  await expect(program.locator(".monitor-heading")).toContainText("· B ·");
  // A closed menu returns the focus to where it was, so Enter accepts.
  await row.focus();
  await page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Review", exact: true })
    .click();
  await page.keyboard.press("Escape");
  await expect(row).toBeFocused();
});
