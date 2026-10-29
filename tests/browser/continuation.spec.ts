import { expect, test, type APIRequestContext } from "@playwright/test";
import { openMovie, seedDirect } from "./direct-fixture";

// docs/CONTINUATION.md: a continuing segment hands over its predecessor's motion on the Continuation track. Python
// resolves the method, the lengths (5, 22, 39, 56) and the recommendation. Segment 02 continues Segment 01 and cuts
// to a reverse that plans a pinned keyframe, so paired latents are recommended and masked handoff cannot render;
// Segment 03 continues Segment 02 without a cut, so masked handoff is recommended and paired stays a choice.
let data: Awaited<ReturnType<typeof seedDirect>>;
let third: { id: string };
test.beforeAll(async ({ request }) => {
  data = await seedDirect(request);
  const movie = await request.get(`/api/direction/movies/${data.movie.id}`, {
    headers: data.headers,
  });
  const added = await request.post(
    `/api/direction/movies/${data.movie.id}/segments`,
    {
      headers: data.headers,
      data: {
        expected_revision: (await movie.json()).revision,
        content: data.second.content,
        delivered_frames: 96,
        continues: true,
        phases: [{ start_frame: 0, content: data.second.phases[0].content }],
      },
    },
  );
  expect(added.ok(), await added.text()).toBeTruthy();
  third = await added.json();
});

async function handoff(request: APIRequestContext, id: string) {
  const response = await request.get(`/api/direction/segments/${id}`, {
    headers: data.headers,
  });
  const segment = await response.json();
  return [segment.continuation_method, segment.context_frames];
}

test("the continuation track drags its context and undoes it", async ({
  page,
  request,
}) => {
  await openMovie(page, data.movie.title);
  const status = page.locator(".studio-status").getByRole("status");
  const band = page.getByRole("button", {
    name: /^Continuation into Segment 02/,
  });
  const grip = page.getByRole("slider", {
    name: "Context length into Segment 02",
  });
  await expect(band).toHaveAccessibleName(
    "Continuation into Segment 02 · Paired latents · 22 frames of context · Recommended",
  );
  await expect(grip).toHaveAttribute("aria-valuenow", "22");
  await expect(grip).toHaveAttribute("aria-valuetext", "22 frames");

  // The leading edge drags left, away from the join, and snaps to the next length the join can carry.
  const perFrame = (await band.boundingBox())!.width / 22;
  const edge = (await grip.boundingBox())!;
  const y = edge.y + edge.height / 2;
  const x = edge.x + edge.width / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x - 17 * perFrame, y, { steps: 8 });
  await expect(page.locator(".resize-edge-value")).toHaveText("39 f");
  // Escape abandons the drag; nothing is saved.
  await page.keyboard.press("Escape");
  await page.mouse.up();
  await expect(grip).toHaveAttribute("aria-valuenow", "22");
  expect(await handoff(request, data.second.id)).toEqual([null, null]);

  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x - 17 * perFrame, y, { steps: 8 });
  await page.mouse.up();
  await expect(status).toContainText("Continuation context: 39 frames");
  await expect(grip).toHaveAttribute("aria-valuenow", "39");
  await expect(band).toHaveAccessibleName(/Paired latents · 39 frames/);
  expect(await handoff(request, data.second.id)).toEqual([null, 39]);
  await page.keyboard.press("Control+z");
  await expect(status).toContainText("Change undone");
  await expect(grip).toHaveAttribute("aria-valuenow", "22");
  expect(await handoff(request, data.second.id)).toEqual([null, null]);

  // The keyboard moves the edge too: ArrowLeft lengthens, Home and End reach the ends; the default is stored as null.
  // One edit at a time: a key pressed while the last edit saves is dropped, so each waits for the save.
  await grip.focus();
  for (const [key, frames] of [
    ["ArrowLeft", "39"],
    ["Home", "5"],
    ["End", "56"],
    ["ArrowRight", "39"],
    ["ArrowRight", "22"],
  ]) {
    await page.keyboard.press(key);
    await expect(grip).toHaveAttribute("aria-valuenow", frames);
    await expect(grip).not.toHaveAttribute("aria-disabled", "true");
  }
  expect(await handoff(request, data.second.id)).toEqual([null, null]);

  // Fitted, the default band shows its length; zoomed in, its method and whether that follows the rule.
  await expect(band).toHaveText("22 f");
  for (let i = 0; i < 4; i++)
    await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  await expect(band.locator("small")).toHaveText("Recommended");
  await expect(band).toContainText("Paired");
  await page.screenshot({
    path: test.info().outputPath("continuation-zoomed-dark.png"),
  });
  await page.getByRole("button", { name: "Fit timeline", exact: true }).click();

  // Masked handoff cannot render with the planned pin; the menu says why instead of offering it.
  await band.click({ button: "right" });
  await page
    .getByRole("menuitem", { name: "Handoff method", exact: true })
    .hover();
  await expect(
    page.getByRole("menuitem", { name: "Automatic · Paired latents" }),
  ).toBeVisible();
  const masked = page.getByRole("menuitem", {
    name: "Masked handoff",
    exact: true,
  });
  await expect(masked).toHaveAttribute("aria-disabled", "true");
  await expect(masked).toHaveAttribute("title", /pinned keyframe/);
  await page.keyboard.press("Escape");
});

