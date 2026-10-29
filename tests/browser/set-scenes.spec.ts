import { test, expect, type APIRequestContext } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { execFileSync } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { developmentPython } from "../../scripts/python.mjs";
import { browserTestOrigin } from "../browser-origin";

// ADR 0019: a scene's first shot uses a set from the timeline; Prepare renders the angle's view from the set, waits for
// its review, then assembles the sheet the first shot asked for.

type Row = { id: string; revision: number };
type Send = <T>(method: string, url: string, data?: unknown) => Promise<T>;

async function api(request: APIRequestContext) {
  const session = await request.post("/api/v1/session", {
    headers: { "X-GAIAVFX-Bootstrap": "1", Origin: browserTestOrigin },
  });
  const headers = { Authorization: `Bearer ${(await session.json()).token}` };
  const send: Send = async (method, url, data) => {
    const response = await request.fetch(url, { method, headers, data });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  };
  return { headers, send };
}

/** A synthetic set bundle imported and accepted, written inside this run's test library so no folder is left behind. */
async function importSet(send: Send, name: string) {
  const folder = path.join(
    process.env.GAIAVFX_TEST_LIBRARY!,
    "test-bundles",
    `${name.replaceAll(" ", "-")}-${Date.now()}`,
  );
  execFileSync(
    developmentPython(path.resolve(".")),
    ["tests/browser-set-bundle.py", folder, name],
    { encoding: "utf8", windowsHide: true },
  );
  const set = await send<{ id: string; version: { id: string } }>(
    "POST",
    "/api/sets/import",
    { folder },
  );
  await send("POST", `/api/sets/${set.id}/versions/${set.version.id}/accept`, {
    expected_current_id: null,
    looks_fixed: [],
  });
  return set;
}

/** A movie with one subject (with an identity picture) and one scene without cuts. */
async function movieWithScene(
  request: APIRequestContext,
  headers: Record<string, string>,
  send: Send,
  title: string,
) {
  const imported = await request.post("/api/v1/imports", {
    headers,
    multipart: {
      file: {
        name: "Ivo.jpg",
        mimeType: "image/jpeg",
        buffer: await fs.readFile(
          "tests/fixtures/concept/arin.jpg",
        ),
      },
    },
  });
  expect(imported.ok(), await imported.text()).toBeTruthy();
  const movie = await send<Row>("POST", "/api/direction/movies", { title });
  const subject = await send<
    Row & { receipt: { after: { revision: number } } }
  >("POST", `/api/direction/movies/${movie.id}/subjects`, {
    expected_revision: movie.revision,
    name: "Ivo",
    description: "An old fisher in a wool sweater",
    keeps: ["the grey sweater"],
    identity_asset_id: (await imported.json()).asset.id,
  });
  return send<Row>("POST", `/api/direction/movies/${movie.id}/segments`, {
    expected_revision: subject.receipt.after.revision,
    content: {
      subjects: [subject.id],
      place: { noun: "shack", description: "A fishing shack.", landmarks: "" },
    },
    delivered_frames: 97,
    continues: false,
    phases: [
      {
        start_frame: 0,
        content: {
          description: "Ivo feeds the stove.",
          screen_direction: "",
          sound: "",
        },
      },
    ],
  });
}

async function openMovie(page: import("@playwright/test").Page, title: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "File", exact: true }).click();
  await page.getByRole("menuitem", { name: /Open movie…/ }).click();
  await page
    .getByRole("dialog", { name: "Open movie", exact: true })
    .getByRole("button", { name: title, exact: true })
    .click();
}

