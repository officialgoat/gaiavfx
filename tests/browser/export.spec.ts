import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import fs from "node:fs/promises";
import { seedProduction } from "./production-fixture";
import { openMovie } from "./direct-fixture";
import type { components } from "../../frontend/src/shared/api.generated";
type S = components["schemas"];

test("Export checks selections, confirms changed takes, assembles and opens the Library result", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  const data = await seedProduction(request, true);
  await openMovie(page, data.movie.title);
  await page
    .getByRole("navigation", { name: "Workspaces" })
    .getByRole("button", { name: "Export", exact: true })
    .click();
  const workspace = page.getByRole("main", { name: "Export workspace" });
  const sequence = workspace.getByRole("region", {
    name: "Selected takes for export",
  });
  const start = workspace.getByRole("button", {
    name: "Export movie",
    exact: true,
  });
  await expect(sequence.getByText("No selected take")).toHaveCount(2);
  await expect(start).toBeDisabled();
  await page.getByRole("button", { name: "File", exact: true }).click();
  await expect(
    page.getByRole("menuitem", { name: /Export movie/ }),
  ).toHaveAttribute("aria-disabled", "true");
  await page.keyboard.press("Escape");

  const chosen = await data.post<S["SelectionDTO"]>(
    `/api/production/reviews/${data.review!.id}/select`,
    {
      candidate_id: data.review!.members[0].candidate_id,
      expected_revision: 0,
    },
  );
  await workspace
    .getByRole("button", { name: "Refresh selected takes" })
    .click();
  await expect(sequence.getByText("No selected take")).toHaveCount(1);
  // Remove the deliberately unselected first segment through its real revision-checked command.
  const movie = await data.get<S["MovieDetailDTO"]>(
    `/api/direction/movies/${data.movie.id}`,
  );
  await data.post(`/api/direction/segments/${data.first.id}/remove`, {
    expected_revision: movie.revision,
  });
  const segment = await data.get<S["SegmentDTO"]>(
    `/api/direction/segments/${data.second.id}`,
  );
  await data.patch(`/api/direction/segments/${segment.id}`, {
    expected_revision: segment.revision,
    delivered_frames: 103,
  });
  await workspace
    .getByRole("button", { name: "Refresh selected takes" })
    .click();
  await expect(
    sequence.getByText("Changed take", { exact: true }),
  ).toBeVisible();
  await expect(
    sequence.getByText("Segment changed since this take."),
  ).toBeVisible();
  await expect(start).toBeDisabled();
  await expect(workspace.getByLabel("Include changed takes")).not.toBeChecked();
  await workspace.getByLabel("Include changed takes").check();
  await expect(start).toBeEnabled();
  await fs.mkdir(".cache/g3-export", { recursive: true });
  await page.screenshot({ path: ".cache/g3-export/ready-dark.png" });
  // Button colours fade for 150 ms; scan the settled theme, as the other specs do.
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.getByRole("button", { name: "Switch to light mode" }).click();
  await page.screenshot({ path: ".cache/g3-export/ready-light.png" });
  expect(
    (
      await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze()
    ).violations,
  ).toEqual([]);

  const posted = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/api/export/movies/${data.movie.id}`) &&
      r.request().method() === "POST",
  );
  await start.click();
  const response = await posted;
  expect(response.status()).toBe(201);
  const operation = (await response.json()) as S["OperationDTO"];
  expect(response.request().postDataJSON().allow_stale).toBe(true);
  await expect(
    workspace.getByRole("progressbar", { name: "Movie export progress" }),
  ).toBeVisible();
  await expect(
    workspace.getByRole("heading", { name: "Movie exported" }),
  ).toBeVisible({ timeout: 60_000 });
  const state = await data.get<S["MovieExportDTO"]>(
    `/api/export/movies/${data.movie.id}`,
  );
  expect(state.latest?.id).toBe(operation.id);
  expect(state.latest?.progress).toBe(1);
  const asset = await data.get<S["AssetDTO"]>(
    `/api/v1/assets/${state.latest!.asset_id}`,
  );
  const provenance = asset.provenance.find((p) => p.type === "movie_export")!;
  expect(provenance.selections).toEqual([
    expect.objectContaining({
      selection_id: chosen.id,
      asset_id: chosen.asset_id,
      frames: 103,
    }),
  ]);
  expect(provenance.stale_segments).toEqual([
    { segment_id: segment.id, reasons: ["Segment changed since this take."] },
  ]);
  await workspace
    .getByRole("button", { name: "Open exported movie in Program" })
    .click();
  const program = workspace.getByRole("region", { name: "Program monitor" });
  await expect(program.locator("video")).toBeVisible();
  await expect
    .poll(() =>
      program
        .locator("video")
        .evaluate((video: HTMLVideoElement) => video.readyState),
    )
    .toBeGreaterThanOrEqual(2);
  await page.screenshot({ path: ".cache/g3-export/result-light.png" });
  // A reload recovers durable delivery rather than forgetting the finished operation.
  await page.reload();
  await page
    .getByRole("navigation", { name: "Workspaces" })
    .getByRole("button", { name: "Export", exact: true })
    .click();
  await expect(
    workspace.getByRole("heading", { name: "Movie exported" }),
  ).toBeVisible();

  await workspace.getByLabel("Include changed takes").check();
  await start.click();
  await workspace
    .getByRole("button", { name: "Cancel export", exact: true })
    .click();
  await expect(
    workspace.getByRole("heading", { name: "Export cancelled" }),
  ).toBeVisible({ timeout: 60_000 });
  const cancelled = await data.get<S["MovieExportDTO"]>(
    `/api/export/movies/${data.movie.id}`,
  );
  expect(cancelled.latest?.asset_id).toBeNull();
});
