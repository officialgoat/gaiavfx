import { test, expect, type Page } from "@playwright/test";
import { filmAsset, mockRecast, pictureAsset } from "./recast-fixtures";

// ADR 0017 U4b: cast edits on the film's tracks and cards. A picture the user gives sets the target at once; the
// agent's proposals wait for Confirm or Reject; merging asks first.

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

const header = (id: string) => `[data-row="cast-${id}"] .cast-header`;
const card = (page: Page) => page.locator(".film-properties");
const lastCall = (mock: Awaited<ReturnType<typeof mockRecast>>) =>
  mock.calls.at(-1) ?? {
    path: "",
    method: "",
    body: {} as Record<string, unknown>,
  };

test.beforeEach(async ({ page }) => {
  const mock = await mockRecast(page);
  mock.existing();
  (page as Page & { mock?: typeof mock }).mock = mock;
  await page.goto("/");
  await openFilm(page);
});
const mockOf = (page: Page) =>
  (page as Page & { mock: Awaited<ReturnType<typeof mockRecast>> }).mock;

test("a Library picture dropped on a lane header casts the character at once", async ({
  page,
}) => {
  const mock = mockOf(page);
  await drop(
    page,
    header("character-2"),
    "application/x-gaiavfx-asset",
    pictureAsset.id,
  );
  await expect
    .poll(() => lastCall(mock).path)
    .toBe("/recast-1/characters/character-2/target");
  expect(lastCall(mock).body).toMatchObject({
    asset_id: pictureAsset.id,
    label: "Arin portrait",
  });
  await expect(page.locator(header("character-2"))).toContainText(
    "→ Arin portrait · Mapped",
  );
});

test("the agent's proposal waits for Confirm, and Reject removes it", async ({
  page,
}) => {
  const mock = mockOf(page);
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.locator(header("character-1")).click();
  await expect(card(page)).toContainText("Proposed by the agent");
  await card(page)
    .getByRole("button", { name: "Confirm", exact: true })
    .click();
  await expect
    .poll(() => lastCall(mock).path)
    .toBe("/recast-1/characters/character-1/target/confirm");
  await expect(page.locator(header("character-1"))).toContainText("Mapped");
  await expect(card(page)).not.toContainText("Proposed by the agent");
  await expect(
    card(page).getByRole("button", { name: /^(Confirm|Reject)$/ }),
  ).toHaveCount(0);
  await expect(page.locator(".target-chip.proposed")).toHaveCount(0);
  await card(page).getByRole("button", { name: "Clear", exact: true }).click();
  await expect.poll(() => lastCall(mock).method).toBe("DELETE");
  await expect(page.locator(header("character-1"))).toContainText("No target");
});

test("a picture can be set without dragging", async ({ page }) => {
  const mock = mockOf(page);
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.locator(header("character-2")).click();
  await card(page).getByRole("button", { name: "Set target picture…" }).click();
  const sheet = page.getByRole("dialog", { name: "Set target picture" });
  await sheet.getByRole("button", { name: pictureAsset.name }).click();
  await expect
    .poll(() => lastCall(mock).path)
    .toBe("/recast-1/characters/character-2/target");
  await expect(sheet).toHaveCount(0);
});

test("a character is renamed, hidden, found again and shown", async ({
  page,
}) => {
  const mock = mockOf(page);
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.locator(header("character-0")).click();
  await card(page).getByRole("button", { name: "Rename…" }).click();
  const sheet = page.getByRole("dialog", { name: "Rename character" });
  await sheet.getByLabel("Name").fill("Celia");
  await sheet.getByRole("button", { name: "Rename", exact: true }).click();
  await expect.poll(() => lastCall(mock).body.label).toBe("Celia");
  await expect(page.locator(header("character-0"))).toContainText("Celia");
  await card(page).getByRole("button", { name: "Hide character" }).click();
  await expect.poll(() => lastCall(mock).body.hidden).toBe(true);
  await expect(page.locator('[data-row="cast-character-0"]')).toHaveCount(0);
  const panel = page.locator(".film-project");
  await panel.getByRole("button", { name: /^Cast/ }).click();
  await panel.getByText(/Hidden characters \(1\)/).click();
  await panel.getByRole("button", { name: /^Celia/ }).click();
  await card(page).getByRole("button", { name: "Show character" }).click();
  await expect.poll(() => lastCall(mock).body.hidden).toBe(false);
  await expect(page.locator('[data-row="cast-character-0"]')).toHaveCount(1);
});

