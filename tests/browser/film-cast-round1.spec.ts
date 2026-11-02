import type { Page } from "@playwright/test";
import {
  card,
  expect,
  expectBlind,
  laneHeader as header,
  notice,
  openFilm,
  panel,
  recastCommands,
  reopen,
  test,
  type Scene,
} from "./recast-world";

// Usability round 1 (retained regression coverage B5-B9): the film's Cast area says when one
// picture plays two characters, lists the cast with its screen time in the lanes' order, gathers every "Same person?"
// suggestion in the Cast bin, names what a merge or a track move changed, and moves only the ticked tracks. The recast
// is the service's own (tests/browser_recast.py); another client's edits go through its routes.

/** Mara (the user's picture Arin) and Elias (Gaia's proposal), The courier with none and looks like Mara; 7 shots
 * at 25 fps, taken in turn, so Mara is seen longest; one face in Shot 003 belongs to no one. */
const CAST: Scene = {
  shots: 7,
  length: 50,
  rate: [25, 1],
  characters: ["Mara", "Elias", "The courier"],
  targets: ["confirmed", "proposed", null],
  similar: [[0, 2]],
  unknown: 1,
};
test.use({ scene: CAST });

/** A Same person? pair's key, as the Cast bin orders pairs with equal screen time (cast-actions.ts). */
const pairKey = (a: string, b: string) => [a, b].sort().join("|");

const commands = new WeakMap<Page, ReturnType<typeof recastCommands>>();
const callsOf = (page: Page) => commands.get(page)!;
const moves = (page: Page) =>
  callsOf(page).filter((c) => c.path.endsWith("/tracks/move"));

test.beforeEach(async ({ page, world }) => {
  commands.set(page, recastCommands(page));
  await page.goto("/");
  await openFilm(page, world);
});
test.afterEach(async ({ page }) => expectBlind(page));

async function castBin(page: Page) {
  await panel(page).getByRole("button", { name: /^Cast/ }).click();
}

async function selectCharacter(page: Page, id: string) {
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.locator(header(id)).click();
}

test.describe("one picture for Mara and Elias", () => {
  test.use({ scene: { ...CAST, shared: true } });

  test("B5: a character's card says which other characters its picture also plays", async ({
    page,
    world,
  }) => {
    const [mara, elias, courier] = world.characters;
    const picture = (await world.read()).characters[0].target!.asset_id;
    await selectCharacter(page, mara);
    const note = card(page).getByRole("note");
    await expect(note).toHaveText("This picture also plays Elias.");
    // A third character on the same picture is named too.
    await world.elsewhere("PUT", `/characters/${courier}/target`, {
      asset_id: picture,
      label: "Arin",
    });
    await expect(note).toHaveText(
      "This picture also plays Elias and The courier.",
    );
    // A hidden character is not playing anything on screen, so it is left out.
    await world.elsewhere("PATCH", `/characters/${elias}`, { hidden: true });
    await expect(note).toHaveText("This picture also plays The courier.");
    await page.locator(header(courier)).click();
    await expect(card(page).getByRole("heading", { level: 2 })).toHaveText(
      "The courier",
    );
    await expect(card(page).getByRole("note")).toHaveText(
      "This picture also plays Mara.",
    );
    // A picture no one else plays says nothing.
    await world.elsewhere("DELETE", `/characters/${mara}/target`);
    await expect(card(page).getByRole("note")).toHaveCount(0);
  });
});

