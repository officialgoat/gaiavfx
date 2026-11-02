import type { Page } from "@playwright/test";
import {
  expect,
  openFilm,
  recastCommands,
  test,
  type RecastWorld,
} from "./recast-world";

// CP3 B3 (ADR 0017 amendment item 7, ADR 0012 Amendment 7 §1): a film's cast edits are journaled in the recast. The
// notice after one offers Undo; Mod+Z undoes the film's newest step, a choice or a cast edit, and Mod+Shift+Z redoes
// it; after a reload the stored edits are still reachable. Review's Undo choice takes back choices only. The recast is
// the service's own (tests/browser_recast.py): every undo, refusal and summary below is Python's.

/** Mara (the user's picture) and Elias (Gaia's proposal) and The courier, who looks like Mara; Shot 001 is Mara's,
 * rendered once. */
test.use({
  scene: {
    shots: 6,
    characters: ["Mara", "Elias", "The courier"],
    targets: ["confirmed", "proposed", null],
    similar: [[0, 2]],
    rendered: 1,
  },
});

/** No version's asset name or operation reaches the page: text, titles, labels, alt text or values. */
async function expectBlind(page: Page) {
  const leaks = await page.evaluate(() => {
    const secrets = ["PRIVATE SEED ARM ROUND"];
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
const ids = async (world: RecastWorld) =>
  (await world.read()).characters.map((c) => c.id);
const undoPaths = (calls: ReturnType<typeof recastCommands>) =>
  calls.map((c) => c.path).filter((path) => /\/edits\/[^/]+\/undo$/.test(path));
/** The path of the undo of the recast's stored entry `index`, newest first. */
const undoOf = async (world: RecastWorld, index: number) =>
  `/${world.id}/edits/${(await world.read()).edits![index].id}/undo`;

async function selectCharacter(page: Page, id: string) {
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.locator(header(id)).click();
}

async function editMenu(page: Page) {
  await page.getByRole("button", { name: "Edit", exact: true }).click();
}

/** Merges The courier into Mara from the courier's card, through the merge sheet. */
async function mergeCourier(page: Page, world: RecastWorld) {
  await selectCharacter(page, world.characters[2]);
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
async function chooseFirst(page: Page, world: RecastWorld) {
  const versions = (await world.read()).shots[0].versions;
  await page.getByRole("button", { name: "Shot 001", exact: true }).click();
  await page.keyboard.press("1");
  await page.keyboard.press("Enter");
  await expect
    .poll(async () => (await world.read()).shots[0].choice?.version)
    .toBe(versions[0].id);
  return versions[0].id;
}

async function reopen(page: Page, world: RecastWorld) {
  await page.reload();
  await expect(page.locator(".timeline-heading")).toContainText(world.title);
}

/** Another client renames a character: a later change to rows the page's own last edit touched. Resolves once the
 * page has read the recast since, so its next command carries the current revision. */
async function renameElsewhere(
  world: RecastWorld,
  index: number,
  label: string,
  page: Page,
) {
  const read = await world.read();
  const response = await page.request.patch(
    `/api/recasts/${world.id}/characters/${world.characters[index]}`,
    {
      headers: world.headers,
      data: { expected_revision: read.revision, label },
    },
  );
  expect(response.ok(), await response.text()).toBeTruthy();
  const renamed = (await response.json()).revision;
  await page.waitForResponse(
    async (r) =>
      new URL(r.url()).pathname === `/api/recasts/${world.id}` &&
      r.request().method() === "GET" &&
      (await r.json()).revision === renamed,
    { timeout: 10_000 },
  );
}

const LATER_CHANGE =
  "A later change touched the same characters or face tracks. Undo that change first.";

// A16: the film's undo is the recast's; it never asks Direction for a receipt or its undo.
const direction = new WeakMap<Page, string[]>();
const commands = new WeakMap<Page, ReturnType<typeof recastCommands>>();
test.beforeEach(async ({ page, world }) => {
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
  commands.set(page, recastCommands(page));
  await page.goto("/");
  await openFilm(page, world);
});
test.afterEach(async ({ page }) => {
  expect(direction.get(page)).toEqual([]);
  await expectBlind(page);
});
const callsOf = (page: Page) => commands.get(page)!;

test("A11: a merge's notice offers Undo; Undo puts the characters back, Redo merges again, and the keys name it", async ({
  page,
  world,
}) => {
  const calls = callsOf(page);
  const all = world.characters;
  const merged = all.slice(0, 2);
  await mergeCourier(page, world);
  expect(await ids(world)).toEqual(merged);
  await editMenu(page);
  const undo = page.getByRole("menuitem", {
    name: /^Undo: Merged The courier into Mara/,
  });
  await expect(undo).toBeVisible();
  await expect(undo).toContainText("Ctrl+Z");
  await expect(page.getByRole("menuitem", { name: /^Redo: / })).toHaveCount(0);
  await page.keyboard.press("Escape");

  const merge = await undoOf(world, 0);
  await notice(page).getByRole("button", { name: "Undo", exact: true }).click();
  await expect(notice(page)).toContainText(
    "Undone: Merged The courier into Mara",
  );
  await expect.poll(() => ids(world)).toEqual(all);
  await expect(page.locator(header(all[2]))).toBeVisible();
  expect(undoPaths(calls)).toEqual([merge]);
  await editMenu(page);
  const redo = page.getByRole("menuitem", {
    name: /^Redo: Merged The courier into Mara/,
  });
  await expect(redo).toBeVisible();
  await expect(redo).toContainText("Ctrl+Shift+Z");
  await page.keyboard.press("Escape");

  // Redo is the undo of the inverse entry.
  const inverse = await undoOf(world, 0);
  await notice(page).getByRole("button", { name: "Redo", exact: true }).click();
  await expect(notice(page)).toContainText(
    "Redone: Merged The courier into Mara",
  );
  await expect.poll(() => ids(world)).toEqual(merged);
  expect(undoPaths(calls).at(-1)).toBe(inverse);
  // The keys walk the same stack (each waits for the film to settle: a key while saving does nothing).
  await page.keyboard.press("Control+z");
  await expect(notice(page)).toContainText(
    "Undone: Merged The courier into Mara",
  );
  await expect.poll(() => ids(world)).toEqual(all);
  await page.keyboard.press("Control+Shift+Z");
  await expect(notice(page)).toContainText(
    "Redone: Merged The courier into Mara",
  );
  await expect.poll(() => ids(world)).toEqual(merged);
  const edits = (await world.read()).edits!;
  expect(edits.map((e) => e.kind)).toEqual([
    "redo",
    "undo",
    "redo",
    "undo",
    "edit",
  ]);
  expect(edits[0].summary).toBe("Redo: Merged The courier into Mara");
});

test("A12: one stack orders a choice and a track move; Review's Undo choice skips the move", async ({
  page,
  world,
}) => {
  const [mara, elias] = world.characters;
  const track = (await world.read()).characters[0].tracks[0].id;
  const moved = async () =>
    (await world.read()).characters
      .find((c) => c.id === elias)!
      .tracks.some((t) => t.id === track);
  const chosen = await chooseFirst(page, world);
  const choice = async () => (await world.read()).shots[0].choice?.version;
  await selectCharacter(page, mara);
  await card(page).getByRole("button", { name: "Face tracks…" }).click();
  const drawer = page.getByRole("dialog", { name: "Mara · tracks" });
  await drawer.getByRole("checkbox").nth(0).check();
  await drawer.getByLabel("Move to").selectOption(elias);
  await drawer.getByRole("button", { name: "Move 1 track" }).click();
  await expect(notice(page)).toContainText("Moved 1 track to Elias.");
  expect(await moved()).toBe(true);

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
  expect(await moved()).toBe(false);
  expect(await choice()).toBe(chosen);
  await editMenu(page);
  await expect(
    page.getByRole("menuitem", { name: /^Undo choice/ }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+z");
  await expect(live(page)).toHaveText("Undone: Shot 001 is unchosen.");
  await expect.poll(choice).toBeUndefined();
  expect(await moved()).toBe(false);

  // Redo brings back the choice, then the move.
  await page.keyboard.press("Control+Shift+Z");
  await expect(live(page)).toHaveText("Redone: Shot 001: version A chosen.");
  await expect.poll(choice).toBe(chosen);
  await page.keyboard.press("Control+Shift+Z");
  await expect(notice(page)).toContainText(
    "Redone: Moved 1 face track to Elias",
  );
  expect(await moved()).toBe(true);

  // Review's Undo choice takes back the choice and leaves the newer move.
  await page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Review", exact: true })
    .click();
  await page.getByRole("menuitem", { name: /^Undo choice/ }).click();
  await expect(live(page)).toHaveText("Undone: Shot 001 is unchosen.");
  await expect.poll(choice).toBeUndefined();
  expect(await moved()).toBe(true);
  await editMenu(page);
  await expect(
    page.getByRole("menuitem", { name: /^Undo: Moved 1 face track to Elias/ }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
});

test("A13: clearing a picture and setting a pair aside are undone through their stored entries", async ({
  page,
  world,
}) => {
  const calls = callsOf(page);
  // The picture as the user decided it; Clear stops its face sheet, which the feed starts again after Undo.
  const target = async () => {
    const value = (await world.read()).characters[0].target;
    return value && { ...value, reference: undefined };
  };
  const before = await target();
  const notSame = async () => (await world.read()).not_same?.length;
  await selectCharacter(page, world.characters[0]);
  await card(page).getByRole("button", { name: "Clear", exact: true }).click();
  await expect(notice(page)).toContainText("Cleared Mara's picture.");
  await expect.poll(target).toBeNull();
  const clear = await undoOf(world, 0);
  await notice(page).getByRole("button", { name: "Undo", exact: true }).click();
  await expect(notice(page)).toContainText("Undone: Cleared Mara's picture");
  await expect.poll(target).toEqual(before);

  await card(page)
    .getByRole("button", { name: "Not the same person: The courier and Mara" })
    .click();
  await expect(notice(page)).toContainText(
    "Set aside Mara and The courier: not the same person.",
  );
  await expect.poll(notSame).toBe(1);
  await expect(card(page).locator(".film-card-similar")).toHaveCount(0);
  await editMenu(page);
  await expect(
    page.getByRole("menuitem", {
      name: /^Undo: Set aside Mara and The courier/,
    }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  const aside = await undoOf(world, 0);
  await page.keyboard.press("Control+z");
  await expect.poll(notSame).toBe(0);
  await expect(card(page).locator(".film-card-similar")).toHaveCount(1);
  expect(undoPaths(calls)).toEqual([clear, aside]);
});

// A picture's new name is the picture set again: journaled, so it joins the stack like any cast edit.
test("renaming a picture offers Undo, which puts the old name back", async ({
  page,
  world,
}) => {
  const calls = callsOf(page);
  const target = async () => (await world.read()).characters[0].target;
  const before = await target();
  await selectCharacter(page, world.characters[0]);
  const label = card(page).getByLabel("Target label for Mara", { exact: true });
  await label.fill("Arin in the film");
  await label.press("Enter");
  await expect
    .poll(async () => (await target())?.label)
    .toBe("Arin in the film");
  await notice(page).getByRole("button", { name: "Undo", exact: true }).click();
  await expect.poll(target).toEqual(before);
  expect(undoPaths(calls)).toHaveLength(1);
});

test("A14: a refused undo shows the service's sentence, and the next Mod+Z reaches the step below", async ({
  page,
  world,
}) => {
  const calls = callsOf(page);
  const hidden = async () => (await world.read()).characters[1].hidden;
  await chooseFirst(page, world);
  await selectCharacter(page, world.characters[1]);
  await card(page)
    .getByRole("button", { name: "Hide character", exact: true })
    .click();
  await expect(notice(page)).toContainText("Hid Elias.");
  await expect.poll(hidden).toBe(true);
  // Elsewhere, Elias is renamed after the hide: the service refuses to undo the hide past that change.
  await renameElsewhere(world, 1, "Elias Rowe", page);
  await page.keyboard.press("Control+z");
  await expect(notice(page)).toHaveText(new RegExp(`^${LATER_CHANGE}`));
  await expect(notice(page).getByRole("button", { name: "Undo" })).toHaveCount(
    0,
  );
  expect(await hidden()).toBe(true);
  // The refused edit left the stack: the choice below it is next.
  await editMenu(page);
  await expect(
    page.getByRole("menuitem", { name: /^Undo choice/ }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+z");
  await expect
    .poll(async () => (await world.read()).shots[0].choice)
    .toBeNull();
  expect(await hidden()).toBe(true);
  expect(undoPaths(calls)).toHaveLength(1);
});

test("A15: after a reload, Mod+Z undoes the stored merge and Mod+Shift+Z redoes it from the stored inverse", async ({
  page,
  world,
}) => {
  const calls = callsOf(page);
  const all = world.characters;
  const merged = all.slice(0, 2);
  await mergeCourier(page, world);
  await reopen(page, world);
  await editMenu(page);
  await expect(
    page.getByRole("menuitem", { name: /^Undo: Merged The courier into Mara/ }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  const merge = await undoOf(world, 0);
  await page.keyboard.press("Control+z");
  await expect(notice(page)).toContainText(
    "Undone: Merged The courier into Mara",
  );
  await expect.poll(() => ids(world)).toEqual(all);
  expect(undoPaths(calls)).toEqual([merge]);
  // Another reload: the stored inverse is the redo, until a newer edit ends that chain.
  await reopen(page, world);
  await editMenu(page);
  await expect(
    page.getByRole("menuitem", { name: /^Redo: Merged The courier into Mara/ }),
  ).toBeVisible();
  // An undo entry is only ever redone: nothing older is left to undo.
  await expect(page.getByRole("menuitem", { name: /^Undo: / })).toHaveCount(0);
  await page.keyboard.press("Escape");
  const inverse = await undoOf(world, 0);
  await page.keyboard.press("Control+Shift+Z");
  await expect(notice(page)).toContainText(
    "Redone: Merged The courier into Mara",
  );
  await expect.poll(() => ids(world)).toEqual(merged);
  expect(undoPaths(calls).at(-1)).toBe(inverse);
  await reopen(page, world);
  await selectCharacter(page, all[1]);
  await card(page)
    .getByRole("button", { name: "Hide character", exact: true })
    .click();
  await expect(notice(page)).toContainText("Hid Elias.");
  await page.keyboard.press("Control+z");
  await expect(notice(page)).toContainText("Undone: Hid Elias");
  await expect
    .poll(async () => (await world.read()).characters[1].hidden)
    .toBe(false);
  await reopen(page, world);
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

// Astra R-B: after a reload a refused stored entry is passed over, so a later Mod+Z reaches the one below it.
test("after a reload, a refused stored undo is passed over for the entry below it", async ({
  page,
  world,
}) => {
  const calls = callsOf(page);
  const hidden = async (index: number) =>
    (await world.read()).characters[index].hidden;
  await hide(page, world.characters[1], "Elias");
  await hide(page, world.characters[2], "The courier");
  await reopen(page, world);
  // Another client renames The courier between two of the page's reads: the page still holds the stored hide as its
  // newest edit, and the service refuses it at the page's old revision.
  const waiting: (() => void)[] = [];
  let holding = true;
  await page.route(`**/api/recasts/${world.id}`, async (route) => {
    if (route.request().method() === "GET" && holding)
      await new Promise<void>((release) => waiting.push(release));
    await route.continue();
  });
  const read = await world.read();
  const renamed = await page.request.patch(
    `/api/recasts/${world.id}/characters/${world.characters[2]}`,
    {
      headers: world.headers,
      data: { expected_revision: read.revision, label: "The rider" },
    },
  );
  expect(renamed.ok(), await renamed.text()).toBeTruthy();
  await page.keyboard.press("Control+z");
  await expect(notice(page)).toHaveText(
    /^The recast changed; reload before editing\./,
  );
  expect(await hidden(2)).toBe(true);
  const current = (await renamed.json()).revision;
  const fresh = page.waitForResponse(
    async (r) =>
      new URL(r.url()).pathname === `/api/recasts/${world.id}` &&
      r.request().method() === "GET" &&
      (await r.json()).revision === current,
    { timeout: 10_000 },
  );
  holding = false;
  for (const release of waiting) release();
  await page.unroute(`**/api/recasts/${world.id}`);
  await fresh;
  // The page reads the rename as the newest stored edit; past it, the refused hide is passed over for Elias's.
  await page.keyboard.press("Control+z");
  await expect(notice(page)).toContainText(
    "Undone: Renamed The courier to The rider",
  );
  await page.keyboard.press("Control+z");
  await expect.poll(() => hidden(1)).toBe(false);
  expect(await hidden(2)).toBe(true);
  const paths = undoPaths(calls);
  expect(paths).toHaveLength(3);
  expect(new Set(paths).size).toBe(3);
});

// Astra R-C: undo two edits, reload, and both redo; a redo keeps the chain of undos going.
test("after a reload, every undone edit can be redone in turn", async ({
  page,
  world,
}) => {
  const hidden = async () =>
    (await world.read()).characters.map((c) => c.hidden);
  await hide(page, world.characters[1], "Elias");
  await hide(page, world.characters[2], "The courier");
  await page.keyboard.press("Control+z");
  await expect(notice(page)).toContainText("Undone: Hid The courier");
  await page.keyboard.press("Control+z");
  await expect(notice(page)).toContainText("Undone: Hid Elias");
  await expect.poll(hidden).toEqual([false, false, false]);
  await reopen(page, world);
  await page.keyboard.press("Control+Shift+Z");
  await expect(notice(page)).toContainText("Redone: Hid Elias");
  await expect.poll(hidden).toEqual([false, true, false]);
  await page.keyboard.press("Control+Shift+Z");
  await expect(notice(page)).toContainText("Redone: Hid The courier");
  await expect.poll(hidden).toEqual([false, true, true]);
  await editMenu(page);
  await expect(page.getByRole("menuitem", { name: /^Redo: / })).toHaveCount(0);
  await page.keyboard.press("Escape");
});
