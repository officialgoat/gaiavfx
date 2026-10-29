import { test, expect, type Page } from "@playwright/test";
import { filmAsset, mockRecast, pictureAsset } from "./recast-fixtures";
import { PROCESS_MEDIA } from "../../frontend/src/features/generation/process-media";

// Usability round 1 (retained regression coverage B5-B9): the film's Cast area says when one
// picture plays two characters, lists the cast with its screen time in the lanes' order, gathers every "Same person?"
// suggestion in the Cast bin, names what a merge or a track move changed, and moves only the ticked tracks.

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
const panel = (page: Page) => page.locator(".film-project");
const notice = (page: Page) => page.locator(".film-message.notice");
const moves = (mock: Mock) =>
  mock.calls.filter((c) => c.path.endsWith("/tracks/move"));

async function reopen(page: Page) {
  await page.reload();
  await expect(page.locator(".timeline-heading")).toContainText(filmAsset.name);
}

async function castBin(page: Page) {
  await panel(page).getByRole("button", { name: /^Cast/ }).click();
}

async function selectCharacter(page: Page, id: string) {
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.locator(header(id)).click();
}

test.beforeEach(async ({ page }) => {
  const mock = await mockRecast(page);
  mock.existing();
  (page as Page & { mock?: Mock }).mock = mock;
  await page.goto("/");
  await openFilm(page);
});
const mockOf = (page: Page) => (page as Page & { mock: Mock }).mock;

test("B5: a character's card says which other characters its picture also plays", async ({
  page,
}) => {
  const mock = mockOf(page);
  // The fixture casts the Arin picture on Mara and proposes the same picture for Elias.
  await selectCharacter(page, "character-0");
  const note = card(page).getByRole("note");
  await expect(note).toHaveText("This picture also plays Elias.");
  // A third character on the same picture is named too.
  mock.value.characters[2].target = {
    asset_id: pictureAsset.id,
    label: "Arin",
    state: "confirmed",
    proposed_by: "user",
    face: "face",
  } as never;
  await expect(note).toHaveText(
    "This picture also plays Elias and The courier.",
  );
  // A hidden character is not playing anything on screen, so it is left out.
  mock.value.characters[1].hidden = true;
  await expect(note).toHaveText("This picture also plays The courier.");
  await page.locator(header("character-2")).click();
  await expect(card(page).getByRole("heading", { level: 2 })).toHaveText(
    "The courier",
  );
  await expect(card(page).getByRole("note")).toHaveText(
    "This picture also plays Mara.",
  );
  // A picture no one else plays says nothing.
  mock.value.characters[0].target = null;
  await expect(card(page).getByRole("note")).toHaveCount(0);
  await expectBlind(page);
});

test("B6: the Cast bin shows screen time, in the same order as the timeline's cast lanes", async ({
  page,
}) => {
  const mock = mockOf(page);
  // The courier has a confirmed picture, Mara none, Elias only a proposal: confirmed first, then screen time.
  mock.value.characters[2].target = {
    asset_id: pictureAsset.id,
    label: "Courier picture",
    state: "confirmed",
    proposed_by: "user",
    face: "face",
  } as never;
  mock.value.characters[0].target = null;
  await reopen(page);
  await castBin(page);
  const lanes = await page
    .locator('[data-row^="cast-"]')
    .evaluateAll((els) =>
      els.map((el) => el.getAttribute("data-row")!.replace("cast-", "")),
    );
  expect(lanes).toEqual(["character-2", "character-0", "character-1"]);
  const rows = panel(page).locator(".project-record").filter({
    hasNotText: "Unknown faces",
  });
  const names = await rows.evaluateAll((els) =>
    els.map((el) => el.querySelector("span")!.firstChild!.textContent),
  );
  expect(names).toEqual(
    lanes.map((id) => mock.value.characters.find((c) => c.id === id)!.label),
  );
  // 25 fps: 400, 1200 and 800 frames on screen.
  await expect(rows.nth(0)).toContainText(
    "→ Courier picture · Mapped · 16 s in 26 shots",
  );
  await expect(rows.nth(1)).toContainText("No target · 48 s in 27 shots");
  await expect(rows.nth(2)).toContainText(
    "→ Arin proposal · Proposed · 32 s in 27 shots",
  );
  // P3 plurals: one unknown track reads as one.
  await expect(
    panel(page).getByRole("button", { name: /^Unknown faces/ }),
  ).toContainText("1 face track with no character");
  await expectBlind(page);
});