test.describe("only The courier mapped", () => {
  test.use({
    scene: {
      ...CAST,
      targets: [null, "proposed", "confirmed"],
      labels: [null, null, "Courier picture"],
    },
  });

  test("B6: the Cast bin shows screen time, in the same order as the timeline's cast lanes", async ({
    page,
    world,
  }) => {
    const [mara, elias, courier] = world.characters;
    const read = await world.read();
    const said = (id: string) => {
      const c = read.characters.find((c) => c.id === id)!;
      return `${Math.round(c.screen_time_frames / 25)} s in ${c.shot_count} shots`;
    };
    await castBin(page);
    // The courier has a confirmed picture, Mara none, Elias only a proposal: confirmed first, then screen time.
    const lanes = await page
      .locator('[data-row^="cast-"]')
      .evaluateAll((els) =>
        els.map((el) => el.getAttribute("data-row")!.replace("cast-", "")),
      );
    expect(lanes).toEqual([courier, mara, elias]);
    const rows = panel(page).locator(".project-record").filter({
      hasNotText: "Unknown faces",
    });
    const names = await rows.evaluateAll((els) =>
      els.map((el) => el.querySelector("span")!.firstChild!.textContent),
    );
    expect(names).toEqual(["The courier", "Mara", "Elias"]);
    await expect(rows.nth(0)).toContainText(
      `→ Courier picture · Mapped · ${said(courier)}`,
    );
    await expect(rows.nth(1)).toContainText(`No target · ${said(mara)}`);
    await expect(rows.nth(2)).toContainText(
      `→ Arin proposal · Proposed · ${said(elias)}`,
    );
    // P3 plurals: one unknown track reads as one.
    await expect(
      panel(page).getByRole("button", { name: /^Unknown faces/ }),
    ).toContainText("1 face track with no character");
  });
});

test.describe("The courier looks like Mara and Elias, a hidden extra like Mara", () => {
  // Mara and Elias look alike too, but both have pictures; Mara is seen longer than Elias.
  test.use({
    scene: {
      ...CAST,
      shots: 9,
      characters: ["Mara", "Elias", "The courier", "Extra"],
      targets: ["confirmed", "proposed", null, null],
      similar: [
        [0, 2],
        [1, 2],
        [0, 1],
        [0, 3],
      ],
      hidden: [3],
    },
  });

  test("B7: the Cast bin lists every Same person? pair once, and Not the same person keeps it apart", async ({
    page,
    world,
  }) => {
    const [mara, , courier] = world.characters;
    await castBin(page);
    const list = panel(page).getByRole("list", {
      name: "Same person suggestions",
    });
    await expect(panel(page).getByText("Same person? (2)")).toBeVisible();
    // Both directions of a suggestion are one pair, ordered by screen time (CP3: the score is not calibrated, so no
    // percentage shows); the picture keeps its character.
    await expect(list.getByRole("listitem")).toHaveCount(2);
    await expect(list.getByRole("listitem").nth(0)).toContainText(
      "The courier · Mara",
    );
    await expect(list.getByRole("listitem").nth(1)).toContainText(
      "The courier · Elias",
    );
    await expect(panel(page)).toContainText(
      "Suggested from face likeness, which is not calibrated. Compare the two faces before merging.",
    );
    await expect(panel(page).getByText(/% alike/)).toHaveCount(0);
    await expect(list).not.toContainText("Extra");
    // Merge asks first, in the merge sheet.
    await list
      .getByRole("button", { name: "Merge The courier into Mara…" })
      .click();
    const sheet = page.getByRole("dialog", { name: "Merge characters" });
    await expect(sheet).toContainText("Merge The courier into Mara?");
    await page.keyboard.press("Escape");
    await expect(sheet).toHaveCount(0);
    expect(callsOf(page).some((c) => c.path.endsWith("/merge"))).toBe(false);
    // Not the same person: stored in the recast (CP3); the pair leaves the bin and the cards, and focus stays in the
    // list. This browser keeps no copy.
    await list
      .getByRole("button", {
        name: "Not the same person: The courier and Mara",
      })
      .click();
    await expect
      .poll(() => callsOf(page).find((c) => c.path.endsWith("/not-same"))?.body)
      .toMatchObject({
        pairs: [{ character_a: courier, character_b: mara }],
      });
    await expect(list.getByRole("listitem")).toHaveCount(1);
    await expect(list).not.toContainText("Mara");
    await expect(
      list.getByRole("button", {
        name: "Not the same person: The courier and Elias",
      }),
    ).toBeFocused();
    expect(
      await page.evaluate(
        (id) => localStorage.getItem(`gaiavfx:recast-not-same:${id}`),
        world.id,
      ),
    ).toBeNull();
    await selectCharacter(page, courier);
    const similar = card(page).locator(".film-card-similar");
    await expect(similar).toHaveCount(1);
    await expect(similar).toContainText("Looks like Elias");
    // The dismissal is the recast's: it lasts across a reload.
    await reopen(page, world);
    await castBin(page);
    await expect(list.getByRole("listitem")).toHaveCount(1);
    await expect(list.getByRole("listitem")).toContainText(
      "The courier · Elias",
    );
    await selectCharacter(page, mara);
    await expect(card(page).getByRole("heading", { level: 2 })).toHaveText(
      "Mara",
    );
    await expect(
      card(page).getByRole("heading", { name: "Same person?" }),
    ).toHaveCount(0);
    // The last pair set aside: the list goes, and focus lands on the cast.
    await list
      .getByRole("button", {
        name: "Not the same person: The courier and Elias",
      })
      .click();
    await expect(panel(page).locator(".film-same-person")).toHaveCount(0);
    await expect(panel(page).locator(".project-record").first()).toBeFocused();
  });
});

