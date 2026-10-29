import { expect, test, type Page, type TestInfo } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { seedProduction } from "./production-fixture";
import { openMovie, seedDirect } from "./direct-fixture";
import { scriptedAgent } from "./chat-fixture";

async function audit(page: Page, info: TestInfo, state: string, theme: string) {
  // A restored plan can arrive before its transcript; capture a settled conversation, not that loading frame.
  await expect(page.locator(".chat-human")).toContainText(
    "Fill more of the courtyard stage",
  );
  await page
    .getByRole("region", { name: "Plan revision 1" })
    .scrollIntoViewIfNeeded();
  const file = `fill-more-${theme}-${state}.png`;
  const screenshot = process.env.GAIAVFX_REVIEW_DIR
    ? path.join(process.env.GAIAVFX_REVIEW_DIR, file)
    : info.outputPath(file);
  await page.screenshot({ path: screenshot });
  await info.attach(file, { path: screenshot, contentType: "image/png" });
  const result = await new AxeBuilder({ page })
    .include('[aria-label="Movie conversation"]')
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(result.violations).toEqual([]);
}

test("Fill more waits, shows its next version, and opens the stage in both themes", async ({
  page,
  request,
}, info) => {
  test.setTimeout(540_000);
  await page.emulateMedia({ reducedMotion: "reduce" });
  const data = await seedProduction(request, true);
  await data.post(`/api/production/reviews/${data.review!.id}/select`, {
    candidate_id: data.review!.members[0].candidate_id,
    expected_revision: 0,
  });
  await data.post("/api/production/set-seeds", {
    segment_id: data.second.id,
    shot: 0,
    name: "Courtyard to extend",
    use_for_place: true,
    fill_unseen: false,
  });
  let setId = "";
  await expect
    .poll(
      async () => {
        const stages =
          await data.get<{ id: string; name: string }[]>("/api/sets");
        setId = stages.find((s) => s.name === "Courtyard to extend")?.id ?? "";
        return setId;
      },
      { timeout: 120_000 },
    )
    .not.toBe("");
  const stage = await data.get<{ versions: { id: string }[] }>(
    `/api/sets/${setId}`,
  );
  await data.post(
    `/api/sets/${setId}/versions/${stage.versions[0].id}/accept`,
    {
      expected_current_id: null,
      looks_fixed: [],
    },
  );
  const agent = await scriptedAgent(request, data.headers);
  try {
    // The spec owns this fake-worker registration; other specs retain the missing-engine scenario.
    await data.post("/api/test/stage-repair-engines?enabled=true");
    await agent.script([
      {
        tool: "propose_plan",
        thought: "",
        input: {
          goal: "Fill more of the courtyard",
          steps: [
            {
              kind: "build",
              title: "Fill more of the courtyard stage",
              target: setId,
              jobs: 6,
            },
          ],
          questions: [],
        },
      },
      { tool: "fill_set_more", thought: "", input: { set_id: setId } },
      {
        tool: "say",
        thought: "",
        input: {
          text: "The new courtyard version is ready for your decision.",
        },
      },
      {
        tool: "say",
        thought: "",
        input: { text: "The earlier courtyard version stays accepted." },
      },
    ]);
    await openMovie(page, data.movie.title);
    await page
      .getByLabel("Message to Gaia")
      .fill("Fill more of the courtyard stage");
    await page.getByLabel("Message to Gaia").press("Enter");
    const plan = page.getByRole("region", { name: "Plan revision 1" });
    await expect(plan).toBeVisible({ timeout: 20_000 });
    // Capture the current misleading first-version wording separately from the lifecycle assertions below.
    await expect(plan).toContainText("Fill more of the courtyard stage");
    await audit(page, info, "proposed", "dark");
    await plan.getByRole("button", { name: "Approve", exact: true }).click();
    const note = page.locator(".chat-build-note");
    await expect(note).toHaveText("Gaia answers when the build ends.", {
      timeout: 30_000,
    });
    await expect(plan.locator(".stage-build")).toContainText(
      /Rebuilding|Filling|round/i,
    );
    await expect(
      page
        .locator("article.chat-message")
        .filter({ hasText: "The new courtyard version is ready" }),
    ).toHaveCount(0);
    for (const theme of ["dark", "light"] as const) {
      // Change the stored preference and reload, exercising persisted in-flight conversation state too.
      await page.evaluate((value) => {
        const saved = JSON.parse(
          localStorage.getItem("gaiavfx-preferences") ?? "{}",
        );
        localStorage.setItem(
          "gaiavfx-preferences",
          JSON.stringify({ ...saved, state: { ...saved.state, theme: value } }),
        );
      }, theme);
      await page.reload();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(note).toBeVisible();
      await audit(page, info, "running", theme);
    }
    const step = plan.locator(".stage-build");
    await expect(step).toContainText("v2 is built", { timeout: 300_000 });
    await expect(step).toContainText(
      "It waits for you in the stage workbench.",
    );
    await expect(note).toHaveCount(0);
    await expect(
      page
        .locator("article.chat-message")
        .filter({ hasText: "The new courtyard version is ready" }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(page.locator(".chat-transcript")).not.toContainText(
      "accepting is theirs",
    );
    for (const theme of ["dark", "light"] as const) {
      await page.evaluate((value) => {
        const saved = JSON.parse(
          localStorage.getItem("gaiavfx-preferences")!,
        );
        localStorage.setItem(
          "gaiavfx-preferences",
          JSON.stringify({ ...saved, state: { ...saved.state, theme: value } }),
        );
      }, theme);
      await page.reload();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(
        step.getByRole("button", { name: "Open stage" }),
      ).toBeVisible();
      await audit(page, info, "waiting", theme);
    }
    await step.getByRole("button", { name: "Open stage" }).click();
    await expect(
      page.getByRole("main", { name: "Stage Courtyard to extend" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "v2 ready · Review" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "v2 ready · Review" }).click();
    await expect(
      page.getByRole("button", { name: "Accept v2", exact: true }),
    ).toBeVisible();
    const result = await data.get<{
      current_version_id: string;
      versions: { id: string }[];
    }>(`/api/sets/${setId}`);
    expect(result.current_version_id).toBe(stage.versions[0].id);
    await data.post(
      `/api/sets/${setId}/versions/${result.versions[1].id}/discard`,
    );
    await expect
      .poll(
        async () => {
          const sessions = await data.get<
            { plan: { steps: { status: string }[] } }[]
          >(`/api/agent/sessions?movie_id=${data.movie.id}`);
          return sessions[0].plan.steps[0].status;
        },
        { timeout: 60_000 },
      )
      .toBe("skipped");
    expect(
      (await data.get<{ current_version_id: string }>(`/api/sets/${setId}`))
        .current_version_id,
    ).toBe(stage.versions[0].id);
  } finally {
    await agent.restore();
    await data.post("/api/test/stage-repair-engines?enabled=false");
  }
});

test("a proposed Fill more step describes a new version of an existing stage", async ({
  page,
  request,
}) => {
  // A response fixture isolates the plan-card copy; the preceding test exercises the real plan and build.
  const data = await seedDirect(request);
  const opened = await request.post("/api/agent/sessions", {
    headers: data.headers,
    data: { movie_id: data.movie.id },
  });
  expect(opened.ok()).toBeTruthy();
  const session = await opened.json();
  session.state = "waiting_human";
  session.activity = "awaiting_approval";
  session.plan = {
    id: "fill-plan",
    revision: 1,
    state: "proposed",
    goal: "Fill more of the existing courtyard stage",
    steps: [
      {
        kind: "build",
        title: "Fill more of Courtyard v1",
        target: "accepted-stage",
        jobs: 6,
        status: "pending",
        receipts: [],
        review_of: "",
      },
    ],
    budget_jobs: 6,
    questions: [],
    note: "",
    proposed_turn: 0,
    grant: null,
    revision_requested: false,
  };
  await page.route(`**/api/agent/sessions/${session.id}?*`, (route) =>
    route.fulfill({ json: { session, turns: [] } }),
  );
  await openMovie(page, data.movie.title);
  const plan = page.getByRole("region", { name: "Plan revision 1" });
  await expect(plan).toContainText("Fill more of Courtyard v1");
  // Astra's test pass found every pending build called a first-version build, Fill more included.
  await expect(plan).not.toContainText("you decide its first version");
  await expect(plan).toContainText(
    "Fills more of a stage you accepted; you decide its new version",
  );
});
