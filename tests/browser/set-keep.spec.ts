import { expect, test, type Page, type TestInfo } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { developmentPython } from "../../scripts/python.mjs";
import { seedProduction } from "./production-fixture";

// ADR 0020 §8: Keep this still. A stage built with filling from the scene's still shot; the browser service's fake
// bends the first round's first still, so consolidation sets it aside. The workbench says why, in Python's words,
// and Keep this still queues it for Run queued fixes, whose words say what the next version keeps.

type Keyframe = {
  id: string;
  key: string;
  label: string;
  role: string;
  dropped: string | null;
  can_keep: boolean;
};

async function audit(
  page: Page,
  info: TestInfo,
  theme: string,
  state = "queued",
) {
  const file = `set-keep-${theme}-${state}.png`;
  const screenshot = process.env.GAIAVFX_REVIEW_DIR
    ? path.join(process.env.GAIAVFX_REVIEW_DIR, file)
    : info.outputPath(file);
  await page.screenshot({ path: screenshot });
  await info.attach(file, { path: screenshot, contentType: "image/png" });
  const results = await new AxeBuilder({ page })
    .include(".set-workbench")
    .exclude(".set-viewer-canvas")
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(results.violations).toEqual([]);
}

test("a still filling set aside says why, and Keep this still queues it for the next version", async ({
  page,
  request,
}, info) => {
  test.setTimeout(420_000);
  // Audit settled theme colours, not the 150 ms button transition between themes.
  await page.emulateMedia({ reducedMotion: "reduce" });
  const data = await seedProduction(request, true);
  await data.post(`/api/production/reviews/${data.review!.id}/select`, {
    candidate_id: data.review!.members[0].candidate_id,
    expected_revision: 0,
  });
  // The scene's second shot stands still, so filling builds it.
  await data.post("/api/production/set-seeds", {
    segment_id: data.second.id,
    shot: 1,
    name: "Courtyard kept",
    use_for_place: false,
    fill_unseen: true,
  });
  let setId = "";
  await expect
    .poll(
      async () => {
        const stages =
          await data.get<{ id: string; name: string }[]>("/api/sets");
        setId = stages.find((s) => s.name === "Courtyard kept")?.id ?? "";
        return setId;
      },
      { timeout: 300_000, intervals: [1000, 2000, 5000] },
    )
    .not.toBe("");
  const state = await data.get<{ version: { keyframes: Keyframe[] } }>(
    `/api/sets/${setId}`,
  );
  const aside = state.version.keyframes.find((k) => k.key === "r1p0")!;
  expect(aside.role).toBe("appearance-only");
  expect(aside.can_keep).toBe(true);
  expect(aside.dropped).toMatch(
    /^Filling set this still aside: its depth disagreed with the stage by/,
  );

  for (const theme of ["dark", "light"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    await page.addInitScript((value) => {
      localStorage.setItem(
        "gaiavfx-preferences",
        JSON.stringify({ state: { theme: value }, version: 1 }),
      );
    }, theme);
    await page.goto("/");
    await page.getByRole("button", { name: "Library", exact: true }).click();
    await page.getByRole("button", { name: "Stages", exact: true }).click();
    await page
      .getByRole("button", { name: /Courtyard kept/ })
      .first()
      .click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await page.getByRole("tab", { name: "Keyframes" }).click();
    await page
      .getByRole("tabpanel", { name: "Keyframes" })
      .getByRole("button", { name: new RegExp(`^${aside.label}\\b`) })
      .click();
    const inspector = page.getByRole("complementary", {
      name: "Inspector and chat",
    });
    await expect(inspector).toContainText(aside.dropped!);
    const keep = inspector.getByRole("button", { name: "Keep this still" });
    if (theme === "dark") {
      await keep.click();
      await expect(
        inspector.getByRole("button", { name: "Keep queued" }),
      ).toBeDisabled();
    } else {
      await expect(
        inspector.getByRole("button", { name: "Keep queued" }),
      ).toBeVisible();
    }
    await audit(page, info, theme);
  }
  const queued = await data.get<{
    run: { words: string };
    fixes: { id: string; state: string }[];
  }>(`/api/sets/${setId}`);
  expect(queued.run.words).toContain(`Rebuilds v1 keeping ${aside.label}`);

  // A stale workbench must show Python's refusal if its geometry disappeared after the card was loaded.
  const fix = queued.fixes.find((f) => f.state === "queued")!;
  const cancelled = await request.delete(`/api/sets/${setId}/fixes/${fix.id}`, {
    headers: data.headers,
  });
  expect(cancelled.ok()).toBeTruthy();
  await page.reload();
  await page.getByRole("tab", { name: "Keyframes" }).click();
  await page
    .getByRole("tabpanel", { name: "Keyframes" })
    .getByRole("button", { name: new RegExp(`^${aside.label}\\b`) })
    .click();
  const keep = page.getByRole("button", {
    name: "Keep this still",
    exact: true,
  });
  await expect(keep).toBeEnabled();
  execFileSync(
    developmentPython(path.resolve(".")),
    [
      "tests/browser/stage-evidence.py",
      process.env.GAIAVFX_TEST_LIBRARY!,
      setId,
      aside.key,
    ],
    { encoding: "utf8", windowsHide: true },
  );
  await keep.click();
  const refusal = page
    .getByRole("alert")
    .filter({ hasText: "geometry filling measured" });
  await expect(refusal).toContainText(
    `The geometry filling measured for ${aside.label} is no longer in the Library, so it cannot be kept.`,
  );
  for (const theme of ["dark", "light"] as const) {
    const toggle = page.getByRole("button", {
      name: `Switch to ${theme} mode`,
      exact: true,
    });
    if (await toggle.count()) await toggle.click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await expect(refusal).toBeVisible();
    await audit(page, info, theme, "missing-geometry");
  }
  const refused = await data.get<{
    fixes: { state: string }[];
    version: { keyframes: Keyframe[] };
  }>(`/api/sets/${setId}`);
  expect(refused.fixes.some((f) => f.state === "queued")).toBe(false);
  expect(
    refused.version.keyframes.find((k) => k.id === aside.id)!.can_keep,
  ).toBe(false);
});
