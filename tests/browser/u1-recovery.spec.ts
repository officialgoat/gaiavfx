import { expect, test } from "@playwright/test";
import { seedChat } from "./chat-fixture";
import { seedDirect, openMovie } from "./direct-fixture";
import { seedProduction } from "./production-fixture";

test("failed conversation recovery preserves readable history and survives reload", async ({
  page,
  request,
}) => {
  const data = await seedChat(request);
  try {
    await data.script([
      {
        tool: "say",
        thought: "",
        input: { text: "The old conversation is retained." },
      },
    ]);
    await openMovie(page, data.movie.title);
    await page.getByLabel("Message to Gaia").fill("Keep the courtyard quiet.");
    await page.getByLabel("Message to Gaia").press("Enter");
    await expect(
      page.getByText("The old conversation is retained.", { exact: true }),
    ).toBeVisible();
    const old = await data.session();
    await page.route(`**/api/agent/sessions/${old.id}?*`, async (route) => {
      const response = await route.fetch();
      const value = await response.json();
      value.session.state = "failed";
      value.session.activity = "failed";
      value.session.stop_reason = "Request budget exceeds context";
      await route.fulfill({ json: value });
    });
    await page.reload();
    await expect(
      page.getByText("Request budget exceeds context", { exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Start a new conversation", exact: true })
      .click();
    await expect.poll(async () => (await data.session()).id).not.toBe(old.id);
    await page.unrouteAll({ behavior: "wait" });
    await page.getByText("Earlier conversations", { exact: true }).click();
    await page
      .getByRole("button", { name: "Conversation 1 · stopped" })
      .click();
    await expect(
      page.getByText("The old conversation is retained.", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Earlier conversation · Read-only", { exact: false }),
    ).toBeVisible();
    await expect(page.getByLabel("Message to Gaia")).toHaveCount(0);
    await page
      .getByRole("button", { name: "Return to current conversation" })
      .click();
    await expect(page.getByLabel("Message to Gaia")).toBeVisible();
    await page.reload();
    await expect(page.getByLabel("Message to Gaia")).toBeVisible();
    await expect(
      page.getByText("The old conversation is retained.", { exact: true }),
    ).toHaveCount(0);
    const before = await data.session();
    await page
      .getByRole("navigation", { name: "Application menus" })
      .getByRole("button", { name: "Agent", exact: true })
      .click();
    await page
      .getByRole("menuitem", { name: "New conversation", exact: true })
      .click();
    await expect
      .poll(async () => (await data.session()).id)
      .not.toBe(before.id);
  } finally {
    await data.restore();
  }
});

test("Stop plan is available in the card and menus and permits a replacement", async ({
  page,
  request,
}) => {
  const data = await seedChat(request);
  try {
    await data.script([data.proposal()]);
    await openMovie(page, data.movie.title);
    await page.getByLabel("Message to Gaia").fill("Plan the reverse.");
    await page.getByLabel("Message to Gaia").press("Enter");
    await expect(
      page.getByRole("button", { name: "Approve", exact: true }),
    ).toBeEnabled();
    // A human question parks the approved plan until the director decides what to do.
    await data.script([
      {
        tool: "ask_human",
        thought: "",
        input: { question: "Which light should we keep?" },
      },
    ]);
    await page.getByRole("button", { name: "Approve", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Stop plan", exact: true }),
    ).toBeEnabled();
    // The shared suite's large Library can hit the harness schema ceiling before
    // the scripted question. That stranded executing plan must be stoppable too.
    await expect(page.locator(".chat-activity")).toHaveText(
      /^(waiting human|failed)$/,
      { timeout: 20_000 },
    );
    const parked = await data.session();
    if (parked.state === "failed")
      expect(parked.stop_reason).toContain("the schema uses");
    const card = page.getByRole("region", { name: "Plan revision 1" });
    await card.click({ button: "right" });
    await expect(
      page.getByRole("menuitem", { name: "Stop plan", exact: true }),
    ).toHaveAttribute("aria-disabled", "false");
    await page.keyboard.press("Escape");
    await page
      .getByRole("navigation", { name: "Application menus" })
      .getByRole("button", { name: "Agent", exact: true })
      .click();
    await expect(
      page.getByRole("menuitem", { name: "Stop plan", exact: true }),
    ).toHaveAttribute("aria-disabled", "false");
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Stop plan", exact: true }).click();
    await expect.poll(async () => (await data.session()).state).toBe("idle");
    await data.script([data.proposal("A replacement plan")]);
    await page.getByLabel("Message to Gaia").fill("Try a different plan.");
    await page.getByLabel("Message to Gaia").press("Enter");
    await expect(
      page.getByText("A replacement plan", { exact: true }),
    ).toBeVisible();
    expect((await data.session()).plan_history.at(-1).state).toBe("superseded");
  } finally {
    await data.restore();
  }
});

test("angle text saves through Properties and Undo preserves structured placement", async ({
  page,
  request,
}) => {
  const data = await seedDirect(request);
  await openMovie(page, data.movie.title);
  await page.getByRole("button", { name: /^Places/ }).click();
  await page
    .locator(".project-record")
    .filter({ hasText: "Arcade courtyard" })
    .last()
    .click();
  await page.getByRole("tab", { name: "Properties", exact: true }).click();
  const form = page.getByRole("form", { name: `Edit angle ${data.to.name}` });
  await form.getByLabel("Name (required)").fill("Inside the gallery");
  await form
    .getByLabel("Description")
    .fill("The sunlit courtyard beyond the railing.");
  await form.getByRole("button", { name: "Save angle" }).click();
  await page.screenshot({ path: ".cache/u1/angle-editor.png" });
  const saved = page.getByRole("form", {
    name: "Edit angle Inside the gallery",
  });
  await expect(saved.getByLabel("Description")).toHaveValue(
    "The sunlit courtyard beyond the railing.",
  );
  await page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Edit", exact: true })
    .click();
  await page.getByRole("menuitem", { name: /^Undo/ }).click();
  await expect(
    page.getByRole("form", { name: `Edit angle ${data.to.name}` }),
  ).toBeVisible();
  const angles = await (
    await request.get(`/api/direction/places/${data.to.place_id}/angles`, {
      headers: data.headers,
    })
  ).json();
  expect(
    angles.find((a: { id: string }) => a.id === data.to.id).placement,
  ).toEqual(data.to.placement);
});

test("pin form shows placement labels, hints and facing-dependent required fields", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  const data = await seedProduction(request);
  await openMovie(page, data.movie.title);
  await page.getByRole("button", { name: /Cut f36/ }).click();
  await page
    .getByRole("navigation", { name: "Workspaces" })
    .getByRole("button", { name: "Prepare", exact: true })
    .click();
  await page.getByRole("button", { name: /^Prepare pin/ }).click();
  const form = page.getByRole("dialog", { name: "Prepare pin", exact: true });
  await form
    .getByRole("combobox", { name: "Facing", exact: true })
    .selectOption("screen_left");
  for (const label of ["Distance", "Along", "Barrier", "Feet", "Occlusion"]) {
    const input = form.getByLabel(label, { exact: true });
    await expect(input).toBeVisible();
    await expect(input).toHaveAttribute("required", "");
    await expect(input.locator("..")).toContainText(`${label} (required)`);
    await expect(input.locator("..").locator("small")).not.toBeEmpty();
  }
  await form.getByLabel("Distance", { exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: ".cache/u1/pin-placement.png" });
  await expect(
    form.getByLabel("Behind the subject", { exact: true }),
  ).not.toHaveAttribute("required", "");
  await form
    .getByRole("combobox", { name: "Facing", exact: true })
    .selectOption("toward_camera");
  await expect(
    form.getByLabel("Distance", { exact: true }),
  ).not.toHaveAttribute("required", "");
  await expect(
    form.getByLabel("Behind the subject", { exact: true }),
  ).toHaveAttribute("required", "");
});
