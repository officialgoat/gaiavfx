import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { filmAsset, mockRecast, pictureAsset } from "./recast-fixtures";

// U5a: the Recast page's tests of its landing list, face sheets, merge suggestion, playback, pinned headers and axe,
// ported to the film document: the "Open film" sheet (File › Open film…), the character card in Properties, the
// Program monitor and the film's tracks (retained regression coverage).

test.use({ reducedMotion: "reduce" });

type Mock = Awaited<ReturnType<typeof mockRecast>>;

const openSheetDialog = (page: Page) =>
  page.getByRole("dialog", { name: "Open film", exact: true });

/** File › Open film…: the sheet that lists existing recasts and Library videos. */
async function openSheet(page: Page) {
  await page.getByRole("button", { name: "File", exact: true }).click();
  await page.getByRole("menuitem", { name: /Open film/ }).click();
  await expect(openSheetDialog(page)).toBeVisible();
  return openSheetDialog(page);
}

/** Opens the fixture film from its Library video (the recast is found, or created when none exists). */
async function openFilm(page: Page) {
  const sheet = await openSheet(page);
  await sheet
    .getByRole("button", { name: filmAsset.name, exact: true })
    .click();
  // A film with no recast asks before its analysis (ADR 0017 §1); an existing recast opens at once.
  const ask = page.getByRole("dialog", { name: "Analyse this film?" });
  const heading = page.locator(".timeline-heading", {
    hasText: filmAsset.name,
  });
  await expect(ask.or(heading)).toBeVisible();
  if (await ask.isVisible())
    await ask.getByRole("button", { name: "Analyse film" }).click();
  await expect(heading).toBeVisible();
}

const program = (page: Page) =>
  page.getByRole("region", { name: "Program monitor" });
const header = (id: string) => `[data-row="cast-${id}"] .cast-header`;
const card = (page: Page) => page.locator(".film-properties");

/** Mara's card in Properties. */
async function openMara(page: Page) {
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.locator(header("character-0")).click();
  await expect(
    card(page).getByRole("heading", { name: "Mara", exact: true }),
  ).toBeVisible();
}

/** The status line under a card's "Face sheet" heading. */
const sheetStatus = (page: Page) =>
  card(page).locator(
    'xpath=.//h3[normalize-space()="Face sheet"]/following-sibling::p[@role="status"][1]',
  );

const waitingSheet = (mock: Mock) => {
  mock.value.characters[0].target!.reference = {
    asset_id: pictureAsset.id,
    state: "waiting",
  };
};

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

async function useTheme(page: Page, theme: "dark" | "light") {
  await page.emulateMedia({ colorScheme: theme });
  await page.addInitScript((value) => {
    localStorage.setItem(
      "gaiavfx-preferences",
      JSON.stringify({ state: { theme: value }, version: 1 }),
    );
  }, theme);
}

/** axe's default rule set (WCAG and best practice), on the whole page or, as the Recast page's test did, on the
 * workspace (the film's `main`, which holds the tracks, the monitors and Properties). */
async function expectAxeClean(page: Page, scope?: string) {
  const builder = new AxeBuilder({ page });
  const audit = await (scope ? builder.include(scope) : builder).analyze();
  expect(
    audit.violations.map((v) => ({
      id: v.id,
      nodes: v.nodes.map((n) => n.target.join(" ")),
    })),
  ).toEqual([]);
}

// Rows 1, 23 and 29 (1)-(5) (8): the "Open film" sheet.