test("one header dropped on another asks before merging", async ({ page }) => {
  const mock = mockOf(page);
  await drop(
    page,
    header("character-0"),
    "application/x-gaiavfx-character",
    "character-2",
  );
  const sheet = page.getByRole("dialog", { name: "Merge characters" });
  await expect(sheet).toContainText("Merge The courier into Mara?");
  expect(mock.calls.some((c) => c.path.endsWith("/merge"))).toBe(false);
  await sheet.getByRole("button", { name: "Merge characters" }).click();
  await expect
    .poll(() => lastCall(mock).path)
    .toBe("/recast-1/characters/merge");
  expect(lastCall(mock).body).toMatchObject({
    sources: ["character-2"],
    into: "character-0",
  });
  await expect(page.locator('[data-row="cast-character-2"]')).toHaveCount(0);
});

test("film items have menus: cast edits on a lane, review on a shot", async ({
  page,
}) => {
  await page
    .locator('[data-row="cast-character-1"] .cast-item')
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
    .getByRole("button", { name: "Shot 002", exact: true })
    .click({ button: "right" });
  await expect(
    page.getByRole("menuitem", { name: /^Accept and go to next/ }),
  ).toBeVisible();
  await expect(page.getByRole("menuitem", { name: /^Retake/ })).toBeVisible();
});

// Fix pass after Astra R3 (21 October).

test("a waiting face sheet is shown before it can be approved", async ({
  page,
}) => {
  const mock = mockOf(page);
  mock.value.characters[0].target!.reference = {
    state: "waiting",
    asset_id: "face-sheet",
  } as never;
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.locator(header("character-0")).click();
  await expect(card(page)).toContainText("Waiting for your review");
  await expect(card(page).locator(".film-card-target")).toHaveCount(2);
  await expect(
    card(page).getByRole("button", { name: "Approve face sheet" }),
  ).toBeVisible();
});

