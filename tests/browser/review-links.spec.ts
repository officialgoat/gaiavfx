import { expect, test } from "@playwright/test";
import { seedProduction } from "./production-fixture";
import { openMovie } from "./direct-fixture";
import type { components } from "../../frontend/src/shared/api.generated";
type S = components["schemas"];

test("chat review links open the exact older operation or review receipt", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  const data = await seedProduction(request, true);
  const session = await data.post<S["HarnessSessionDTO"]>(
    "/api/agent/sessions",
    { movie_id: data.movie.id },
  );
  const plan: S["AgentPlanDTO"] = {
    id: "review-link-plan",
    revision: 1,
    state: "executing",
    goal: "Review the courtyard views",
    budget_jobs: 0,
    note: "",
    proposed_turn: 1,
    revision_requested: false,
    steps: [
      {
        kind: "review",
        title: "Review earlier survey",
        target: data.cut.id,
        jobs: 0,
        status: "awaiting_user",
        review_of: data.survey.id,
      },
    ],
  };
  const turn: S["TurnDTO"] = {
    id: "render-review-receipt",
    ordinal: 2,
    role: "agent",
    state: "accepted",
    action: {
      tool: "read_candidates",
      input: { operation_id: data.render!.id },
    },
    result: null,
    receipts: [{ kind: "review_set", id: data.review!.id }],
    provenance: null,
    created_at: "2026-10-03",
    updated_at: "2026-10-03",
  };
  const state: S["SessionStateDTO"] = {
    session: {
      ...session,
      plan,
      activity: "waiting_human",
      state: "waiting_human",
    },
    turns: [turn],
  };
  // Only the transcript is scripted. Every target and review action uses the real API.
  await page.route(`**/api/agent/sessions/${session.id}?*`, (route) =>
    route.fulfill({ json: state }),
  );
  // An older operation may no longer be in the recent Jobs read. Resolve its own ID.
  await page.route("**/api/production/operations?*", async (route) => {
    const response = await route.fetch();
    const operations: S["OperationDTO"][] = await response.json();
    await route.fulfill({
      json: operations.filter(
        (o) => ![data.survey.id, data.render!.id].includes(o.id),
      ),
    });
  });
  await openMovie(page, data.movie.title);
  const planCard = page.getByRole("region", { name: "Plan revision 1" });
  const exactOperation = page.waitForResponse((response) =>
    response
      .url()
      .endsWith(`/api/production/operations/${data.survey.id}/summary`),
  );
  const exactViews = page.waitForResponse(
    (response) =>
      response
        .url()
        .endsWith(`/api/production/surveys/${data.survey.id}/review`) &&
      response.request().method() === "POST",
  );
  await planCard
    .getByRole("button", { name: "Open in Review", exact: true })
    .click();
  expect(await (await exactOperation).json()).toEqual({
    id: data.survey.id,
    kind: "survey",
  });
  expect((await (await exactViews).json()).id).toBe(data.views.id);
  await expect(
    page.getByRole("region", { name: "Source monitor" }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("region", { name: "Prepare reference flow" })
      .getByRole("button", { name: /Candidate A/ }),
  ).toBeVisible();
  const direct = page
    .getByRole("navigation", { name: "Workspaces" })
    .getByRole("button", { name: "Direct", exact: true });
  await direct.click();
  const tool = page.locator(".chat-tool");
  await tool.locator("summary").click();
  const exactReview = page.waitForResponse((response) =>
    response.url().endsWith(`/api/production/reviews/${data.review!.id}`),
  );
  await tool
    .getByRole("button", { name: "Open in Review", exact: true })
    .click();
  expect((await (await exactReview).json()).id).toBe(data.review!.id);
  const monitors = page.getByRole("region", {
    name: "Synchronized candidate monitors",
  });
  await expect(monitors).toBeVisible();
  await expect(monitors.locator("video")).toHaveCount(3);
  expect(await page.locator("body").innerHTML()).not.toContain(data.render!.id);
  const asset = await data.get<S["AssetDTO"]>(
    `/api/v1/assets/${data.review!.members[0].asset_id}`,
  );
  const searched = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === "/api/v1/assets" &&
      url.searchParams.get("q") === asset.name
    );
  });
  await page.getByRole("textbox", { name: "Search project" }).fill(asset.name);
  expect(
    (await (await searched).json()).items.some(
      (a: S["AssetDTO"]) => a.id === asset.id,
    ),
  ).toBeTruthy();
  // Survey and render outputs may share a filename. Match the media identity.
  for (const member of data.review!.members)
    await expect(
      page.locator(`.project-asset img[src*="/media/${member.asset_id}/"]`),
    ).toHaveCount(0);
  await direct.click();
  await tool.locator("summary").focus();
  await page.keyboard.press("Shift+F10");
  await page
    .getByRole("menuitem", { name: "Open in Review", exact: true })
    .click();
  await expect(monitors).toBeVisible();
});