test("B8: a merge and a track move say what they changed", async ({
  page,
  world,
}) => {
  const [mara, elias, courier] = world.characters;
  await selectCharacter(page, courier);
  await card(page)
    .getByRole("button", { name: "Merge The courier into Mara…" })
    .click();
  const sheet = page.getByRole("dialog", { name: "Merge characters" });
  await sheet
    .getByRole("button", { name: "Merge characters", exact: true })
    .click();
  await expect(notice(page)).toContainText("Merged The courier into Mara.");
  // Two tracks to Elias, then one to Unknown faces: the count and its plural, and the destination's name.
  await page.locator(header(mara)).click();
  await card(page).getByRole("button", { name: "Face tracks…" }).click();
  let drawer = page.getByRole("dialog", { name: "Mara · tracks" });
  await drawer.getByRole("checkbox").nth(0).check();
  await drawer.getByRole("checkbox").nth(1).check();
  await drawer.getByLabel("Move to").selectOption(elias);
  await drawer.getByRole("button", { name: "Move 2 tracks" }).click();
  await expect(notice(page)).toContainText("Moved 2 tracks to Elias.");
  expect(moves(page).at(-1)?.body.to).toBe(elias);
  expect(moves(page).at(-1)?.body.track_ids).toHaveLength(2);
  await expect(drawer).toHaveCount(0);
  await card(page).getByRole("button", { name: "Face tracks…" }).click();
  drawer = page.getByRole("dialog", { name: "Mara · tracks" });
  await drawer.getByRole("checkbox").nth(0).check();
  await drawer.getByLabel("Move to").selectOption("unknown");
  await drawer.getByRole("button", { name: "Move 1 track" }).click();
  await expect(notice(page)).toContainText("Moved 1 track to Unknown faces.");
  expect(moves(page).at(-1)?.body).toMatchObject({ to: "unknown" });
});