test("the Open film sheet hides generated videos until they are asked for", async ({
  page,
}) => {
  await mockRecast(page);
  const requests: string[] = [];
  await page.route(
    (url) => url.pathname === "/api/v1/assets",
    (route) => {
      const url = new URL(route.request().url());
      if (url.searchParams.get("kind") !== "video") return route.fallback();
      requests.push(url.search);
      const generated = {
        ...filmAsset,
        id: "generated",
        name: "shot.mkv",
        provenance: [{ type: "shot_assembly", source_asset_id: filmAsset.id }],
      };
      const items =
        url.searchParams.get("imported_only") === "true"
          ? [filmAsset]
          : [generated, filmAsset];
      return route.fulfill({
        json: { items, total: items.length, offset: 0, limit: 40 },
      });
    },
  );
  await page.goto("/");
  const sheet = await openSheet(page);
  const videos = sheet.locator("section").filter({
    has: page.getByRole("heading", { name: "Library videos", exact: true }),
  });
  const film = videos.getByRole("button", {
    name: filmAsset.name,
    exact: true,
  });
  const generated = videos.getByRole("button", {
    name: "shot.mkv",
    exact: true,
  });
  await expect(film).toBeVisible();
  await expect(generated).toHaveCount(0);
  await expect(film).toContainText("1:20 · 25 fps");
  await videos.getByLabel("Show generated videos").check();
  await expect(generated).toBeVisible();
  await videos.getByLabel("Show generated videos").uncheck();
  await expect(generated).toHaveCount(0);
  expect(requests.some((q) => q.includes("imported_only=true"))).toBeTruthy();
  // A replacement's process files are never offered as a film, generated videos shown or not.
  expect(
    requests.every((q) =>
      new URLSearchParams(q.split("?")[1])
        .get("exclude")
        ?.includes("shot_assembly"),
    ),
  ).toBeTruthy();
});

test("the Open film sheet explains that Recast is not available (501)", async ({
  page,
}) => {
  await mockRecast(page);
  await page.route("**/api/recasts", (route) =>
    route.fulfill({ status: 501, json: { detail: "pending" } }),
  );
  await page.goto("/");
  const sheet = await openSheet(page);
  await expect(sheet.getByRole("alert")).toContainText(
    "Recast is not available in this service yet",
  );
});

test("the Open film sheet shows Library metadata, readable analysis states and posters; the title bar names the film", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  let sheet = await openSheet(page);
  await expect(
    sheet.getByRole("button", { name: filmAsset.name, exact: true }),
  ).toContainText("1:20 · 25 fps");
  await expect(sheet.locator(".recast-existing")).toContainText("Analysed");
  await expect(
    sheet.getByRole("button", { name: "Previous", exact: true }),
  ).toBeVisible();
  const poster = sheet.locator(".recast-asset-list img").first();
  await expect(poster).toHaveAttribute("src", "/recast-fixture/before-cut.jpg");
  await expect
    .poll(() => poster.evaluate((img: HTMLImageElement) => img.naturalWidth))
    .toBeGreaterThan(0);
  // A cached first frame can be black even though the film has usable footage.
  await page.route("**/recast-fixture/before-cut.jpg", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16" fill="black"/></svg>',
    }),
  );
  await page.reload();
  sheet = await openSheet(page);
  await expect(poster).toHaveAttribute("src", /^data:image\/jpeg/);
  await sheet
    .getByRole("button", { name: filmAsset.name, exact: true })
    .click();
  await expect(page.locator(".timeline-heading")).toContainText(filmAsset.name);
  await expect(page.locator(".studio-document")).toContainText(
    ` · ${filmAsset.name}`,
  );
  await expect(page.locator(".studio-titlebar .project-title")).toHaveCount(1);
  await expectBlind(page);
});

// Row 29 (6) (7): the target label.

test("a mapped target's label reads without its extension and is edited in place", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  mock.value.characters[0].target!.label = "CS079.png";
  await page.goto("/");
  await openFilm(page);
  await openMara(page);
  const label = card(page).getByLabel("Target label for Mara", {
    exact: true,
  });
  await expect(label).toHaveValue("CS079");
  await label.fill("Arin in the film");
  await label.press("Enter");
  await expect
    .poll(() => mock.value.characters[0].target!.label)
    .toBe("Arin in the film");
  expect(
    mock.calls.filter(
      (c) => c.path === "/recast-1/characters/character-0/target",
    ),
  ).toEqual([
    {
      path: "/recast-1/characters/character-0/target",
      method: "PUT",
      body: {
        expected_revision: 1,
        asset_id: pictureAsset.id,
        label: "Arin in the film",
      },
    },
  ]);
  await expect(label).toHaveValue("Arin in the film");
  await expectBlind(page);
});

