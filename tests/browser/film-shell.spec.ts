import { test, expect, type Page } from "@playwright/test";
import { filmAsset, mockRecast } from "./recast-fixtures";
import { openMovie, seedDirect } from "./direct-fixture";

// ADR 0017 U3a: a film opens as a document in the track workspace, and its keys and menus never reach the movie
// that stays loaded behind it.

function watch(page: Page) {
  const direction: string[] = [];
  const problems: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/direction/") && request.method() !== "GET")
      direction.push(`${request.method()} ${request.url()}`);
  });
  page.on("console", (message) => {
    if (
      message.type() === "error" &&
      /Unclassified commands|Shared shortcuts/.test(message.text())
    )
      problems.push(message.text());
  });
  return { direction, problems };
}

async function openFilm(page: Page) {
  await page.getByRole("button", { name: "File", exact: true }).click();
  await page.getByRole("menuitem", { name: /Open film/ }).click();
  const dialog = page.getByRole("dialog", { name: "Open film" });
  await dialog
    .getByRole("button", { name: new RegExp(filmAsset.name) })
    .first()
    .click();
  await expect(
    page.getByRole("button", { name: "Shot 001", exact: true }),
  ).toBeVisible();
}

async function returnToMovie(page: Page, title: string) {
  await page.getByRole("button", { name: "File", exact: true }).click();
  await page.getByRole("menuitem", { name: /Open movie/ }).click();
  await page
    .getByRole("dialog", { name: "Open movie", exact: true })
    .getByRole("button", { name: title, exact: true })
    .click();
  await expect(
    page.locator(".cut-item").filter({ hasText: "f36" }),
  ).toBeVisible();
}