test("B9: a track move sends only the ticked tracks on screen, and the button counts them", async ({
  page,
  world,
}) => {
  const [mara, elias] = world.characters;
  const tracks = (await world.read()).characters[0].tracks;
  const inShot = (index: number) =>
    tracks.find((t) => t.shot_id === world.shots[index])!.id;
  await selectCharacter(page, mara);
  await card(page).getByRole("button", { name: "Face tracks…" }).click();
  const drawer = page.getByRole("dialog", { name: "Mara · tracks" });
  const move = drawer.getByRole("button", { name: /^Move/ });
  // Nothing ticked: the button waits, and says why in words a keyboard user reaches.
  await expect(move).toHaveText("Move tracks");
  await expect(move).toBeDisabled();
  await expect(move).toHaveAccessibleDescription(
    "Tick the tracks to move first.",
  );
  const shot = (number: string) =>
    drawer.getByRole("checkbox", { name: new RegExp(`^Shot ${number},`) });
  await shot("001").check();
  await expect(move).toHaveText("Move 1 track");
  // Another client (the agent, another window) moves the ticked Shot 001 track to Elias; the next poll shows it gone.
  await world.elsewhere("POST", "/tracks/move", {
    track_ids: [inShot(0)],
    to: elias,
  });
  await expect(shot("001")).toHaveCount(0, { timeout: 8000 });
  // The track that left is no longer ticked, counted or sent.
  await expect(move).toHaveText("Move tracks");
  await expect(move).toBeDisabled();
  await shot("004").check();
  await expect(move).toHaveText("Move 1 track");
  await drawer.getByLabel("Move to").selectOption("unknown");
  await move.click();
  await expect.poll(() => moves(page).length).toBe(1);
  expect(moves(page)[0].body).toMatchObject({
    track_ids: [inShot(3)],
    to: "unknown",
  });
  await expect(drawer).toHaveCount(0);
});

test("the film's Assets bin leaves out a replacement's process files", async ({
  page,
}) => {
  const queries: URLSearchParams[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.endsWith("/api/v1/assets")) queries.push(url.searchParams);
  });
  await panel(page)
    .getByRole("button", { name: /^Assets/ })
    .click();
  await expect(
    panel(page).locator('.film-asset[draggable="true"]').first(),
  ).toBeVisible();
  const pictures = queries.filter((q) => q.get("kind") === "image");
  expect(pictures.length).toBeGreaterThan(0);
  for (const query of pictures) expect(query.get("hide_process")).toBe("true");
});

// CP3: a pair set aside in this browser before the recast stored them moves into the recast once, then the browser
// forgets it; pairs naming a character that is gone are dropped.
test("pairs set aside in this browser before CP3 move into the recast once", async ({
  page,
  world,
}) => {
  const [mara, , courier] = world.characters;
  const key = `gaiavfx:recast-not-same:${world.id}`;
  await page.addInitScript(
    ([key, pairs]) => {
      if (sessionStorage.getItem("seeded")) return;
      sessionStorage.setItem("seeded", "1");
      localStorage.setItem(key, JSON.stringify(pairs));
    },
    [key, [`${mara}|${courier}`, `${mara}|gone`]] as const,
  );
  await reopen(page, world);
  const pairs = async () =>
    (
      callsOf(page).find((c) => c.path.endsWith("/not-same"))?.body.pairs as
        { character_a: string; character_b: string }[] | undefined
    )?.map((p) => [p.character_a, p.character_b].sort());
  await expect.poll(pairs).toEqual([[mara, courier].sort()]);
  await expect
    .poll(() => page.evaluate((key) => localStorage.getItem(key), key))
    .toBeNull();
  await reopen(page, world);
  expect(
    callsOf(page).filter((c) => c.path.endsWith("/not-same")),
  ).toHaveLength(1);
});

test.describe("The courier looks like Mara and Elias, who are seen as long", () => {
  test.use({
    scene: {
      ...CAST,
      shots: 6,
      targets: ["confirmed", null, null],
      similar: [
        [1, 2],
        [0, 2],
      ],
    },
  });

  // Astra R-A: with equal screen time the order is stable (by the pair's key), never by the score.
  test("Same person? pairs with equal screen time keep a stable order", async ({
    page,
    world,
  }) => {
    const [mara, elias, courier] = world.characters;
    const order = [
      ["Mara", pairKey(courier, mara)],
      ["Elias", pairKey(courier, elias)],
    ].sort((a, b) => a[1].localeCompare(b[1]));
    await castBin(page);
    const items = panel(page)
      .getByRole("list", { name: "Same person suggestions" })
      .getByRole("listitem");
    await expect(items).toHaveCount(2);
    await expect(items.nth(0)).toContainText(order[0][0]);
    await expect(items.nth(1)).toContainText(order[1][0]);
  });
});
