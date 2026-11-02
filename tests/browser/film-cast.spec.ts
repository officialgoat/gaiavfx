import type { Page } from "@playwright/test";
import {
  card,
  expect,
  expectBlind,
  laneHeader as header,
  openFilm,
  panel,
  recastCommands,
  reopen,
  test,
  type Scene,
} from "./recast-world";

// ADR 0017 U4b: cast edits on the film's tracks and cards. A picture the user gives sets the target at once; the
// agent's proposals wait for Confirm or Reject; merging asks first. The recast is the service's own
// (tests/browser_recast.py): every edit below is a real command.

/** Mara (the user's picture Arin), Elias (Gaia's proposal) and The courier (no picture, looks like Mara), who take
 * the shots in turn; one face in Shot 003 belongs to no one; Mara's shots are planned. 25 fps, 2 s shots. */
const CAST: Scene = {
  shots: 8,
  length: 50,
  rate: [25, 1],
  characters: ["Mara", "Elias", "The courier"],
  targets: ["confirmed", "proposed", null],
  similar: [[0, 2]],
  unknown: 1,
  planned: true,
  pictures: ["Arin portrait"],
};
test.use({ scene: CAST });

/** An HTML drag and drop with a scripted DataTransfer, as a pointer drag would deliver it. */
async function drop(page: Page, selector: string, type: string, data: string) {
  await page.locator(selector).evaluate(
    (element, [kind, value]) => {
      const transfer = new DataTransfer();
      transfer.setData(kind, value);
      for (const name of ["dragenter", "dragover", "drop"])
        element.dispatchEvent(
          new DragEvent(name, {
            dataTransfer: transfer,
            bubbles: true,
            cancelable: true,
          }),
        );
    },
    [type, data],
  );
}

const commands = new WeakMap<Page, ReturnType<typeof recastCommands>>();
const callsOf = (page: Page) => commands.get(page)!;
const lastCall = (page: Page) =>
  callsOf(page).at(-1) ?? { path: "", method: "", body: {} };

test.beforeEach(async ({ page, world }) => {
  commands.set(page, recastCommands(page));
  await page.goto("/");
  await openFilm(page, world);
});
test.afterEach(async ({ page }) => expectBlind(page));

async function select(page: Page, id: string) {
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.locator(header(id)).click();
}

test("a Library picture dropped on a lane header casts the character at once", async ({
  page,
  world,
}) => {
  const [courier, picture] = [world.characters[2], world.pictures[0]];
  await drop(page, header(courier), "application/x-gaiavfx-asset", picture.id);
  await expect
    .poll(() => lastCall(page).path)
    .toBe(`/${world.id}/characters/${courier}/target`);
  expect(lastCall(page).body).toMatchObject({
    asset_id: picture.id,
    label: picture.label,
  });
  await expect(page.locator(header(courier))).toContainText(
    `→ ${picture.label} · Mapped`,
  );
});

test("the agent's proposal waits for Confirm, and Reject removes it", async ({
  page,
  world,
}) => {
  const elias = world.characters[1];
  await select(page, elias);
  await expect(card(page)).toContainText("Proposed by the agent");
  await card(page)
    .getByRole("button", { name: "Confirm", exact: true })
    .click();
  await expect
    .poll(() => lastCall(page).path)
    .toBe(`/${world.id}/characters/${elias}/target/confirm`);
  await expect(page.locator(header(elias))).toContainText("Mapped");
  await expect(card(page)).not.toContainText("Proposed by the agent");
  await expect(
    card(page).getByRole("button", { name: /^(Confirm|Reject)$/ }),
  ).toHaveCount(0);
  await expect(page.locator(".target-chip.proposed")).toHaveCount(0);
  await card(page).getByRole("button", { name: "Clear", exact: true }).click();
  await expect.poll(() => lastCall(page).method).toBe("DELETE");
  await expect(page.locator(header(elias))).toContainText("No target");
});

