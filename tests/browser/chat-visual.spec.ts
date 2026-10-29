import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import fs from "node:fs/promises";
import { seedChat } from "./chat-fixture";
import { openMovie } from "./direct-fixture";

test("chat and polish in both layouts and themes", async ({
  page,
  request,
}) => {
  const data = await seedChat(request);
  try {
    await data.script([
      data.proposal(),
      { tool: "plan_step", thought: "", input: { step: 0, status: "done" } },
      {
        tool: "ask_human",
        thought: "",
        input: {
          question:
            "Ready for the next step. Keep the courtyard light before rendering?",
        },
      },
    ]);
    await openMovie(page, data.movie.title);
    await page.getByLabel("Search project").fill("Arin reference.jpg");
    await page
      .getByRole("button", { name: "Arin reference.jpg image" })
      .click();
    await page
      .getByLabel("Message to Gaia")
      .fill(
        "Help me make the reverse read clearly. Keep Arin and the courtyard consistent.",
      );
    await page.getByLabel("Message to Gaia").press("Enter");
    await expect(
      page.getByRole("region", { name: "Plan revision 1" }),
    ).toBeVisible({ timeout: 20_000 });
    await page.getByRole("button", { name: /Cut f36 · Reverse/ }).click();
    await page.getByRole("slider", { name: "Playhead", exact: true }).focus();
    await page.keyboard.press("Home");
    await page.keyboard.press("Shift+ArrowRight");
    await expect(page.locator(".ruler-scale > span").first()).toHaveText("00");
    await expect(page.locator(".ruler-scale")).not.toContainText(/\ds/);
    await expect(page.locator(".cut-item")).toContainText("f36 Reverse");
    await expect(page.locator(".coverage-chip")).toContainText(
      "Reveals new area",
    );
    await expect(
      page.getByRole("button", { name: "Razor tool", exact: true }),
    ).toBeEnabled();
    // Nine movie rows, the Continuation row (docs/CONTINUATION.md) among them.
    await expect(page.locator(".track-row .track-label svg")).toHaveCount(9);
    await expect(
      page.getByRole("button", { name: "Play preview", exact: true }),
    ).toHaveCount(2);
    await expect(page.locator(".workspace-breadcrumb")).toContainText(
      "Segment 02 / reverse",
    );
    await fs.mkdir(".cache/s6-chat", { recursive: true });
    await page.emulateMedia({ reducedMotion: "reduce" });
    for (const theme of ["dark", "light"]) {
      const toggle = page.getByRole("button", {
        name: `Switch to ${theme} mode`,
        exact: true,
      });
      if (await toggle.count()) await toggle.click();
      for (const layout of ["default", "full"]) {
        await page
          .getByRole("button", { name: "Workspace", exact: true })
          .click();
        await page
          .getByRole("menuitem", {
            name:
              layout === "full" ? "Timeline full width" : "Default timeline",
            exact: true,
          })
          .click();
        await page.screenshot({
          path: `.cache/s6-chat/direct-${theme}-${layout}.png`,
        });
        const audit = await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
          .analyze();
        expect(audit.violations).toEqual([]);
      }
      await page
        .getByRole("navigation", { name: "Workspaces" })
        .getByRole("button", { name: "Agent", exact: true })
        .click();
      const chatWidth = await page
        .locator(".dock-inspector")
        .evaluate((e) => e.clientWidth);
      expect(chatWidth).toBeGreaterThanOrEqual(538);
      await page.screenshot({ path: `.cache/s6-chat/agent-${theme}.png` });
      await page
        .getByRole("navigation", { name: "Workspaces" })
        .getByRole("button", { name: "Direct", exact: true })
        .click();
    }
    await page.getByRole("button", { name: "Workspace", exact: true }).click();
    await page
      .getByRole("menuitem", { name: "Default timeline", exact: true })
      .click();
    await page.getByRole("button", { name: "Approve", exact: true }).click();
    await expect(page.locator(".chat-markdown")).toContainText(
      "Ready for the next step.",
      { timeout: 20_000 },
    );
    await page.getByRole("button", { name: "Workspace", exact: true }).click();
    await page
      .getByRole("menuitem", { name: "Timeline full width", exact: true })
      .click();
    for (const theme of ["dark", "light"]) {
      const toggle = page.getByRole("button", {
        name: `Switch to ${theme} mode`,
        exact: true,
      });
      if (await toggle.count()) await toggle.click();
      const progress = page.getByRole("region", { name: "Plan progress" });
      await expect(progress).toContainText("9 jobs");
      await expect(progress.locator(".step-done")).toContainText("Done");
      await expect(
        progress.getByRole("button", { name: "Plan details" }),
      ).toBeVisible();
      await page.screenshot({
        path: `.cache/s6-chat/direct-${theme}-full-progress.png`,
      });
    }
    for (const width of [1200, 900, 720]) {
      await page.setViewportSize({ width, height: 900 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await expect(page.locator(".chat-composer")).toBeAttached();
    }
    await page.screenshot({ path: ".cache/s6-chat/direct-light-720.png" });
  } finally {
    await data.restore();
  }
});
