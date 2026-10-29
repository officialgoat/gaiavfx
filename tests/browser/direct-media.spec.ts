import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";
import fs from "node:fs/promises";
import { openMovie, seedDirect } from "./direct-fixture";

test("real UI import and Source/Program playback; candidate names stay private", async ({
  page,
  request,
}) => {
  const data = await seedDirect(request);
  const folder = path.resolve(".cache/s6-media");
  await fs.mkdir(folder, { recursive: true });
  const video = path.join(
    folder,
    `seed-999-technique-private-${process.pid}.mp4`,
  );
  const tools =
    process.env.GAIAVFX_MEDIA_DIR ?? path.resolve(".runtime/media");
  execFileSync(
    path.join(tools, process.platform === "win32" ? "ffmpeg.exe" : "ffmpeg"),
    [
      "-nostdin",
      "-y",
      "-loglevel",
      "error",
      "-loop",
      "1",
      "-i",
      "tests/fixtures/concept/arin.jpg",
      "-t",
      "2",
      "-vf",
      "scale=320:-2",
      "-r",
      "24",
      "-c:v",
      "libx264",
      "-pix_fmt",
      "yuv420p",
      video,
    ],
    { windowsHide: true, timeout: 15_000 },
  );
  await openMovie(page, data.movie.title);
  const imported = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/imports") && r.request().method() === "POST",
  );
  await page
    .getByLabel("Import project media", { exact: true })
    .setInputFiles(video);
  const importResponse = await imported;
  expect(importResponse.ok()).toBe(true);
  const asset = (await importResponse.json()).asset;
  await page
    .getByRole("button", { name: `${asset.name} video`, exact: true })
    .click();
  const source = page
    .getByRole("region", { name: "Source monitor" })
    .locator("video");
  await page
    .getByRole("region", { name: "Source monitor" })
    .getByRole("button", { name: "Play preview" })
    .click();
  await expect
    .poll(() => source.evaluate((e: HTMLVideoElement) => e.currentTime))
    .toBeGreaterThan(0.25);
  await page
    .getByRole("region", { name: "Source monitor" })
    .getByRole("button", { name: "Pause preview" })
    .click();

  // Production read fixtures avoid starting inference. Media and the authored
  // movie remain real API data; this separately verifies the monitor projection.
  await page.route("**/api/production/selections?*", (route) =>
    route.fulfill({
      json: {
        current: {
          id: "selected-fixture",
          movie_id: data.movie.id,
          segment_id: data.first.id,
          candidate_id: "selected-candidate",
          asset_id: asset.id,
          authority: "user",
          basis: null,
          selected_at: "2026-09-30T12:00:00Z",
          superseded_at: null,
          revision: 1,
        },
        history: [],
        revision: 1,
      },
    }),
  );
  await page.route("**/api/production/operations?*", (route) =>
    route.fulfill({
      json: [
        {
          id: "operation-private",
          kind: "render",
          state: "succeeded",
          awaits_review: true,
          request: { cut_id: data.cut.id },
          budget_reserved: 1,
          budget_released_at: null,
          cancel_requested: false,
          review: null,
          error: null,
          created_at: "2026-09-30T12:00:00Z",
          updated_at: "2026-09-30T12:00:00Z",
          jobs: [{ seed: 999, integration: "private-technique" }],
          candidates: [
            {
              id: "candidate-private",
              job_id: "job-private",
              operation_id: "operation-private",
              asset_id: asset.id,
              artifact_id: null,
              output_key: "one",
              kind: "video",
              created_at: "2026-09-30T12:00:00Z",
            },
          ],
        },
      ],
    }),
  );
  await page.reload();
  await page.getByRole("button", { name: "Segment 02", exact: true }).click();
  await page
    .getByLabel("Program preview", { exact: true })
    .selectOption("candidate-private");
  const program = page.getByRole("region", { name: "Program monitor" });
  await expect(program).toContainText("Candidate 1");
  await expect(program).not.toContainText("999");
  await expect(program).not.toContainText("private");
  const player = program.locator("video");
  await program.getByRole("button", { name: "Play preview" }).click();
  await expect
    .poll(() => player.evaluate((e: HTMLVideoElement) => e.currentTime))
    .toBeGreaterThan(0.25);
  await program.getByRole("button", { name: "Pause preview" }).click();
  await page.getByRole("slider", { name: "Playhead", exact: true }).focus();
  await page.keyboard.press("Home");
  await expect
    .poll(() => player.evaluate((e: HTMLVideoElement) => e.currentTime))
    .toBeLessThan(0.1);
  await program.getByRole("button", { name: "Next frame" }).click();
  await expect(
    page.getByRole("slider", { name: "Playhead", exact: true }),
  ).toHaveAttribute("aria-valuenow", "121");
  await program.getByRole("button", { name: "Previous frame" }).click();
  await expect(
    page.getByRole("slider", { name: "Playhead", exact: true }),
  ).toHaveAttribute("aria-valuenow", "120");
  await program.getByRole("button", { name: "Mute preview" }).click();
  await expect
    .poll(() => player.evaluate((e: HTMLVideoElement) => e.muted))
    .toBe(true);
});
