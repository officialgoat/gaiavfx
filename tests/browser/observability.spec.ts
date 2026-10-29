import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import fs from "node:fs/promises";

test("Langfuse settings keep keys write-only, persist independently, and handle delivery states", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const section = page.getByRole("region", { name: "Langfuse tracing" });
  await expect(section.getByLabel("Enable hosted tracing")).not.toBeChecked();
  await section
    .getByLabel("Public key", { exact: true })
    .fill("pk-lf-browser-fixture");
  await section
    .getByLabel("Secret key", { exact: true })
    .fill("sk-lf-browser-fixture");
  await expect(
    section.getByRole("button", { name: "Test connection", exact: true }),
  ).toBeDisabled();
  const response = page.waitForResponse(
    (r) =>
      r.url().endsWith("/settings/telemetry") && r.request().method() === "PUT",
  );
  await section.getByRole("button", { name: "Save tracing settings" }).click();
  const payload = await (await response).text();
  expect(payload).not.toContain("sk-lf-browser-fixture");
  expect(payload).not.toContain('"public_key"');
  await expect(section.getByLabel("Secret key", { exact: true })).toHaveValue(
    "",
  );
  await expect(
    section.getByText("Tracing stopped. Pending evidence is preserved."),
  ).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    section.getByLabel("Public key", { exact: true }),
  ).toHaveAttribute("placeholder", /pk-lf-/);
  await expect(section.getByLabel("Secret key", { exact: true })).toHaveValue(
    "",
  );
  await page.route("**/settings/telemetry/test", (route) =>
    route.fulfill({
      status: 422,
      json: { detail: "The region and key do not match." },
    }),
  );
  await section
    .getByRole("button", { name: "Test connection", exact: true })
    .click();
  await expect(section.getByRole("alert")).toContainText(
    "region and key do not match",
  );
  await section.getByLabel("API key storage").selectOption("portable");
  await expect(section.getByLabel("Key-storage passphrase")).toBeVisible();
  await expect(
    section.getByRole("button", { name: "Unlock key storage" }),
  ).toBeDisabled();
  await section.getByLabel("API key storage").selectOption("session");
  await section.getByText("Storage & delivery", { exact: true }).click();
  await expect(section.getByLabel("Local limit (GiB)")).toHaveValue("10");
  await expect(
    section.getByText(/Pending evidence is preserved at the limit/),
  ).toBeVisible();
});

test("tracing status and operation failures remain usable in dark and compact light Settings", async ({
  page,
}) => {
  const counts = {
    enabled: true,
    connection: "configured",
    observations: { pending: 12, uncertain: 1 },
    media: { pending: 2 },
    bytes: 10485760,
    capture_error: null,
    last_delivery: 1789800000,
    errors: [
      {
        id: "pending-1",
        state: "uncertain",
        error: "Receipt remains uncertain. No duplicate observation was sent.",
      },
    ],
  };
  await page.route("**/settings/telemetry/status", (route) =>
    route.fulfill({ json: counts }),
  );
  await page.route("**/settings/telemetry", (route) =>
    route.fulfill({
      json: {
        enabled: true,
        base_url: "https://cloud.langfuse.com",
        environment: "development",
        storage_limit_gib: 10,
        credential_storage: "session",
        public_key_hint: "pk-lf-…test",
        has_secret: true,
        project_id: "browser-fixture",
      },
    }),
  );
  await page.route("**/settings/telemetry/retry", (route) =>
    route.fulfill({ json: counts }),
  );
  await fs.mkdir(".impeccable/review/langfuse", { recursive: true });
  for (const [theme, width, height] of [
    ["dark", 1440, 900],
    ["light", 720, 900],
  ] as const) {
    await page.setViewportSize({ width, height });
    await page.goto("/");
    const toggle = page.getByRole("button", {
      name: `Switch to ${theme} mode`,
      exact: true,
    });
    if (await toggle.count()) await toggle.click();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    const section = page.getByRole("region", { name: "Langfuse tracing" });
    await section.scrollIntoViewIfNeeded();
    await expect(
      section.getByText("Project verified", { exact: true }),
    ).toBeVisible();
    await expect(section.getByText(/13 observations pending/)).toBeVisible();
    await section.getByRole("button", { name: "Retry delivery" }).click();
    await expect(
      section.getByText(/Uncertain receipts will be checked/),
    ).toBeVisible();
    await section.locator("h3").scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `.impeccable/review/langfuse/${theme}-${width}.png`,
    });
    await section.getByText("Storage & delivery", { exact: true }).click();
    await section
      .getByLabel("Environment", { exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `.impeccable/review/langfuse/${theme}-${width}-storage.png`,
    });
    const axe = await new AxeBuilder({ page })
      .include(".telemetry-settings")
      .analyze();
    expect(axe.violations).toEqual([]);
    expect(
      await section.evaluate((node) => node.scrollWidth <= node.clientWidth),
    ).toBe(true);
    await page.keyboard.press("Escape");
  }
  await page.route("**/api/production/operations?*", (route) =>
    route.fulfill({
      json: [
        {
          id: "fixture-operation",
          kind: "survey_angle",
          state: "failed",
          awaits_review: false,
          request: {},
          budget_reserved: 1,
          budget_released_at: null,
          cancel_requested: false,
          review: null,
          error: "Fixture validation failure",
          created_at: "2026-09-30T10:00:00Z",
          updated_at: "2026-09-30T10:00:00Z",
          jobs: [],
          candidates: [],
        },
      ],
    }),
  );
  await page.getByRole("button", { name: /^Jobs/ }).click();
  const jobs = page.getByRole("dialog", { name: "Jobs", exact: true });
  await expect(jobs.getByText("survey angle", { exact: true })).toBeVisible();
  await expect(
    jobs.getByText("Fixture validation failure", { exact: true }),
  ).toBeVisible();
});

test("failed status fetch cannot claim configured tracing is off", async ({
  page,
}) => {
  await page.route("**/settings/telemetry", (route) =>
    route.fulfill({
      json: {
        enabled: true,
        base_url: "https://cloud.langfuse.com",
        environment: "development",
        storage_limit_gib: 10,
        credential_storage: "session",
        public_key_hint: "pk-lf-…test",
        has_secret: true,
        project_id: "browser-fixture",
      },
    }),
  );
  await page.route("**/settings/telemetry/status", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Status unavailable in fixture" },
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const section = page.getByRole("region", { name: "Langfuse tracing" });
  await expect(
    section.getByText("Delivery status unavailable", { exact: true }),
  ).toBeVisible({ timeout: 15000 });
  await expect(
    section.getByText("Tracing is off", { exact: true }),
  ).toHaveCount(0);
  await section
    .getByText("Delivery status unavailable", { exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: ".impeccable/review/langfuse/status-unavailable.png",
  });
});