// Row 26 (1) (3) (4): storage that throws.

test("the film opens and casts a character when storage is unavailable", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const mock = await mockRecast(page, 8);
  mock.value.characters.forEach((c) => {
    c.target = null;
  });
  await page.addInitScript(() => {
    const read = Storage.prototype.getItem;
    const write = Storage.prototype.setItem;
    Storage.prototype.getItem = function (key) {
      if (key.startsWith("recast:") || key.startsWith("gaiavfx:"))
        throw new Error("Unavailable");
      return read.call(this, key);
    };
    Storage.prototype.setItem = function (key, value) {
      if (key.startsWith("recast:") || key.startsWith("gaiavfx:"))
        throw new Error("Unavailable");
      return write.call(this, key, value);
    };
  });
  await page.goto("/");
  await openFilm(page);
  const chips = page.locator('[data-row^="cast-"] .target-chip');
  await expect(chips).toHaveCount(3);
  for (let i = 0; i < 3; i++)
    await expect(chips.nth(i)).toHaveText("No target");
  await openMara(page);
  await card(page)
    .getByRole("button", { name: "Set target picture…", exact: true })
    .click();
  await page
    .getByRole("dialog", { name: "Set target picture" })
    .getByRole("button", { name: pictureAsset.name, exact: true })
    .click();
  await expect
    .poll(() =>
      mock.calls.filter(
        (c) =>
          c.method === "PUT" &&
          c.path === "/recast-1/characters/character-0/target",
      ),
    )
    .toHaveLength(1);
  expect(
    mock.calls.find((c) => c.path.endsWith("character-0/target"))!.body,
  ).toMatchObject({ asset_id: pictureAsset.id, label: "Arin portrait" });
  await expect(page.locator(header("character-0"))).toContainText(
    "→ Arin portrait · Mapped",
  );
  expect(errors).toEqual([]);
  await expectBlind(page);
});

// Row 27, all but (7) (the chooser's order and portraits are in film-cast.spec.ts).

test("a similar face opens a centred, explicit merge with both portraits", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/");
  await openFilm(page);
  await openMara(page);
  const suggestion = card(page).getByRole("button", {
    name: "Merge The courier into Mara…",
    exact: true,
  });
  await suggestion.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", {
    name: "Merge characters",
    exact: true,
  });
  await expect(dialog.getByAltText("Mara face", { exact: true })).toBeVisible();
  await expect(
    dialog.getByAltText("The courier face", { exact: true }),
  ).toBeVisible();
  await expect(dialog).toContainText("48 s on screen");
  await expect(dialog).toContainText("16 s on screen");
  // The character seen less, without a picture, merges into the one with the picture.
  await expect(dialog).toContainText("Merge The courier into Mara?");
  const box = (await dialog.boundingBox())!;
  expect(Math.abs(box.x + box.width / 2 - 640)).toBeLessThan(2);
  expect(Math.abs(box.y + box.height / 2 - 360)).toBeLessThan(2);
  expect(
    mock.calls.filter((c) => c.path.endsWith("/characters/merge")),
  ).toHaveLength(0);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(suggestion).toBeFocused();
  // Merging a character with a picture away says that its picture goes with it.
  await card(page)
    .getByRole("button", { name: "Merge with…", exact: true })
    .click();
  await dialog
    .getByRole("group", { name: "Merge into" })
    .getByRole("button", { name: /The courier/ })
    .click();
  await expect(dialog.getByRole("note")).toHaveText(
    "Mara’s picture Arin will be removed.",
  );
  expect(
    mock.calls.filter((c) => c.path.endsWith("/characters/merge")),
  ).toHaveLength(0);
  await dialog
    .getByRole("button", { name: "Merge characters", exact: true })
    .click();
  await expect
    .poll(
      () => mock.calls.find((c) => c.path.endsWith("/characters/merge"))?.body,
    )
    .toMatchObject({ sources: ["character-0"], into: "character-2" });
  await expectBlind(page);
});

