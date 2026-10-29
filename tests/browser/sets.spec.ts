import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { developmentPython } from "../../scripts/python.mjs";
import { browserTestOrigin } from "../browser-origin";

// ADR 0018 §6: Library › Sets imports a bundle, the set workbench opens as a full-window page, the picture is never
// covered, marks and saved views are kept, and Escape peels back one layer at a time before it closes the page.

function bundle() {
  // Inside this run's test library, so a run leaves no bundle folders behind it.
  const folder = path.join(
    process.env.GAIAVFX_TEST_LIBRARY!,
    "test-bundles",
    `browser-set-bundle-${Date.now()}`,
  );
  return execFileSync(
    developmentPython(path.resolve(".")),
    ["tests/browser-set-bundle.py", folder],
    { encoding: "utf8", windowsHide: true },
  ).trim();
}

/** A finished build of the Test room (no engine runs; see tests/browser-set-build.py). */
function finishBuild() {
  return execFileSync(
    developmentPython(path.resolve(".")),
    [
      "tests/browser-set-build.py",
      process.env.GAIAVFX_TEST_LIBRARY!,
      "Test room",
    ],
    { encoding: "utf8", windowsHide: true },
  ).trim();
}

function scopeProblems(page: Page) {
  const problems: string[] = [];
  page.on("console", (message) => {
    if (
      message.type() === "error" &&
      /Unclassified commands|Shared shortcuts/.test(message.text())
    )
      problems.push(message.text());
  });
  return problems;
}

/** A place built as the Test room with one angle, for the view's camera (ADR 0019 §2), through the API. */
async function seedPlace(request: APIRequestContext) {
  const session = await request.post("/api/v1/session", {
    headers: { "X-GAIAVFX-Bootstrap": "1", Origin: browserTestOrigin },
  });
  const headers = { Authorization: `Bearer ${(await session.json()).token}` };
  const place = await (
    await request.post("/api/direction/places", {
      headers,
      data: { name: "Set test shack" },
    })
  ).json();
  await request.post(`/api/direction/places/${place.id}/angles`, {
    headers,
    data: { name: "Window wall" },
  });
  const room = (
    await (await request.get("/api/sets", { headers })).json()
  ).find((s: { name: string }) => s.name === "Test room");
  const linked = await request.put(`/api/direction/places/${place.id}/set`, {
    headers,
    data: { expected_revision: place.revision, set_id: room.id },
  });
  expect(linked.ok(), await linked.text()).toBeTruthy();
}

