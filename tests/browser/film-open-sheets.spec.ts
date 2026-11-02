import type { Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import {
  card,
  expect,
  expectBlind,
  laneHeader as header,
  recastCommands,
  test,
  type RecastWorld,
  type Scene,
} from "./recast-world";

// U5a: the Recast page's tests of its landing list, face sheets, merge suggestion, playback, pinned headers and axe,
// ported to the film document: the "Open film" sheet (File › Open film…), the character card in Properties, the
// Program monitor and the film's tracks (retained regression coverage). The recast is the service's own
// (tests/browser_recast.py).

test.use({ reducedMotion: "reduce" });

/** Mara (the user's picture Arin), Elias (the user's picture too) and The courier (none, looks like Mara), taking
 * the shots in turn; 2 s shots at 25 fps. */
const FILM: Scene = {
  shots: 8,
  length: 50,
  rate: [25, 1],
  characters: ["Mara", "Elias", "The courier"],
  targets: ["confirmed", "confirmed", null],
  similar: [[0, 2]],
  pictures: ["Arin portrait"],
};
test.use({ scene: FILM });
/** The same cast in an 80 s film. */
const LONG: Scene = { ...FILM, length: 250 };

const openSheetDialog = (page: Page) =>
  page.getByRole("dialog", { name: "Open film", exact: true });

/** File › Open film…: the sheet that lists existing recasts and Library videos. */
async function openSheet(page: Page) {
  await page.getByRole("button", { name: "File", exact: true }).click();
  await page.getByRole("menuitem", { name: /Open film/ }).click();
  await expect(openSheetDialog(page)).toBeVisible();
  return openSheetDialog(page);
}

/** Opens the world's film from its Library video (the recast is found, or created when none exists). */
async function openFilm(page: Page, world: RecastWorld) {
  const sheet = await openSheet(page);
  await sheet
    .getByRole("button", { name: world.film.name, exact: true })
    .click();
  // A film with no recast asks before its analysis (ADR 0017 §1); an existing recast opens at once.
  const ask = page.getByRole("dialog", { name: "Analyse this film?" });
  const heading = page.locator(".timeline-heading", {
    hasText: world.film.name,
  });
  // Opening waits behind the newest movie's reads (recast-world.ts openFilm).
  await expect(ask.or(heading)).toBeVisible({ timeout: 20_000 });
  if (await ask.isVisible())
    await ask.getByRole("button", { name: "Analyse film" }).click();
  await expect(heading).toBeVisible({ timeout: 20_000 });
}

const program = (page: Page) =>
  page.getByRole("region", { name: "Program monitor" });

/** Mara's card in Properties. */
async function openMara(page: Page, world: RecastWorld) {
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.locator(header(world.characters[0])).click();
  await expect(
    card(page).getByRole("heading", { name: "Mara", exact: true }),
  ).toBeVisible();
}

/** The status line under a card's "Face sheet" heading. */
const sheetStatus = (page: Page) =>
  card(page).locator(
    'xpath=.//h3[normalize-space()="Face sheet"]/following-sibling::p[@role="status"][1]',
  );

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

const seconds = async (world: RecastWorld, index: number) =>
  Math.round(
    (await world.read()).characters.find(
      (c) => c.id === world.characters[index],
    )!.screen_time_frames / 25,
  );

// Rows 1, 23 and 29 (1)-(5) (8): the "Open film" sheet.

test.describe("a Library film with no recast", () => {
  test.use({ scene: { ...LONG, recast: false } });

  test("the Open film sheet hides generated videos until they are asked for", async ({
    page,
    world,
  }) => {
    const film = await (
      await page.request.get(`/api/v1/assets/${world.film.asset_id}`, {
        headers: world.headers,
      })
    ).json();
    const requests: string[] = [];
    await page.route(
      (url) => url.pathname === "/api/v1/assets",
      (route) => {
        const url = new URL(route.request().url());
        if (url.searchParams.get("kind") !== "video") return route.fallback();
        requests.push(url.search);
        const generated = {
          ...film,
          id: "generated",
          name: "shot.mkv",
          provenance: [{ type: "shot_assembly", source_asset_id: film.id }],
        };
        const items =
          url.searchParams.get("imported_only") === "true"
            ? [film]
            : [generated, film];
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
    const listed = videos.getByRole("button", {
      name: world.film.name,
      exact: true,
    });
    const generated = videos.getByRole("button", {
      name: "shot.mkv",
      exact: true,
    });
    await expect(listed).toBeVisible();
    await expect(generated).toHaveCount(0);
    await expect(listed).toContainText("1:20 · 25 fps");
    await videos.getByLabel("Show generated videos").check();
    await expect(generated).toBeVisible();
    await videos.getByLabel("Show generated videos").uncheck();
    await expect(generated).toHaveCount(0);
    expect(requests.some((q) => q.includes("imported_only=true"))).toBeTruthy();
    // A replacement's process files are never offered as a film, generated videos shown or not: Python names them.
    expect(
      requests.every(
        (q) =>
          new URLSearchParams(q.split("?")[1]).get("hide_process") === "true",
      ),
    ).toBeTruthy();
  });

  // ADR 0017 §1, the user at CP1: creating a film's recast starts its analysis, so a new film asks first.
  test("asks before its analysis; Cancel creates nothing", async ({
    page,
    world,
  }) => {
    const calls = recastCommands(page);
    const creates = () => calls.filter((c) => c.path === "");
    await page.goto("/");
    const sheet = await openSheet(page);
    const film = sheet.getByRole("button", {
      name: world.film.name,
      exact: true,
    });
    await film.click();
    const ask = page.getByRole("dialog", { name: "Analyse this film?" });
    await expect(ask).toContainText(world.film.name);
    await expect(ask).toContainText("1:20 · 25 fps");
    await expect(ask).toContainText("runs on the CPU inside ComfyUI");
    await ask.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(ask).toHaveCount(0);
    // Nothing was created, and the Open film sheet waits for another choice.
    expect(creates()).toHaveLength(0);
    await expect(sheet).toBeVisible();
    await expect(
      page.locator(".timeline-heading", { hasText: world.film.name }),
    ).toHaveCount(0);
    // Escape is a cancel too.
    await film.click();
    await expect(ask).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(ask).toHaveCount(0);
    expect(creates()).toHaveLength(0);
    await film.click();
    await ask.getByRole("button", { name: "Analyse film" }).click();
    await expect(page.locator(".timeline-heading")).toContainText(
      world.film.name,
    );
    expect(creates()).toHaveLength(1);
    expect(creates()[0].body).toMatchObject({
      source_asset_id: world.film.asset_id,
    });
    await expectBlind(page);
  });
});

test("the Open film sheet explains that Recast is not available (501)", async ({
  page,
}) => {
  await page.route("**/api/recasts", (route) =>
    route.fulfill({ status: 501, json: { detail: "pending" } }),
  );
  await page.goto("/");
  const sheet = await openSheet(page);
  await expect(sheet.getByRole("alert")).toContainText(
    "Recast is not available in this service yet",
  );
});

test.describe("an 80 s film", () => {
  test.use({ scene: LONG });

  test("the Open film sheet shows Library metadata, readable analysis states and posters; the title bar names the film", async ({
    page,
    world,
  }) => {
    await page.goto("/");
    let sheet = await openSheet(page);
    await expect(
      sheet.getByRole("button", { name: world.film.name, exact: true }),
    ).toContainText("1:20 · 25 fps");
    const existing = sheet.locator(".recast-existing", {
      hasText: world.title,
    });
    await expect(existing).toContainText("Analysed");
    await expect(
      sheet.getByRole("button", { name: "Previous", exact: true }),
    ).toBeVisible();
    // The film's poster in the Library list.
    const poster = sheet
      .getByRole("button", { name: world.film.name, exact: true })
      .locator("img");
    await expect
      .poll(() => poster.evaluate((img: HTMLImageElement) => img.naturalWidth))
      .toBeGreaterThan(0);
    const src = (await poster.getAttribute("src"))!;
    expect(src).not.toMatch(/^data:/);
    // A cached first frame can be black even though the film has usable footage.
    await page.route(
      (url) => url.pathname === new URL(src, page.url()).pathname,
      (route) =>
        route.fulfill({
          contentType: "image/svg+xml",
          body: '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16" fill="black"/></svg>',
        }),
    );
    await page.reload();
    sheet = await openSheet(page);
    await expect(poster).toHaveAttribute("src", /^data:image\/jpeg/);
    await sheet
      .getByRole("button", { name: world.film.name, exact: true })
      .click();
    await expect(page.locator(".timeline-heading")).toContainText(
      world.film.name,
    );
    await expect(page.locator(".studio-document")).toContainText(
      ` · ${world.film.name}`,
    );
    await expect(page.locator(".studio-titlebar .project-title")).toHaveCount(
      1,
    );
    await expectBlind(page);
  });
});

test("a film that has a recast opens without asking", async ({
  page,
  world,
}) => {
  const calls = recastCommands(page);
  await page.goto("/");
  const sheet = await openSheet(page);
  await sheet
    .getByRole("button", { name: world.film.name, exact: true })
    .click();
  await expect(page.locator(".timeline-heading")).toContainText(
    world.film.name,
  );
  await expect(
    page.getByRole("dialog", { name: "Analyse this film?" }),
  ).toHaveCount(0);
  expect(calls.filter((c) => c.path === "")).toHaveLength(0);
});

// Pilot T8-F3: the Open film sheet lists imported videos; when there are none it says how to include films made here.
test("an empty Open film list says how to include generated videos", async ({
  page,
}) => {
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

// Row 29 (6) (7): the target label.

test.describe("Mara's picture labelled CS079.png", () => {
  test.use({ scene: { ...FILM, labels: ["CS079.png"] } });

  test("a mapped target's label reads without its extension and is edited in place", async ({
    page,
    world,
  }) => {
    const calls = recastCommands(page);
    const before = await world.read();
    await page.goto("/");
    await openFilm(page, world);
    await openMara(page, world);
    const label = card(page).getByLabel("Target label for Mara", {
      exact: true,
    });
    await expect(label).toHaveValue("CS079");
    await label.fill("Arin in the film");
    await label.press("Enter");
    await expect
      .poll(async () => (await world.read()).characters[0].target!.label)
      .toBe("Arin in the film");
    const path = `/${world.id}/characters/${world.characters[0]}/target`;
    expect(calls.filter((c) => c.path === path)).toEqual([
      {
        path,
        method: "PUT",
        body: {
          expected_revision: before.revision,
          asset_id: before.characters[0].target!.asset_id,
          label: "Arin in the film",
        },
      },
    ]);
    await expect(label).toHaveValue("Arin in the film");
    await expectBlind(page);
  });
});

// Row 26 (1) (3) (4): storage that throws.

test.describe("no character has a picture", () => {
  test.use({ scene: { ...FILM, targets: [null, null, null] } });

  test("the film opens and casts a character when storage is unavailable", async ({
    page,
    world,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const calls = recastCommands(page);
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
    const picture = world.pictures[0];
    const mara = world.characters[0];
    await page.goto("/");
    await openFilm(page, world);
    const chips = page.locator('[data-row^="cast-"] .target-chip');
    await expect(chips).toHaveCount(3);
    for (let i = 0; i < 3; i++)
      await expect(chips.nth(i)).toHaveText("No target");
    await openMara(page, world);
    await card(page)
      .getByRole("button", { name: "Set target picture…", exact: true })
      .click();
    await page
      .getByRole("dialog", { name: "Set target picture" })
      .getByRole("button", { name: new RegExp(picture.label) })
      .click();
    const path = `/${world.id}/characters/${mara}/target`;
    await expect
      .poll(() => calls.filter((c) => c.method === "PUT" && c.path === path))
      .toHaveLength(1);
    expect(calls.find((c) => c.path === path)!.body).toMatchObject({
      asset_id: picture.id,
      label: picture.label,
    });
    await expect(page.locator(header(mara))).toContainText(
      `→ ${picture.label} · Mapped`,
    );
    expect(errors).toEqual([]);
    await expectBlind(page);
  });
});

// Row 27, all but (7) (the chooser's order and portraits are in film-cast.spec.ts).

test("a similar face opens a centred, explicit merge with both portraits", async ({
  page,
  world,
}) => {
  const calls = recastCommands(page);
  const merges = () =>
    calls.filter((c) => c.path.endsWith("/characters/merge"));
  const [mara, courier] = [await seconds(world, 0), await seconds(world, 2)];
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/");
  await openFilm(page, world);
  await openMara(page, world);
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
  await expect(dialog).toContainText(`${mara} s on screen`);
  await expect(dialog).toContainText(`${courier} s on screen`);
  // The character without a picture merges into the one with the picture.
  await expect(dialog).toContainText("Merge The courier into Mara?");
  const box = (await dialog.boundingBox())!;
  expect(Math.abs(box.x + box.width / 2 - 640)).toBeLessThan(2);
  expect(Math.abs(box.y + box.height / 2 - 360)).toBeLessThan(2);
  expect(merges()).toHaveLength(0);
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
  expect(merges()).toHaveLength(0);
  await dialog
    .getByRole("button", { name: "Merge characters", exact: true })
    .click();
  await expect
    .poll(() => merges()[0]?.body)
    .toMatchObject({
      sources: [world.characters[0]],
      into: world.characters[2],
    });
  await expectBlind(page);
});

// Rows 33 to 39 and 41: face sheets in the character card.

test("no face sheet shows before one is made", async ({ page, world }) => {
  await page.goto("/");
  await openFilm(page, world);
  await openMara(page, world);
  await expect(card(page).getByText("Mapped")).toBeVisible();
  await expect(
    card(page).getByRole("heading", { name: "Face sheet", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: /face sheet/i })).toHaveCount(
    0,
  );
});

test.describe("Mara's face sheet waits for review", () => {
  test.use({ scene: { ...FILM, rendered: 2, sheets: ["waiting"] } });

  for (const action of ["Approve", "Reject"] as const)
    test(`${action} is a single human request`, async ({ page, world }) => {
      const calls = recastCommands(page);
      await page.goto("/");
      await openFilm(page, world);
      await openMara(page, world);
      await expect(
        card(page).getByRole("heading", { name: "Face sheet", exact: true }),
      ).toHaveCount(1);
      await expect(
        card(page).getByRole("img", {
          name: "Face sheet for Mara",
          exact: true,
        }),
      ).toBeVisible();
      await expect(sheetStatus(page)).toHaveText("Waiting for your review");
      for (const name of ["Approve", "Reject"])
        await expect(
          card(page).getByRole("button", {
            name: `${name} face sheet for Mara`,
            exact: true,
          }),
        ).toBeVisible();
      const revision = (await world.read()).revision;
      await card(page)
        .getByRole("button", { name: `${action} face sheet for Mara` })
        .click();
      await expect(sheetStatus(page)).toHaveText(
        action === "Approve"
          ? "Approved: shots rendered from now on use it"
          : "Rejected",
      );
      const path = `/${world.id}/characters/${world.characters[0]}/reference/${action.toLowerCase()}`;
      expect(calls.filter((c) => c.path.includes("/reference/"))).toEqual([
        { path, method: "POST", body: { expected_revision: revision } },
      ]);
      await expectBlind(page);
    });

  test("the face-sheet thumbnail opens the full preview before approval", async ({
    page,
    world,
  }) => {
    const calls = recastCommands(page);
    await page.goto("/");
    await openFilm(page, world);
    await openMara(page, world);
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
    expect(calls.filter((c) => c.path.includes("/reference/"))).toHaveLength(0);
    await expectBlind(page);
  });

  // Row 40 (3) (4): the face sheet at 390 px.
  test("both decisions stay at 390 px without sideways scrolling", async ({
    page,
    world,
  }) => {
    await page.goto("/");
    await openFilm(page, world);
    await openMara(page, world);
    await page.setViewportSize({ width: 390, height: 844 });
    for (const name of ["Approve", "Reject"])
      await expect(
        card(page).getByRole("button", {
          name: `${name} face sheet for Mara`,
          exact: true,
        }),
      ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  });

  // Rows 24 (5) and 40 (1) (2): axe's default rule set, both themes, with a shot selected and with a character card
  // showing a waiting face sheet.
  for (const theme of ["dark", "light"] as const)
    for (const scope of ["main", undefined])
      test(`the ${scope ? "film workspace" : "whole film page"} passes axe's default rules with a shot and with a waiting face sheet, ${theme}`, async ({
        page,
        world,
      }) => {
        await useTheme(page, theme);
        await page.goto("/");
        await openFilm(page, world);
        await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
        await page.getByRole("tab", { name: "Properties" }).click();
        await page
          .getByRole("button", { name: "Shot 002", exact: true })
          .click();
        await expect(
          card(page).getByRole("heading", { name: "Shot 002", exact: true }),
        ).toBeVisible();
        await expect(program(page).locator(".monitor-heading")).toContainText(
          "Shot 002 · A",
        );
        await expectAxeClean(page, scope);
        await openMara(page, world);
        await expect(
          card(page).getByRole("img", {
            name: "Face sheet for Mara",
            exact: true,
          }),
        ).toBeVisible();
        await expect(sheetStatus(page)).toHaveText("Waiting for your review");
        await expectAxeClean(page, scope);
        await expectBlind(page);
      });
});

for (const [state, words] of [
  ["approved", "Approved: shots rendered from now on use it"],
  ["rejected", "Rejected"],
] as const)
  test.describe(`Mara's face sheet ${state}`, () => {
    test.use({ scene: { ...FILM, sheets: [state] } });

    test(`reads "${words}" and offers no approval`, async ({ page, world }) => {
      await page.goto("/");
      await openFilm(page, world);
      await openMara(page, world);
      await expect(
        card(page).getByRole("heading", { name: "Face sheet", exact: true }),
      ).toHaveCount(1);
      await expect(sheetStatus(page)).toHaveText(words);
      await expect(
        page.getByRole("button", { name: /^(Approve|Reject) face sheet/ }),
      ).toHaveCount(0);
      await expectBlind(page);
    });
  });

// A face sheet being made, or one that failed, lasts too short in the live runner to read; the page's words for
// them are checked on the service's own read with only the sheet's state changed.
for (const [state, words] of [
  ["making", "Being made"],
  ["failed", "Failed"],
] as const)
  test.describe(`Mara's face sheet ${state}`, () => {
    test.use({ scene: { ...FILM, sheets: ["waiting"] } });

    test(`reads "${words}" and offers no approval`, async ({ page, world }) => {
      await page.route(
        (url) => url.pathname === `/api/recasts/${world.id}`,
        async (route) => {
          if (route.request().method() !== "GET") return route.fallback();
          const response = await route.fetch();
          const read = await response.json();
          read.characters[0].target.reference = {
            ...read.characters[0].target.reference,
            asset_id: null,
            state,
          };
          return route.fulfill({ response, json: read });
        },
      );
      await page.goto("/");
      await openFilm(page, world);
      await openMara(page, world);
      await expect(
        card(page).getByRole("heading", { name: "Face sheet", exact: true }),
      ).toHaveCount(1);
      await expect(sheetStatus(page)).toHaveText(words);
      await expect(
        page.getByRole("button", { name: /^(Approve|Reject) face sheet/ }),
      ).toHaveCount(0);
      await expectBlind(page);
    });
  });

// Row 24 (3) (4): Program plays the comparison in sync, at each film rate.

for (const [num, den] of [
  [25, 1],
  [24000, 1001],
  [30000, 1001],
] as const)
  test.describe(`a ${Number((num / den).toFixed(3))} fps film`, () => {
    // The film is encoded at this rate, so the players decode real 23.976 or 29.97 fps media.
    test.use({ scene: { ...FILM, rate: [num, den], rendered: 2 } });

    test("Program plays the shot's original and version in sync", async ({
      page,
      world,
    }) => {
      const fps = num / den;
      const shot = (await world.read()).shots[1];
      await page.goto("/");
      await openFilm(page, world);
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
        .evaluateAll((vs) =>
          vs.map((v) => (v as HTMLVideoElement).currentTime),
        );
      expect(original - shot.start / fps).toBeGreaterThan(0.2);
      expect(Math.abs(original - shot.start / fps - version)).toBeLessThan(
        0.12,
      );
      await expectBlind(page);
    });
  });

// Row 24 (2), the film's version: the track headers stay pinned while the tracks scroll sideways.

test("track headers stay pinned when the timeline scrolls sideways", async ({
  page,
  world,
}) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/");
  await openFilm(page, world);
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
