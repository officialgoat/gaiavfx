import { test, expect, type Page } from "@playwright/test";
import { LATER_CHANGE, filmAsset, mockRecast } from "./recast-fixtures";

// CP3 B3 (ADR 0017 amendment item 7, ADR 0012 Amendment 7 §1): a film's cast edits are journaled in the recast. The
// notice after one offers Undo; Mod+Z undoes the film's newest step, a choice or a cast edit, and Mod+Shift+Z redoes
// it; after a reload the stored edits are still reachable. Review's Undo choice takes back choices only.

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

const header = (id: string) => `[data-row="cast-${id}"] .cast-header`;
const card = (page: Page) => page.locator(".film-properties");
const notice = (page: Page) => page.locator(".film-message.notice");
const live = (page: Page) => page.locator("[aria-live=polite].sr-only");
const ids = (mock: Mock) => mock.value.characters.map((c) => c.id);
const undoCalls = (mock: Mock) =>
  mock.calls.filter((c) => /\/edits\/[^/]+\/undo$/.test(c.path));
const ALL = ["character-0", "character-1", "character-2"];
const MERGED = ["character-0", "character-1"];

async function selectCharacter(page: Page, id: string) {
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.locator(header(id)).click();
}

async function editMenu(page: Page) {
  await page.getByRole("button", { name: "Edit", exact: true }).click();
}

/** Merges The courier into Mara from the courier's card, through the merge sheet. */
async function mergeCourier(page: Page) {
  await selectCharacter(page, "character-2");
  await card(page)
    .getByRole("button", { name: "Merge The courier into Mara…" })
    .click();
  await page
    .getByRole("dialog", { name: "Merge characters" })
    .getByRole("button", { name: "Merge characters", exact: true })
    .click();
  await expect(notice(page)).toContainText("Merged The courier into Mara.");
}

/** Shows version A of Shot 001 and accepts it: a choice. */
async function chooseFirst(page: Page, mock: Mock) {
  await page.getByRole("button", { name: "Shot 001", exact: true }).click();
  await page.keyboard.press("1");
  await page.keyboard.press("Enter");
  await expect
    .poll(() => mock.value.shots[0].choice?.version)
    .toBe("version-0-A");
}

async function reopen(page: Page) {
  await page.reload();
  await expect(page.locator(".timeline-heading")).toContainText(filmAsset.name);
}

// A16: the film's undo is the recast's; it never asks Direction for a receipt or its undo.
const direction = new WeakMap<Page, string[]>();
test.beforeEach(async ({ page }) => {
  const seen: string[] = [];
  direction.set(page, seen);
  page.on("request", (request) => {
    // The shell lists movies and places at start; receipts and their undo are what a movie's edit history uses.
    const path = new URL(request.url()).pathname;
    if (
      path.includes("/receipts") ||
      (path.startsWith("/api/direction") && path.includes("/undo"))
    )
      seen.push(`${request.method()} ${path}`);
  });
  // No movie in this library: the shared test backend keeps other specs' movies, and the shell would open the first
  // one and read its receipts at start, before the film opens.
  await page.route("**/api/direction/movies", (route) =>
    route.fulfill({ json: [] }),
  );
  const mock = await mockRecast(page);
  mock.existing();
  (page as Page & { mock?: Mock }).mock = mock;
  await page.goto("/");
  await openFilm(page);
});
test.afterEach(async ({ page }) => {
  expect(direction.get(page)).toEqual([]);
  await expectBlind(page);
});
const mockOf = (page: Page) => (page as Page & { mock: Mock }).mock;