test("a picture can be set without dragging", async ({ page, world }) => {
  const courier = world.characters[2];
  await select(page, courier);
  await card(page).getByRole("button", { name: "Set target picture…" }).click();
  const sheet = page.getByRole("dialog", { name: "Set target picture" });
  await sheet
    .getByRole("button", { name: new RegExp(world.pictures[0].label) })
    .click();
  await expect
    .poll(() => lastCall(page).path)
    .toBe(`/${world.id}/characters/${courier}/target`);
  await expect(sheet).toHaveCount(0);
});

test("a character is renamed, hidden, found again and shown", async ({
  page,
  world,
}) => {
  const mara = world.characters[0];
  await select(page, mara);
  await card(page).getByRole("button", { name: "Rename…" }).click();
  const sheet = page.getByRole("dialog", { name: "Rename character" });
  await sheet.getByLabel("Name").fill("Celia");
  await sheet.getByRole("button", { name: "Rename", exact: true }).click();
  await expect.poll(() => lastCall(page).body.label).toBe("Celia");
  await expect(page.locator(header(mara))).toContainText("Celia");
  await card(page).getByRole("button", { name: "Hide character" }).click();
  await expect.poll(() => lastCall(page).body.hidden).toBe(true);
  await expect(page.locator(`[data-row="cast-${mara}"]`)).toHaveCount(0);
  await panel(page).getByRole("button", { name: /^Cast/ }).click();
  await panel(page)
    .getByText(/Hidden characters \(1\)/)
    .click();
  await panel(page)
    .getByRole("button", { name: /^Celia/ })
    .click();
  await card(page).getByRole("button", { name: "Show character" }).click();
  await expect.poll(() => lastCall(page).body.hidden).toBe(false);
  await expect(page.locator(`[data-row="cast-${mara}"]`)).toHaveCount(1);
});

test("one header dropped on another asks before merging", async ({
  page,
  world,
}) => {
  const [mara, , courier] = world.characters;
  await drop(page, header(mara), "application/x-gaiavfx-character", courier);
  const sheet = page.getByRole("dialog", { name: "Merge characters" });
  await expect(sheet).toContainText("Merge The courier into Mara?");
  expect(callsOf(page).some((c) => c.path.endsWith("/merge"))).toBe(false);
  await sheet.getByRole("button", { name: "Merge characters" }).click();
  await expect
    .poll(() => lastCall(page).path)
    .toBe(`/${world.id}/characters/merge`);
  expect(lastCall(page).body).toMatchObject({
    sources: [courier],
    into: mara,
  });
  await expect(page.locator(`[data-row="cast-${courier}"]`)).toHaveCount(0);
});

test("film items have menus: cast edits on a lane, review on a shot", async ({
  page,
  world,
}) => {
  await page
    .locator(`[data-row="cast-${world.characters[1]}"] .cast-item`)
    .first()
    .click({ button: "right" });
  for (const name of [
    "Set target picture…",
    "Confirm proposed picture",
    "Rename…",
    "Merge with…",
    "Hide character",
  ])
    await expect(
      page.getByRole("menuitem", { name: new RegExp(`^${name}`) }),
    ).toBeVisible();
  await page.keyboard.press("Escape");
  await page
    .getByRole("button", { name: "Shot 001", exact: true })
    .click({ button: "right" });
  await expect(
    page.getByRole("menuitem", { name: /^Accept and go to next/ }),
  ).toBeVisible();
  await expect(page.getByRole("menuitem", { name: /^Retake/ })).toBeVisible();
});