test("B7: the Cast bin lists every Same person? pair once, and Not the same person hides it in this browser", async ({
  page,
}) => {
  const mock = mockOf(page);
  const [mara, elias, courier] = mock.value.characters;
  // The courier also looks like Elias (who has a picture); Mara and Elias look alike but both have pictures; a hidden
  // extra looks most like Mara.
  // Scores ranked the other way: the order must come from screen time, not the score (CP3).
  elias.similar = [
    { character_id: courier.id, similarity: 0.88 },
    { character_id: mara.id, similarity: 0.9 },
  ];
  courier.similar = [
    { character_id: mara.id, similarity: 0.7 },
    { character_id: elias.id, similarity: 0.88 },
  ];
  const extra = {
    ...structuredClone(courier),
    id: "character-3",
    label: "Extra",
    hidden: true,
    similar: [{ character_id: mara.id, similarity: 0.95 }],
  };
  mara.similar = [
    { character_id: courier.id, similarity: 0.7 },
    { character_id: elias.id, similarity: 0.9 },
    { character_id: extra.id, similarity: 0.95 },
  ];
  mock.value.characters.push(extra);
  await reopen(page);
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
  expect(mock.calls.some((c) => c.path.endsWith("/merge"))).toBe(false);
  // Not the same person: stored in the recast (CP3); the pair leaves the bin and the cards, and focus stays in the
  // list. This browser keeps no copy.
  await list
    .getByRole("button", { name: "Not the same person: The courier and Mara" })
    .click();
  await expect
    .poll(() => mock.calls.find((c) => c.path.endsWith("/not-same"))?.body)
    .toMatchObject({
      pairs: [{ character_a: "character-2", character_b: "character-0" }],
    });
  await expect(list.getByRole("listitem")).toHaveCount(1);
  await expect(list).not.toContainText("Mara");
  await expect(
    list.getByRole("button", {
      name: "Not the same person: The courier and Elias",
    }),
  ).toBeFocused();
  expect(
    await page.evaluate(() =>
      localStorage.getItem("gaiavfx:recast-not-same:recast-1"),
    ),
  ).toBeNull();
  await selectCharacter(page, "character-2");
  const similar = card(page).locator(".film-card-similar");
  await expect(similar).toHaveCount(1);
  await expect(similar).toContainText("Looks like Elias");
  // The dismissal is the recast's: it lasts across a reload.
  await reopen(page);
  await castBin(page);
  await expect(list.getByRole("listitem")).toHaveCount(1);
  await expect(list.getByRole("listitem")).toContainText("The courier · Elias");
  await selectCharacter(page, "character-0");
  await expect(card(page).getByRole("heading", { level: 2 })).toHaveText(
    "Mara",
  );
  await expect(
    card(page).getByRole("heading", { name: "Same person?" }),
  ).toHaveCount(0);
  // The last pair set aside: the list goes, and focus lands on the cast.
  await list
    .getByRole("button", { name: "Not the same person: The courier and Elias" })
    .click();
  await expect(panel(page).locator(".film-same-person")).toHaveCount(0);
  await expect(panel(page).locator(".project-record").first()).toBeFocused();
  await expectBlind(page);
});

test("B8: a merge and a track move say what they changed", async ({ page }) => {
  const mock = mockOf(page);
  await selectCharacter(page, "character-2");
  await card(page)
    .getByRole("button", { name: "Merge The courier into Mara…" })
    .click();
  const sheet = page.getByRole("dialog", { name: "Merge characters" });
  await sheet
    .getByRole("button", { name: "Merge characters", exact: true })
    .click();
  await expect(notice(page)).toContainText("Merged The courier into Mara.");
  // Two tracks to Elias, then one to Unknown faces: the count and its plural, and the destination's name.
  await page.locator(header("character-0")).click();
  await card(page).getByRole("button", { name: "Face tracks…" }).click();
  let drawer = page.getByRole("dialog", { name: "Mara · tracks" });
  await drawer.getByRole("checkbox").nth(0).check();
  await drawer.getByRole("checkbox").nth(1).check();
  await drawer.getByLabel("Move to").selectOption("character-1");
  await drawer.getByRole("button", { name: "Move 2 tracks" }).click();
  await expect(notice(page)).toContainText("Moved 2 tracks to Elias.");
  expect(moves(mock).at(-1)?.body).toMatchObject({
    track_ids: ["track-0-0", "track-1-0"],
    to: "character-1",
  });
  await expect(drawer).toHaveCount(0);
  await card(page).getByRole("button", { name: "Face tracks…" }).click();
  drawer = page.getByRole("dialog", { name: "Mara · tracks" });
  await drawer.getByRole("checkbox").nth(0).check();
  await drawer.getByLabel("Move to").selectOption("unknown");
  await drawer.getByRole("button", { name: "Move 1 track" }).click();
  await expect(notice(page)).toContainText("Moved 1 track to Unknown faces.");
  expect(moves(mock).at(-1)?.body).toMatchObject({ to: "unknown" });
  await expectBlind(page);
});