test("A11: a merge's notice offers Undo; Undo puts the characters back, Redo merges again, and the keys name it", async ({
  page,
}) => {
  const mock = mockOf(page);
  await mergeCourier(page);
  expect(ids(mock)).toEqual(MERGED);
  await editMenu(page);
  const undo = page.getByRole("menuitem", {
    name: /^Undo: Merged The courier into Mara/,
  });
  await expect(undo).toBeVisible();
  await expect(undo).toContainText("Ctrl+Z");
  await expect(page.getByRole("menuitem", { name: /^Redo: / })).toHaveCount(0);
  await page.keyboard.press("Escape");

  await notice(page).getByRole("button", { name: "Undo", exact: true }).click();
  await expect(notice(page)).toContainText(
    "Undone: Merged The courier into Mara",
  );
  await expect.poll(() => ids(mock)).toEqual(ALL);
  await expect(page.locator(header("character-2"))).toBeVisible();
  expect(undoCalls(mock).map((c) => [c.path, c.body])).toEqual([
    ["/recast-1/edits/edit-1/undo", { expected_revision: 2 }],
  ]);
  await editMenu(page);
  const redo = page.getByRole("menuitem", {
    name: /^Redo: Merged The courier into Mara/,
  });
  await expect(redo).toBeVisible();
  await expect(redo).toContainText("Ctrl+Shift+Z");
  await page.keyboard.press("Escape");

  // Redo is the undo of the inverse entry.
  await notice(page).getByRole("button", { name: "Redo", exact: true }).click();
  await expect(notice(page)).toContainText(
    "Redone: Merged The courier into Mara",
  );
  await expect.poll(() => ids(mock)).toEqual(MERGED);
  expect(undoCalls(mock).at(-1)?.path).toBe("/recast-1/edits/edit-2/undo");
  // The keys walk the same stack (each waits for the film to settle: a key while saving does nothing).
  await page.keyboard.press("Control+z");
  await expect(notice(page)).toContainText(
    "Undone: Merged The courier into Mara",
  );
  expect(ids(mock)).toEqual(ALL);
  await page.keyboard.press("Control+Shift+Z");
  await expect(notice(page)).toContainText(
    "Redone: Merged The courier into Mara",
  );
  expect(ids(mock)).toEqual(MERGED);
  expect(mock.edits.map((e) => e.kind)).toEqual([
    "redo",
    "undo",
    "redo",
    "undo",
    "edit",
  ]);
  expect(mock.value.edits?.[0].summary).toBe(
    "Redo: Merged The courier into Mara",
  );
});