test("a lane header opens its character's menu, and its face tracks", async ({
  page,
  world,
}) => {
  const mara = world.characters[0];
  await page.locator(header(mara)).click({ button: "right" });
  await expect(page.getByRole("menuitem", { name: /^Rename…/ })).toBeVisible();
  await expect(
    page.getByRole("menuitem", { name: /^Clear target picture/ }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await select(page, mara);
  await card(page).getByRole("button", { name: "Face tracks…" }).click();
  await expect(
    page.getByRole("dialog", { name: "Mara · tracks" }),
  ).toBeVisible();
});

test("a cast sheet shows its own failure", async ({ page, world }) => {
  // The rename meets an edit made elsewhere: the service's own refusal, shown in the sheet.
  await page.route("**/api/recasts/*/characters/*", async (route) => {
    if (route.request().method() !== "PATCH") return route.fallback();
    const body = route.request().postDataJSON();
    return route.fallback({
      postData: JSON.stringify({
        ...body,
        expected_revision: body.expected_revision - 1,
      }),
    });
  });
  await select(page, world.characters[0]);
  await card(page).getByRole("button", { name: "Rename…" }).click();
  const sheet = page.getByRole("dialog", { name: "Rename character" });
  await sheet.getByLabel("Name").fill("Celia");
  await sheet.getByRole("button", { name: "Rename", exact: true }).click();
  await expect(sheet.getByRole("alert")).toContainText(
    "The recast changed; reload before editing.",
  );
});

test("a shot is excluded and included again from its menu", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: "Shot 001", exact: true })
    .click({ button: "right" });
  await page.getByRole("menuitem", { name: /^Exclude shot/ }).click();
  await expect.poll(() => lastCall(page).body.included).toBe(false);
  await expect(
    page.getByRole("button", { name: "Shot 001 (excluded)", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Shot 001 (excluded)", exact: true })
    .click({ button: "right" });
  await page.getByRole("menuitem", { name: /^Include shot/ }).click();
  await expect.poll(() => lastCall(page).body.included).toBe(true);
});

test("the merge chooser leads with similar faces and the suggestion keeps the picture", async ({
  page,
  world,
}) => {
  const [mara, , courier] = world.characters;
  const read = await world.read();
  const seconds = Math.round(
    read.characters.find((c) => c.id === courier)!.screen_time_frames / 25,
  );
  // The courier has no picture and Mara has one, so the suggestion merges The courier into Mara from either card.
  await select(page, courier);
  await card(page)
    .getByRole("button", { name: "Merge The courier into Mara…" })
    .click();
  const sheet = page.getByRole("dialog", { name: "Merge characters" });
  await expect(sheet).toContainText("Merge The courier into Mara?");
  await expect(
    sheet.getByRole("img", { name: "The courier face" }),
  ).toBeVisible();
  await expect(sheet.getByRole("img", { name: "Mara face" })).toBeVisible();
  expect(callsOf(page).some((c) => c.path.endsWith("/merge"))).toBe(false);
  await page.keyboard.press("Escape");
  await expect(sheet).toHaveCount(0);
  await page.locator(header(mara)).click();
  await expect(
    card(page).getByRole("button", { name: "Merge The courier into Mara…" }),
  ).toBeVisible();
  await card(page).getByRole("button", { name: "Merge with…" }).click();
  const choices = sheet.getByRole("group", { name: "Merge into" });
  // Similar faces first, then screen time: Elias is seen longer than The courier but does not look alike.
  expect(
    await choices
      .getByRole("button")
      .evaluateAll((els) => els.map((el) => el.getAttribute("aria-label"))),
  ).toEqual(["Merge into The courier", "Merge into Elias"]);
  const choice = choices.getByRole("button", {
    name: "Merge into The courier",
  });
  await expect(
    choice.getByRole("img", { name: "The courier face" }),
  ).toBeVisible();
  await expect(choice).toContainText(`${seconds} s on screen`);
  await expect(choice).toContainText("Similar face");
  await choice.click();
  await expect(sheet).toContainText("Merge Mara into The courier?");
  await expect(sheet.getByRole("note")).toHaveText(
    "Mara’s picture Arin will be removed.",
  );
  await sheet.getByRole("button", { name: "Merge characters" }).click();
  await expect
    .poll(() => lastCall(page).path)
    .toBe(`/${world.id}/characters/merge`);
  expect(lastCall(page).body).toMatchObject({ sources: [mara], into: courier });
});

// Parity row 25 (3): a character's lane has no item where the character is not seen, and its items say how much.
test("a cast lane draws an item only where its character is seen", async ({
  page,
  world,
}) => {
  const labels = await page
    .locator(`[data-row="cast-${world.characters[0]}"] .cast-item`)
    .evaluateAll((els) => els.map((el) => el.getAttribute("aria-label") ?? ""));
  // Mara takes every third shot from Shot 001.
  expect(labels.map((l) => l.split(":")[0])).toEqual([
    "Mara, shot 001",
    "Mara, shot 004",
    "Mara, shot 007",
  ]);
  expect(labels.every((l) => /^Mara, shot \d{3}: seen \d+%$/.test(l))).toBe(
    true,
  );
});

// The old page's Unknown faces drawer, kept for the film: tracks no character holds move onto one.
test.describe("two faces that belong to no one", () => {
  test.use({ scene: { ...CAST, unknown: 2 } });

  test("move onto a character from the Cast bin or the Review menu", async ({
    page,
    world,
  }) => {
    const unknown = (await world.read()).unknown_faces.map((f) => f.track_id);
    const moves = () =>
      callsOf(page).filter((c) => c.path.endsWith("/tracks/move"));
    await panel(page).getByRole("button", { name: /^Cast/ }).click();
    await panel(page)
      .getByRole("button", { name: /^Unknown faces/ })
      .click();
    const drawer = page.getByRole("dialog", { name: "Unknown faces" });
    await expect(
      drawer.getByRole("checkbox", { name: "Shot 003, unknown identity" }),
    ).toBeVisible();
    await drawer.getByRole("checkbox").first().check();
    await drawer.getByLabel("Move to").selectOption(world.characters[1]);
    await drawer.getByRole("button", { name: "Move 1 track" }).click();
    await expect.poll(() => moves().length).toBe(1);
    const moved = moves()[0].body;
    expect(moved.to).toBe(world.characters[1]);
    expect(moved.track_ids).toHaveLength(1);
    expect(unknown).toContain((moved.track_ids as string[])[0]);
    await page.keyboard.press("Escape");
    // The Review menu opens the same drawer while a face still belongs to no one.
    await page
      .getByRole("navigation", { name: "Application menus" })
      .getByRole("button", { name: "Review", exact: true })
      .click();
    await page.getByRole("menuitem", { name: /^Unknown faces/ }).click();
    await expect(drawer).toBeVisible();
    await drawer.getByRole("checkbox").first().check();
    await drawer.getByLabel("Move to").selectOption(world.characters[1]);
    await drawer.getByRole("button", { name: "Move 1 track" }).click();
    await expect.poll(() => moves().length).toBe(2);
    // With no unknown tracks left the command says why.
    await page.keyboard.press("Escape");
    await expect(
      panel(page).getByRole("button", { name: /^Unknown faces/ }),
    ).toHaveAttribute("title", "Every face track belongs to a character.", {
      timeout: 8000,
    });
  });
});

test.describe("a face sheet waiting for review", () => {
  test.use({ scene: { ...CAST, sheets: ["waiting"] } });

  test("is shown before it can be approved, and its buttons name the character", async ({
    page,
    world,
  }) => {
    await select(page, world.characters[0]);
    await expect(card(page)).toContainText("Waiting for your review");
    await expect(card(page).locator(".film-card-target")).toHaveCount(2);
    await expect(
      card(page).getByRole("button", { name: "Approve face sheet for Mara" }),
    ).toBeVisible();
    await expect(
      card(page).getByRole("button", { name: "Reject face sheet for Mara" }),
    ).toBeVisible();
  });
});

test.describe("only The courier mapped", () => {
  // The Recast page's fixture: only the courier is mapped, Mara has no picture, Elias only a proposal.
  test.use({
    scene: {
      ...CAST,
      targets: [null, "proposed", "confirmed"],
      labels: [null, null, "CS079.png"],
      planned: false,
    },
  });

  test("confirmed targets lead the cast lanes, labels read without file extensions", async ({
    page,
    world,
  }) => {
    const [mara, elias, courier] = world.characters;
    // Confirmed targets first, then screen time: a proposal does not count as a mapping.
    expect(
      await page
        .locator('[data-row^="cast-"]')
        .evaluateAll((els) => els.map((el) => el.getAttribute("data-row"))),
    ).toEqual([`cast-${courier}`, `cast-${mara}`, `cast-${elias}`]);
    await expect(page.locator(header(courier))).toContainText(
      "→ CS079 · Mapped",
    );
  });
});

test.describe("two characters who look alike, both with pictures", () => {
  test.use({
    scene: {
      ...CAST,
      targets: ["confirmed", "proposed", "confirmed"],
      labels: [null, null, "Bea"],
      planned: false,
    },
  });

  test("are not suggested as one person", async ({ page, world }) => {
    const [mara, , courier] = world.characters;
    for (const [id, name] of [
      [mara, "Mara"],
      [courier, "The courier"],
    ]) {
      await select(page, id);
      await expect(card(page).getByRole("heading", { level: 2 })).toHaveText(
        name,
      );
      await expect(
        card(page).getByRole("heading", { name: "Same person?" }),
      ).toHaveCount(0);
    }
  });
});

test.describe("a person with no face in Shot 001", () => {
  const evidence = {
    candidates: [{ continuity: 0.8, appearance: 0.85 }],
  };

  test.describe("proposed for Elias by the analysis", () => {
    test.use({
      scene: { ...CAST, links: [{ shot: 0, character: 1, evidence }] },
    });

    test("waits for a human decision", async ({ page, world }) => {
      await page.getByRole("tab", { name: "Properties" }).click();
      await page.getByRole("button", { name: "Shot 001", exact: true }).click();
      await card(page)
        .getByText(/Person links \(1, some proposed\)/)
        .click();
      await expect(card(page)).toContainText(
        "Track back → Elias · Proposed by the analysis",
      );
      await expect(card(page)).toContainText("clothing 0.85 (not calibrated)");
      await card(page)
        .locator(".film-card-link")
        .getByRole("button", { name: "Confirm" })
        .click();
      await expect
        .poll(() => lastCall(page).path)
        .toMatch(
          new RegExp(
            `^/${world.id}/shots/${world.shots[0]}/appearances/[^/]+/back/confirm$`,
          ),
        );
      expect(lastCall(page).body).toMatchObject({
        character_id: world.characters[1],
      });
      await card(page).getByRole("button", { name: "Suggest links" }).click();
      await expect
        .poll(() => lastCall(page).path)
        .toBe(`/${world.id}/shots/${world.shots[0]}/appearances/suggest`);
    });
  });

  test.describe("confirmed for Elias", () => {
    test.use({
      scene: {
        ...CAST,
        links: [{ shot: 0, character: 1, state: "confirmed", evidence }],
      },
    });

    // Fable #3 P2-2: a shot's people links are decided through commands, which name their reasons.
    test("is decided through commands with reasons", async ({
      page,
      world,
    }) => {
      await page.getByRole("tab", { name: "Properties" }).click();
      await page.getByRole("button", { name: "Shot 001", exact: true }).click();
      await card(page)
        .getByText(/Person links \(1\)/)
        .click();
      const row = card(page).locator(".film-card-link");
      await expect(row).toContainText("Track back → Elias · Confirmed");
      await expect(row.getByRole("button", { name: "Confirm" })).toHaveCount(0);
      await row.getByRole("button", { name: "Clear decision" }).click();
      await expect.poll(() => lastCall(page).method).toBe("DELETE");
      expect(lastCall(page).path).toMatch(
        new RegExp(
          `^/${world.id}/shots/${world.shots[0]}/appearances/[^/]+/back$`,
        ),
      );
      // The shot menu suggests links; a shot with no plan (The courier's) says why it cannot.
      await page
        .getByRole("button", { name: "Shot 003 (excluded)", exact: true })
        .click({ button: "right" });
      const suggest = page.getByRole("menuitem", {
        name: /^Suggest person links/,
      });
      await expect(suggest).toHaveAttribute("aria-disabled", "true");
      await page.keyboard.press("Escape");
      // The People section keeps its open state across shots: set it rather than toggle it.
      await card(page)
        .locator("details", { hasText: "Person links" })
        .evaluate((d: HTMLDetailsElement) => (d.open = true));
      await expect(
        card(page).getByRole("button", { name: "Suggest links" }),
      ).toHaveAttribute(
        "title",
        "Plan the shot first: links join its window tracks.",
      );
    });
  });
});