test("Use a set for a scene says what changes, then the References row shows what the first shot still needs", async ({
  page,
  request,
}) => {
  const title = `Use set · ${Math.random().toString(36).slice(2, 6)}`;
  const { headers, send } = await api(request);
  await importSet(send, "Porch room");
  const porch = await send<Row>("POST", "/api/direction/places", {
    name: `${title} porch`,
  });
  await movieWithScene(request, headers, send, title);
  await openMovie(page, title);
  const segment = page.locator(".segment-item").first();
  await segment.click({ button: "right" });
  await page
    .getByRole("menuitem", { name: /Use a stage for this scene/ })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Use a stage for this scene",
  });
  await dialog.getByRole("radio", { name: /Porch room/ }).check();
  await dialog
    .getByRole("combobox", { name: "Place", exact: true })
    .selectOption(porch.id);
  await dialog.getByLabel("New angle's name").fill("Porch, wide");
  const changes = dialog.getByRole("region", { name: "What changes" });
  await expect(changes).toContainText(
    `The place “${title} porch” is built as the stage “Porch room”.`,
  );
  await expect(changes).toContainText("opens on Porch, wide, a new angle of");
  await expect(changes).toContainText("has no camera in the stage yet");
  await expect(changes).toContainText("Undo restores this scene's first shot");
  await dialog.getByRole("button", { name: "Use Porch room" }).click();
  await expect(dialog).toBeHidden();
  const chip = page.getByRole("button", {
    name: /First-shot references · Stage · Porch room · v1/,
  });
  await expect(chip).toContainText("No camera for the first shot in the stage");
  await chip.click();
  await page.getByRole("tab", { name: "Properties" }).click();
  const firstShot = page.getByRole("region", { name: "First shot" });
  const facts = firstShot.locator("dl.first-shot-place");
  await expect(facts).toContainText(`${title} porch`);
  await expect(facts).toContainText("Porch room · v1");
  await expect(facts).toContainText(
    "Pictures of the stage · No camera for the first shot in the stage",
  );
  await expect(
    firstShot.getByRole("button", { name: "Open its stage" }),
  ).toBeEnabled();

  const results = await new AxeBuilder({ page })
    .include(".properties-content")
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(results.violations).toEqual([]);
});

test("a scene in a set gets its first shot's sheet from views rendered from the set", async ({
  page,
  request,
}) => {
  const title = `Set scene · ${Math.random().toString(36).slice(2, 6)}`;
  const { headers, send } = await api(request);
  const set = await importSet(send, "Scene room");
  const place = await send<Row>("POST", "/api/direction/places", {
    name: `${title} shack`,
  });
  const door = await send<Row>(
    "POST",
    `/api/direction/places/${place.id}/angles`,
    { name: "Door" },
  );
  const segment = await movieWithScene(request, headers, send, title);
  await send("POST", `/api/direction/segments/${segment.id}/use-set`, {
    expected_revision: segment.revision,
    set_id: set.id,
    angle_id: door.id,
  });
  const views = await send<{ views: { id: string }[] }>(
    "POST",
    `/api/sets/${set.id}/views`,
    {
      version_id: set.version.id,
      name: "Door",
      viewmat: [
        [1, 0, 0, 0],
        [0, 1, 0, 0],
        [0, 0, 1, 0],
        [0, 0, 0, 1],
      ],
      fov: 60,
      width: 320,
      height: 180,
    },
  );
  await send(
    "PUT",
    `/api/sets/${set.id}/views/${views.views.at(-1)!.id}/angle`,
    { version_id: set.version.id, angle_id: door.id },
  );
  await openMovie(page, title);
  await page.locator(".segment-item").first().click();
  await page.getByRole("button", { name: "Prepare", exact: true }).click();
  const flow = page.getByRole("region", { name: "Prepare reference flow" });
  await expect(flow.getByText("Prepare · Door · first shot")).toBeVisible();
  const step = (name: string) => flow.getByRole("region", { name });
  // A place built as a set: its camera, then views rendered from it; a first shot has no pin.
  await expect(step("Camera")).toContainText("Aimed");
  await expect(step("Views")).toContainText("Missing");
  await expect(step("Pin")).toHaveCount(0);
  await expect(step("Plate")).toHaveCount(0);

  await step("Views")
    .getByRole("button", { name: /Render views from the stage/ })
    .click();
  const form = page.getByRole("dialog", {
    name: "Render views from the stage",
  });
  await expect(form.getByLabel("Door")).toBeChecked();
  await form
    .getByRole("button", { name: "Render views from the stage", exact: true })
    .click();
  await expect(step("Views")).toContainText("Awaiting your review", {
    timeout: 30_000,
  });
  await step("Views").getByRole("button", { name: "Review in Source" }).click();
  await expect(
    flow.getByRole("heading", { name: "Stage views" }),
  ).toBeVisible();
  await flow.getByRole("button", { name: "Approve reference" }).click();
  await flow.getByRole("button", { name: "Reference flow" }).click();
  await expect(step("Views")).toContainText("Approved");

  await step("Sheet")
    .getByRole("button", { name: /^Assemble sheet/ })
    .click();
  const sheet = page.getByRole("dialog", { name: "Assemble sheet" });
  await expect(sheet).toContainText(
    "Origin: views rendered from the stage this place is built as.",
  );
  await sheet
    .getByRole("button", { name: "Assemble sheet", exact: true })
    .click();
  await expect(step("Sheet")).toContainText("Assembled", { timeout: 30_000 });
  await expect(
    page.getByRole("status").filter({ hasText: "Ready to render" }).first(),
  ).toBeVisible();

  const results = await new AxeBuilder({ page })
    .include(".prepare-panel")
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(results.violations).toEqual([]);
});
