import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("explains the archive before offering a fresh library", async ({
  page,
}) => {
  const path = "X:/Films/My previous library.pre-rebuild-2026-09";
  await page.route("**/api/v1/library", (route) =>
    route.fulfill({
      json: {
        library: null,
        archived: {
          path,
          revision: "0022_agent_and_place_text",
          archived_at: "2026-09-30T12:00:00+00:00",
        },
      },
    }),
  );
  await page.goto("/");
  const notice = page
    .getByRole("status")
    .filter({ hasText: "previous library" });
  await expect(notice).toHaveText(
    `Your previous library was moved to ${path}. It stays readable by the earlier GAIAVFX build (pre-rebuild-2026-09).`,
  );
  const form = page.locator(".library-form");
  await expect(
    form.getByRole("button", { name: "Create library", exact: true }),
  ).toBeVisible();
  expect((await notice.boundingBox())!.y).toBeLessThan(
    (await form.boundingBox())!.y,
  );
});

test("creates and reopens a real local library", async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto("/");
  await page
    .getByLabel("Library name", { exact: true })
    .fill("Browser film library");
  await page
    .getByRole("button", { name: "Create library", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Start directing." }),
  ).toBeVisible({ timeout: 60_000 });
  await page.reload();
  await expect(page.locator(".project-title")).toHaveText(
    "Browser film library",
  );
  await page
    .locator(".workspace-tools")
    .getByRole("button", { name: "Library", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Library", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Import media", exact: true }),
  ).toBeVisible();
});

test("docked panels resize by keyboard and persist independently", async ({
  page,
}) => {
  await page.goto("/");
  const seam = page.getByRole("separator", {
    name: "Resize Project panel",
    exact: true,
  });
  await expect(seam).toHaveAttribute("aria-valuenow", "218");
  await seam.focus();
  await page.keyboard.press("ArrowRight");
  await expect(seam).toHaveAttribute("aria-valuenow", "228");
  await page.reload();
  await expect(seam).toHaveAttribute("aria-valuenow", "228");
  await page.getByRole("tab", { name: "Properties", exact: true }).click();
  await page.reload();
  await expect(
    page.getByRole("tab", { name: "Properties", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.getByRole("button", { name: "Prepare", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Prepare references", exact: true }),
  ).toBeVisible();
  await expect(seam).toHaveAttribute("aria-valuenow", "218");
  await page.getByRole("button", { name: "Direct", exact: true }).click();
  await expect(seam).toHaveAttribute("aria-valuenow", "228");
});

test("Library, Images, Characters and Wardrobe retain their shared shell", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .locator(".workspace-tools")
    .getByRole("button", { name: "Library", exact: true })
    .click();
  for (const workspace of ["Library", "Images", "Characters", "Wardrobe"]) {
    await page
      .getByRole("navigation", { name: "Library sections" })
      .getByRole("button", { name: workspace, exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: workspace, exact: true }),
    ).toBeVisible();
    await expect(page.getByLabel("Search library")).toBeVisible();
  }
  await page
    .getByRole("navigation", { name: "Library sections" })
    .getByRole("button", { name: "Library", exact: true })
    .click();
  await page.getByLabel("Search library").fill("unsaved search");
  await page
    .getByRole("button", { name: "Switch to light mode", exact: true })
    .click();
  await expect(page.getByLabel("Search library")).toHaveValue("unsaved search");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByRole("button", { name: "Jobs", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "Jobs", exact: true }),
  ).toContainText("No jobs yet.");
});

test("service failure offers recovery", async ({ page }) => {
  await page.route("**/api/v1/library", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Library unavailable in fixture" },
    }),
  );
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText(
    "Library unavailable in fixture",
  );
  await page.unroute("**/api/v1/library");
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.locator(".project-title")).toHaveText(
    "Browser film library",
  );
});

test("Library stays accessible in both themes and compact windows", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .locator(".workspace-tools")
    .getByRole("button", { name: "Library", exact: true })
    .click();
  for (const theme of ["dark", "light"]) {
    const toggle = page.getByRole("button", {
      name: `Switch to ${theme} mode`,
      exact: true,
    });
    if (await toggle.count()) await toggle.click();
    await page.emulateMedia({ reducedMotion: "reduce" });
    for (const width of [1440, 900, 720]) {
      await page.setViewportSize({ width, height: 900 });
      await expect
        .poll(() =>
          page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
        )
        .toBe(true);
    }
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(results.violations).toEqual([]);
  }
});
