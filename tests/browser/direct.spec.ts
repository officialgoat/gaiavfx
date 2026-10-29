import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import fs from "node:fs/promises";
import { openMovie, seedDirect } from "./direct-fixture";
import { PROCESS_MEDIA } from "../../frontend/src/features/generation/process-media";

test.describe("Direct on the real Direction and Library APIs", () => {
  let data: Awaited<ReturnType<typeof seedDirect>>;
  test.beforeAll(async ({ request }) => {
    data = await seedDirect(request);
  });

  test("central tracks, cut plans, Project and Source media", async ({
    page,
  }) => {
    await openMovie(page, data.movie.title);
    await expect(
      page.getByRole("button", { name: "Segment 01", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Segment 02", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: /identity picture/ }),
    ).toHaveCount(2);
    await expect(
      page.getByRole("button", { name: /Dialogue: Meet me/ }),
    ).toHaveCount(2);
    await page.getByRole("button", { name: /Cut f36/ }).click();
    await page.getByRole("tab", { name: "Properties", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Cut at f36" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Recommended technique" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Next actions" }),
    ).toBeVisible();
    await page.getByLabel("Search project").fill("Arin reference.jpg");
    await page
      .getByRole("button", { name: "Arin reference.jpg image" })
      .click();
    await expect(
      page
        .getByRole("region", { name: "Source monitor" })
        .getByRole("img", { name: "Arin reference.jpg" }),
    ).toBeVisible();
    await page.getByLabel("Search project").fill("not-in-the-library");
    await expect(page.locator(".project-assets .project-asset")).toHaveCount(0);
  });

  // Pilot T8-F3: the Library's counts wait for the Library instead of reading 0 while it loads.
  test("the Library reads Loading until its media arrive, never a false 0", async ({
    page,
  }) => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route("**/api/v1/assets?*", async (route) => {
      await held;
      await route.fallback();
    });
    await openMovie(page, data.movie.title);
    await page.getByRole("button", { name: "Library", exact: true }).click();
    await expect(page.getByText("Loading assets…")).toBeVisible();
    await expect(page.getByText(/^0 assets$/)).toHaveCount(0);
    release();
    await expect(page.getByText(/^\d+ assets$/)).toBeVisible();
  });

  // Pilot T1-F2: a character replacement's process files (intermediates, assembled previews, the composites that are
  // a replaced shot's versions) stay out of the movie's reference bin; the Library filters them by provenance.
  test("the reference bin leaves out replacement process files", async ({
    page,
  }) => {
    const queries: URLSearchParams[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname === "/api/v1/assets") queries.push(url.searchParams);
    });
    await openMovie(page, data.movie.title);
    await page.getByLabel("Search project").fill("Arin reference.jpg");
    await expect(
      page.getByRole("button", { name: "Arin reference.jpg image" }),
    ).toBeVisible();
    const bin = queries.find((q) => q.get("limit") === "30");
    expect(bin?.get("exclude")).toBe(PROCESS_MEDIA);
    expect(PROCESS_MEDIA.split(",")).toEqual(
      expect.arrayContaining([
        "replacement_intermediate",
        "shot_assembly",
        "generation_candidate:composite",
      ]),
    );
  });

  // Usability A2 (T2-F1, T6-F1, T8-F3): the Library hides the same process files until "Show process files" is on.
  test("the Library hides replacement process files until they are asked for", async ({
    page,
  }) => {
    const queries: URLSearchParams[] = [];
    const composite = {
      ...data.asset,
      id: "process-composite",
      name: "composite.mkv",
      kind: "video",
      provenance: [{ type: "generation_candidate", output_key: "composite" }],
    };
    // Only the Library's own listing carries a collection filter; the backend filter is tested in test_library_api.
    await page.route(
      (url) =>
        url.pathname === "/api/v1/assets" && url.searchParams.has("collection"),
      (route) => {
        const query = new URL(route.request().url()).searchParams;
        queries.push(query);
        const items = query.has("exclude")
          ? [data.asset]
          : [data.asset, composite];
        return route.fulfill({
          json: { items, total: items.length, offset: 0, limit: 60 },
        });
      },
    );
    await openMovie(page, data.movie.title);
    await page.getByRole("button", { name: "Library", exact: true }).click();
    const library = page.getByRole("region", { name: "Creative library" });
    const toggle = library.getByRole("switch", { name: "Show process files" });
    const process = library.getByRole("button", {
      name: "Inspect composite.mkv",
    });
    await expect(
      library.getByRole("button", { name: `Inspect ${data.asset.name}` }),
    ).toBeVisible();
    await expect(toggle).not.toBeChecked();
    await expect(process).toHaveCount(0);
    expect(queries.at(-1)?.get("exclude")).toBe(PROCESS_MEDIA);
    expect(PROCESS_MEDIA.split(",")).toEqual(
      expect.arrayContaining([
        "replacement_intermediate",
        "shot_assembly",
        "generation_candidate:composite",
      ]),
    );
    await toggle.check();
    await expect(process).toBeVisible();
    expect(queries.at(-1)?.has("exclude")).toBe(false);
    await toggle.uncheck();
    await expect(process).toHaveCount(0);
  });

  // Usability B18 (T8-F1): Program shows the take of the segment under the playhead, at a frame inside that segment,
  // not the selected segment's take.
  test("Program follows the playhead's segment, not the selected one", async ({
    page,
    request,
  }) => {
    const imported = await request.post("/api/v1/imports", {
      headers: data.headers,
      multipart: {
        file: {
          name: "Segment two take.mp4",
          mimeType: "video/mp4",
          buffer: await fs.readFile("backend/tests/fixtures/harness/take.mp4"),
        },
      },
    });
    expect(imported.ok(), await imported.text()).toBeTruthy();
    const take = (await imported.json()).asset;
    // Segment 02 has a selected take; Segment 01 has none (the real API answers for it).
    await page.route("**/api/production/selections?*", (route) =>
      new URL(route.request().url()).searchParams.get("segment_id") ===
      data.second.id
        ? route.fulfill({
            json: {
              current: {
                id: "selection-two",
                movie_id: data.movie.id,
                segment_id: data.second.id,
                candidate_id: "candidate-two",
                asset_id: take.id,
                authority: "user",
                basis: null,
                selected_at: "2026-10-22T12:00:00Z",
                superseded_at: null,
                revision: 1,
              },
              history: [],
              revision: 1,
            },
          })
        : route.fallback(),
    );
    await openMovie(page, data.movie.title);
    const program = page.getByRole("region", { name: "Program monitor" });
    const playhead = page.getByRole("slider", {
      name: "Playhead",
      exact: true,
    });
    await page.getByRole("button", { name: "Segment 01", exact: true }).click();
    await playhead.focus();
    await page.keyboard.press("End");
    for (let i = 0; i < 4; i++) await page.keyboard.press("Shift+ArrowLeft");
    // 239 - 4 × 24: frame 23 of Segment 02, which starts at 120.
    await expect(playhead).toHaveAttribute("aria-valuenow", "143");
    await expect(program).toContainText("Segment two take.mp4");
    await expect(program).toContainText("Selected take");
    await expect(program.locator(".timecode")).toHaveText("00:00:00:23");
    // The other way round: Segment 02 selected, playhead in Segment 01, which has no take.
    await page.getByRole("button", { name: "Segment 02", exact: true }).click();
    await playhead.focus();
    await page.keyboard.press("Home");
    await expect(program).toContainText("No media");
    await expect(program).toContainText("Select a take in Review");
  });

  test("context menus run typed commands and undo/redo through receipts", async ({
    page,
    request,
  }) => {
    await openMovie(page, data.movie.title);
    const cut = page.getByRole("button", { name: /Cut f36/ });
    const savedStatus = page.locator(".studio-status").getByRole("status");
    await cut.click({ button: "right" });
    await page
      .getByRole("menuitem", { name: "Set relation", exact: true })
      .hover();
    await page
      .getByRole("menuitem", { name: "Side view", exact: true })
      .click();
    await expect(cut).toHaveAccessibleName(/Side view/);
    await expect(savedStatus).toContainText("Cut updated");
    await page.keyboard.press("Control+z");
    await expect(cut).toHaveAccessibleName(/Reverse/);
    await expect(savedStatus).toContainText("Change undone");
    await page.keyboard.press("Control+Shift+z");
    await expect(cut).toHaveAccessibleName(/Side view/);
    await expect(savedStatus).toContainText("Change redone");
    await page.keyboard.press("Control+z");
    await expect(cut).toHaveAccessibleName(/Reverse/);
    await expect(savedStatus).toContainText("Change undone");
    await cut.focus();
    await page.keyboard.press("Shift+F10");
    await expect(
      page.getByRole("menuitem", { name: "Set relation", exact: true }),
    ).toBeVisible();
    await page.keyboard.press("Home");
    await page.keyboard.press("ArrowDown");
    await expect(
      page.getByRole("menuitem", { name: "Set relation", exact: true }),
    ).toBeFocused();
    await page.keyboard.press("ArrowRight");
    await expect(
      page.getByRole("menuitem", { name: "Same side / closer", exact: true }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await page.keyboard.press("Escape");
    await expect(cut).toBeFocused();
    await page.keyboard.press("Alt+ArrowRight");
    await expect(page.getByRole("button", { name: /Cut f37/ })).toBeVisible();
    const pins = page.getByRole("button", { name: /Pin needed.*f49/ });
    await expect(pins).toBeVisible();
    await expect(savedStatus).toContainText("Cut updated");
    await page.keyboard.press("Control+z");
    await expect(cut).toBeVisible();
    await expect(savedStatus).toContainText("Change undone");
    const position = (await cut.boundingBox())!;
    await page.mouse.move(position.x + 10, position.y + 12);
    await page.mouse.down();
    await page.mouse.move(position.x + 35, position.y + 12, { steps: 5 });
    await page.mouse.up();
    await expect(cut).toHaveCount(0);
    const moved = await request.get(`/api/direction/cuts/${data.cut.id}`, {
      headers: data.headers,
    });
    const movedFrame = (await moved.json()).frame;
    await expect(
      page.getByRole("button", {
        name: new RegExp(`Pin needed.*f${movedFrame + 12}`),
      }),
    ).toBeVisible();
    await expect(savedStatus).toContainText("Cut moved");
    await page.keyboard.press("Control+z");
    await expect(cut).toBeVisible();
    const receipts = await request.get(
      `/api/direction/movies/${data.movie.id}/receipts`,
      { headers: data.headers },
    );
    const rows = await receipts.json();
    expect(rows.some((r: { undoes: string | null }) => r.undoes)).toBe(true);
  });

  test("create phase and cut, with explicit revision conflict recovery", async ({
    page,
  }) => {
    await openMovie(page, data.movie.title);
    await page
      .getByRole("button", { name: "Segment 01", exact: true })
      .click({ button: "right" });
    await page.getByRole("menuitem", { name: /Add phase at playhead/ }).click();
    const phase = page.getByRole("dialog", { name: "Add phase", exact: true });
    await phase.getByLabel("Delivered frame").fill("24");
    await phase
      .getByLabel("Direction", { exact: true })
      .fill("Arin slows at the railing.");
    await phase
      .getByRole("button", { name: "Create phase", exact: true })
      .click();
    await expect(
      page.getByRole("button", {
        name: "Phase: Arin slows at the railing.",
        exact: true,
      }),
    ).toBeVisible();
    await page.getByRole("region", { name: "Timeline panel" }).focus();
    await page.keyboard.press("Control+Shift+k");
    const cut = page.getByRole("dialog", { name: "Add cut", exact: true });
    await cut.getByLabel("Delivered frame").fill("48");
    await cut.getByLabel("From angle").selectOption(data.from.id);
    await cut.getByLabel("To angle").selectOption(data.to.id);
    await cut.getByLabel("Relation", { exact: true }).selectOption("reverse");
    await cut.getByRole("button", { name: "Create cut", exact: true }).click();
    const marker = page.getByRole("button", { name: /Cut f48/ });
    await expect(marker).toBeVisible();
    await marker.click();
    await page.getByRole("tab", { name: "Properties", exact: true }).click();
    await page.route("**/api/direction/cuts/*", async (route) => {
      if (route.request().method() === "PATCH")
        await route.fulfill({
          status: 409,
          json: { detail: "This cut changed after it was read." },
        });
      else await route.continue();
    });
    await page.getByLabel("Delivered frame", { exact: true }).fill("50");
    await page.getByRole("button", { name: "Save cut", exact: true }).click();
    await expect(
      page.getByRole("form", { name: "Edit cut at f48" }).getByRole("alert"),
    ).toContainText("Review the refreshed details");
    await expect(marker).toBeVisible();
    await page.unroute("**/api/direction/cuts/*");
    await page.getByRole("button", { name: "Save cut", exact: true }).click();
    await expect(page.getByRole("button", { name: /Cut f50/ })).toBeVisible();
    await page
      .getByRole("button", { name: "Segment 02", exact: true })
      .click({ button: "right" });
    await page.getByRole("menuitem", { name: /Add phase at playhead/ }).click();
    const contextual = page.getByRole("dialog", {
      name: "Add phase",
      exact: true,
    });
    await contextual.getByLabel("Delivered frame").fill("84");
    await contextual
      .getByLabel("Direction", { exact: true })
      .fill("Arin turns to the arcade.");
    await contextual
      .getByRole("button", { name: "Create phase", exact: true })
      .click();
    await expect(
      page.getByRole("button", {
        name: "Phase: Arin turns to the arcade.",
        exact: true,
      }),
    ).toBeVisible();
  });

  test("layouts, pointer resizing, timeline navigation and both theme captures", async ({
    page,
  }) => {
    await openMovie(page, data.movie.title);
    const right = page.getByRole("separator", {
      name: "Resize Chat and Properties panel",
    });
    const box = (await right.boundingBox())!;
    await page.mouse.move(box.x + 2, box.y + 40);
    await page.mouse.down();
    await page.mouse.move(box.x - 18, box.y + 40);
    await page.mouse.up();
    await expect(right).toHaveAttribute("aria-valuenow", "440");
    await page.getByRole("button", { name: "Window", exact: true }).click();
    await page
      .getByRole("menuitem", { name: "Reset to saved layout", exact: true })
      .click();
    await expect(right).toHaveAttribute("aria-valuenow", "420");
    const ruler = page.getByRole("slider", { name: "Playhead", exact: true });
    await ruler.focus();
    await page.keyboard.press("ArrowRight");
    await expect(ruler).toHaveAttribute("aria-valuenow", "1");
    await page.getByRole("button", { name: "Zoom in", exact: true }).click();
    await expect(
      page.getByRole("slider", { name: "Timeline zoom" }),
    ).toHaveValue("1.3");
    await page
      .getByRole("button", { name: "Fit timeline", exact: true })
      .click();
    await fs.mkdir(".cache/s6-build", { recursive: true });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.getByLabel("Search project").fill("Arin reference.jpg");
    await page
      .getByRole("button", { name: "Arin reference.jpg image" })
      .click();
    for (const theme of ["dark", "light"]) {
      const toggle = page.getByRole("button", {
        name: `Switch to ${theme} mode`,
        exact: true,
      });
      if (await toggle.count()) await toggle.click();
      for (const layout of ["default", "full"]) {
        await page.getByRole("button", { name: "View", exact: true }).click();
        await page
          .getByRole("menuitem", {
            name:
              layout === "default" ? "Default timeline" : "Timeline full width",
            exact: true,
          })
          .click();
        await expect(page.locator(".studio-dock")).toHaveAttribute(
          "data-layout",
          layout,
        );
        await page.screenshot({
          path: `.cache/s6-build/direct-${theme}-${layout}.png`,
        });
      }
      const audit = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze();
      expect(audit.violations).toEqual([]);
    }
    await page.reload();
    await expect(page.locator(".studio-dock")).toHaveAttribute(
      "data-layout",
      "full",
    );
    for (const width of [1200, 900, 720]) {
      await page.setViewportSize({ width, height: 800 });
      await expect
        .poll(() =>
          page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
        )
        .toBe(true);
      await expect(
        page.getByRole("region", { name: "Timeline panel" }),
      ).toBeAttached();
    }
  });
});
