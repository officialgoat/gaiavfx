import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";
import fs from "node:fs/promises";
import { developmentPython } from "../../scripts/python.mjs";
import { seedChat } from "./chat-fixture";
import { seedDirect, openMovie } from "./direct-fixture";

test.use({ actionTimeout: 15000, reducedMotion: "reduce" });

test("replacement preparation, blind review and automatic decisions", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  const data = await seedDirect(request);
  const fixture = JSON.parse(
    execFileSync(
      developmentPython(path.resolve(".")),
      ["tests/browser-replacement.py", process.env.GAIAVFX_TEST_LIBRARY!],
      { encoding: "utf8", windowsHide: true },
    ),
  );
  await openMovie(page, data.movie.title);
  await page
    .getByRole("navigation", { name: "Workspaces" })
    .getByRole("button", { name: "Prepare", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Replace character…", exact: true })
    .click();
  const form = page.getByRole("dialog", {
    name: "Replace character",
    exact: true,
  });
  await form.getByLabel("Source clip").selectOption(fixture.source);
  await form.getByLabel("Analysed shot").selectOption(fixture.analysis);
  const person = form.getByRole("button", { name: "Person 1", exact: true });
  await expect(person).toBeVisible();
  await expect(
    form.getByRole("button", { name: "Person 3", exact: true }),
  ).toContainText("Ambiguous");
  await expect(form.locator(".source-track-poster image")).toHaveAttribute(
    "href",
    /^data:image/,
  );
  await person.hover();
  await expect(form.getByLabel("Person 1 outline")).toBeVisible();
  await form
    .getByRole("heading", { name: "Replace character", exact: true })
    .hover();
  await expect(form.getByLabel("Person 1 outline")).toHaveCount(0);
  await person.focus();
  await expect(form.getByLabel("Person 1 outline")).toBeVisible();
  await page.keyboard.press("Space");
  await expect(person).toHaveAttribute("aria-pressed", "true");
  await form.getByLabel("Target for person 1").selectOption({ label: "Arin" });
  await form.getByLabel("yellow mug context").selectOption("context");
  await expect(person).toContainText("left · foreground");
  await expect(person).not.toContainText("Ambiguous");
  await form.getByLabel("Renders", { exact: true }).selectOption("1");
  await expect(form.getByText("First round: 1 renders.")).toBeVisible();
  await form.getByLabel("Renders", { exact: true }).selectOption("4");
  await expect(form.getByText("First round: 2 renders.")).toBeVisible();
  await expect(
    form.getByRole("button", { name: "Start replacement", exact: true }),
  ).toBeEnabled();
  await fs.mkdir(".cache/replacement-review", { recursive: true });
  await page.screenshot({ path: ".cache/replacement-review/prepare-dark.png" });
  const starting = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/production/replacements") &&
      r.request().method() === "POST",
  );
  await form
    .getByRole("button", { name: "Start replacement", exact: true })
    .click();
  const started = await starting;
  expect(started.ok(), await started.text()).toBeTruthy();
  const operation = await started.json();
  const submitted = started.request().postDataJSON();
  expect(submitted.performers[0]).not.toHaveProperty("role");
  expect(submitted.performers[0]).not.toHaveProperty("position");
  expect(operation.request.facts.performers[0].position).toBe("at the left");
  expect(operation.budget_reserved).toBe(4);
  expect(operation.request.facts.source_asset).toBe(fixture.source);
  expect(operation.request.facts.performers[0].track_id).toBe("person-left");
  expect(operation.request.facts.performers[0].target_asset).toBe(
    data.asset.id,
  );
  // No inference: the browser service has a fake gateway; cancel this newly queued intent.
  await request.post(`/api/production/operations/${operation.id}/cancel`, {
    headers: data.headers,
  });
  await expect(form).toHaveCount(0);
  await page.getByRole("tab", { name: "Jobs", exact: true }).click();
  const job = page
    .locator(".task-item")
    .filter({ hasText: "Likeness check failed." });
  await expect(
    job.getByText("Round 2: inverted · crop · Automatic decision", {
      exact: true,
    }),
  ).toBeVisible();
  const blindOperationReads: string[] = [];
  page.on("request", (req) => {
    if (
      req.method() === "GET" &&
      req.url().endsWith(`/api/production/operations/${fixture.operation}`)
    )
      blindOperationReads.push(req.url());
  });
  await job.getByRole("button", { name: "Open review", exact: true }).click();
  const comparison = page.getByRole("region", {
    name: "Synchronized candidate monitors",
  });
  await expect(
    comparison.getByRole("region", { name: "Source monitor", exact: true }),
  ).toBeVisible();
  await expect(comparison.locator("video")).toHaveCount(3);
  const reveal = comparison.getByRole("button", {
    name: "Reveal",
    exact: true,
  });
  await expect(reveal).toBeDisabled();
  const html = await page.locator("body").innerHTML();
  for (const hidden of [
    fixture.operation,
    "987650",
    "987651",
    "Revealed details",
    "inverted",
    "Round 2",
  ])
    expect(html).not.toContain(hidden);
  await page.getByRole("button", { name: "Rank candidate A" }).focus();
  await page.keyboard.press("Alt+ArrowUp");
  await page.getByRole("button", { name: "Rank candidate B" }).focus();
  await page.keyboard.press("Alt+t");
  await expect(page.getByLabel("Rank 1, tied", { exact: true })).toBeVisible();
  await page.getByLabel("Usable ranks").selectOption("1");
  await expect(
    page.getByRole("region", { name: "Replacement checks", exact: true }),
  ).toContainText("Not calibrated");
  await expect(
    page.getByRole("region", { name: "Replacement checks", exact: true }),
  ).toContainText("Measured: 0.73");
  await expect(
    page.getByRole("region", { name: "Replacement checks", exact: true }),
  ).toContainText("threshold is not configured");
  const checks = page.getByRole("region", {
    name: "Replacement checks",
    exact: true,
  });
  await expect(
    checks
      .locator(".replacement-check")
      .filter({ hasText: "background geometry" }),
  ).toContainText("Unsupported");
  await expect(checks).not.toContainText("letterbox difference");
  const tags = page.locator("#review-tags input");
  await expect(tags).toHaveCount(6);
  await tags.first().check();
  await page.getByLabel("Note for candidate B").fill("Synthetic review note");
  await page.getByRole("button", { name: "Save ranking", exact: true }).click();
  await expect(reveal).toBeEnabled();
  const saved = await request.get(
    `/api/production/reviews/${fixture.review.id}`,
    { headers: data.headers },
  );
  const ranked = await saved.json();
  for (const member of ranked.members) {
    const check = member.replacement_checks.find(
      (c: { check: string }) => c.check === "arcface_target",
    );
    expect(check.measured).toBe(
      member.asset_id === fixture.candidate_assets[0] ? 0.2 : 0.8,
    );
    expect(member).not.toHaveProperty("round");
    expect(member).not.toHaveProperty("arm");
    expect(member).not.toHaveProperty("seed");
    expect(member).not.toHaveProperty("job_id");
    expect(
      member.replacement_checks.some(
        (c: { check: string }) => c.check === "letterbox_difference",
      ),
    ).toBe(false);
  }
  expect(ranked.ranking.tiers[0]).toHaveLength(2);
  expect(ranked.ranking.usable_tiers).toBe(1);
  expect(Object.values(ranked.ranking.notes)).toContain(
    "Synthetic review note",
  );
  await expect(
    comparison.getByRole("button", { name: "Play all candidates" }),
  ).toBeEnabled();
  await comparison
    .getByRole("slider", { name: "Scrub all candidates" })
    .fill("24");
  await expect
    .poll(() =>
      comparison
        .getByRole("region", { name: "Source monitor", exact: true })
        .locator("video")
        .evaluate((v: HTMLVideoElement) => Math.round(v.currentTime * 24)),
    )
    .toBe(36);
  await page.getByRole("button", { name: "Switch to light mode" }).click();
  await page.screenshot({ path: ".cache/replacement-review/review-light.png" });
  expect(blindOperationReads).toEqual([]);
  await reveal.click();
  await expect(
    page.getByRole("heading", { name: "Revealed details" }),
  ).toBeVisible();
  await expect(page.locator(".revealed-details")).toContainText("98765");
  await expect(page.locator(".revealed-details")).toContainText("Round");
  await expect(page.locator(".revealed-details")).toContainText("crop");
  await expect(checks).toContainText("letterbox difference");
  const approving = page.waitForResponse((r) =>
    r.url().endsWith("/api/production/replacements/approve"),
  );
  await page
    .getByRole("button", { name: "Approve derivative", exact: true })
    .click();
  const approved = await approving;
  expect(approved.ok(), await approved.text()).toBeTruthy();
  expect((await approved.json()).provenance.source_asset).toBe(fixture.source);
  await expect(
    page.getByText("Replacement approved as a derivative in Library", {
      exact: true,
    }),
  ).toBeVisible();
});