test("B9: a track move sends only the ticked tracks on screen, and the button counts them", async ({
  page,
}) => {
  const mock = mockOf(page);
  await selectCharacter(page, "character-0");
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
  // Another edit (the agent, another window) moves the ticked Shot 001 track to Elias; the next poll shows it gone.
  const [mara, elias] = mock.value.characters;
  const gone = mara.tracks.find((t) => t.id === "track-0-0")!;
  mara.tracks = mara.tracks.filter((t) => t !== gone);
  elias.tracks.push(gone);
  mock.value.revision++;
  await expect(shot("001")).toHaveCount(0);
  // The track that left is no longer ticked, counted or sent.
  await expect(move).toHaveText("Move tracks");
  await expect(move).toBeDisabled();
  await shot("002").check();
  await expect(move).toHaveText("Move 1 track");
  await drawer.getByLabel("Move to").selectOption("unknown");
  await move.click();
  await expect.poll(() => moves(mock).length).toBe(1);
  expect(moves(mock)[0].body).toMatchObject({
    track_ids: ["track-1-0"],
    to: "unknown",
  });
  await expect(drawer).toHaveCount(0);
  await expectBlind(page);
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
  for (const query of pictures)
    expect(query.get("exclude")).toBe(PROCESS_MEDIA);
  await expectBlind(page);
});

// CP3: a pair set aside in this browser before the recast stored them moves into the recast once, then the browser
// forgets it; pairs naming a character that is gone are dropped.
test("pairs set aside in this browser before CP3 move into the recast once", async ({
  page,
}) => {
  const mock = mockOf(page);
  await page.addInitScript(() => {
    if (sessionStorage.getItem("seeded")) return;
    sessionStorage.setItem("seeded", "1");
    localStorage.setItem(
      "gaiavfx:recast-not-same:recast-1",
      JSON.stringify(["character-0|character-2", "character-0|gone"]),
    );
  });
  await reopen(page);
  await expect
    .poll(() => mock.calls.find((c) => c.path.endsWith("/not-same"))?.body)
    .toMatchObject({
      pairs: [{ character_a: "character-0", character_b: "character-2" }],
    });
  await expect
    .poll(() =>
      page.evaluate(() =>
        localStorage.getItem("gaiavfx:recast-not-same:recast-1"),
      ),
    )
    .toBeNull();
  await reopen(page);
  expect(mock.calls.filter((c) => c.path.endsWith("/not-same"))).toHaveLength(
    1,
  );
});

// Astra R-A: with equal screen time the order is stable (by the pair's key), never by the score.
test("Same person? pairs with equal screen time keep a stable order", async ({
  page,
}) => {
  const mock = mockOf(page);
  const [mara, elias, courier] = mock.value.characters;
  elias.target = null;
  elias.screen_time_frames = mara.screen_time_frames;
  courier.similar = [
    { character_id: elias.id, similarity: 0.95 },
    { character_id: mara.id, similarity: 0.5 },
  ];
  elias.similar = [{ character_id: courier.id, similarity: 0.95 }];
  mara.similar = [{ character_id: courier.id, similarity: 0.5 }];
  await reopen(page);
  await castBin(page);
  const items = panel(page)
    .getByRole("list", { name: "Same person suggestions" })
    .getByRole("listitem");
  await expect(items).toHaveCount(2);
  await expect(items.nth(0)).toContainText("Mara");
  await expect(items.nth(1)).toContainText("Elias");
});
