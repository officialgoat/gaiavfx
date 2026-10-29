import { test, expect, type Page } from "@playwright/test";
import { headPassTargetReason } from "../../frontend/src/features/recast/commands";
import { filmAsset, mockRecast } from "./recast-fixtures";

// U5a: the Recast page's head-pass tests, ported to the film (retained regression coverage rows 6 to 17
// and 30 to 32). The film's head pass is `HeadPassControl` in the Shot card (Properties); when Properties is closed,
// or a character or the Recast tool is selected, the same command asks through the "Head pass" sheet instead.

test.use({ reducedMotion: "reduce" });

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
const headPassCalls = (mock: Mock) =>
  mock.calls.filter((c) => c.path.endsWith("/head-pass"));
const reviewMenu = (page: Page) =>
  page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Review", exact: true });

/** Opens the fixture film with Properties showing the named shot's card. */
async function start(page: Page, mock: Mock, shot = "Shot 001") {
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.getByRole("button", { name: shot, exact: true }).click();
  await expect(
    card(page).getByRole("heading", { name: shot, exact: true }),
  ).toBeVisible();
}

async function reload(page: Page, shot = "Shot 001") {
  await page.reload();
  await expect(page.locator(".timeline-heading")).toContainText(filmAsset.name);
  await expect(
    card(page).getByRole("heading", { name: shot, exact: true }),
  ).toBeVisible();
}

/** Review › Head pass, chosen by keyboard: the command every film surface shares. */
async function headPassFromReviewMenu(page: Page) {
  await reviewMenu(page).click();
  await page.getByRole("menuitem", { name: /^Head pass/ }).press("Enter");
}

test("head pass sends the displayed version and current revision once, then announces reserved work", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  const shot = mock.value.shots[5];
  shot.status = "needs_review";
  shot.plan!.renders_per_window = 4;
  shot.plan!.windows = Array.from({ length: 7 }, (_, index) => ({
    ...shot.plan!.windows[0],
    index,
  }));
  // An older layer must not supply the new request's notice counts.
  shot.layers = [
    {
      character_id: "character-0",
      state: "settled",
      windows_settled: 1,
      windows_total: 1,
      renders_reserved: 1,
    },
  ];
  mock.value.characters[0].label = "Character 2";
  await start(page, mock, "Shot 006");
  // The tile only shows B (preview first, the user at CP1); the head pass acts on the version Program shows.
  await card(page).getByRole("button", { name: "B 2", exact: true }).click();
  await expect(
    card(page).getByRole("button", { name: "B 2", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(mock.calls.filter((c) => c.path.endsWith("/choice"))).toEqual([]);
  expect(shot.choice).toBeNull();
  await expect(program(page).locator(".monitor-heading")).toContainText(
    "Shot 006 · B",
  );
  const button = headPass(page);
  await expect(button).toBeEnabled();
  const revision = mock.value.revision;
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
    "Shot 006: head pass queued on version B · 7 windows · 7 renders",
  );
  expect(headPassCalls(mock)).toEqual([
    {
      path: "/recast-1/shots/shot-5/head-pass",
      method: "POST",
      body: {
        expected_revision: revision,
        version: "version-5-B",
        character_id: "character-0",
      },
    },
  ]);
  await expect(layerStatus(page)).toHaveText(
    "Head pass · Character 2 · 0/7 windows",
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

test("head pass picker cancels without a request and chooses one of two mapped performers by keyboard", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  mock.value.shots[0].plan!.performers = ["character-0", "character-1"];
  mock.value.characters[1].target!.state = "confirmed";
  await start(page, mock);
  const button = headPass(page);
  await expect(button).toHaveAttribute(
    "title",
    "Head pass on version A: choose Mara or Elias",
  );
  await button.click();
  await expect(picker(page)).toBeVisible();
  expect(headPassCalls(mock)).toHaveLength(0);
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
  expect(headPassCalls(mock)).toEqual([
    {
      path: "/recast-1/shots/shot-0/head-pass",
      method: "POST",
      body: {
        expected_revision: 1,
        version: "version-0-A",
        character_id: "character-1",
      },
    },
  ]);
  await expect(sheet(page)).toHaveCount(0);
  await expectBlind(page);
});

test("with Properties closed, the Head pass sheet cancels without a request and chooses one of two performers by keyboard", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  mock.value.shots[0].plan!.performers = ["character-0", "character-1"];
  mock.value.characters[1].target!.state = "confirmed";
  mock.existing();
  await page.goto("/");
  await openFilm(page);
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
  expect(headPassCalls(mock)).toHaveLength(0);
  await page.keyboard.press("Escape");
  await expect(sheet(page)).toHaveCount(0);
  expect(headPassCalls(mock)).toHaveLength(0);
  await headPassFromReviewMenu(page);
  await expect(sheetChoices(page)).toHaveCount(2);
  await expect(sheetChoices(page).nth(0)).toContainText("Mara");
  await expect(sheetChoices(page).nth(1)).toContainText("Elias");
  await sheetChoices(page).nth(1).press("Enter");
  await expect(sheet(page)).toHaveCount(0);
  await expect(notice(page)).toContainText(
    "Shot 001: head pass queued on version A · 1 window · 1 render",
  );
  expect(headPassCalls(mock)).toEqual([
    {
      path: "/recast-1/shots/shot-0/head-pass",
      method: "POST",
      body: {
        expected_revision: 1,
        version: "version-0-A",
        character_id: "character-1",
      },
    },
  ]);
  await expectBlind(page);
});

test("closing the Head pass sheet returns focus to the Review menu that opened it", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  mock.value.shots[0].plan!.performers = ["character-0", "character-1"];
  mock.value.characters[1].target!.state = "confirmed";
  mock.existing();
  await page.goto("/");
  await openFilm(page);
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
  expect(headPassCalls(mock)).toHaveLength(0);
});

test("head pass readiness explains Original, missing versions, unsettled shots and missing performers", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  await start(page, mock);
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
  const shot = mock.value.shots[0];
  const versions = shot.versions;
  shot.versions = [];
  await expect(button).toHaveAttribute(
    "title",
    "This shot has no settled version.",
  );
  await expect(button).toBeDisabled();
  shot.versions = versions;
  for (const status of ["rendering", "queued", "analysing"] as const) {
    shot.status = status;
    await reload(page);
    await expect(button).toBeDisabled();
    await expect(button).toHaveAttribute(
      "title",
      "Wait for this shot to settle before starting a head pass.",
    );
  }
  shot.status = "needs_review";
  shot.plan!.performers = [];
  await expect(button).toHaveAttribute(
    "title",
    "Choose a mapped character in this shot's plan.",
  );
  await expect(button).toBeDisabled();
  expect(headPassCalls(mock)).toHaveLength(0);
  await expectBlind(page);
});