async function openSets(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Library", exact: true }).click();
  await page.getByRole("button", { name: "Stages", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Stages", exact: true }),
  ).toBeVisible();
}

test.describe("sets", () => {
  test.describe.configure({ mode: "serial" });

  test("a bundle imports into Library › Sets as a proposed version", async ({
    page,
  }) => {
    const problems = scopeProblems(page);
    await openSets(page);
    await page.getByRole("button", { name: /Import stage bundle/ }).click();
    await page.getByLabel(/Bundle folder/).fill(bundle());
    await page.getByRole("button", { name: "Import", exact: true }).click();
    const card = page.getByRole("button", { name: /Test room/ });
    await expect(card).toBeVisible();
    await expect(card).toContainText("Proposed v1");
    await expect(card).toContainText("1 open issue");
    expect(problems).toEqual([]);
  });

  test("the workbench shows the set, keeps marks and views, and Escape peels back", async ({
    page,
    request,
  }) => {
    await seedPlace(request);
    const problems = scopeProblems(page);
    await openSets(page);
    await page
      .getByRole("button", { name: /Test room/ })
      .first()
      .click();
    const workbench = page.getByRole("main", { name: "Stage Test room" });
    await expect(workbench).toBeVisible();
    await expect(
      workbench.getByRole("heading", { name: "Test room", level: 1 }),
    ).toBeVisible();
    // The splat loads into the stage; the map, the Judge's issue and the set card are beside it, not over it.
    await expect(page.locator(".set-stage")).toHaveAttribute(
      "data-state",
      "ready",
      { timeout: 20_000 },
    );
    await expect(
      page.getByRole("img", { name: /Map of the stage/ }),
    ).toBeVisible();
    const issues = page.getByRole("tabpanel", { name: "Issues" });
    await expect(
      issues.getByRole("button", { name: /Floaters/ }),
    ).toContainText("Judge");
    await expect(
      page.getByRole("heading", { name: "Test room", level: 2 }),
    ).toBeVisible();
    await expect(
      page.locator(".set-inspector").getByText("Proposed v1", { exact: true }),
    ).toBeVisible();

    // Mark: M, a click on the picture, words, Mark.
    await page.keyboard.press("m");
    await expect(
      page.getByRole("heading", { name: "Mark a problem" }),
    ).toBeVisible();
    const stage = page.locator(".set-program");
    const box = (await stage.boundingBox())!;
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await expect(
      page.getByRole("heading", { name: /What is wrong here/ }),
    ).toBeVisible();
    await page
      .getByLabel("Say what you see")
      .fill("The orange wall shimmers at its top edge.");
    const inspector = page.getByRole("complementary", {
      name: "Inspector and chat",
    });
    await inspector.getByRole("button", { name: "Mark", exact: true }).click();
    await expect(
      issues.getByRole("button", { name: /The orange wall shimmers/ }),
    ).toContainText("You");

    // Escape: the selection first, then the page.
    await page.locator("body").press("Escape");
    await expect(
      page.getByRole("heading", { name: "Test room", level: 2 }),
    ).toBeVisible();

    // Save view keeps the camera under a name.
    await page.keyboard.press("s");
    await expect(
      page.getByRole("heading", { name: "Save view" }),
    ).toBeVisible();
    await inspector
      .getByRole("button", { name: "Save view", exact: true })
      .click();
    await expect(
      page
        .getByRole("tabpanel", { name: "Views" })
        .getByRole("button", { name: /View 1/ }),
    ).toBeVisible();
    // Its picture was kept, and it can be the camera of a shot angle of a place built as this set.
    await expect(
      inspector.getByRole("img", { name: /Saved view View 1/ }),
    ).toBeVisible();
    const cameraOf = inspector.getByLabel("Camera of");
    await cameraOf.selectOption({ label: "Set test shack · Window wall" });
    await expect(cameraOf).toHaveValue(/.+/);
    await expect(cameraOf.locator("option:checked")).toHaveText(
      "Set test shack · Window wall",
    );

    // Movie commands wait while a set is open, and say why.
    await page.getByRole("button", { name: "Segment", exact: true }).click();
    const newSegment = page.getByRole("menuitem", { name: /New segment/ });
    if (await newSegment.count()) await expect(newSegment).toBeDisabled();
    await page.keyboard.press("Escape");

    const results = await new AxeBuilder({ page })
      .include(".set-workbench")
      .exclude(".set-viewer-canvas")
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(results.violations).toEqual([]);

    await page.locator("body").press("Escape");
    await page.locator("body").press("Escape");
    await expect(
      page.getByRole("heading", { name: "Stages", exact: true }),
    ).toBeVisible();
    expect(problems).toEqual([]);
  });

  test("Run queued fixes starts a build and says plainly when it stops", async ({
    page,
  }) => {
    const problems = scopeProblems(page);
    await openSets(page);
    await page
      .getByRole("button", { name: /Test room/ })
      .first()
      .click();
    await expect(page.locator(".set-stage")).toHaveAttribute(
      "data-state",
      "ready",
      { timeout: 20_000 },
    );
    const run = page.getByRole("button", { name: /Run queued fixes/ });
    await expect(run).toHaveText("Run queued fixes (0)");
    await expect(run).toBeDisabled();
    // Exclude K1: its card offers it, the button counts it and says what will happen.
    await page.getByRole("tab", { name: "Keyframes" }).click();
    await page
      .getByRole("tabpanel", { name: "Keyframes" })
      .getByRole("button", { name: /^K1/ })
      .click();
    const inspector = page.getByRole("complementary", {
      name: "Inspector and chat",
    });
    await inspector.getByRole("button", { name: "Exclude from stage" }).click();
    await expect(
      inspector.getByRole("button", { name: "Exclusion queued" }),
    ).toBeDisabled();
    await expect(run).toHaveText("Run queued fixes (1)");
    await expect(run).toHaveAttribute("title", /without K1.*proposed v2/);
    await run.click();
    // The test app has no engines installed: the build stops while preparing, the banner says so and what stands.
    const banner = page
      .getByRole("alert")
      .filter({ hasText: "The build failed while preparing" });
    await expect(banner).toBeVisible({ timeout: 20_000 });
    await expect(banner).toContainText("v1 is unchanged");
    await expect(
      banner.getByRole("button", { name: "Retry build" }),
    ).toBeVisible({ timeout: 20_000 });
    await expect(
      inspector.getByRole("button", { name: "Queue again" }),
    ).toBeVisible();
    const results = await new AxeBuilder({ page })
      .include(".set-workbench")
      .exclude(".set-viewer-canvas")
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(results.violations).toEqual([]);
    await banner.getByRole("button", { name: "Dismiss" }).click();
    await expect(banner).toBeHidden();
    expect(problems).toEqual([]);
  });

  test("Fill more unseen space queues an extension that Run queued fixes says it will fill", async ({
    page,
  }) => {
    const problems = scopeProblems(page);
    await openSets(page);
    await page
      .getByRole("button", { name: /Test room/ })
      .first()
      .click();
    await expect(page.locator(".set-stage")).toHaveAttribute(
      "data-state",
      "ready",
      { timeout: 20_000 },
    );
    const run = page.getByRole("button", { name: /Run queued fixes/ });
    const fill = page.getByRole("button", { name: "Fill more unseen space" });
    await expect(run).toHaveAttribute("title", /fill more unseen space first/);
    await fill.click();
    // Python queues one extension and words the run it adds to (ADR 0020 §4).
    await expect(run).toHaveText("Run queued fixes (1)");
    await expect(run).toHaveAttribute(
      "title",
      /fills in what no view has seen around it: up to 2 rounds and 6 renders, about 15 to 25 minutes.*proposed v2/,
    );
    // It belongs to no card: the toolbar says it is queued and offers its Cancel.
    const queued = page
      .getByRole("status")
      .filter({ hasText: "Fill more queued" });
    await expect(queued).toBeVisible();
    await expect(fill).toHaveCount(0);
    const results = await new AxeBuilder({ page })
      .include(".set-workbench")
      .exclude(".set-viewer-canvas")
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(results.violations).toEqual([]);
    await queued.getByRole("button", { name: "Cancel" }).click();
    await expect(run).toHaveText("Run queued fixes (0)");
    await expect(fill).toBeVisible();
    expect(problems).toEqual([]);
  });

  test("a finished build is reviewed Before and After, and only what looks fixed is fixed", async ({
    page,
  }) => {
    const problems = scopeProblems(page);
    finishBuild();
    await openSets(page);
    await page
      .getByRole("button", { name: /Test room/ })
      .first()
      .click();
    await expect(page.locator(".set-stage")).toHaveAttribute(
      "data-state",
      "ready",
      { timeout: 20_000 },
    );
    // The finished build waits in the toolbar; opening it takes over the Inspector, the camera stays.
    await page.getByRole("button", { name: "v2 ready · Review" }).click();
    const inspector = page.getByRole("complementary", {
      name: "Inspector and chat",
    });
    await expect(
      inspector.getByRole("heading", { name: "Review v2" }),
    ).toBeVisible();
    const caption = page.locator(".set-version-caption");
    await expect(caption).toHaveText("After · v2");
    await page.keyboard.press("v");
    await expect(caption).toHaveText("Before · v1", {
      timeout: 20_000,
    });
    await expect(
      inspector.getByRole("button", { name: "Before · v1" }),
    ).toHaveAttribute("aria-pressed", "true");
    await inspector
      .getByRole("button", { name: "K1 · look from there" })
      .click();
    const results = await new AxeBuilder({ page })
      .include(".set-workbench")
      .exclude(".set-viewer-canvas")
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(results.violations).toEqual([]);
    // The user says what looks fixed; Accept makes v2 current and says what stays.
    await inspector
      .getByRole("region", { name: "Issues it addressed" })
      .getByLabel("Looks fixed")
      .check();
    // The set's other open issues are listed to flip at, too.
    await expect(
      inspector.getByRole("region", { name: "Still open" }),
    ).toContainText("The orange wall shimmers");
    await inspector.getByRole("button", { name: "Accept v2" }).click();
    await expect(
      inspector.getByText(/v2 is current\. v1 is kept/),
    ).toBeVisible();
    await expect(caption).toBeHidden();
    await page.getByRole("tab", { name: "Keyframes" }).click();
    await expect(
      page
        .getByRole("tabpanel", { name: "Keyframes" })
        .getByRole("button", { name: /A1/ }),
    ).toContainText("Excluded · colour only · now A1");
    await page.getByRole("tab", { name: "Issues" }).click();
    await page
      .getByRole("tabpanel", { name: "Issues" })
      .getByRole("button", { name: /A dark smear hangs/ })
      .click();
    await expect(inspector.getByText("Fixed", { exact: true })).toBeVisible();
    expect(problems).toEqual([]);
  });

  test("the workbench reads in the light theme too", async ({ page }) => {
    await openSets(page);
    await page.getByRole("button", { name: /Switch to light mode/ }).click();
    await page
      .getByRole("button", { name: /Test room/ })
      .first()
      .click();
    await expect(page.locator(".set-stage")).toHaveAttribute(
      "data-state",
      "ready",
      { timeout: 20_000 },
    );
    const results = await new AxeBuilder({ page })
      .include(".set-workbench")
      .exclude(".set-viewer-canvas")
      .withTags(["wcag2aa"])
      .analyze();
    expect(results.violations).toEqual([]);
    await page.getByRole("button", { name: /Switch to dark mode/ }).click();
  });
});
