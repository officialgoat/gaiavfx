import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { seedDirect, openMovie } from "./direct-fixture";
import { seedChat } from "./chat-fixture";
import { seedProduction } from "./production-fixture";
import type { components } from "../../frontend/src/shared/api.generated";
type S = components["schemas"];

test("place sound saves in Properties, refuses people inline, and Undo restores it", async ({
  page,
  request,
}) => {
  const data = await seedDirect(request);
  await openMovie(page, data.movie.title);
  await page.getByRole("button", { name: /^Places/ }).click();
  await page
    .locator(".project-record")
    .filter({ hasText: "Arcade courtyard" })
    .last()
    .click();
  await page.getByRole("tab", { name: "Properties", exact: true }).click();
  const form = page.getByRole("form", { name: "Edit place Arcade courtyard" });
  await form
    .getByLabel("Description", { exact: true })
    .fill("Empty courtyard behind the iron railing.");
  await form
    .getByLabel("Sound", { exact: true })
    .fill("Arin walking in the courtyard.");
  await form
    .getByLabel("Overhead view")
    .fill("Straight down over the roof and gallery.");
  await form.getByRole("button", { name: "Save place" }).click();
  await expect(form.getByRole("alert")).toContainText(
    "Describe only the empty place",
  );
  await form
    .getByLabel("Sound", { exact: true })
    .fill("Wind in the trees and a distant fountain.");
  await form.getByRole("button", { name: "Save place" }).click();
  await expect(page.getByText("Place saved", { exact: true })).toBeVisible();
  await expect(form.getByRole("alert")).toHaveCount(0);
  await page.screenshot({ path: ".cache/u3/place-editor-dark.png" });
  await page.getByRole("button", { name: "Switch to light mode" }).click();
  await page.screenshot({ path: ".cache/u3/place-editor-light.png" });
  await page.getByRole("button", { name: "Switch to dark mode" }).click();
  await page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Edit", exact: true })
    .click();
  await page.getByRole("menuitem", { name: /^Undo/ }).click();
  await expect(form.getByLabel("Sound", { exact: true })).toHaveValue("");
  await expect(form.getByLabel("Description", { exact: true })).toHaveValue("");
  await expect(form.getByLabel("Overhead view")).toHaveValue("");
});

