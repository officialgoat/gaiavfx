import { expect, test, type Locator } from "@playwright/test";
import fs from "node:fs/promises";
import { seedProduction } from "./production-fixture";
import { openMovie } from "./direct-fixture";
import AxeBuilder from "@axe-core/playwright";

async function loadedCandidates(comparison: Locator) {
  await expect
    .poll(() =>
      comparison
        .locator("video")
        .evaluateAll(
          (videos: HTMLVideoElement[]) =>
            videos.length === 3 &&
            videos.every((v) => !!v.currentSrc && v.readyState >= 2),
        ),
    )
    .toBe(true);
}

test.describe("Prepare and Review through real APIs and fake Comfy", () => {
  test.use({ reducedMotion: "reduce" });
  test.setTimeout(180_000);
  test("survey approval by button and keyboard, rejected attempts by context menu", async ({
    page,
    request,
  }) => {
    const data = await seedProduction(request);
    await openMovie(page, data.movie.title);
    await page.getByRole("button", { name: /Cut f36/ }).click();
    await page
      .getByRole("navigation", { name: "Workspaces" })
      .getByRole("button", { name: "Prepare", exact: true })
      .click();
    const flow = page.getByRole("region", { name: "Prepare reference flow" });
    await expect(flow).toBeVisible();
    await expect
      .poll(() =>
        flow
          .getByRole("region", { name: "Views", exact: true })
          .locator(".prepare-image img")
          .evaluate((img: HTMLImageElement) => img.naturalWidth),
      )
      .toBeGreaterThan(0);
    await fs.mkdir(".cache/s6-build", { recursive: true });
    await page.screenshot({ path: ".cache/s6-build/prepare-flow-dark.png" });
    await page.getByRole("button", { name: "Switch to light mode" }).click();
    await page.screenshot({ path: ".cache/s6-build/prepare-flow-light.png" });
    await page.getByRole("button", { name: "Switch to dark mode" }).click();
    await flow
      .getByRole("region", { name: "Views", exact: true })
      .getByRole("button", { name: "Review in Source" })
      .click();
    await expect(
      page.getByRole("region", { name: "Source monitor" }),
    ).toBeVisible();
    await expect
      .poll(() =>
        page
          .getByRole("region", { name: "Source monitor" })
          .locator("img")
          .evaluate(
            (img: HTMLImageElement) => img.complete && img.naturalWidth > 0,
          ),
      )
      .toBe(true);
    await page
      .getByRole("button", { name: "Approve reference", exact: true })
      .click();
    await expect(
      page.getByText("Reference approved", { exact: true }),
    ).toBeVisible();
    await expect(
      flow.getByRole("button", { name: /Candidate A.*Approved/ }),
    ).toBeVisible();
    const source = page.getByRole("region", { name: "Source monitor" });
    await flow.getByRole("button", { name: /Candidate B/ }).click();
    await source.focus();
    await page.keyboard.press("Control+Shift+a");
    await expect(
      flow.getByRole("button", { name: /Candidate B.*Approved/ }),
    ).toBeVisible();
    await flow.getByRole("button", { name: /Candidate C/ }).click();
    await source.locator(".monitor-well").click({ button: "right" });
    await page.getByRole("menuitem", { name: /^Reject/ }).click();
    await flow
      .getByLabel("Rejection reason (required)")
      .fill("Railings drift in this view.");
    await flow
      .getByRole("button", { name: "Reject candidates", exact: true })
      .click();
    await expect(
      flow.getByRole("button", { name: /Candidate C.*Rejected/ }),
    ).toBeVisible();
    const judged = await data.get<any>(
      `/api/production/reviews/${data.views.id}`,
    );
    expect(judged.rejection.note).toBe("Railings drift in this view.");
    await page.getByRole("button", { name: "Segment 01", exact: true }).click();
    await expect(source).toHaveCount(0);
    await page
      .getByRole("button", { name: "Review", exact: true })
      .first()
      .click();
    await expect(
      page.getByRole("menuitem", { name: /Approve reference/ }),
    ).toHaveAttribute("aria-disabled", "true");
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: /Cut f36/ }).click();
    await expect(source).toBeVisible();
    await fs.mkdir(".cache/s6-build", { recursive: true });
    await flow
      .getByText("Rejected: Railings drift in this view.")
      .scrollIntoViewIfNeeded();
    await page.screenshot({ path: ".cache/s6-build/prepare-source-dark.png" });
    expect(
      (
        await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
          .analyze()
      ).violations,
    ).toEqual([]);
    const layout = await data.get<{ tiles: (string | number[])[] }>(
      `/api/production/places/${data.from.place_id}/sheet-tiles`,
    );
    expect(
      layout.tiles.filter((tile) => typeof tile === "string"),
    ).toHaveLength(2);
    expect(layout.tiles.filter(Array.isArray)).toHaveLength(1);
    await flow.getByRole("button", { name: "Reference flow" }).click();
    await flow.getByRole("button", { name: /^Assemble sheet/ }).click();
    const assembly = page.getByRole("dialog", {
      name: "Assemble sheet",
      exact: true,
    });
    await expect(
      assembly
        .getByRole("list", { name: "New sheet panels" })
        .getByRole("listitem"),
    ).toHaveCount(3);
    await expect(
      assembly.getByText("Blank panel", { exact: true }),
    ).toBeVisible();
    await page.screenshot({ path: ".cache/s6-build/prepare-sheet-dark.png" });
    const started = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/production/sheets") &&
        response.request().method() === "POST",
    );
    await assembly
      .getByRole("button", { name: "Assemble sheet", exact: true })
      .click();
    const response = await started;
    expect(response.ok(), await response.text()).toBeTruthy();
    expect(response.request().postDataJSON().tiles).toEqual(layout.tiles);
    await data.finished((await response.json()).id);
    await expect(assembly).not.toBeVisible();
    await expect(
      flow.getByRole("region", { name: "Sheet", exact: true }),
    ).toContainText("Assembled");
    // A filmmaker can omit padding without changing the approved views or the
    // earlier immutable sheet. This is real assembly, not simulated inference.
    const original = await response.json();
    await flow.getByRole("button", { name: /^Assemble sheet/ }).click();
    const includeEmpty = assembly.getByLabel("Include empty panels");
    await expect(includeEmpty).toBeChecked();
    for (let i = 0; i < 3; i++) {
      await includeEmpty.uncheck();
      await expect(
        assembly.getByText("Blank panel", { exact: true }),
      ).toHaveCount(0);
      await includeEmpty.check();
      await expect(
        assembly.getByText("Blank panel", { exact: true }),
      ).toBeVisible();
    }
    await includeEmpty.uncheck();
    const unpaddedStarted = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/production/sheets") &&
        r.request().method() === "POST",
    );
    await assembly
      .getByRole("button", { name: "Assemble sheet", exact: true })
      .click();
    const unpaddedResponse = await unpaddedStarted;
    expect(unpaddedResponse.ok(), await unpaddedResponse.text()).toBeTruthy();
    const unpadded = await unpaddedResponse.json();
    expect(unpaddedResponse.request().postDataJSON().tiles).toEqual(
      layout.tiles.filter((tile) => typeof tile === "string"),
    );
    expect(unpadded.id).not.toBe(original.id);
    await data.finished(unpadded.id);
    expect(
      (await data.get<any>(`/api/production/operations/${original.id}`)).request
        .tiles,
    ).toEqual(original.request.tiles);
    await page.reload();
    expect(
      (await data.get<any>(`/api/production/operations/${unpadded.id}`)).request
        .tiles,
    ).toEqual(unpadded.request.tiles);
  });

  test("blind tie ranking, synchronized playback, reveal, selection and stale take", async ({
    page,
    request,
  }) => {
    const data = await seedProduction(request, true);
    expect(new Set(data.review!.members.map((m) => m.asset_id)).size).toBe(3);
    const delayed = data.review!.members[2].asset_id;
    let release!: () => void;
    const loaded = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(`**/api/v1/assets/${delayed}`, async (route) => {
      await loaded;
      await route.continue();
    });
    await openMovie(page, data.movie.title);
    await page.getByRole("button", { name: /Cut f36/ }).click();
    await page.keyboard.press("Control+Shift+r");
    const comparison = page.getByRole("region", {
      name: "Synchronized candidate monitors",
    });
    await expect(comparison).toBeVisible();
    await expect(comparison.locator("video")).toHaveCount(2);
    await expect(
      comparison.getByRole("button", { name: "Play all candidates" }),
    ).toBeDisabled();
    release();
    await expect(comparison.locator("video")).toHaveCount(3);
    const reveal = comparison.getByRole("button", {
      name: "Reveal",
      exact: true,
    });
    await expect(reveal).toBeDisabled();
    const before = await page.locator("body").innerHTML();
    for (const forbidden of [
      data.render!.id,
      "760001",
      "760002",
      "760003",
      "Revealed details",
    ])
      expect(before).not.toContain(forbidden);
    await expect(
      page
        .getByRole("region", { name: "Candidate A prescreen" })
        .getByText("Not checked", { exact: true })
        .first(),
    ).toBeVisible();
    await expect(
      comparison.getByRole("button", { name: "Play all candidates" }),
    ).toBeEnabled();
    await comparison
      .getByRole("button", { name: "Play all candidates" })
      .click();
    await expect
      .poll(async () =>
        comparison
          .locator("video")
          .evaluateAll((nodes: HTMLVideoElement[]) =>
            nodes.every((v) => v.currentTime > 0.1 && !v.paused),
          ),
      )
      .toBeTruthy();
    await comparison
      .getByRole("button", { name: "Pause all candidates" })
      .click();
    await expect
      .poll(async () =>
        comparison
          .locator("video")
          .evaluateAll(
            (nodes: HTMLVideoElement[]) =>
              Math.max(...nodes.map((v) => v.currentTime)) -
              Math.min(...nodes.map((v) => v.currentTime)),
          ),
      )
      .toBeLessThan(0.15);
    await page.getByRole("button", { name: "Rank candidate A" }).focus();
    await page.keyboard.press("Alt+ArrowUp");
    await page.getByRole("button", { name: "Rank candidate B" }).focus();
    await page.keyboard.press("Alt+t");
    await page.getByRole("button", { name: "Rank candidate C" }).focus();
    await page.keyboard.press("Alt+u");
    await expect(
      page.getByLabel("Rank 1, tied", { exact: true }),
    ).toBeVisible();
    // Drag breaks and restores the same tie without changing the monitor order.
    await page
      .getByRole("button", { name: "Rank candidate B" })
      .dragTo(page.getByText("Drop at the end", { exact: true }));
    await page
      .getByRole("button", { name: "Rank candidate B" })
      .dragTo(page.getByLabel("Rank 1", { exact: true }));
    await expect(
      page.getByLabel("Rank 1, tied", { exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Candidate C", exact: true })
      .click();
    const taxonomy = await data.get<any>("/api/production/review-tags");
    const applicable = taxonomy.tags.filter((tag: any) =>
      tag.applies_to.includes("segment_takes"),
    );
    await expect(page.locator("#review-tags input")).toHaveCount(
      applicable.length,
    );
    await page.getByLabel(applicable[0].label, { exact: true }).check();
    await page
      .getByLabel("Note for candidate C")
      .fill("Keep A and B tied; C drifts.");
    await page
      .getByRole("button", { name: "Run prescreen again", exact: true })
      .click();
    await expect(
      page.getByText("Prescreen updated", { exact: true }),
    ).toBeVisible();
    await page.getByLabel("Usable ranks").selectOption("1");
    await page
      .getByRole("button", { name: "Save ranking", exact: true })
      .click();
    await expect(reveal).toBeEnabled();
    await loadedCandidates(comparison);
    expect(await page.locator("body").innerHTML()).not.toContain(
      data.render!.id,
    );
    const saved = await data.get<any>(
      `/api/production/reviews/${data.review!.id}`,
    );
    expect(saved.ranking.tiers[0]).toHaveLength(2);
    expect(saved.ranking.usable_tiers).toBe(1);
    const third = data.review!.members.find((m) => m.letter === "C")!;
    expect(saved.ranking.tags[third.candidate_id]).toContain(applicable[0].id);
    expect(saved.ranking.notes[third.candidate_id]).toContain("C drifts");
    await fs.mkdir(".cache/s6-build", { recursive: true });
    await page.screenshot({ path: ".cache/s6-build/review-blind-dark.png" });
    expect(
      (
        await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
          .analyze()
      ).violations,
    ).toEqual([]);
    await page.getByRole("button", { name: "Switch to light mode" }).click();
    await page.screenshot({ path: ".cache/s6-build/review-blind-light.png" });
    expect(
      (
        await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
          .analyze()
      ).violations,
    ).toEqual([]);
    await page.getByRole("button", { name: "Switch to dark mode" }).click();
    await reveal.click();
    await loadedCandidates(comparison);
    await expect(
      page.getByRole("heading", { name: "Revealed details" }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Candidate B", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Select for the segment", exact: true })
      .click();
    await expect(
      page.getByText("Take selected for the segment", { exact: true }),
    ).toBeVisible();
    await loadedCandidates(comparison);
    const state = await data.get<any>(
      `/api/production/selections?movie_id=${data.movie.id}&segment_id=${data.second.id}`,
    );
    expect(state.current.candidate_id).toBe(
      data.review!.members.find((m) => m.letter === "B")!.candidate_id,
    );
    expect(state.current.stale).toBe(false);
    // Changed insertion wording keeps the original pin and requires a fresh approval.
    const cut = await data.get<any>(`/api/direction/cuts/${data.cut.id}`);
    await data.patch(`/api/direction/cuts/${data.cut.id}`, {
      expected_revision: cut.revision,
      frame: 37,
      insertion: {
        who: "Arin",
        looks: "plaid shirt",
        pronouns: "he",
        facing: "toward_camera",
        plate: "empty courtyard",
        keep: "walls",
        where: "by the entrance",
        size: "one third",
        behind: "the arcade",
      },
    });
    await page.reload();
    await page.getByRole("button", { name: /Cut f37/ }).click();
    await page
      .getByRole("navigation", { name: "Workspaces" })
      .getByRole("button", { name: "Prepare", exact: true })
      .click();
    const pinBinding = page
      .locator(".reference-binding")
      .filter({ has: page.getByText("Pin", { exact: true }) });
    await expect(pinBinding.getByText(/Changed since approval:/)).toBeVisible();
    const beforeOperations = await data.get<any[]>(
      "/api/production/operations?limit=100",
    );
    await pinBinding
      .getByRole("button", { name: "Re-approve", exact: true })
      .click();
    await expect(pinBinding.getByText(/Changed since approval:/)).toHaveCount(
      0,
    );
    expect(
      (await data.get<any[]>("/api/production/operations?limit=100")).length,
    ).toBe(beforeOperations.length);
    await pinBinding
      .getByLabel("pin suitability reason")
      .fill("The framing does not suit this cut.");
    await pinBinding
      .getByRole("button", { name: "Unsuitable for this cut", exact: true })
      .click();
    await expect(
      pinBinding.getByText("Unsuitable", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Render", exact: true }).click();
    await expect(
      page.getByRole("menuitem", { name: /Render segment/ }),
    ).toHaveAttribute("aria-disabled", "true");
    await page.keyboard.press("Escape");
    await pinBinding
      .getByRole("button", { name: "Suits this cut", exact: true })
      .click();
    const segment = await data.get<any>(
      `/api/direction/segments/${data.second.id}`,
    );
    await data.patch(`/api/direction/phases/${segment.phases[0].id}`, {
      expected_revision: segment.phases[0].revision,
      content: { ...segment.phases[0].content, description: "Arin stops." },
    });
    await page.reload();
    await page.getByRole("button", { name: /Cut f37/ }).click();
    await expect(page.locator(".take-stale")).toHaveText(
      "Changed since this take",
    );
    // Reload now restores Prepare; open Direct explicitly to inspect its monitor.
    await expect(
      page.getByRole("main", { name: "Prepare workspace" }),
    ).toBeVisible();
    await page
      .getByRole("navigation", { name: "Workspaces" })
      .getByRole("button", { name: "Direct", exact: true })
      .click();
    // Program shows the take under the playhead (usability B18): move it into Segment 02, the take's segment.
    await page.getByRole("slider", { name: "Playhead", exact: true }).focus();
    await page.keyboard.press("End");
    await expect(
      page
        .getByRole("region", { name: "Program monitor" })
        .getByText(/Changed since this take/),
    ).toBeVisible();
  });

  test("producer retry keeps the request key and opens the existing operation", async ({
    page,
    request,
  }) => {
    const data = await seedProduction(request);
    await openMovie(page, data.movie.title);
    await page.getByRole("button", { name: /Cut f36/ }).click();
    await page
      .getByRole("navigation", { name: "Workspaces" })
      .getByRole("button", { name: "Prepare", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Prepare plate…", exact: true })
      .click();
    const dialog = page.getByRole("dialog", {
      name: "Prepare plate",
      exact: true,
    });
    await dialog
      .getByLabel("Source image or video")
      .selectOption(data.asset.id);
    await dialog.getByLabel("Subject to remove").fill("Arin");
    await dialog.getByLabel("Background to preserve").fill("stone walls");
    const requests: any[] = [],
      statuses: number[] = [];
    await page.route("**/api/production/removals", async (route) => {
      requests.push(route.request().postDataJSON());
      const response = await route.fetch();
      statuses.push(response.status());
      if (requests.length === 1)
        await route.fulfill({
          status: 503,
          json: { detail: "Simulated lost response" },
        });
      else await route.fulfill({ response });
    });
    await dialog
      .getByRole("button", { name: "Prepare plate", exact: true })
      .click();
    await expect(dialog.getByRole("alert")).toContainText(
      "Simulated lost response",
    );
    await dialog
      .getByRole("button", { name: "Prepare plate", exact: true })
      .click();
    await expect(dialog).toBeHidden();
    expect(statuses).toEqual([201, 200]);
    expect(requests[0].request_key).toBe(requests[1].request_key);
    await expect(page.locator(".review-notice")).toContainText(
      "Follow progress in Jobs",
    );
  });
});
