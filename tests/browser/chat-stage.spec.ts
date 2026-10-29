import { expect, test, type Page, type TestInfo } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { seedProduction } from "./production-fixture";
import { openMovie } from "./direct-fixture";
import { scriptedAgent } from "./chat-fixture";

// ADR 0020 §8: the chat during the agent's stage build. A scripted model plans a build step and, once approved, builds
// a stage from the scene's take (filling off, so the browser service's fakes finish in seconds); the chat shows the
// build in Python's words, why the agent is quiet while it runs, and the way into the stage workbench once its v1
// waits for the user. The user's decision ends the step.

async function audit(page: Page, info: TestInfo, state: string, theme: string) {
  const file = `chat-stage-${theme}-${state}.png`;
  const screenshot = process.env.GAIAVFX_REVIEW_DIR
    ? path.join(process.env.GAIAVFX_REVIEW_DIR, file)
    : info.outputPath(file);
  await page.screenshot({ path: screenshot });
  await info.attach(file, { path: screenshot, contentType: "image/png" });
  const results = await new AxeBuilder({ page })
    .include('[aria-label="Movie conversation"]')
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(results.violations).toEqual([]);
}

for (const theme of ["dark", "light"] as const) {
  test(`the agent's stage build shows its progress, why it is quiet and Open stage; ${theme}`, async ({
    page,
    request,
  }, info) => {
    test.setTimeout(300_000);
    await page.emulateMedia({ colorScheme: theme });
    await page.addInitScript((value) => {
      localStorage.setItem(
        "gaiavfx-preferences",
        JSON.stringify({ state: { theme: value }, version: 1 }),
      );
    }, theme);
    const data = await seedProduction(request, true);
    await data.post(`/api/production/reviews/${data.review!.id}/select`, {
      candidate_id: data.review!.members[0].candidate_id,
      expected_revision: 0,
    });
    const agent = await scriptedAgent(request, data.headers);
    try {
      await agent.script([
        {
          tool: "propose_plan",
          thought: "",
          input: {
            goal: "Keep the courtyard for later scenes",
            steps: [
              {
                kind: "build",
                title: "Build the courtyard stage from this take",
                target: data.second.id,
                jobs: 6,
              },
            ],
            questions: [],
          },
        },
        {
          tool: "build_set_from_take",
          thought: "",
          input: {
            segment_id: data.second.id,
            shot: 0,
            name: "Courtyard stage",
            fill_unseen: false,
          },
        },
        {
          tool: "say",
          thought: "",
          input: {
            text: "The courtyard stage is built; its v1 waits for you in the stage workbench.",
          },
        },
        {
          tool: "say",
          thought: "",
          input: { text: "The courtyard is a stage now." },
        },
      ]);
      await openMovie(page, data.movie.title);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await page
        .getByLabel("Message to Gaia")
        .fill("Build a stage from the second scene's take");
      await page.getByLabel("Message to Gaia").press("Enter");
      const plan = page.getByRole("region", { name: "Plan revision 1" });
      await expect(plan).toBeVisible({ timeout: 20_000 });
      await expect(plan).toContainText(
        "Builds a new stage from this scene's take; you decide its first version",
      );
      await plan.getByRole("button", { name: "Approve", exact: true }).click();

      // While the build runs the agent is quiet, and the chat says why, in Python's words.
      const note = page.locator(".chat-build-note");
      await expect(note).toHaveText("Gaia answers when the build ends.", {
        timeout: 30_000,
      });
      await expect(
        page.locator('.chat-message .stage-build[data-state="started"]'),
      ).toContainText("Building Courtyard stage from First shot");
      // The plan's step follows the build in Python's words.
      await expect(plan.locator(".stage-build")).toContainText(
        /Building from this take|Fitting the splat|is built/,
      );

      // Built: the step waits for the user, with the way into the workbench in place of a review stub.
      const step = plan.locator(".stage-build");
      await expect(step).toContainText(
        "Courtyard stage is built: its v1 waits for you in the stage workbench.",
        { timeout: 120_000 },
      );
      await expect(note).toHaveCount(0);
      const system = page.locator(".chat-system").filter({
        hasText: "Courtyard stage is built",
      });
      await expect(system.locator(".set-receipt")).toContainText(
        "Stage · Courtyard stage",
      );
      // The agent's reply after the build (an article; the plan card is not one).
      await expect(
        page.locator("article.chat-message").filter({
          hasText: "The courtyard stage is built",
        }),
      ).toBeVisible({ timeout: 20_000 });
      // The agent's own words and the build's id never reach the transcript.
      const session = (
        await (
          await request.get(`/api/agent/sessions?movie_id=${data.movie.id}`, {
            headers: data.headers,
          })
        ).json()
      )[0];
      const transcript = page.locator(".chat-transcript");
      await expect(transcript).not.toContainText("accepting is theirs");
      await expect(transcript).not.toContainText(session.build.operation_id);
      await audit(page, info, "waiting", theme);

      await step.getByRole("button", { name: "Open stage" }).click();
      await expect(
        page.getByRole("main", { name: "Stage Courtyard stage" }),
      ).toBeVisible();

      // The user accepts v1 in the workbench (here through its route); the step completes and the agent hears it.
      const stage = await data.get<{
        id: string;
        versions: { id: string }[];
      }>(`/api/sets/${session.build.set_id}`);
      await data.post(
        `/api/sets/${stage.id}/versions/${stage.versions[0].id}/accept`,
        { expected_current_id: null, looks_fixed: [] },
      );
      await expect
        .poll(
          async () =>
            (
              await (
                await request.get(`/api/agent/sessions/${session.id}`, {
                  headers: data.headers,
                })
              ).json()
            ).session.plan.steps[0].status,
          { timeout: 60_000 },
        )
        .toBe("done");
    } finally {
      await agent.restore();
    }
  });
}