test("empty plate is fitted for review; rejecting wrong views resumes the agent's survey step", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  const data = await seedChat(request);
  const get = async <T>(url: string): Promise<T> => {
    const response = await request.get(url, { headers: data.headers });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  };
  const patch = async (url: string, body: unknown) => {
    const response = await request.patch(url, {
      headers: data.headers,
      data: body,
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  };
  const finished = async (id: string) => {
    await expect
      .poll(
        async () =>
          (await get<S["OperationDTO"]>(`/api/production/operations/${id}`))
            .state,
        { timeout: 60_000 },
      )
      .toBe("succeeded");
  };
  const act = (tool: string, input: object) => ({ thought: "", tool, input });
  const note =
    "Wrong reverse: look across the railing from inside the gallery.";
  try {
    await patch(`/api/direction/places/${data.from.place_id}`, {
      movie_id: data.movie.id,
      expected_revision: 1,
      description: "Empty courtyard with stone arches and an iron railing",
      sound: "Wind in the trees",
      overhead: "Straight down over the courtyard and gallery roof",
    });
    for (const angle of [data.from, data.to])
      await patch(`/api/direction/angles/${angle.id}`, {
        movie_id: data.movie.id,
        expected_revision: angle.revision,
        name: angle.name,
        description:
          angle.id === data.from.id
            ? "Along the courtyard railing"
            : "Looking down the gallery",
      });
    await patch(`/api/direction/cuts/${data.cut.id}`, {
      expected_revision: data.cut.revision,
      insertion: {
        who: "Arin",
        looks: "plaid shirt",
        pronouns: "he",
        facing: "toward_camera",
        plate: "empty courtyard",
        keep: "walls",
        where: "by the railing",
        size: "one third",
        behind: "the arcade",
      },
    });
    const assets = await get<{ items: S["AssetDTO"][] }>(
      "/api/v1/assets?limit=100",
    );
    const source = assets.items.find((a) => a.name === "Courtyard.jpg")!;
    expect(source).toBeTruthy();
    await openMovie(page, data.movie.title);
    await page.getByRole("button", { name: /Cut f36/ }).click();
    await page
      .getByRole("navigation", { name: "Workspaces" })
      .getByRole("button", { name: "Prepare", exact: true })
      .click();
    const flow = page.getByRole("region", { name: "Prepare reference flow" });
    await flow.getByRole("button", { name: /^Prepare plate/ }).click();
    const form = page.getByRole("dialog", {
      name: "Prepare plate",
      exact: true,
    });
    await form.getByLabel("Source image or video").selectOption(source.id);
    await form.getByLabel("This image is already empty").check();
    await expect(form.getByLabel("Subject to remove")).toHaveCount(0);
    await expect(
      form.getByLabel("Seeds (one per candidate) (required)"),
    ).toHaveCount(0);
    await page.screenshot({ path: ".cache/u3/empty-plate.png" });
    const started = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/production/plates") &&
        r.request().method() === "POST",
    );
    await form
      .getByRole("button", { name: "Prepare plate", exact: true })
      .click();
    const response = await started;
    expect(response.status()).toBe(201);
    expect(response.request().postDataJSON()).toMatchObject({
      empty: true,
      source_asset: source.id,
    });
    const plate: S["OperationDTO"] = await response.json();
    expect(plate.budget_reserved).toBe(0);
    expect(plate.jobs.map((j) => j.step)).toEqual(["fit_frame"]);
    await finished(plate.id);
    await flow
      .getByRole("region", { name: "Plate", exact: true })
      .getByRole("button", { name: "Review in Source" })
      .click();
    await expect(
      page.getByRole("region", { name: "Source monitor" }).locator("img"),
    ).toBeVisible();
    const plateReview = await get<S["ReviewDTO"]>(
      `/api/production/reviews/${plate.id}`,
    );
    expect(plateReview.purpose).toBe("plate");
    const image = await get<S["AssetDTO"]>(
      `/api/v1/assets/${plateReview.members[0].asset_id}`,
    );
    expect(image.info).toMatchObject({ width: 768, height: 448 });
    expect(
      (await get<S["PlaceDTO"]>(`/api/direction/places/${data.from.place_id}`))
        .plate_asset_id,
    ).toBeNull();
    await flow
      .getByRole("button", { name: "Approve reference", exact: true })
      .click();
    await expect(
      flow.getByRole("button", { name: /Candidate A.*Approved/ }),
    ).toBeVisible();
    await data.script([
      act("propose_plan", {
        goal: "Survey the reverse for review",
        steps: [
          {
            kind: "prepare",
            title: "Survey the place views",
            target: data.cut.id,
            jobs: 1,
          },
        ],
      }),
    ]);
    await page.getByRole("tab", { name: "Chat", exact: true }).click();
    await page
      .getByLabel("Message to Gaia")
      .fill(
        "Survey the reverse for review; ask before spending on a correction.",
      );
    await page.getByLabel("Message to Gaia").press("Enter");
    await expect(
      page.getByRole("button", { name: "Approve", exact: true }),
    ).toBeEnabled({ timeout: 20_000 });
    await data.script([
      act("prepare_cut", { cut_id: data.cut.id, seed: 311 }),
      act("say", { text: "Review the survey views in the Review workspace." }),
    ]);
    await page.getByRole("button", { name: "Approve", exact: true }).click();
    await expect
      .poll(async () => (await data.session()).plan?.steps[0].status, {
        timeout: 60_000,
      })
      .toBe("awaiting_user");
    const waiting = await data.session();
    const oldSurvey = waiting.plan.steps[0].review_of;
    await flow
      .getByRole("button", { name: "Reference flow", exact: true })
      .click();
    await flow
      .getByRole("region", { name: "Views", exact: true })
      .getByRole("button", { name: "Review in Source", exact: true })
      .click();
    await expect(
      flow.getByRole("heading", { name: "Survey views" }),
    ).toBeVisible();
    await flow.getByRole("button", { name: "Reject…", exact: true }).click();
    const rejection = flow.getByRole("form", {
      name: "Reject reference candidates",
    });
    await expect(
      rejection.getByRole("button", { name: "Reject candidates" }),
    ).toBeDisabled();
    await rejection.getByLabel("Rejection reason (required)").fill("   ");
    await expect(
      rejection.getByRole("button", { name: "Reject candidates" }),
    ).toBeDisabled();
    await rejection.getByLabel("Rejection reason (required)").fill(note);
    for (const viewport of [
      { width: 1440, height: 900 },
      { width: 1280, height: 800 },
    ]) {
      await page.setViewportSize(viewport);
      for (const control of [
        rejection.getByLabel("Rejection reason (required)"),
        rejection.getByText("Reject this set of candidates.", { exact: false }),
        rejection.getByRole("button", { name: "Reject candidates" }),
        rejection.getByRole("button", { name: "Cancel", exact: true }),
      ])
        await expect(control).toBeInViewport({ ratio: 1 });
      expect(
        await page.evaluate(() => ({ x: window.scrollX, y: window.scrollY })),
      ).toEqual({ x: 0, y: 0 });
      expect(
        (
          await new AxeBuilder({ page })
            .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
            .analyze()
        ).violations,
      ).toEqual([]);
      await page.screenshot({
        path: `.cache/u4/rejection-${viewport.width}.png`,
      });
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({ path: ".cache/u3/reject-views.png" });
    await expect(flow.getByText(/\bseed\b/i)).toHaveCount(0);
    await patch(`/api/direction/angles/${data.to.id}`, {
      movie_id: data.movie.id,
      expected_revision: 2,
      name: data.to.name,
      description:
        "From inside the gallery, looking across the railing into the courtyard",
    });
    await data.script([
      act("propose_plan", {
        goal: "Survey the reverse for review",
        steps: [
          {
            kind: "prepare",
            title: "Survey the corrected place views",
            target: data.cut.id,
            jobs: 1,
          },
        ],
      }),
    ]);
    await rejection.getByRole("button", { name: "Reject candidates" }).click();
    await expect(
      flow.getByText(`Rejected: ${note}`, { exact: true }),
    ).toBeVisible();
    // Rejection resumes the survey step, but its first job is already spent.
    // Approve the correction's exact cost before submitting the replacement.
    await page.getByRole("tab", { name: "Chat", exact: true }).click();
    const correction = page.getByRole("region", { name: "Plan revision 2" });
    await expect(
      correction.getByRole("button", { name: "Approve", exact: true }),
    ).toBeEnabled({ timeout: 20_000 });
    await data.script([
      act("prepare_cut", { cut_id: data.cut.id, seed: 312 }),
      act("say", {
        text: "Review the corrected survey views in the Review workspace.",
      }),
    ]);
    await correction
      .getByRole("button", { name: "Approve", exact: true })
      .click();
    await expect
      .poll(async () => (await data.session()).plan?.steps[0].review_of, {
        timeout: 60_000,
      })
      .not.toBe(oldSurvey);
    const resumed = await data.session();
    await expect
      .poll(async () => (await data.session()).plan?.steps[0].status, {
        timeout: 60_000,
      })
      .toBe("awaiting_user");
    const state = await get<S["SessionStateDTO"]>(
      `/api/agent/sessions/${resumed.id}`,
    );
    expect(
      state.turns.some((t) =>
        String(t.action?.message ?? "").includes(
          `The user rejected the view candidates: '${note}'; prepare again with corrected inputs or ask`,
        ),
      ),
    ).toBeTruthy();
    const newer = await get<S["OperationDTO"]>(
      `/api/production/operations/${(await data.session()).plan.steps[0].review_of}`,
    );
    expect(newer.kind).toBe("survey");
    expect(newer.request.seed).toBe(312);
    const rejected = await get<S["ReviewDTO"]>(
      `/api/production/reviews/${oldSurvey}`,
    );
    expect(rejected.rejection?.note).toBe(note);
    expect(rejected.ranking.tiers).toEqual([]);
    const evidence = await get<S["EvidenceDTO"][]>(
      `/api/direction/angles/${data.to.id}/evidence`,
    );
    expect(evidence.every((e) => !e.approved)).toBeTruthy();
  } finally {
    const session = await data.session();
    if (session)
      await request.post(`/api/agent/sessions/${session.id}/cancel`, {
        headers: data.headers,
      });
    await data.restore();
  }
});

test("pin height explains the fraction and shows the server refusal inline", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  const data = await seedProduction(request);
  for (const member of data.views.members)
    await data.post("/api/production/views/approve", {
      candidate_id: member.candidate_id,
    });
  await openMovie(page, data.movie.title);
  await page.getByRole("button", { name: /Cut f36/ }).click();
  await page
    .getByRole("navigation", { name: "Workspaces" })
    .getByRole("button", { name: "Prepare", exact: true })
    .click();
  await page.getByRole("button", { name: /^Prepare pin/ }).click();
  const form = page.getByRole("dialog", { name: "Prepare pin", exact: true });
  await form
    .getByLabel("Subject reference (required)")
    .selectOption(data.asset.id);
  for (const [label, value] of [
    ["Subject", "Arin"],
    ["Appearance", "plaid shirt"],
    ["Target place", "empty courtyard"],
    ["Keep unchanged", "walls"],
    ["Position", "by the railing"],
    ["Behind the subject", "the arcade"],
    ["Subject height (fraction of the image, e.g. one third)", "full body"],
  ])
    await form.getByRole("textbox", { name: label, exact: true }).fill(value);
  await form.getByRole("button", { name: "Prepare pin", exact: true }).click();
  await expect(form.getByRole("alert")).toContainText("fraction");
});