test.describe("film document shell", () => {
  let data: Awaited<ReturnType<typeof seedDirect>>;
  test.beforeAll(async ({ request }) => {
    data = await seedDirect(request);
  });

  test("a film opens from File › Open film and reload restores it", async ({
    page,
  }) => {
    const mock = await mockRecast(page);
    mock.existing();
    const seen = watch(page);
    await openMovie(page, data.movie.title);
    await openFilm(page);
    await expect(page.locator(".workspace-breadcrumb")).toContainText(
      filmAsset.name,
    );
    await expect(page.locator(".timeline-heading")).toContainText(
      filmAsset.name,
    );
    await page.reload();
    await expect(
      page.getByRole("button", { name: "Shot 001", exact: true }),
    ).toBeVisible();
    await expect(page.locator(".workspace-breadcrumb")).toContainText(
      filmAsset.name,
    );
    expect(seen.problems).toEqual([]);
  });

  test("a film's keys and menus never reach the movie behind it, and the movie keeps its place", async ({
    page,
  }) => {
    const mock = await mockRecast(page);
    mock.existing();
    const seen = watch(page);
    await openMovie(page, data.movie.title);
    await page.getByRole("button", { name: "Segment 02", exact: true }).click();
    await page.getByRole("button", { name: "Zoom in" }).click();
    const playhead = page.getByRole("slider", { name: "Playhead" });
    await playhead.focus();
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    const before = {
      playhead: await playhead.getAttribute("aria-valuenow"),
      zoom: await page.getByLabel("Timeline zoom").inputValue(),
    };

    await openFilm(page);
    await page.getByRole("button", { name: "Shot 002", exact: true }).click();
    for (const key of [
      "Control+k",
      "Control+Shift+K",
      "Control+Enter",
      "Delete",
      "Control+z",
      "Control+Shift+Z",
      "Enter",
      "+",
      "+",
    ])
      await page.keyboard.press(key);
    await page.waitForTimeout(300);
    expect(seen.direction).toEqual([]);

    await page.getByRole("button", { name: "Segment", exact: true }).click();
    const split = page.getByRole("menuitem", {
      name: /Split segment at playhead/,
    });
    await expect(split).toHaveAttribute("aria-disabled", "true");
    await expect(split).toContainText("Works on movies; a film is open");
    await page.keyboard.press("Escape");
    const razor = page.getByRole("button", { name: "Razor tool" });
    await expect(razor).toBeDisabled();
    await expect(razor).toHaveAttribute("title", "Films are cut by analysis");

    await returnToMovie(page, data.movie.title);
    await expect(
      page.getByRole("button", { name: "Segment 02", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(playhead).toHaveAttribute(
      "aria-valuenow",
      before.playhead ?? "",
    );
    await expect(page.getByLabel("Timeline zoom")).toHaveValue(before.zoom);
    expect(seen.direction).toEqual([]);
    expect(seen.problems).toEqual([]);
  });

  test("a movie's keys never reach a recast", async ({ page }) => {
    const mock = await mockRecast(page);
    mock.existing();
    const seen = watch(page);
    await openMovie(page, data.movie.title);
    await page.getByRole("button", { name: "Segment 01", exact: true }).click();
    for (const key of ["1", "0", "r", "q", "w", "\\", "i", "o"])
      await page.keyboard.press(key);
    await page.waitForTimeout(300);
    expect(mock.calls).toEqual([]);
    expect(seen.problems).toEqual([]);
  });
});

// U5b: the Recast page retired. A saved "recast" workspace opens Direct with the film it was on; nothing names the
// page any more.
const workspaces = (page: Page) =>
  page.getByRole("navigation", { name: "Workspaces" });

test("a saved Recast workspace opens Direct with the film and shot it was on", async ({
  page,
}) => {
  const watched = watch(page);
  const mock = await mockRecast(page);
  mock.existing();
  await page.addInitScript(() => {
    if (sessionStorage.getItem("migrated")) return;
    sessionStorage.setItem("migrated", "1");
    localStorage.setItem("gaiavfx:workspace", "recast");
    localStorage.setItem(
      "gaiavfx:recast-place",
      JSON.stringify({ id: "recast-1", shotId: "shot-6" }),
    );
    localStorage.removeItem("gaiavfx:document");
  });
  await page.goto("/");
  await expect(
    workspaces(page).getByRole("button", { name: "Direct", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  await expect(page.locator(".timeline-heading")).toContainText(filmAsset.name);
  await expect(page.locator(".workspace-breadcrumb")).toContainText("Shot 007");
  // The migration is done once: the workspace is saved as Direct, and a reload keeps the film.
  expect(
    await page.evaluate(() => localStorage.getItem("gaiavfx:workspace")),
  ).toBe("direct");
  await page.reload();
  await expect(page.locator(".timeline-heading")).toContainText(filmAsset.name);
  expect(watched.problems).toEqual([]);
});

test("without storage, and with an unknown saved workspace, the app opens Direct", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    localStorage.setItem("gaiavfx:workspace", "somewhere-else");
  });
  await page.goto("/");
  await expect(
    workspaces(page).getByRole("button", { name: "Direct", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  const fresh = await page.context().newPage();
  fresh.on("pageerror", (error) => errors.push(error.message));
  await fresh.addInitScript(() => {
    const read = Storage.prototype.getItem;
    Storage.prototype.getItem = function (key) {
      if (key.startsWith("gaiavfx:")) throw new Error("Unavailable");
      return read.call(this, key);
    };
  });
  await fresh.goto("/");
  await expect(
    workspaces(fresh).getByRole("button", { name: "Direct", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  expect(errors).toEqual([]);
  await fresh.close();
});

test("no workspace tab or menu item names the retired Recast page", async ({
  page,
}) => {
  await page.goto("/");
  expect(
    await workspaces(page)
      .getByRole("button")
      .evaluateAll((els) => els.map((el) => el.textContent?.trim())),
  ).toEqual(["Prepare", "Direct", "Agent", "Review", "Export"]);
  await page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Window", exact: true })
    .click();
  await expect(
    page.getByRole("menuitem", { name: /^Direct workspace/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("menuitem", { name: /Recast workspace/ }),
  ).toHaveCount(0);
});

// Pilot T8-F7: the movie's last edit notice ("Change undone") does not follow into a film.
test("the movie's edit notice stays with the movie when a film opens", async ({
  page,
  request,
}) => {
  const data = await seedDirect(request);
  const mock = await mockRecast(page);
  mock.existing();
  await openMovie(page, data.movie.title);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByRole("menuitem", { name: /^Undo/ }).click();
  await expect(page.locator(".studio-status")).toContainText("Change undone");
  await openFilm(page);
  await expect(page.locator(".studio-status")).not.toContainText(
    "Change undone",
  );
  await expect(page.locator(".studio-status")).toContainText("Local library");
});
