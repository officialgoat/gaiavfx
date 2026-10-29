import { test, expect } from "@playwright/test";

test("Local engines: the folder saves, readiness and licences are shown and never block", async ({
  page,
}) => {
  await page.route("**/api/v1/settings/engines", (route) =>
    route.fulfill({
      json: {
        root: "X:\\GaiaEngines",
        root_source: "setting",
        engines: [
          {
            name: "rvc",
            home: "X:\\GaiaEngines\\rvc",
            installed: true,
            worker: true,
            tasks: ["convert", "train"],
            gpu_tasks: ["convert", "train"],
            licences: ["Apache-2.0", "MIT"],
            files: { ok: 17, missing: 0, mismatch: 0 },
            verified_at: "2026-10-21T12:00:00+00:00",
            probe: "ok",
            python: "3.12.12",
            cuda_device: "NVIDIA GeForce RTX 5090",
            drift: [],
            probed_at: "2026-10-21T12:00:00+00:00",
          },
          {
            name: "tts",
            home: "X:\\GaiaEngines\\tts",
            installed: false,
            worker: false,
            tasks: ["generate"],
            gpu_tasks: ["generate"],
            licences: ["Fish Audio Research License (non-commercial)"],
            files: null,
            verified_at: null,
            probe: null,
            python: null,
            cuda_device: null,
            drift: [],
            probed_at: null,
          },
        ],
      },
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const engines = page.getByRole("list", { name: "Local engines" });
  await expect(engines.getByText("Ready", { exact: true })).toBeVisible();
  await expect(
    engines.getByText("Python 3.12.12 · NVIDIA GeForce RTX 5090"),
  ).toBeVisible();
  await expect(engines.getByText("Not installed")).toBeVisible();
  await expect(engines.getByText(/Fish Audio Research License/)).toBeVisible();
  await expect(page.getByText("Using X:\\GaiaEngines.")).toBeVisible();

  const folder = page.getByLabel("Local engines folder");
  await folder.fill("relative\\engines");
  await page.getByRole("button", { name: "Save connections" }).click();
  await expect(page.getByRole("alert")).toContainText("absolute folder");
  await folder.fill("X:\\GaiaEngines");
  const saved = page.waitForResponse(
    (r) =>
      r.url().endsWith("/settings/connections") &&
      r.request().method() === "PUT",
  );
  await page.getByRole("button", { name: "Save connections" }).click();
  expect((await (await saved).json()).engines_root).toBe("X:\\GaiaEngines");
  await expect(page.getByText("Connections saved")).toBeVisible();
});

test("Expressive recordings: the folder saves and its clips are counted by category (ADR 0016 P5)", async ({
  page,
}) => {
  const library = {
    folder: "",
    state: "not_set",
    clips: 0,
    categories: {} as Record<string, number>,
    skipped: 0,
  };
  await page.route("**/api/v1/settings/voice-library", (route) =>
    route.fulfill({ json: library }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const line = page.getByText(/^Not set: screams and cries/);
  await expect(line).toBeVisible();

  const folder = page.getByLabel("Expressive recordings folder");
  await folder.fill("relative\\voices");
  await page.getByRole("button", { name: "Save connections" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Enter an absolute folder for the expressive recordings, or leave it empty.",
  );
  // The service scans the saved folder; Settings asks again after a save.
  Object.assign(library, {
    folder: "X:\\GaiaVoices",
    state: "ready",
    clips: 16,
    categories: { scream: 12, sob: 4 },
    skipped: 2,
  });
  await folder.fill("X:\\GaiaVoices");
  const saved = page.waitForResponse(
    (r) =>
      r.url().endsWith("/settings/connections") &&
      r.request().method() === "PUT",
  );
  await page.getByRole("button", { name: "Save connections" }).click();
  const sent = await saved;
  expect(sent.request().postDataJSON().expressive_library).toBe(
    "X:\\GaiaVoices",
  );
  expect((await sent.json()).expressive_library).toBe("X:\\GaiaVoices");
  await expect(page.getByText("Connections saved")).toBeVisible();
  await expect(
    page.getByText("16 clips: scream 12, sob 4 · 2 files skipped"),
  ).toBeVisible();

  // A folder that is gone says so.
  Object.assign(library, {
    state: "missing",
    clips: 0,
    categories: {},
    skipped: 0,
  });
  await page.getByRole("button", { name: "Save connections" }).click();
  await expect(page.getByText("Folder not found.")).toBeVisible();
  // Cleared, the generators make those sounds (and the shared test library is left without a folder).
  Object.assign(library, { folder: "", state: "not_set" });
  await folder.fill("");
  const cleared = page.waitForResponse(
    (r) =>
      r.url().endsWith("/settings/connections") &&
      r.request().method() === "PUT",
  );
  await page.getByRole("button", { name: "Save connections" }).click();
  expect((await (await cleared).json()).expressive_library).toBe("");
  await expect(line).toBeVisible();
});
