import type { Page } from "@playwright/test";
import {
  expect,
  openFilm,
  recastCommands,
  reopen,
  test,
  type RecastWorld,
} from "./recast-world";

// U5a: the Recast page's head-pass tests, ported to the film (retained regression coverage rows 6 to 17
// and 30 to 32). The film's head pass is `HeadPassControl` in the Shot card (Properties); when Properties is closed,
// or a character or the Recast tool is selected, the same command asks through the "Head pass" sheet instead. The
// recast is the service's own (tests/browser_recast.py): why a head pass may not start is Python's reading.

test.use({ reducedMotion: "reduce" });

/** Mara, Elias and The courier, every one seen in Shot 001; Shot 001 and Shot 002 are rendered. */
const ENSEMBLE = {
  shots: 4,
  characters: ["Mara", "Elias", "The courier"],
  ensemble: 1,
  rendered: 2,
};

/** No version's asset name reaches the page: text, titles, labels, alt text or values. */
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

const program = (page: Page) =>
  page.getByRole("region", { name: "Program monitor" });
const card = (page: Page) => page.locator(".film-properties");
const headPass = (page: Page) =>
  card(page).getByRole("button", { name: "Head pass", exact: true });
// Radix names the menu after its trigger (aria-labelledby wins over the content's aria-label).
const picker = (page: Page) =>
  page.getByRole("menu", { name: "Head pass", exact: true });
const sheet = (page: Page) =>
  page.getByRole("dialog", { name: "Head pass", exact: true });
const sheetChoices = (page: Page) =>
  sheet(page).getByRole("group", { name: "Head pass for" }).getByRole("button");
const notice = (page: Page) => page.locator(".film-message.notice");
const alert = (page: Page) => page.locator(".film-message[role='alert']");
const layerStatus = (page: Page) =>
  card(page)
    .getByRole("status")
    .filter({ hasText: /^Head pass/ });
const headPassCalls = (calls: ReturnType<typeof recastCommands>) =>
  calls.filter((c) => c.path.endsWith("/head-pass"));
const reviewMenu = (page: Page) =>
  page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Review", exact: true });

/** Opens the world's film with Properties showing the named shot's card. */
async function start(page: Page, world: RecastWorld, shot = "Shot 001") {
  const calls = recastCommands(page);
  await page.goto("/");
  await openFilm(page, world);
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.getByRole("button", { name: shot, exact: true }).click();
  await expect(
    card(page).getByRole("heading", { name: shot, exact: true }),
  ).toBeVisible();
  return calls;
}

async function reload(page: Page, world: RecastWorld, shot = "Shot 001") {
  await reopen(page, world);
  await expect(
    card(page).getByRole("heading", { name: shot, exact: true }),
  ).toBeVisible();
}

/** Review › Head pass, chosen by keyboard: the command every film surface shares. */
async function headPassFromReviewMenu(page: Page) {
  await reviewMenu(page).click();
  await page.getByRole("menuitem", { name: /^Head pass/ }).press("Enter");
}

/** The body the page sends for a head pass on a version, at the recast's current revision. */
async function headPassBody(world: RecastWorld, shot: number, letter: string) {
  const read = await world.read();
  const version = read.shots[shot].versions.find((v) => v.letter === letter)!;
  return { expected_revision: read.revision, version: version.id };
}

