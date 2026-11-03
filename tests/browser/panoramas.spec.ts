import {
  expect,
  test,
  type APIRequestContext,
  type Page,
  type TestInfo,
} from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { browserTestOrigin } from "../browser-origin";
import { openMovie } from "./direct-fixture";
import { seedProduction } from "./production-fixture";

// Panoramas in Library › Stages: a place described in words becomes two 360° candidates (the browser service's fakes
// draw the synthetic room and close its seam); the person looks around them, chooses one and builds a stage from it
// (filling off, so the fakes finish in seconds); a picture grows into two panoramas that keep it. Keyboard first, in
// both themes, with axe on each state.

async function themed(page: Page, theme: "dark" | "light") {
  await page.emulateMedia({ colorScheme: theme });
  await page.addInitScript((value) => {
    localStorage.setItem(
      "gaiavfx-preferences",
      JSON.stringify({ state: { theme: value }, version: 1 }),
    );
  }, theme);
}

async function openStages(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Library", exact: true }).click();
  await page.getByRole("button", { name: "Stages", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Stages", exact: true }),
  ).toBeVisible();
}

async function audit(page: Page, info: TestInfo, name: string) {
  const file = `panoramas-${name}.png`;
  const shot = process.env.GAIAVFX_REVIEW_DIR
    ? path.join(process.env.GAIAVFX_REVIEW_DIR, file)
    : info.outputPath(file);
  await page.screenshot({ path: shot });
  await info.attach(file, { path: shot, contentType: "image/png" });
  const results = await new AxeBuilder({ page })
    .include(".sets-library")
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(results.violations).toEqual([]);
}

/** A still in the Library to grow a panorama from: grey, between black letterbox bars. */
async function importPicture(request: APIRequestContext) {
  const session = await request.post("/api/v1/session", {
    headers: { "X-GAIAVFX-Bootstrap": "1", Origin: browserTestOrigin },
  });
  const headers = { Authorization: `Bearer ${(await session.json()).token}` };
  // A 64x36 PNG: rows 4-31 grey (120), rows outside black.
  const { deflateSync } = await import("node:zlib");
  const width = 64;
  const height = 36;
  const raw = Buffer.alloc((width * 3 + 1) * height);
  for (let y = 0; y < height; y++) {
    const value = y >= 4 && y < 32 ? 120 : 0;
    raw.fill(value, y * (width * 3 + 1) + 1, (y + 1) * (width * 3 + 1));
  }
  const chunk = (type: string, data: Buffer) => {
    const body = Buffer.concat([Buffer.from(type), data]);
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    let crc = 0xffffffff;
    for (const byte of body) {
      crc ^= byte;
      for (let k = 0; k < 8; k++) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
    const sum = Buffer.alloc(4);
    sum.writeUInt32BE((crc ^ 0xffffffff) >>> 0);
    return Buffer.concat([length, body, sum]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header.set([8, 2, 0, 0, 0], 8);
  const png = Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
  const imported = await request.post("/api/v1/imports", {
    headers,
    multipart: {
      file: { name: "hall-frame.png", mimeType: "image/png", buffer: png },
    },
  });
  expect(imported.ok(), await imported.text()).toBeTruthy();
}

for (const theme of ["dark", "light"] as const) {
  test(`a place in words becomes a chosen panorama and a stage; ${theme}`, async ({
    page,
  }, info) => {
    test.setTimeout(240_000);
    await themed(page, theme);
    await openStages(page);
    const place = `A narrow city-flat hall with worn parquet (${theme})`;

    // The form, by keyboard: the place, one landmark behind, the light.
    await page
      .getByRole("button", { name: "New stage from a panorama…" })
      .click();
    await expect(page.getByRole("radio", { name: "Words" })).toBeChecked();
    await page.getByLabel("The place").fill(place);
    await page.getByRole("button", { name: "Add a landmark" }).click();
    await page.getByLabel("Landmark 1", { exact: true }).fill("Entrance door");
    await page
      .getByLabel("Where landmark 1 stands")
      .selectOption({ label: "Behind" });
    await page.getByLabel("Light", { exact: true }).fill("Overcast afternoon");
    await expect(
      page.getByText(/renders\. Krea 2 and Fibo 2\.0/),
    ).toBeVisible();
    await page.getByText("What Krea 2 reads").click();
    await expect(
      page.getByText(/img-txt-2-360, .*Entrance door at 180 degrees/),
    ).toBeVisible();
    await audit(page, info, `${theme}-form`);
    await page.getByRole("button", { name: "Draw two panoramas" }).click();

    // The review: two candidates once drawn, their seams checked in words.
    await expect(page.getByRole("heading", { name: place })).toBeVisible();
    await expect(
      page.getByText("Choose the panorama to build a stage from."),
    ).toBeVisible({
      timeout: 60_000,
    });
    const letters = page.getByRole("group", { name: "Look at" });
    await expect(letters.getByRole("radio")).toHaveCount(2);
    await expect(page.getByText(/^Closed: where the edges join/)).toBeVisible();

    // The viewer: arrows turn, S faces the seam, Flat shows the whole picture with its seam at both edges.
    const viewer = page.getByRole("application");
    await viewer.focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.locator(".panorama-readout")).toContainText("5° right");
    await page.keyboard.press("s");
    await expect(page.locator(".panorama-readout")).toContainText(
      "Behind (180°)",
    );
    await page.getByRole("button", { name: "Flat" }).click();
    await expect(
      page.getByRole("img", { name: /the whole panorama flat/ }),
    ).toBeVisible();
    await expect(page.getByText("Behind · seam 180°").first()).toBeVisible();
    await audit(page, info, `${theme}-review`);
    await page.getByRole("button", { name: "Flat" }).click();

    // B is looked at, then chosen; only then can a stage be built.
    await letters.getByRole("radio").nth(1).check();
    await page.getByRole("button", { name: "Choose panorama B" }).click();
    await expect(page.locator(".panorama-chosen")).toHaveText(/Chosen/);
    const build = page.getByRole("form", {
      name: /Build a stage from panorama B/,
    });
    await expect(build.getByLabel("Stage name")).toHaveValue(
      /A narrow city-flat hall/,
    );
    await build.getByLabel("Fill in what the panorama never saw").uncheck();
    await expect(build.getByText(/About 4 minutes of GPU work/)).toBeVisible();
    await build.getByLabel("Stage name").fill(`Hall ${theme}`);
    await audit(page, info, `${theme}-chosen`);
    await build.getByRole("button", { name: "Build the stage" }).click();
    await expect(page.getByText(/its v1 waits for you/)).toBeVisible({
      timeout: 120_000,
    });
    await page.getByRole("button", { name: "Open the stage" }).click();
    await expect(
      page.getByRole("main", { name: `Stage Hall ${theme}` }),
    ).toBeVisible();
    await page.getByRole("tab", { name: "Keyframes" }).click();
    await page.getByRole("button", { name: /K0/ }).first().click();
    await expect(page.getByText("360° panorama")).toBeVisible();
    await page
      .getByRole("button", { name: "Look around the panorama" })
      .click();
    const look = page.getByRole("application", {
      name: /the stage's panorama/,
    });
    await look.focus();
    await page.keyboard.press("Shift+ArrowLeft");
    await expect(page.locator(".panorama-readout")).toContainText("15° left");
  });
}

test("a picture grows into two panoramas that keep it", async ({
  page,
  request,
}, info) => {
  test.setTimeout(180_000);
  await importPicture(request);
  await openStages(page);
  await page
    .getByRole("button", { name: "New stage from a panorama…" })
    .click();
  await page.getByRole("radio", { name: "A picture" }).check();
  await page.getByRole("radio", { name: "hall-frame.png" }).first().check();
  await expect(
    page.getByText(
      /Keeps your picture; Fibo 2.0 continues the place you describe/,
    ),
  ).toBeVisible();
  await page
    .getByLabel("The place in words")
    .fill("The grown hall: parquet and a coat rack");
  await page.getByRole("button", { name: "Grow two panoramas" }).click();
  await expect(
    page.getByRole("heading", { name: "The grown hall" }),
  ).toBeVisible();
  await expect(
    page.getByText("Choose the panorama to build a stage from."),
  ).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByText(/Kept at the front, 70° wide/)).toBeVisible();
  await expect(
    page.getByText("Everything around it is invented."),
  ).toBeVisible();
  await audit(page, info, "picture-review");
  // Back to the list: the attempt is there with both candidates.
  await page.getByRole("button", { name: "All stages" }).click();
  const row = page.getByRole("button", { name: /The grown hall/ });
  await expect(row).toContainText("Choose one");
  await expect(row.locator("img")).toHaveCount(2);
});

test("a scene's approved plate grows into two panoramas from Prepare", async ({
  page,
  request,
}) => {
  // The scene's place has an approved plate (its people removed): one click grows it, and the attempt opens in
  // Library › Stages, where the person chooses one and builds the stage.
  const data = await seedProduction(request);
  await openMovie(page, data.movie.title);
  await page.getByRole("button", { name: /Cut f36/ }).click();
  await page
    .getByRole("navigation", { name: "Workspaces" })
    .getByRole("button", { name: "Prepare", exact: true })
    .click();
  const flow = page.getByRole("region", { name: "Prepare reference flow" });
  const grow = flow.getByRole("button", {
    name: "Grow a 360 panorama from the plate",
  });
  await expect(grow).toBeVisible();
  await grow.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Grown from a picture")).toBeVisible();
  await expect(
    page.getByText("Choose the panorama to build a stage from."),
  ).toBeVisible({ timeout: 60_000 });
});