// Rows 33 to 39 and 41: face sheets in the character card.

for (const action of ["Approve", "Reject"] as const)
  test(`a waiting face sheet: ${action} is a single human request`, async ({
    page,
  }) => {
    const mock = await mockRecast(page, 8);
    mock.existing();
    waitingSheet(mock);
    await page.goto("/");
    await openFilm(page);
    await openMara(page);
    await expect(
      card(page).getByRole("heading", { name: "Face sheet", exact: true }),
    ).toHaveCount(1);
    await expect(
      card(page).getByRole("img", { name: "Face sheet for Mara", exact: true }),
    ).toBeVisible();
    await expect(sheetStatus(page)).toHaveText("Waiting for your review");
    await expect(
      card(page).getByRole("button", {
        name: "Approve face sheet for Mara",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      card(page).getByRole("button", {
        name: "Reject face sheet for Mara",
        exact: true,
      }),
    ).toBeVisible();
    await card(page)
      .getByRole("button", { name: `${action} face sheet for Mara` })
      .click();
    await expect(sheetStatus(page)).toHaveText(
      action === "Approve"
        ? "Approved: shots rendered from now on use it"
        : "Rejected",
    );
    expect(
      mock.calls.filter((c) =>
        c.path.endsWith(`/reference/${action.toLowerCase()}`),
      ),
    ).toEqual([
      {
        path: `/recast-1/characters/character-0/reference/${action.toLowerCase()}`,
        method: "POST",
        body: { expected_revision: 1 },
      },
    ]);
    await expectBlind(page);
  });

for (const [state, words] of [
  ["making", "Being made"],
  ["approved", "Approved: shots rendered from now on use it"],
  ["failed", "Failed"],
  ["rejected", "Rejected"],
] as const)
  test(`face sheet ${state}: reads "${words}" and offers no approval`, async ({
    page,
  }) => {
    const mock = await mockRecast(page, 8);
    mock.existing();
    mock.value.characters[0].target!.reference = {
      asset_id: state === "approved" ? pictureAsset.id : null,
      state,
    };
    await page.goto("/");
    await openFilm(page);
    await openMara(page);
    await expect(
      card(page).getByRole("heading", { name: "Face sheet", exact: true }),
    ).toHaveCount(1);
    await expect(sheetStatus(page)).toHaveText(words);
    await expect(
      page.getByRole("button", { name: /^(Approve|Reject) face sheet/ }),
    ).toHaveCount(0);
    await expectBlind(page);
  });

test("no face sheet shows before one is made", async ({ page }) => {
  const mock = await mockRecast(page, 8);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  await openMara(page);
  await expect(card(page).getByText("Mapped")).toBeVisible();
  await expect(
    card(page).getByRole("heading", { name: "Face sheet", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: /face sheet/i })).toHaveCount(
    0,
  );
});

test("the face-sheet thumbnail opens the full preview before approval", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  mock.existing();
  waitingSheet(mock);
  await page.goto("/");
  await openFilm(page);
  await openMara(page);
  const thumbnail = card(page).getByRole("button", {
    name: "Open face sheet for Mara",
    exact: true,
  });
  await expect(
    thumbnail.getByRole("img", { name: "Face sheet for Mara", exact: true }),
  ).toBeVisible();
  await expect(sheetStatus(page)).toHaveText("Waiting for your review");
  await thumbnail.click();
  const preview = page.getByRole("dialog", {
    name: "Face sheet for Mara",
    exact: true,
  });
  await expect(preview).toBeVisible();
  await expect(preview.locator(".asset-player img")).toBeVisible();
  await expect(preview.locator(".asset-player img")).not.toHaveAttribute(
    "alt",
    "",
  );
  await preview.getByRole("button", { name: "Close panel" }).click();
  await expect(preview).toHaveCount(0);
  await expect(thumbnail).toBeFocused();
  expect(mock.calls.filter((c) => c.path.includes("/reference/"))).toHaveLength(
    0,
  );
  await expectBlind(page);
});

// Rows 24 (5) and 40 (1) (2): axe's default rule set, both themes, with a shot selected and with a character card
// showing a waiting face sheet.

async function auditFilm(page: Page, theme: "dark" | "light", scope?: string) {
  await useTheme(page, theme);
  const mock = await mockRecast(page);
  mock.existing();
  waitingSheet(mock);
  await page.goto("/");
  await openFilm(page);
  await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.getByRole("button", { name: "Shot 002", exact: true }).click();
  await expect(
    card(page).getByRole("heading", { name: "Shot 002", exact: true }),
  ).toBeVisible();
  await expect(program(page).locator(".monitor-heading")).toContainText(
    "Shot 002 · A",
  );
  await expectAxeClean(page, scope);
  await openMara(page);
  await expect(
    card(page).getByRole("img", { name: "Face sheet for Mara", exact: true }),
  ).toBeVisible();
  await expect(sheetStatus(page)).toHaveText("Waiting for your review");
  await expectAxeClean(page, scope);
  await expectBlind(page);
}

for (const theme of ["dark", "light"] as const) {
  test(`the film workspace passes axe's default rules with a shot and with a waiting face sheet, ${theme}`, async ({
    page,
  }) => {
    await auditFilm(page, theme, "main");
  });

  test(`the whole film page passes axe's default rules with a shot and with a waiting face sheet, ${theme}`, async ({
    page,
  }) => {
    await auditFilm(page, theme);
  });
}

// Row 40 (3) (4): the face sheet at 390 px.

test("a waiting face sheet keeps both decisions at 390 px without sideways scrolling", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  mock.existing();
  waitingSheet(mock);
  await page.goto("/");
  await openFilm(page);
  await openMara(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    card(page).getByRole("button", {
      name: "Approve face sheet for Mara",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    card(page).getByRole("button", {
      name: "Reject face sheet for Mara",
      exact: true,
    }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

// Row 24 (3) (4): Program plays the comparison in sync, at each film rate.

for (const [num, den] of [
  [25, 1],
  [24000, 1001],
  [30000, 1001],
] as const)
  test(`Program plays the shot's original and version in sync, ${Number((num / den).toFixed(3))} fps`, async ({
    page,
  }) => {
    // The fixture film is encoded at this rate too, so the players decode real 23.976 or 29.97 fps media.
    const mock = await mockRecast(page, 40, [num, den]);
    mock.existing();
    const fps = num / den;
    const shot = mock.value.shots[1];
    await page.goto("/");
    await openFilm(page);
    await page.getByRole("button", { name: "Shot 002", exact: true }).click();
    await expect(program(page).locator(".monitor-heading")).toContainText(
      "Shot 002 · A",
    );
    const play = program(page).getByRole("button", {
      name: "Play comparison",
      exact: true,
    });
    await expect(play).toBeEnabled();
    await play.click();
    const before = program(page).locator(".recast-before video");
    await expect
      .poll(() =>
        before.evaluate(
          (v: HTMLVideoElement, start) => v.currentTime - start,
          shot.start / fps,
        ),
      )
      .toBeGreaterThan(0.2);
    await program(page)
      .getByRole("button", { name: "Pause comparison", exact: true })
      .click();
    const [original, version] = await program(page)
      .locator(".recast-wipe video")
      .evaluateAll((vs) => vs.map((v) => (v as HTMLVideoElement).currentTime));
    expect(original - shot.start / fps).toBeGreaterThan(0.2);
    expect(Math.abs(original - shot.start / fps - version)).toBeLessThan(0.12);
    await expectBlind(page);
  });

// Row 24 (2), the film's version: the track headers stay pinned while the tracks scroll sideways.

test("track headers stay pinned when the timeline scrolls sideways", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/");
  await openFilm(page);
  const scroll = page.locator(".timeline-scroll");
  const zoomIn = page.getByRole("button", { name: "Zoom in", exact: true });
  for (let i = 0; i < 12; i++) {
    if ((await scroll.evaluate((el) => el.scrollWidth - el.clientWidth)) > 1000)
      break;
    await zoomIn.click();
  }
  expect(
    await scroll.evaluate((el) => el.scrollWidth - el.clientWidth),
  ).toBeGreaterThan(1000);
  await scroll.evaluate((el) => {
    el.scrollLeft = el.scrollWidth;
  });
  await expect
    .poll(() => scroll.evaluate((el) => el.scrollLeft))
    .toBeGreaterThan(1000);
  const pane = (await scroll.boundingBox())!;
  const labels = page.locator(".timeline-scroll .track-label");
  const cast = page.locator('[data-row^="cast-"] > .track-label');
  await expect(cast).toHaveCount(3);
  for (const row of ["shots", "takes"])
    await expect(
      page.locator(`.track-row[data-row="${row}"] > .track-label`),
    ).toHaveCount(1);
  const xs = await labels.evaluateAll((els) =>
    els.map((el) => el.getBoundingClientRect().x),
  );
  expect(xs.length).toBeGreaterThanOrEqual(6);
  for (const x of xs) expect(Math.abs(x - pane.x)).toBeLessThanOrEqual(1);
  await expect(cast.first().locator(".cast-header")).toBeVisible();
  await expectBlind(page);
});

// ADR 0017 §1, the user at CP1: creating a film's recast starts its analysis, so a new film asks first.
test("a film with no recast asks before its analysis; Cancel creates nothing", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  const creates = () => mock.calls.filter((c) => c.path === "");
  await page.goto("/");
  const sheet = await openSheet(page);
  await sheet
    .getByRole("button", { name: filmAsset.name, exact: true })
    .click();
  const ask = page.getByRole("dialog", { name: "Analyse this film?" });
  await expect(ask).toContainText(filmAsset.name);
  await expect(ask).toContainText("1:20 · 25 fps");
  await expect(ask).toContainText("runs on the GPU through ComfyUI");
  await ask.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(ask).toHaveCount(0);
  // Nothing was created, and the Open film sheet waits for another choice.
  expect(creates()).toHaveLength(0);
  await expect(sheet).toBeVisible();
  await expect(
    page.locator(".timeline-heading", { hasText: filmAsset.name }),
  ).toHaveCount(0);
  // Escape is a cancel too.
  await sheet
    .getByRole("button", { name: filmAsset.name, exact: true })
    .click();
  await expect(ask).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(ask).toHaveCount(0);
  expect(creates()).toHaveLength(0);
  await sheet
    .getByRole("button", { name: filmAsset.name, exact: true })
    .click();
  await ask.getByRole("button", { name: "Analyse film" }).click();
  await expect(page.locator(".timeline-heading")).toContainText(filmAsset.name);
  expect(creates()).toHaveLength(1);
  expect(creates()[0].body).toMatchObject({ source_asset_id: filmAsset.id });
  await expectBlind(page);
});

test("a film that has a recast opens without asking", async ({ page }) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  const sheet = await openSheet(page);
  await sheet
    .getByRole("button", { name: filmAsset.name, exact: true })
    .click();
  await expect(page.locator(".timeline-heading")).toContainText(filmAsset.name);
  await expect(
    page.getByRole("dialog", { name: "Analyse this film?" }),
  ).toHaveCount(0);
  expect(mock.calls.filter((c) => c.path === "")).toHaveLength(0);
});

// Pilot T8-F3: the Open film sheet lists imported videos; when there are none it says how to include films made here.
test("an empty Open film list says how to include generated videos", async ({
  page,
}) => {
  await mockRecast(page);
  await page.route("**/api/v1/assets?*", (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("kind") !== "video") return route.fallback();
    return route.fulfill({
      json: { items: [], total: 0, offset: 0, limit: 40 },
    });
  });
  await page.goto("/");
  const sheet = await openSheet(page);
  await expect(sheet).toContainText(
    "No imported videos. Tick Show generated videos to include films made here, or import a film in Library.",
  );
});