test("A12: one stack orders a choice and a track move; Review's Undo choice skips the move", async ({
  page,
}) => {
  const mock = mockOf(page);
  const moved = () =>
    mock.value.characters[1].tracks.some((t) => t.id === "track-0-0");
  await chooseFirst(page, mock);
  await selectCharacter(page, "character-0");
  await card(page).getByRole("button", { name: "Face tracks…" }).click();
  const drawer = page.getByRole("dialog", { name: "Mara · tracks" });
  await drawer.getByRole("checkbox").nth(0).check();
  await drawer.getByLabel("Move to").selectOption("character-1");
  await drawer.getByRole("button", { name: "Move 1 track" }).click();
  await expect(notice(page)).toContainText("Moved 1 track to Elias.");
  expect(moved()).toBe(true);

  // The newest step first: the move, then the choice; the Edit menu names each.
  await editMenu(page);
  await expect(
    page.getByRole("menuitem", { name: /^Undo: Moved 1 face track to Elias/ }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+z");
  await expect(notice(page)).toContainText(
    "Undone: Moved 1 face track to Elias",
  );
  expect(moved()).toBe(false);
  expect(mock.value.shots[0].choice?.version).toBe("version-0-A");
  await editMenu(page);
  await expect(
    page.getByRole("menuitem", { name: /^Undo choice/ }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+z");
  await expect(live(page)).toHaveText("Undone: Shot 001 is unchosen.");
  expect(mock.value.shots[0].choice).toBeNull();
  expect(moved()).toBe(false);

  // Redo brings back the choice, then the move.
  await page.keyboard.press("Control+Shift+Z");
  await expect(live(page)).toHaveText("Redone: Shot 001: version A chosen.");
  expect(mock.value.shots[0].choice?.version).toBe("version-0-A");
  await page.keyboard.press("Control+Shift+Z");
  await expect(notice(page)).toContainText(
    "Redone: Moved 1 face track to Elias",
  );
  expect(moved()).toBe(true);

  // Review's Undo choice takes back the choice and leaves the newer move.
  await page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Review", exact: true })
    .click();
  await page.getByRole("menuitem", { name: /^Undo choice/ }).click();
  await expect(live(page)).toHaveText("Undone: Shot 001 is unchosen.");
  expect(mock.value.shots[0].choice).toBeNull();
  expect(moved()).toBe(true);
  await editMenu(page);
  await expect(
    page.getByRole("menuitem", { name: /^Undo: Moved 1 face track to Elias/ }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
});

test("A13: clearing a picture and setting a pair aside are undone through their stored entries", async ({
  page,
}) => {
  const mock = mockOf(page);
  const target = structuredClone(mock.value.characters[0].target);
  await selectCharacter(page, "character-0");
  await card(page).getByRole("button", { name: "Clear", exact: true }).click();
  await expect(notice(page)).toContainText("Cleared Mara's picture.");
  await expect.poll(() => mock.value.characters[0].target).toBeNull();
  await notice(page).getByRole("button", { name: "Undo", exact: true }).click();
  await expect(notice(page)).toContainText("Undone: Cleared Mara's picture");
  await expect.poll(() => mock.value.characters[0].target).toEqual(target);

  await card(page)
    .getByRole("button", { name: "Not the same person: The courier and Mara" })
    .click();
  await expect(notice(page)).toContainText(
    "Set aside Mara and The courier: not the same person.",
  );
  await expect.poll(() => mock.value.not_same?.length).toBe(1);
  await expect(card(page).locator(".film-card-similar")).toHaveCount(0);
  await editMenu(page);
  await expect(
    page.getByRole("menuitem", {
      name: /^Undo: Set aside Mara and The courier/,
    }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+z");
  await expect.poll(() => mock.value.not_same?.length).toBe(0);
  await expect(card(page).locator(".film-card-similar")).toHaveCount(1);
  expect(undoCalls(mock).map((c) => c.path)).toEqual([
    "/recast-1/edits/edit-1/undo",
    "/recast-1/edits/edit-3/undo",
  ]);
});

// A picture's new name is the picture set again: journaled, so it joins the stack like any cast edit.
test("renaming a picture offers Undo, which puts the old name back", async ({
  page,
}) => {
  const mock = mockOf(page);
  const target = structuredClone(mock.value.characters[0].target);
  await selectCharacter(page, "character-0");
  const label = card(page).getByLabel("Target label for Mara", { exact: true });
  await label.fill("Arin in the film");
  await label.press("Enter");
  await expect
    .poll(() => mock.value.characters[0].target?.label)
    .toBe("Arin in the film");
  await notice(page).getByRole("button", { name: "Undo", exact: true }).click();
  await expect.poll(() => mock.value.characters[0].target).toEqual(target);
  expect(undoCalls(mock)).toHaveLength(1);
});

test("A14: a refused undo shows the service's sentence, and the next Mod+Z reaches the step below", async ({
  page,
}) => {
  const mock = mockOf(page);
  await chooseFirst(page, mock);
  await selectCharacter(page, "character-1");
  await card(page)
    .getByRole("button", { name: "Hide character", exact: true })
    .click();
  await expect(notice(page)).toContainText("Hid Elias.");
  await expect.poll(() => mock.value.characters[1].hidden).toBe(true);
  mock.refuseNextUndo(LATER_CHANGE);
  await page.keyboard.press("Control+z");
  await expect(notice(page)).toHaveText(new RegExp(`^${LATER_CHANGE}`));
  await expect(notice(page).getByRole("button", { name: "Undo" })).toHaveCount(
    0,
  );
  expect(mock.value.characters[1].hidden).toBe(true);
  // The refused edit left the stack: the choice below it is next.
  await editMenu(page);
  await expect(
    page.getByRole("menuitem", { name: /^Undo choice/ }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+z");
  await expect.poll(() => mock.value.shots[0].choice).toBeNull();
  expect(mock.value.characters[1].hidden).toBe(true);
  expect(undoCalls(mock)).toHaveLength(1);
});

test("A15: after a reload, Mod+Z undoes the stored merge and Mod+Shift+Z redoes it from the stored inverse", async ({
  page,
}) => {
  const mock = mockOf(page);
  await mergeCourier(page);
  await reopen(page);
  await editMenu(page);
  await expect(
    page.getByRole("menuitem", { name: /^Undo: Merged The courier into Mara/ }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+z");
  await expect(notice(page)).toContainText(
    "Undone: Merged The courier into Mara",
  );
  expect(ids(mock)).toEqual(ALL);
  expect(undoCalls(mock).map((c) => c.path)).toEqual([
    "/recast-1/edits/edit-1/undo",
  ]);
  // Another reload: the stored inverse is the redo, until a newer edit ends that chain.
  await reopen(page);
  await editMenu(page);
  await expect(
    page.getByRole("menuitem", { name: /^Redo: Merged The courier into Mara/ }),
  ).toBeVisible();
  // An undo entry is only ever redone: nothing older is left to undo.
  await expect(page.getByRole("menuitem", { name: /^Undo: / })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+Shift+Z");
  await expect(notice(page)).toContainText(
    "Redone: Merged The courier into Mara",
  );
  expect(ids(mock)).toEqual(MERGED);
  expect(undoCalls(mock).at(-1)?.path).toBe("/recast-1/edits/edit-2/undo");
  await reopen(page);
  await selectCharacter(page, "character-1");
  await card(page)
    .getByRole("button", { name: "Hide character", exact: true })
    .click();
  await expect(notice(page)).toContainText("Hid Elias.");
  await page.keyboard.press("Control+z");
  await expect(notice(page)).toContainText("Undone: Hid Elias");
  expect(mock.value.characters[1].hidden).toBe(false);
  await reopen(page);
  await editMenu(page);
  // The undone hide is the redo now; the newest live edit to undo is the redone merge, named as the merge.
  await expect(
    page.getByRole("menuitem", { name: /^Redo: Hid Elias/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("menuitem", { name: /^Undo: Redo: / }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("menuitem", { name: /^Undo: Merged The courier into Mara/ }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
});

/** Hides a character from its card: a journaled cast edit. */
async function hide(page: Page, id: string, name: string) {
  await selectCharacter(page, id);
  await card(page)
    .getByRole("button", { name: "Hide character", exact: true })
    .click();
  await expect(notice(page)).toContainText(`Hid ${name}.`);
}

// Astra R-B: after a reload a refused stored entry is passed over, so the next Mod+Z reaches the one below it.
test("after a reload, a refused stored undo is passed over for the entry below it", async ({
  page,
}) => {
  const mock = mockOf(page);
  await hide(page, "character-1", "Elias");
  await hide(page, "character-2", "The courier");
  await reopen(page);
  mock.refuseNextUndo(LATER_CHANGE);
  await page.keyboard.press("Control+z");
  await expect(notice(page)).toHaveText(new RegExp(`^${LATER_CHANGE}`));
  expect(mock.value.characters[2].hidden).toBe(true);
  await page.keyboard.press("Control+z");
  await expect.poll(() => mock.value.characters[1].hidden).toBe(false);
  expect(mock.value.characters[2].hidden).toBe(true);
  const paths = undoCalls(mock).map((c) => c.path);
  expect(paths).toHaveLength(2);
  expect(paths[0]).not.toBe(paths[1]);
});

// Astra R-C: undo two edits, reload, and both redo; a redo keeps the chain of undos going.
test("after a reload, every undone edit can be redone in turn", async ({
  page,
}) => {
  const mock = mockOf(page);
  await hide(page, "character-1", "Elias");
  await hide(page, "character-2", "The courier");
  await page.keyboard.press("Control+z");
  await expect(notice(page)).toContainText("Undone: Hid The courier");
  await page.keyboard.press("Control+z");
  await expect(notice(page)).toContainText("Undone: Hid Elias");
  expect(mock.value.characters.map((c) => c.hidden)).toEqual([
    false,
    false,
    false,
  ]);
  await reopen(page);
  await page.keyboard.press("Control+Shift+Z");
  await expect(notice(page)).toContainText("Redone: Hid Elias");
  expect(mock.value.characters[1].hidden).toBe(true);
  await page.keyboard.press("Control+Shift+Z");
  await expect(notice(page)).toContainText("Redone: Hid The courier");
  expect(mock.value.characters[2].hidden).toBe(true);
  await editMenu(page);
  await expect(page.getByRole("menuitem", { name: /^Redo: / })).toHaveCount(0);
  await page.keyboard.press("Escape");
});