test("head pass progress survives reload and reports failure, cancellation and blind settlement", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  const shot = mock.value.shots[0];
  shot.layers = [
    {
      character_id: "character-0",
      state: "rendering",
      windows_settled: 3,
      windows_total: 7,
      renders_reserved: 7,
    },
  ];
  await start(page, mock);
  await expect(layerStatus(page)).toHaveText("Head pass · Mara · 3/7 windows");
  await reload(page);
  await expect(layerStatus(page)).toHaveText("Head pass · Mara · 3/7 windows");
  shot.layers[0].windows_settled = 4;
  await expect(layerStatus(page)).toHaveText("Head pass · Mara · 4/7 windows");
  // The film also shows the pass on Mara's head-pass lane under the Recast tool.
  await page.getByRole("button", { name: "Recast lanes", exact: true }).click();
  const pass = page.locator('[data-row="passes-character-0"] .track-item');
  await expect(pass).toHaveAttribute(
    "aria-label",
    "Shot 001, head pass: rendering · 4 of 7 windows",
  );
  shot.layers[0].state = "failed";
  await expect(layerStatus(page)).toHaveText("Head pass failed · Mara");
  shot.layers[0].state = "cancelled";
  await expect(layerStatus(page)).toHaveText("Head pass cancelled");
  shot.layers[0].state = "settled";
  shot.versions.push({
    ...shot.versions[0],
    id: "derived-version",
    letter: "C",
  });
  await expect(layerStatus(page)).toHaveCount(0);
  await expect(
    card(page)
      .locator(".version-tiles")
      .getByRole("button", { name: "C 3", exact: true }),
  ).toBeVisible();
  await expect(card(page).locator(".version-tiles")).not.toContainText(
    /head|parent/i,
  );
  // The settled pass names no version, so the derived version stays anonymous.
  await expect(pass).toHaveAttribute(
    "aria-label",
    "Shot 001, head pass: settled · 4 of 7 windows",
  );
  await expect(headPass(page)).toBeEnabled();
  await expectBlind(page);
});

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
  }) => {
    const mock = await mockRecast(page, 8);
    const before = structuredClone(mock.value);
    const requests: unknown[] = [];
    await page.route("**/api/recasts/*/shots/*/head-pass", async (route) => {
      requests.push(route.request().postDataJSON());
      await route.fulfill({
        status: refusal.status,
        json: { detail: refusal.message },
      });
    });
    await start(page, mock);
    await headPass(page).click();
    await expect(
      alert(page).filter({ hasText: refusal.message }),
    ).toBeVisible();
    expect(requests).toHaveLength(1);
    expect(mock.value).toEqual(before);
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
}) => {
  const mock = await mockRecast(page, 8);
  await start(page, mock);
  const button = headPass(page);
  await expect(button).toBeEnabled();
  // Just after a poll, so the film still holds the old revision when it asks (the next poll is 2 s away).
  await page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/recasts/recast-1") &&
      response.request().method() === "GET",
  );
  mock.value.revision++;
  await button.click();
  await expect(
    alert(page).filter({
      hasText: "The recast changed. Refresh and try again.",
    }),
  ).toBeVisible();
  expect(headPassCalls(mock)).toHaveLength(1);
  expect(mock.value.shots[0].layers).toBeUndefined();
  await expect(notice(page)).toHaveCount(0);
  await expectBlind(page);
});

