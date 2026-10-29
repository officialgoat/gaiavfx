import { test, expect } from "@playwright/test";
import { openMovie } from "./direct-fixture";
import { referenceReading, seedChat } from "./chat-fixture";

test.describe("Movie chat on the real S7d API and scripted model", () => {
  let data: Awaited<ReturnType<typeof seedChat>>;
  test.beforeEach(async ({ request }) => {
    data = await seedChat(request);
  });
  test.afterEach(async () => {
    await data?.restore();
  });

  test("attachment, safe Markdown, plan approval, progress, timeline link and receipt undo", async ({
    page,
    request,
  }) => {
    await data.script([
      ...referenceReading(data.asset.id),
      {
        tool: "say",
        thought: "",
        input: {
          text: "The **reverse** is clear.\n\n- Keep the light\n- Use `f36`\n\n```html\n<img src=x onerror=alert(1)>\n```",
        },
      },
      data.proposal(),
      {
        tool: "edit_cut",
        thought: "",
        input: {
          op: "relation",
          cut_id: data.cut.id,
          expected_revision: data.cut.revision,
          relation: "side",
          crosses_barrier: false,
        },
      },
      { tool: "plan_step", thought: "", input: { step: 0, status: "done" } },
      {
        // S7e keeps the render step actionable: pause for the actual open decision.
        tool: "ask_human",
        thought: "",
        input: {
          question:
            "The edit is ready for you. Keep the courtyard light before rendering?",
        },
      },
    ]);
    await openMovie(page, data.movie.title);
    await page
      .getByRole("button", { name: "Attach a reference", exact: true })
      .click();
    await page
      .getByRole("textbox", { name: "Search attachments" })
      .fill("Arin reference.jpg");
    await page
      .getByRole("region", { name: "Attach from Library" })
      .getByRole("button", { name: "Arin reference.jpg" })
      .click();
    await page
      .getByRole("region", { name: "Attach from Library" })
      .getByRole("button", { name: "Close", exact: true })
      .click();
    await page.getByLabel("Message to Gaia").fill("Discuss this reference");
    await page.getByRole("tab", { name: "Properties", exact: true }).click();
    await page.getByRole("tab", { name: "Jobs", exact: true }).click();
    await page.getByRole("tab", { name: "Chat", exact: true }).click();
    await expect(page.getByLabel("Message to Gaia")).toHaveValue(
      "Discuss this reference",
    );
    await expect(page.locator(".chat-composer .chat-attachment")).toHaveCount(
      1,
    );
    await page.getByLabel("Message to Gaia").press("Enter");
    await expect(page.locator(".chat-markdown strong")).toHaveText("reverse", {
      timeout: 20_000,
    });
    await expect(page.locator(".chat-markdown img")).toHaveCount(0);
    await expect(page.locator(".chat-markdown pre")).toContainText(
      "<img src=x",
    );
    await expect(page.locator(".chat-human .chat-attachment")).toContainText(
      "Arin reference.jpg",
    );
    const current = await data.session();
    const state = await (
      await request.get(`/api/agent/sessions/${current.id}`, {
        headers: data.headers,
      })
    ).json();
    expect(state.turns[0].action.attachments[0].asset_id).toBe(data.asset.id);
    await page.getByLabel("Message to Gaia").fill("Plan the next edit");
    // A rendered reply can precede the poll that marks its turn idle.
    await expect(
      page.getByRole("button", { name: "Send message", exact: true }),
    ).toBeEnabled();
    await page.getByLabel("Message to Gaia").press("Enter");
    const plan = page.getByRole("region", { name: "Plan revision 1" });
    await expect(plan).toBeVisible({ timeout: 20_000 });
    await expect(plan.locator(".plan-budget")).toContainText("9 jobs");
    await expect(plan).not.toContainText("/ 6");
    await plan.getByRole("button", { name: "Cut f36", exact: true }).click();
    await expect(
      page.getByRole("button", { name: /Cut f36 · Reverse/ }),
    ).toHaveAttribute("aria-pressed", "true");
    await plan.getByRole("button", { name: "Approve", exact: true }).click();
    await expect(
      page
        .locator(".chat-message")
        .filter({ hasText: "The edit is ready for you." }),
    ).toBeVisible({ timeout: 20_000 });
    await expect(plan.locator(".step-done")).toContainText("Done");
    await expect(
      page.getByRole("button", { name: /Cut f36 · Side view/ }),
    ).toBeVisible();
    const card = page.locator(".chat-tool").filter({ hasText: "edit cut" });
    await card.locator("summary").click({ button: "right" });
    await page
      .getByRole("menuitem", { name: "Undo this receipt", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: /Cut f36 · Reverse/ }),
    ).toBeVisible();
    await page
      .getByRole("navigation", { name: "Workspaces" })
      .getByRole("button", { name: "Agent", exact: true })
      .click();
    await expect(
      page.getByRole("main", { name: "Agent workspace" }),
    ).toBeVisible();
    await expect(
      page.getByRole("region", { name: "Movie conversation" }),
    ).toHaveCount(1);
    await expect(page.locator(".chat-human")).toHaveCount(2);
  });

  test("revise, collapsed history, reject, keyboard mode and stable mentions", async ({
    page,
    request,
  }) => {
    await data.script([
      data.proposal(),
      data.proposal("Use a quieter reverse", 2),
    ]);
    await openMovie(page, data.movie.title);
    const input = page.getByLabel("Message to Gaia");
    await input.focus();
    await input.press("Shift+Tab");
    await expect(
      page.getByRole("button", { name: /Plan mode Shift/ }),
    ).toHaveAttribute("aria-pressed", "false");
    await input.press("Shift+Tab");
    await input.fill("Plan @Ar");
    await input.press("ArrowDown");
    await expect(page.getByRole("button", { name: "Arin cast" })).toBeFocused();
    await page.keyboard.press("Enter");
    await page.getByRole("tab", { name: "Properties", exact: true }).click();
    await page.getByRole("tab", { name: "Chat", exact: true }).click();
    await expect(input).toHaveValue("Plan @Arin ");
    await input.press("Enter");
    const plan = page.getByRole("region", { name: "Plan revision 1" });
    await expect(plan).toBeVisible({ timeout: 20_000 });
    const current = await data.session();
    const state = await (
      await request.get(`/api/agent/sessions/${current.id}`, {
        headers: data.headers,
      })
    ).json();
    expect(state.turns[0].action.message).toContain("@Arin (cast:");
    await plan.getByRole("button", { name: "Revise", exact: true }).click();
    await page.getByLabel("Revision note").fill("Make it quieter");
    await page
      .getByRole("button", { name: "Send revision", exact: true })
      .click();
    const revised = page.getByRole("region", { name: "Plan revision 2" });
    await expect(revised).toContainText("Use a quieter reverse", {
      timeout: 20_000,
    });
    await expect(page.locator(".chat-plan-history > summary")).toHaveText(
      "Revised plans (1)",
    );
    await expect(plan).not.toBeVisible();
    await revised.getByRole("button", { name: "Reject", exact: true }).click();
    await page
      .getByLabel("Rejection note (optional)")
      .fill("Try another approach later");
    await page
      .getByRole("button", { name: "Confirm rejection", exact: true })
      .click();
    await expect(page.locator(".chat-system")).toContainText(
      "You rejected plan",
    );
    await expect(page.locator(".chat-plan-history > summary")).toHaveText(
      "Revised plans (2)",
    );
    await input.focus();
    await input.press("Shift+Tab");
    await expect.poll(async () => (await data.session()).plan_mode).toBe(false);
    await page.reload();
    await expect(
      page.getByRole("button", { name: /Plan mode Shift/ }),
    ).toHaveAttribute("aria-pressed", "false");
  });
});