test("the method is chosen from the menu and Properties, against the rule and back", async ({
  page,
  request,
}, testInfo) => {
  await openMovie(page, data.movie.title);
  const status = page.locator(".studio-status").getByRole("status");
  const band = page.getByRole("button", {
    name: /^Continuation into Segment 03/,
  });
  await expect(band).toHaveAccessibleName(
    "Continuation into Segment 03 · Masked handoff · 22 frames of context · Recommended",
  );
  await band.click({ button: "right" });
  await page
    .getByRole("menuitem", { name: "Handoff method", exact: true })
    .hover();
  await page
    .getByRole("menuitem", { name: "Paired latents", exact: true })
    .click();
  await expect(status).toContainText("Handoff: Paired latents");
  await expect(band).toHaveAccessibleName(
    "Continuation into Segment 03 · Paired latents · 22 frames of context · Masked handoff recommended",
  );
  await expect(band).toHaveText("22 f");
  await expect(band).toHaveClass(/off-rule/);
  expect(await handoff(request, third.id)).toEqual(["paired", null]);
  await page.screenshot({
    path: testInfo.outputPath("continuation-dark.png"),
  });
  await page.keyboard.press("Control+z");
  await expect(status).toContainText("Change undone");
  expect(await handoff(request, third.id)).toEqual([null, null]);

  // Properties: the same choices, with the rule's reason and the audio coupling in words.
  await band.click();
  await page.locator("#inspector-Properties-tab").click();
  const section = page.getByRole("region", { name: "Continuation" });
  await expect(section).toContainText("This segment continues the same shot.");
  await expect(section).toContainText(
    "Audio history follows the picture: 22 frames",
  );
  const method = section.getByRole("combobox", { name: "Handoff method" });
  const context = section.getByRole("spinbutton", {
    name: "Context length (frames)",
  });
  await method.selectOption("paired");
  await context.fill("39");
  await section.getByRole("button", { name: "Save continuation" }).click();
  await expect(status).toContainText("Continuation saved");
  expect(await handoff(request, third.id)).toEqual(["paired", 39]);
  await expect(section).toContainText(
    "Audio history keeps its own 24-frame window",
  );
  await expect(section.locator(".rule-chip")).toHaveText(
    "Masked handoff recommended",
  );
  // Automatic and an empty length return both to the rule.
  await method.selectOption("");
  await context.fill("");
  await section.getByRole("button", { name: "Save continuation" }).click();
  await expect(status).toContainText("Continuation saved");
  expect(await handoff(request, third.id)).toEqual([null, null]);
  await expect(section.locator(".rule-chip")).toHaveText("Recommended");

  // Segment 02's masked option says it is unavailable, and why.
  await page
    .getByRole("button", { name: /^Continuation into Segment 02/ })
    .click();
  await expect(
    section.getByRole("option", { name: "Masked handoff · unavailable" }),
  ).toHaveAttribute("disabled", "");
  await expect(section).toContainText("pinned keyframe");

  await page.getByRole("button", { name: "Switch to light mode" }).click();
  await band.click({ button: "right" });
  await page
    .getByRole("menuitem", { name: "Context length", exact: true })
    .hover();
  await expect(
    page.getByRole("menuitem", { name: "22 frames · default" }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("continuation-light.png"),
  });
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Switch to dark mode" }).click();
});