test.describe("one performer, two takes", () => {
  test.use({
    scene: {
      shots: 6,
      characters: ["Character 2"],
      rendered: 6,
      takes: 2,
    },
  });

  test("head pass sends the displayed version and current revision once, then announces reserved work", async ({
    page,
    world,
  }) => {
    const calls = await start(page, world, "Shot 006");
    // The tile only shows B (preview first, the user at CP1); the head pass acts on the version Program shows.
    await card(page).getByRole("button", { name: "B 2", exact: true }).click();
    await expect(
      card(page).getByRole("button", { name: "B 2", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(calls.filter((c) => c.path.endsWith("/choice"))).toEqual([]);
    expect((await world.read()).shots[5].choice).toBeNull();
    await expect(program(page).locator(".monitor-heading")).toContainText(
      "Shot 006 · B",
    );
    const button = headPass(page);
    await expect(button).toBeEnabled();
    const body = await headPassBody(world, 5, "B");
    await expect(button).toHaveAttribute(
      "title",
      "Head pass on version B for Character 2",
    );
    // Two activations before React rerenders still reserve work only once.
    await button.evaluate((element: HTMLButtonElement) => {
      element.click();
      element.click();
    });
    await expect(notice(page)).toContainText(
      "Shot 006: head pass queued on version B · 1 window · 1 render",
    );
    expect(headPassCalls(calls)).toEqual([
      {
        method: "POST",
        path: `/${world.id}/shots/${world.shots[5]}/head-pass`,
        body: { ...body, character_id: world.characters[0] },
      },
    ]);
    const layers = (await world.read()).shots[5].layers ?? [];
    expect(layers.map((l) => [l.character_id, l.state])).toEqual([
      [world.characters[0], "rendering"],
    ]);
    expect(body.version).toBeTruthy();
    await expect(layerStatus(page)).toHaveText(
      "Head pass · Character 2 · 0/1 windows",
    );
    await expect(button).toBeDisabled();
    await expect(
      page.getByRole("button", {
        name: "Shot 006: Rendering (head pass)",
        exact: true,
      }),
    ).toBeVisible();
    await notice(page)
      .getByRole("button", { name: "Dismiss", exact: true })
      .click();
    await expect(notice(page)).toHaveCount(0);
    await expectBlind(page);
  });
});

test.describe("two mapped performers in one shot", () => {
  test.use({
    scene: { ...ENSEMBLE, targets: ["confirmed", "confirmed", null] },
  });

  test("head pass picker cancels without a request and chooses one of two mapped performers by keyboard", async ({
    page,
    world,
  }) => {
    const calls = await start(page, world);
    const button = headPass(page);
    await expect(button).toHaveAttribute(
      "title",
      "Head pass on version A: choose Mara or Elias",
    );
    await button.click();
    await expect(picker(page)).toBeVisible();
    expect(headPassCalls(calls)).toHaveLength(0);
    await page.keyboard.press("Escape");
    await expect(picker(page)).toHaveCount(0);
    await expect(button).toBeFocused();
    await button.press("Enter");
    await expect(picker(page).getByRole("menuitem")).toHaveCount(2);
    await page.keyboard.press("End");
    await expect(
      picker(page).getByRole("menuitem", {
        name: "Head pass on version A for Elias",
        exact: true,
      }),
    ).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(picker(page)).toHaveCount(0);
    await expect(notice(page)).toContainText(
      "Shot 001: head pass queued on version A · 1 window · 1 render",
    );
    expect(headPassCalls(calls)).toHaveLength(1);
    const layers = (await world.read()).shots[0].layers ?? [];
    expect(layers.map((l) => l.character_id)).toEqual([world.characters[1]]);
    await expect(sheet(page)).toHaveCount(0);
    await expectBlind(page);
  });

  test("with Properties closed, the Head pass sheet cancels without a request and chooses one of two performers by keyboard", async ({
    page,
    world,
  }) => {
    const calls = recastCommands(page);
    await page.goto("/");
    await openFilm(page, world);
    // Properties closed: a film whose chat has no movie opens on Properties, so another tab comes forward.
    await page.getByRole("tab", { name: "Jobs" }).click();
    await page.getByRole("button", { name: "Shot 001", exact: true }).click();
    await expect(card(page)).toHaveCount(0);
    await headPassFromReviewMenu(page);
    await expect(sheet(page)).toBeVisible();
    await expect(sheet(page)).toContainText(
      "Run a head pass on version A for one character.",
    );
    await expect(picker(page)).toHaveCount(0);
    expect(headPassCalls(calls)).toHaveLength(0);
    await page.keyboard.press("Escape");
    await expect(sheet(page)).toHaveCount(0);
    expect(headPassCalls(calls)).toHaveLength(0);
    await headPassFromReviewMenu(page);
    await expect(sheetChoices(page)).toHaveCount(2);
    await expect(sheetChoices(page).nth(0)).toContainText("Mara");
    await expect(sheetChoices(page).nth(1)).toContainText("Elias");
    await sheetChoices(page).nth(1).press("Enter");
    await expect(sheet(page)).toHaveCount(0);
    await expect(notice(page)).toContainText(
      "Shot 001: head pass queued on version A · 1 window · 1 render",
    );
    expect(headPassCalls(calls)).toHaveLength(1);
    const layers = (await world.read()).shots[0].layers ?? [];
    expect(layers.map((l) => l.character_id)).toEqual([world.characters[1]]);
    await expectBlind(page);
  });

  test("closing the Head pass sheet returns focus to the Review menu that opened it", async ({
    page,
    world,
  }) => {
    const calls = recastCommands(page);
    await page.goto("/");
    await openFilm(page, world);
    // Properties closed: a film whose chat has no movie opens on Properties, so another tab comes forward.
    await page.getByRole("tab", { name: "Jobs" }).click();
    await page.getByRole("button", { name: "Shot 001", exact: true }).click();
    await expect(card(page)).toHaveCount(0);
    await reviewMenu(page).focus();
    await page.keyboard.press("Enter");
    await page.getByRole("menuitem", { name: /^Head pass/ }).press("Enter");
    await expect(sheet(page)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(sheet(page)).toHaveCount(0);
    await expect(reviewMenu(page)).toBeFocused();
    expect(headPassCalls(calls)).toHaveLength(0);
  });
});

// Python plans at most two mapped characters in a shot (recast_planning), and with one of two eligible the command
// starts it directly; the picker and the sheet list only the characters the shown version lets a head pass re-render.

test.describe("one mapped performer", () => {
  test.use({
    scene: {
      shots: 4,
      characters: ["Mara", "Elias"],
      targets: ["confirmed", "confirmed"],
      rendered: 1,
    },
  });

  test("head pass readiness explains Original, missing versions and unsettled shots", async ({
    page,
    world,
  }) => {
    const calls = await start(page, world);
    const button = headPass(page);
    await expect(button).toBeEnabled();
    await page.keyboard.press("\\");
    await expect(program(page).locator(".monitor-heading")).toContainText(
      "Shot 001 · Original",
    );
    await expect(button).toBeDisabled();
    await expect(button).toHaveAttribute(
      "title",
      "Show a settled version instead of the original.",
    );
    await page.keyboard.press("\\");
    await expect(button).toBeEnabled();
    // Shot 002 was never rendered.
    await page.getByRole("button", { name: "Shot 002", exact: true }).click();
    await expect(button).toHaveAttribute(
      "title",
      "This shot has no settled version.",
    );
    await expect(button).toBeDisabled();
    // A retake leaves Shot 001 rendering until its windows settle.
    await page.getByRole("button", { name: "Shot 001", exact: true }).click();
    await page
      .getByRole("button", { name: /^Shot 001: / })
      .click({ button: "right" });
    await page.getByRole("menuitem", { name: /^Retake/ }).click();
    await page
      .getByRole("dialog", { name: "Retake" })
      .getByRole("button", { name: "Retake", exact: true })
      .click();
    await expect(button).toBeDisabled();
    await expect(button).toHaveAttribute(
      "title",
      "Wait for this shot to settle before starting a head pass.",
    );
    await world.settle("character_replacement");
    await world.settle("shot_assembly");
    await expect(button).toBeEnabled({ timeout: 8000 });
    // A rendered shot keeps its performers in its plan, so "Choose a mapped character in this shot's plan." is the
    // command's refusal for a character that is gone (pytest: test_recast_feed's head pass reason test).
    expect(headPassCalls(calls)).toHaveLength(0);
    await expectBlind(page);
  });

  test("head pass progress survives reload, and a settled pass adds an anonymous version", async ({
    page,
    world,
  }) => {
    await start(page, world);
    await headPass(page).click();
    await expect(layerStatus(page)).toHaveText(
      "Head pass · Mara · 0/1 windows",
    );
    await reload(page, world);
    await expect(layerStatus(page)).toHaveText(
      "Head pass · Mara · 0/1 windows",
    );
    // The film also shows the pass on Mara's head-pass lane under the Recast tool.
    await page
      .getByRole("button", { name: "Recast lanes", exact: true })
      .click();
    const pass = page.locator(
      `[data-row="passes-${world.characters[0]}"] .track-item`,
    );
    await expect(pass).toHaveAttribute(
      "aria-label",
      "Shot 001, head pass: rendering · 0 of 1 windows",
    );
    await world.settle("character_replacement", "succeeded", { head: true });
    await world.settle("shot_assembly");
    await expect(layerStatus(page)).toHaveCount(0, { timeout: 8000 });
    await expect(
      card(page)
        .locator(".version-tiles")
        .getByRole("button", { name: "B 2", exact: true }),
    ).toBeVisible();
    await expect(card(page).locator(".version-tiles")).not.toContainText(
      /head|parent/i,
    );
    // The settled pass names no version, so the derived version stays anonymous.
    await expect(pass).toHaveAttribute(
      "aria-label",
      "Shot 001, head pass: settled · 1 of 1 windows",
    );
    await expectBlind(page);
  });

  test("a failed head pass says so", async ({ page, world }) => {
    await start(page, world);
    await headPass(page).click();
    await expect(layerStatus(page)).toHaveText(
      "Head pass · Mara · 0/1 windows",
    );
    // Its window fails; the layer ends when its assembly settles with nothing to show.
    await world.settle("character_replacement", "failed", { head: true });
    await world.settle("shot_assembly");
    await expect(layerStatus(page)).toHaveText("Head pass failed · Mara", {
      timeout: 8000,
    });
    await expectBlind(page);
  });

  // The service's refusals, injected: the page shows each in its alert and changes nothing.
  for (const refusal of [
    {
      status: 409,
      message: "A head pass already exists for this version and character.",
    },
    { status: 409, message: "Increase the render budget" },
    { status: 422, message: "Choose a mapped character in this shot's plan." },
    { status: 422, message: "Choose an existing settled shot version." },
    {
      status: 422,
      message: "The character was not replaced in this parent version.",
    },
    { status: 422, message: "This target does not have a human face." },
    { status: 409, message: "The target picture is being checked." },
  ]) {
    test(`head pass surfaces ${refusal.status}: ${refusal.message}`, async ({
      page,
      world,
    }) => {
      const before = await world.read();
      const requests: unknown[] = [];
      await page.route("**/api/recasts/*/shots/*/head-pass", async (route) => {
        requests.push(route.request().postDataJSON());
        await route.fulfill({
          status: refusal.status,
          json: { detail: refusal.message },
        });
      });
      await start(page, world);
      await headPass(page).click();
      await expect(
        alert(page).filter({ hasText: refusal.message }),
      ).toBeVisible();
      expect(requests).toHaveLength(1);
      expect((await world.read()).revision).toBe(before.revision);
      await expect(notice(page)).toHaveCount(0);
      await expect(card(page).locator(".shot-card-choice")).toContainText(
        "Unchosen",
      );
      await expect(headPass(page)).toBeEnabled();
      await expectBlind(page);
    });
  }

  test("head pass stale revision follows retake handling without retrying", async ({
    page,
    world,
  }) => {
    const calls = await start(page, world);
    const button = headPass(page);
    await expect(button).toBeEnabled();
    // Just after a poll, so the film still holds the old revision when it asks (the next poll is 2 s away); another
    // client renames Mara meanwhile.
    await page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === `/api/recasts/${world.id}` &&
        response.request().method() === "GET",
    );
    const read = await world.read();
    const renamed = await page.request.patch(
      `/api/recasts/${world.id}/characters/${world.characters[0]}`,
      {
        headers: world.headers,
        data: { expected_revision: read.revision, label: "Mara Vale" },
      },
    );
    expect(renamed.ok()).toBeTruthy();
    await button.click();
    await expect(
      alert(page).filter({
        hasText: "The recast changed; reload before editing.",
      }),
    ).toBeVisible();
    expect(headPassCalls(calls)).toHaveLength(1);
    expect((await world.read()).shots[0].layers ?? []).toEqual([]);
    await expect(notice(page)).toHaveCount(0);
    await expectBlind(page);
  });

  test("a version keeps the pictures it was rendered with: a target without a face chosen later leaves it ready", async ({
    page,
    world,
  }) => {
    await start(page, world);
    const button = headPass(page);
    await expect(button).toBeEnabled();
    await world.target(0, "faceless");
    await reload(page, world);
    await expect(button).toBeEnabled();
    await expect(button).toHaveAttribute(
      "title",
      "Head pass on version A for Mara",
    );
    await expectBlind(page);
  });
});

for (const [kind, reason] of [
  ["faceless", "Head pass needs a target with a human face"],
  ["unreadable", "The target picture could not be checked."],
] as const)
  test.describe(`a version rendered with a ${kind} picture`, () => {
    test.use({
      scene: {
        shots: 4,
        characters: ["Mara", "Elias"],
        targets: [kind, "confirmed"],
        rendered: 1,
      },
    });

    test("head pass says why in the command's words, whatever the target is now", async ({
      page,
      world,
    }) => {
      const calls = await start(page, world);
      const button = headPass(page);
      await expect(button).toBeDisabled();
      await expect(button).toHaveAttribute("title", reason);
      await world.target(0, "confirmed");
      await reload(page, world);
      await expect(button).toBeDisabled();
      await expect(button).toHaveAttribute("title", reason);
      expect(headPassCalls(calls)).toHaveLength(0);
      await expectBlind(page);
    });
  });

test.describe("a performer without a face", () => {
  test.use({
    scene: { ...ENSEMBLE, targets: ["faceless", "confirmed", null] },
  });

  test("joint head pass directly starts the eligible human character", async ({
    page,
    world,
  }) => {
    const calls = await start(page, world);
    const button = headPass(page);
    await expect(button).toBeEnabled();
    await expect(button).toHaveAttribute(
      "title",
      "Head pass on version A for Elias",
    );
    await button.click();
    await expect.poll(() => headPassCalls(calls)).toHaveLength(1);
    await expect
      .poll(async () =>
        ((await world.read()).shots[0].layers ?? []).map((l) => l.character_id),
      )
      .toEqual([world.characters[1]]);
    await expect(picker(page)).toHaveCount(0);
    await expect(sheet(page)).toHaveCount(0);
    // The same command from the Review menu, with a character selected, starts directly too: no sheet asks.
    await world.settle("character_replacement", "succeeded", { head: true });
    await world.settle("shot_assembly");
    await expect(button).toBeEnabled({ timeout: 8000 });
    await page
      .locator(`[data-row="cast-${world.characters[1]}"] .cast-header`)
      .click();
    await expect(
      card(page).getByRole("heading", { name: "Elias", exact: true }),
    ).toBeVisible();
    await headPassFromReviewMenu(page);
    await expect.poll(() => headPassCalls(calls)).toHaveLength(2);
    await expect(sheet(page)).toHaveCount(0);
    await expect(picker(page)).toHaveCount(0);
    await expectBlind(page);
  });
});
