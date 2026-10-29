import { defineConfig } from "@playwright/test";
import { browserTestOrigin } from "./tests/browser-origin";
export default defineConfig({
  testDir: "./tests/browser",
  timeout: 30_000,
  expect: { timeout: 6000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  globalSetup: "./tests/browser-setup.ts",
  projects: [
    { name: "onboarding", testMatch: "**/workspace.spec.ts" },
    {
      name: "suite",
      testIgnore: "**/workspace.spec.ts",
      dependencies: ["onboarding"],
    },
  ],
  use: {
    baseURL: browserTestOrigin,
    viewport: { width: 1440, height: 900 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