test("a lane header opens its character's menu, and its face tracks", async ({
  page,
}) => {
  await page.locator(header("character-0")).click({ button: "right" });
  await expect(page.getByRole("menuitem", { name: /^Rename…/ })).toBeVisible();
  await expect(
    page.getByRole("menuitem", { name: /^Clear target picture/ }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.locator(header("character-0")).click();
  await card(page).getByRole("button", { name: "Face tracks…" }).click();
  await expect(
    page.getByRole("dialog", { name: "Mara · tracks" }),
  ).toBeVisible();
});

test("a head pass for two characters asks which, with Properties closed", async ({
  page,
}) => {
  const mock = mockOf(page);
  mock.value.shots[1].plan!.performers = ["character-0", "character-1"];
  // The reload makes the film read the two performers at once instead of on the next poll.
  await page.reload();
  await expect(page.locator(".timeline-heading")).toContainText(filmAsset.name);
  // Properties closed: a film without a movie opens on Properties, so another tab comes forward.
  await page.getByRole("tab", { name: "Jobs" }).click();
  await page.getByRole("button", { name: "Shot 002", exact: true }).click();
  await page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Review", exact: true })
    .click();
  await page.getByRole("menuitem", { name: /^Head pass/ }).click();
  const sheet = page.getByRole("dialog", { name: "Head pass" });
  await sheet.getByRole("button", { name: /^Elias/ }).click();
  await expect
    .poll(() => lastCall(mock).path)
    .toBe("/recast-1/shots/shot-1/head-pass");
  expect(lastCall(mock).body).toMatchObject({ character_id: "character-1" });
});

test("a cast sheet shows its own failure", async ({ page }) => {
  await page.route("**/api/recasts/*/characters/*", (route) =>
    route.request().method() === "PATCH"
      ? route.fulfill({
          status: 409,
          json: { detail: "The recast changed. Refresh and try again." },
        })
      : route.fallback(),
  );
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.locator(header("character-0")).click();
  await card(page).getByRole("button", { name: "Rename…" }).click();
  const sheet = page.getByRole("dialog", { name: "Rename character" });
  await sheet.getByLabel("Name").fill("Celia");
  await sheet.getByRole("button", { name: "Rename", exact: true }).click();
  await expect(sheet.getByRole("alert")).toContainText(
    "The recast changed. Refresh and try again.",
  );
});

test("a shot's proposed people wait for a human decision", async ({ page }) => {
  const mock = mockOf(page);
  mock.value.shots[1].appearance_links = [
    {
      track_id: "t9",
      analysis_operation_id: "window-op",
      character_id: "character-1",
      state: "proposed",
      proposed_by: "analysis",
      evidence: {
        candidates: [
          { character_id: "character-1", continuity: 0.8, appearance: 0.85 },
        ],
      },
      created_at: "2026-10-21T10:00:00Z",
      decided_at: null,
    },
  ];
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.getByRole("button", { name: "Shot 002", exact: true }).click();
  await card(page)
    .getByText(/Person links \(1, some proposed\)/)
    .click();
  await expect(card(page)).toContainText(
    "Track t9 → Elias · Proposed by the analysis",
  );
  await expect(card(page)).toContainText("clothing 0.85 (not calibrated)");
  await card(page)
    .locator(".film-card-link")
    .getByRole("button", { name: "Confirm" })
    .click();
  await expect
    .poll(() => lastCall(mock).path)
    .toBe("/recast-1/shots/shot-1/appearances/window-op/t9/confirm");
  expect(lastCall(mock).body).toMatchObject({ character_id: "character-1" });
  await card(page).getByRole("button", { name: "Suggest links" }).click();
  await expect
    .poll(() => lastCall(mock).path)
    .toBe("/recast-1/shots/shot-1/appearances/suggest");
});

// U5a: behaviours the Recast page's tests protected, on the film (retained regression coverage).

test("confirmed targets lead the cast lanes, labels read without file extensions", async ({
  page,
}) => {
  const mock = mockOf(page);
  // The Recast page's fixture: only the courier is mapped, Mara has no picture, Elias only a proposal.
  mock.value.characters[2].target = {
    asset_id: pictureAsset.id,
    label: "CS079.png",
    state: "confirmed",
    proposed_by: "user",
    face: "face",
  } as never;
  mock.value.characters[0].target = null;
  await page.reload();
  await expect(page.locator(".timeline-heading")).toContainText(filmAsset.name);
  // Confirmed targets first, then screen time: a proposal does not count as a mapping.
  expect(
    await page
      .locator('[data-row^="cast-"]')
      .evaluateAll((els) => els.map((el) => el.getAttribute("data-row"))),
  ).toEqual(["cast-character-2", "cast-character-0", "cast-character-1"]);
  await expect(page.locator(header("character-2"))).toContainText(
    "→ CS079 · Mapped",
  );
});

test("face-sheet buttons name their character", async ({ page }) => {
  const mock = mockOf(page);
  mock.value.characters[0].target!.reference = {
    state: "waiting",
    asset_id: "face-sheet",
  } as never;
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.locator(header("character-0")).click();
  await expect(
    card(page).getByRole("button", { name: "Approve face sheet for Mara" }),
  ).toBeVisible();
  await expect(
    card(page).getByRole("button", { name: "Reject face sheet for Mara" }),
  ).toBeVisible();
});

test("a shot is excluded and included again from its menu", async ({
  page,
}) => {
  const mock = mockOf(page);
  await page
    .getByRole("button", { name: "Shot 002", exact: true })
    .click({ button: "right" });
  await page.getByRole("menuitem", { name: /^Exclude shot/ }).click();
  await expect.poll(() => lastCall(mock).body.included).toBe(false);
  await expect(
    page.getByRole("button", { name: "Shot 002 (excluded)", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Shot 002 (excluded)", exact: true })
    .click({ button: "right" });
  await page.getByRole("menuitem", { name: /^Include shot/ }).click();
  await expect.poll(() => lastCall(mock).body.included).toBe(true);
});

test("the merge chooser leads with similar faces and the suggestion keeps the picture", async ({
  page,
}) => {
  const mock = mockOf(page);
  await page.getByRole("tab", { name: "Properties" }).click();
  // The courier has no picture and Mara has one, so the suggestion merges The courier into Mara from either card.
  await page.locator(header("character-2")).click();
  await card(page)
    .getByRole("button", { name: "Merge The courier into Mara…" })
    .click();
  const sheet = page.getByRole("dialog", { name: "Merge characters" });
  await expect(sheet).toContainText("Merge The courier into Mara?");
  await expect(
    sheet.getByRole("img", { name: "The courier face" }),
  ).toBeVisible();
  await expect(sheet.getByRole("img", { name: "Mara face" })).toBeVisible();
  expect(mock.calls.some((c) => c.path.endsWith("/merge"))).toBe(false);
  await page.keyboard.press("Escape");
  await expect(sheet).toHaveCount(0);
  await page.locator(header("character-0")).click();
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
  const courier = choices.getByRole("button", {
    name: "Merge into The courier",
  });
  await expect(
    courier.getByRole("img", { name: "The courier face" }),
  ).toBeVisible();
  await expect(courier).toContainText("16 s on screen");
  await expect(courier).toContainText("Similar face");
  await courier.click();
  await expect(sheet).toContainText("Merge Mara into The courier?");
  await expect(sheet.getByRole("note")).toHaveText(
    "Mara’s picture Arin will be removed.",
  );
  await sheet.getByRole("button", { name: "Merge characters" }).click();
  await expect
    .poll(() => lastCall(mock).path)
    .toBe("/recast-1/characters/merge");
  expect(lastCall(mock).body).toMatchObject({
    sources: ["character-0"],
    into: "character-2",
  });
});

test("two characters that both have a picture are not suggested as one person", async ({
  page,
}) => {
  const mock = mockOf(page);
  mock.value.characters[2].target = {
    asset_id: pictureAsset.id,
    label: "Bea",
    state: "confirmed",
    proposed_by: "user",
    face: "face",
  } as never;
  await page.reload();
  await expect(page.locator(".timeline-heading")).toContainText(filmAsset.name);
  await page.getByRole("tab", { name: "Properties" }).click();
  for (const id of ["character-0", "character-2"]) {
    await page.locator(header(id)).click();
    await expect(card(page).getByRole("heading", { level: 2 })).toHaveText(
      id === "character-0" ? "Mara" : "The courier",
    );
    await expect(
      card(page).getByRole("heading", { name: "Same person?" }),
    ).toHaveCount(0);
  }
});

// Parity row 25 (3): a character's lane has no item where the character is not seen, and its items say how much.
test("a cast lane draws an item only where its character is seen", async ({
  page,
}) => {
  const lane = page.locator('[data-row="cast-character-0"] .cast-item');
  const labels = await lane.evaluateAll((els) =>
    els.map((el) => el.getAttribute("aria-label") ?? ""),
  );
  // Mara is seen in shots 1, 2, 4, 5 ... and not in shot 3 (fixture: every third shot from shot 3).
  expect(labels).toContain("Mara, shot 001: seen 80%");
  expect(labels.some((l) => l.startsWith("Mara, shot 003:"))).toBe(false);
  expect(labels.every((l) => /^Mara, shot \d{3}: seen \d+%$/.test(l))).toBe(
    true,
  );
});

// Fable #3 P2-2: a shot's people links are decided through commands, which name their reasons.
test("person links are decided through commands with reasons", async ({
  page,
}) => {
  const mock = mockOf(page);
  const link = {
    track_id: "t9",
    analysis_operation_id: "window-op",
    character_id: "character-1",
    state: "confirmed",
    proposed_by: "analysis",
    evidence: { candidates: [] },
    created_at: "2026-10-21T10:00:00Z",
    decided_at: "2026-10-21T10:01:00Z",
  };
  mock.value.shots[1].appearance_links = [link] as never;
  mock.value.shots[2].plan = null as never;
  await page.reload();
  await expect(page.locator(".timeline-heading")).toContainText(filmAsset.name);
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.getByRole("button", { name: "Shot 002", exact: true }).click();
  await card(page)
    .getByText(/Person links \(1\)/)
    .click();
  const row = card(page).locator(".film-card-link");
  await expect(row).toContainText("Track t9 → Elias · Confirmed");
  await expect(row.getByRole("button", { name: "Confirm" })).toHaveCount(0);
  await row.getByRole("button", { name: "Clear decision" }).click();
  await expect.poll(() => lastCall(mock).method).toBe("DELETE");
  expect(lastCall(mock).path).toBe(
    "/recast-1/shots/shot-1/appearances/window-op/t9",
  );
  // The shot menu suggests links; a shot with no plan says why it cannot.
  await page
    .getByRole("button", { name: "Shot 003", exact: true })
    .click({ button: "right" });
  const suggest = page.getByRole("menuitem", { name: /^Suggest person links/ });
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

// The old page's Unknown faces drawer, kept for the film: tracks no character holds move onto one.
test("unknown face tracks move onto a character from the Cast bin or the Review menu", async ({
  page,
}) => {
  const mock = mockOf(page);
  const panel = page.locator(".film-project");
  await panel.getByRole("button", { name: /^Cast/ }).click();
  await panel.getByRole("button", { name: /^Unknown faces/ }).click();
  const drawer = page.getByRole("dialog", { name: "Unknown faces" });
  await expect(
    drawer.getByRole("checkbox", {
      name: "Shot 003, unknown identity",
    }),
  ).toBeVisible();
  await drawer.getByRole("checkbox").first().check();
  await drawer.getByLabel("Move to").selectOption("character-1");
  await drawer.getByRole("button", { name: "Move 1 track" }).click();
  await expect
    .poll(
      () =>
        mock.calls.find((c) => c.path.endsWith("/tracks/move"))?.body.track_ids,
    )
    .toEqual(["unknown-track"]);
  expect(mock.calls.find((c) => c.path.endsWith("/tracks/move"))?.body.to).toBe(
    "character-1",
  );
  await page.keyboard.press("Escape");
  await page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Review", exact: true })
    .click();
  await page.getByRole("menuitem", { name: /^Unknown faces/ }).click();
  await expect(drawer).toBeVisible();
  // With no unknown tracks left the command says why.
  await page.keyboard.press("Escape");
  mock.value.unknown_faces = [];
  await expect(
    panel.getByRole("button", { name: /^Unknown faces/ }),
  ).toHaveAttribute("title", "Every face track belongs to a character.", {
    timeout: 8000,
  });
});
