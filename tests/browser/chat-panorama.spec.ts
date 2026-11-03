import { expect, test, type Page, type TestInfo } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { seedProduction } from "./production-fixture";
import { openMovie } from "./direct-fixture";
import { scriptedAgent } from "./chat-fixture";

// Gaia and panoramas: a scripted model plans a prepare step titled for panoramas and, once approved, draws two of the
// scene's place (the browser service's fakes draw the synthetic room); the chat shows the attempt in Python's words
// with Review panoramas, which opens the attempt in Library > Stages, where only the user chooses.

async function audit(page: Page, info: TestInfo, name: string) {
  const file = `chat-panorama-${name}.png`;
  const shot = process.env.GAIAVFX_REVIEW_DIR
    ? path.join(process.env.GAIAVFX_REVIEW_DIR, file)
    : info.outputPath(file);
  await page.screenshot({ path: shot });
  await info.attach(file, { path: shot, contentType: "image/png" });
  const results = await new AxeBuilder({ page })
    .include('[aria-label="Movie conversation"]')
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(results.violations).toEqual([]);
}

for (const theme of ["dark", "light"] as const) {
  test(`Gaia draws panoramas the user then chooses in Stages; ${theme}`, async ({
    page,
    request,
  }, info) => {
    test.setTimeout(240_000);
    await page.emulateMedia({ colorScheme: theme });
    await page.addInitScript((value) => {
      localStorage.setItem(
        "gaiavfx-preferences",
        JSON.stringify({ state: { theme: value }, version: 1 }),
      );
    }, theme);
    const data = await seedProduction(request, true);
    const placeId = data.from.place_id as string;
    const agent = await scriptedAgent(request, data.headers);
    try {
      await agent.script([
        {
          tool: "propose_plan",
          thought: "",
          input: {
            goal: "Panoramas of the courtyard to build its stage from",
            steps: [
              {
                kind: "prepare",
                title: "Draw two panoramas of the courtyard",
                target: placeId,
                jobs: 4,
              },
            ],
            questions: [],
          },
        },
        {
          tool: "make_panorama",
          thought: "",
          input: {
            place_id: placeId,
            description: `A sunlit cobblestone courtyard with potted olive trees (${theme})`,
            landmarks: [{ what: "An iron railing", heading: 180 }],
            light: "Late morning sun",
          },
        },
        {
          tool: "say",
          thought: "",
          input: {
            text: "Two panoramas of the courtyard are drawn; choose one in Library > Stages.",
          },
        },
      ]);
      await openMovie(page, data.movie.title);
      await page
        .getByLabel("Message to Gaia")
        .fill("Draw two 360?? panoramas of the courtyard for a stage");
      await page.getByLabel("Message to Gaia").press("Enter");
      const plan = page.getByRole("region", { name: "Plan revision 1" });
      await expect(plan).toBeVisible({ timeout: 20_000 });
      await expect(plan).toContainText("Draw two panoramas of the courtyard");
      await plan.getByRole("button", { name: "Approve", exact: true }).click();

      const card = page.locator(".stage-build", {
        hasText: "Choose one in Library › Stages",
      });
      await expect(card).toBeVisible({ timeout: 60_000 });
      await expect(
        page.getByText("Two panoramas of the courtyard are drawn"),
      ).toBeVisible({ timeout: 60_000 });
      await audit(page, info, theme);

      // Review panoramas opens the attempt where the user, not Gaia, chooses.
      await card.getByRole("button", { name: "Review panoramas" }).click();
      await expect(
        page.getByRole("heading", { name: /A sunlit cobblestone courtyard/ }),
      ).toBeVisible();
      await expect(
        page.getByText("Choose the panorama to build a stage from."),
      ).toBeVisible({ timeout: 60_000 });
      await page.getByRole("button", { name: "Choose panorama A" }).click();
      await expect(page.locator(".panorama-chosen")).toHaveText(/Chosen/);
    } finally {
      await agent.restore();
    }
  });
}