test("head pass explains target face refusals and becomes ready for a human face", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  const shot = mock.value.shots[0];
  const target = mock.value.characters.find(
    (c) => c.id === shot.plan!.performers[0],
  )!.target!;
  const reasons = [
    ["no_face", "Head pass needs a target with a human face"],
    ["checking", "Checking the target picture's face, which needs ComfyUI"],
    ["failed", "The target picture could not be checked."],
  ] as const;
  target.face = "no_face";
  await start(page, mock);
  const button = headPass(page);
  for (const [state, reason] of reasons) {
    target.face = state;
    expect(headPassTargetReason(shot, mock.value.characters)).toBe(reason);
    await expect(button).toBeDisabled();
    await expect(button).toHaveAttribute("title", reason);
  }
  expect(headPassCalls(mock)).toHaveLength(0);
  target.face = "face";
  expect(headPassTargetReason(shot, mock.value.characters)).toBeUndefined();
  await expect(button).toBeEnabled();
  await expectBlind(page);
});

test("joint head pass directly starts the eligible human character", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  const shot = mock.value.shots[0];
  shot.plan!.performers = ["character-0", "character-1"];
  mock.value.characters[0].target!.face = "no_face";
  mock.value.characters[1].target!.state = "confirmed";
  mock.value.characters[1].target!.face = "face";
  expect(headPassTargetReason(shot, mock.value.characters)).toBeUndefined();
  await start(page, mock);
  const button = headPass(page);
  await expect(button).toBeEnabled();
  await expect(button).toHaveAttribute(
    "title",
    "Head pass on version A for Elias",
  );
  await button.click();
  await expect.poll(() => headPassCalls(mock)).toHaveLength(1);
  expect(headPassCalls(mock)[0].body.character_id).toBe("character-1");
  await expect(picker(page)).toHaveCount(0);
  await expect(sheet(page)).toHaveCount(0);
  // The same command from the Review menu, with a character selected, starts directly too: no sheet asks.
  shot.status = "needs_review";
  delete shot.layers;
  await expect(button).toBeEnabled({ timeout: 8000 });
  await page.locator('[data-row="cast-character-1"] .cast-header').click();
  await expect(
    card(page).getByRole("heading", { name: "Elias", exact: true }),
  ).toBeVisible();
  await headPassFromReviewMenu(page);
  await expect.poll(() => headPassCalls(mock)).toHaveLength(2);
  expect(headPassCalls(mock)[1].body.character_id).toBe("character-1");
  await expect(sheet(page)).toHaveCount(0);
  await expect(picker(page)).toHaveCount(0);
  await expectBlind(page);
});

test("head pass picker disables ineligible characters with their reason", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  const shot = mock.value.shots[0];
  shot.plan!.performers = ["character-0", "character-1", "character-2"];
  mock.value.characters[1].target!.state = "confirmed";
  mock.value.characters[2].target = {
    ...mock.value.characters[0].target!,
    face: "no_face",
  };
  await start(page, mock);
  await headPass(page).click();
  const items = picker(page).getByRole("menuitem");
  await expect(items).toHaveCount(3);
  await expect(items.nth(2)).toBeDisabled();
  await expect(items.nth(2)).toHaveAttribute(
    "title",
    "Head pass needs a target with a human face",
  );
  await expect(items.nth(0)).toBeEnabled();
  await expect(items.nth(1)).toBeEnabled();
  await page.keyboard.press("Escape");
  await expect(picker(page)).toHaveCount(0);
  await expectBlind(page);
});

test("the Head pass sheet disables ineligible characters with their reason when the Recast tool is selected", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  const shot = mock.value.shots[0];
  shot.plan!.performers = ["character-0", "character-1", "character-2"];
  mock.value.characters[1].target!.state = "confirmed";
  mock.value.characters[2].target = {
    ...mock.value.characters[0].target!,
    face: "no_face",
  };
  await start(page, mock);
  await page.locator(".timeline-pinned .tool-header").click();
  await expect(
    card(page).getByRole("heading", { name: /^Recast · / }),
  ).toBeVisible();
  await headPassFromReviewMenu(page);
  await expect(picker(page)).toHaveCount(0);
  const items = sheetChoices(page);
  await expect(items).toHaveCount(3);
  await expect(items.nth(2)).toContainText("The courier");
  await expect(items.nth(2)).toBeDisabled();
  await expect(items.nth(2)).toHaveAttribute(
    "title",
    "Head pass needs a target with a human face",
  );
  await expect(items.nth(0)).toBeEnabled();
  await expect(items.nth(1)).toBeEnabled();
  expect(headPassCalls(mock)).toHaveLength(0);
  await expectBlind(page);
});