test("chat analysis receipts and repeated questions keep numbered crops available", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  const data = await seedChat(request);
  try {
    const fixture = JSON.parse(
      execFileSync(
        developmentPython(path.resolve(".")),
        ["tests/browser-replacement.py", process.env.GAIAVFX_TEST_LIBRARY!],
        { encoding: "utf8", windowsHide: true },
      ),
    );
    await data.script([
      {
        tool: "analyze_people",
        thought: "",
        input: {
          asset_id: fixture.source,
          start: 12,
          end: 63,
          concepts: ["person", "yellow mug"],
        },
      },
      {
        tool: "ask_human",
        thought: "",
        input: { question: "Which numbered performer should Arin replace?" },
      },
      {
        tool: "ask_human",
        thought: "",
        input: { question: "Keep this numbered performer for the next shot?" },
      },
    ]);
    await openMovie(page, data.movie.title);
    await page.getByLabel("Search project").fill("Arin reference.jpg");
    await page
      .getByRole("button", { name: "Arin reference.jpg image" })
      .click();
    await expect(
      page
        .getByRole("region", { name: "Source monitor", exact: true })
        .getByRole("img", { name: "Arin reference.jpg" }),
    ).toBeVisible();
    const sourceMonitor = page.getByRole("region", {
      name: "Source monitor",
      exact: true,
    });
    // Capture the selected source before new analysis tiles can appear under the pointer.
    const previousSource = await sourceMonitor.innerText();
    await page
      .getByRole("button", { name: "Attach a reference", exact: true })
      .click();
    await page
      .getByLabel("Search attachments")
      .fill("Synthetic replacement source");
    await page
      .getByRole("region", { name: "Attach from Library" })
      .getByRole("button", {
        name: "Synthetic replacement source",
        exact: true,
      })
      .last()
      .click();
    await page
      .getByRole("region", { name: "Attach from Library" })
      .getByRole("button", { name: "Close", exact: true })
      .click();
    await page
      .getByLabel("Message to Gaia")
      .fill("Replace a person in this clip with Arin.");
    await page.getByLabel("Message to Gaia").press("Enter");
    await expect(
      page.getByText("Which numbered performer should Arin replace?", {
        exact: true,
      }),
    ).toBeVisible({ timeout: 30000 });
    const question = page
      .locator(".chat-assistant")
      .filter({ hasText: "Which numbered performer should Arin replace?" });
    await expect(page.locator(".chat-transcript")).not.toContainText(
      "do not guess",
    );
    await expect(page.locator(".chat-transcript")).not.toContainText(
      "ask the user",
    );
    // Every visible sheet belongs to the question, not its immediately preceding receipt.
    await expect(page.locator(".chat-transcript .contact-sheet")).toHaveCount(
      await question.locator(".contact-sheet").count(),
    );
    await expect(question.locator(".contact-sheet").first()).toContainText(
      "Numbers follow the analysis; objects are numbered too.",
    );
    const tile = question
      .getByRole("button", {
        name: "Person 1",
        exact: true,
      })
      .first();
    await expect(tile).toBeEnabled({ timeout: 30000 });
    await page
      .getByRole("region", { name: "Program monitor", exact: true })
      .hover();
    await tile.focus();
    await expect(
      page
        .getByRole("region", { name: "Source monitor", exact: true })
        .getByLabel("Person 1 outline"),
    ).toBeVisible();
    await question
      .getByRole("button", { name: "Person 3", exact: true })
      .first()
      .hover();
    await expect(
      page
        .getByRole("region", { name: "Source monitor", exact: true })
        .getByLabel("Person 3 outline"),
    ).toBeVisible();
    await page
      .getByRole("region", { name: "Program monitor", exact: true })
      .hover();
    await expect(
      page
        .getByRole("region", { name: "Source monitor", exact: true })
        .getByLabel("Person 1 outline"),
    ).toBeVisible();
    await fs.mkdir(".cache/replacement-review", { recursive: true });
    await page.screenshot({ path: ".cache/replacement-review/chat-dark.png" });
    await page.getByLabel("Message to Gaia").focus();
    await expect(sourceMonitor.getByLabel("Person 1 outline")).toHaveCount(0);
    await expect.poll(() => sourceMonitor.innerText()).toBe(previousSource);
    await expect(
      sourceMonitor.getByRole("img", { name: "Arin reference.jpg" }),
    ).toBeVisible();
    await tile.click();
    await expect(tile).toContainText("Selected");
    const sheet = question.locator(".contact-sheet").first();
    await sheet.getByRole("button", { name: "Person 3", exact: true }).click();
    await expect(
      sheet.getByRole("button", { name: "Person 3", exact: true }),
    ).toContainText("Selected");
    await expect(
      page
        .locator(".chat-human")
        .getByText("Person 1 and person 3", { exact: true }),
    ).toHaveCount(0);
    await question
      .getByRole("button", { name: "Send pair answer", exact: true })
      .click();
    await expect(
      page
        .locator(".chat-human")
        .getByText("Person 1 and person 3", { exact: true }),
    ).toBeVisible();
    const again = page
      .locator(".chat-assistant")
      .filter({ hasText: "Keep this numbered performer for the next shot?" });
    await expect(
      again.getByRole("button", { name: "Person 1", exact: true }).first(),
    ).toBeVisible({ timeout: 30000 });
    const current = await data.session();
    const state = await (
      await request.get(`/api/agent/sessions/${current.id}`, {
        headers: data.headers,
      })
    ).json();
    expect(
      state.turns.filter(
        (t: any) =>
          t.action?.tool === "analyze_people" && t.state === "accepted",
      ),
    ).toHaveLength(1);
    expect(
      state.turns.filter(
        (t: any) => t.action?.tool === "ask_human" && t.state === "accepted",
      ),
    ).toHaveLength(2);
  } finally {
    await data.restore();
  }
});
