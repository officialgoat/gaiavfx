import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import type { components } from "../../frontend/src/shared/api.generated";

type Status = components["schemas"]["SetupStatusDTO"];

function status(): Status {
  return {
    comfy_url: "http://127.0.0.1:8188",
    features: {
      core: {
        verdict: "missing",
        checks: [
          {
            feature: "core",
            item: "model:video.safetensors",
            status: "missing",
            detail:
              "Video model missing. Licence: minimax-h3-community-license-agreement",
            fix: "python -m gaiavfx.setup models download --comfy-dir ComfyUI --features core",
          },
          {
            feature: "core",
            item: "node:GaiaH3AudioVAE",
            status: "missing",
            detail:
              "The audio node is not registered. Restart ComfyUI after installing it.",
            fix: "python -m gaiavfx.setup nodes install --comfy-dir ComfyUI",
          },
        ],
      },
      "image-edit": { verdict: "ok", checks: [] },
      recast: { verdict: "ok", checks: [] },
      stages: { verdict: "ok", checks: [] },
      runtime: {
        verdict: "missing",
        checks: [
          {
            feature: "runtime",
            item: "Assistant",
            status: "missing",
            detail: "Assistant model is not installed.",
            fix: "python -m gaiavfx.setup assistant install",
          },
        ],
      },
    },
  };
}

for (const theme of ["dark", "light"] as const) {
  test(`Setup check: gaps, copy, retry and session dismissal in ${theme}`, async ({
    page,
    context,
  }) => {
    let result = status();
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.route("**/api/v1/setup/status**", (route) =>
      route.fulfill({ json: result }),
    );
    await page.goto("/");
    const switchTheme = page.getByRole("button", {
      name: `Switch to ${theme} mode`,
    });
    if (await switchTheme.count()) await switchTheme.click();
    const banner = page.locator(".setup-banner");
    await expect(banner).toContainText("1 model missing for Core video");
    await page.getByRole("button", { name: "Open setup check" }).click();
    const panel = page.getByRole("region", { name: "Setup check" });
    await expect(panel).toBeVisible();
    await expect(panel.getByText("Core video", { exact: true })).toBeVisible();
    await expect(panel.getByText("Image edit", { exact: true })).toBeVisible();
    await expect(panel.getByText("Recast", { exact: true })).toBeVisible();
    await expect(panel.getByText("Stages", { exact: true })).toBeVisible();
    await expect(
      panel.getByText("Local runtimes", { exact: true }),
    ).toBeVisible();
    const summary = panel.locator("summary").filter({ hasText: "Core video" });
    await summary.focus();
    await page.keyboard.press("Enter");
    const gaps = panel.getByRole("list", { name: "Core video setup gaps" });
    await expect(
      gaps.getByText("Video model missing.", { exact: false }),
    ).toBeVisible();
    await gaps
      .getByRole("button", { name: "Copy fix for video.safetensors" })
      .click();
    await expect(
      gaps.getByRole("status").filter({ hasText: "Copied" }),
    ).toBeVisible();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
      result.features.core.checks[0].fix,
    );
    const a11y = await new AxeBuilder({ page })
      .include(".setup-check")
      .analyze();
    expect(a11y.violations).toEqual([]);
    await page.screenshot({ path: `C:/gvt/b-setup-${theme}.png` });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: `C:/gvt/b-setup-${theme}-narrow.png` });
    expect(await panel.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(
      true,
    );
    await page.setViewportSize({ width: 1440, height: 900 });
    result = {
      ...result,
      features: { ...result.features, core: { verdict: "ok", checks: [] } },
    };
    const refreshed = page.waitForResponse(
      "**/api/v1/setup/status?refresh=true",
    );
    await panel.getByRole("button", { name: "Check again" }).click();
    await refreshed;
    await expect(summary).toContainText("Ready");
    await page.keyboard.press("Escape");
    await expect(banner).toHaveCount(0);
    result = status();
    await page.reload();
    await expect(banner).toBeVisible();
    await page.getByRole("button", { name: "Dismiss setup reminder" }).click();
    await page.reload();
    await expect(banner).toHaveCount(0);
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await expect(
      page.getByRole("region", { name: "Setup check" }),
    ).toBeVisible();
  });
}

test("Setup check recovers from a service error", async ({ page }) => {
  let fail = true;
  await page.route("**/api/v1/setup/status**", (route) =>
    fail
      ? route.fulfill({ status: 503, json: { detail: "Service restarting" } })
      : route.fulfill({ json: status() }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const panel = page.getByRole("region", { name: "Setup check" });
  await expect(panel.getByRole("alert")).toContainText(
    "Setup check could not finish",
  );
  fail = false;
  await panel.getByRole("button", { name: "Check again" }).click();
  await expect(panel.getByText("Core video", { exact: true })).toBeVisible();
  await expect(panel.getByRole("alert")).toHaveCount(0);
});
