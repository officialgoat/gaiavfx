import { expect, test, type Page } from "@playwright/test";
import { openMovie, seedDirect } from "./direct-fixture";
import type { components } from "../../frontend/src/shared/api.generated";

async function undo(page: Page) {
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByRole("menuitem", { name: /^Undo/ }).click();
}

async function inspectCut(page: Page, title: string) {
  await openMovie(page, title);
  await page.getByRole("button", { name: /Cut f36/ }).click();
  await page.getByRole("tab", { name: "Properties", exact: true }).click();
  return page.getByRole("form", { name: "Edit cut at f36" });
}

test("cut context menu confirms removal in page and Undo restores the same cut", async ({
  page,
  request,
}, testInfo) => {
  const data = await seedDirect(request);
  await openMovie(page, data.movie.title);
  const cut = page.getByRole("button", { name: /Cut f36/ });
  await cut.focus();
  await page.keyboard.press("Shift+F10");
  await page.getByRole("menuitem", { name: "Remove cut", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Remove cut", exact: true });
  await expect(dialog).toContainText("frame 36 in Segment 02");
  await page.screenshot({ path: testInfo.outputPath("remove-cut-dark.png") });
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(cut).toBeVisible();
  await expect(cut).toBeFocused();
  await cut.click({ button: "right" });
  await page.getByRole("menuitem", { name: "Remove cut", exact: true }).click();
  await dialog.getByRole("button", { name: "Remove cut", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(cut).toHaveCount(0);
  await expect(page.locator(".phase-item")).toHaveCount(2);
  await expect(page.locator(".studio-status")).toContainText("Cut removed");
  await undo(page);
  await expect(cut).toBeVisible();
  const restored = await request.get(`/api/direction/cuts/${data.cut.id}`, {
    headers: data.headers,
  });
  expect(restored.ok()).toBeTruthy();
  expect(await restored.json()).toMatchObject({ id: data.cut.id, frame: 36 });
});

test("Properties removal retains the server refusal inline and supports retry", async ({
  page,
  request,
}, testInfo) => {
  const data = await seedDirect(request);
  const form = await inspectCut(page, data.movie.title);
  await page.getByRole("button", { name: "Switch to light mode" }).click();
  await form.getByRole("button", { name: "Remove cut", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Remove cut", exact: true });
  // Another writer changes the segment after the confirmation has opened.
  // Removal must refuse the stale revision, refresh, then allow an explicit retry.
  const changed = await request.patch(
    `/api/direction/segments/${data.second.id}`,
    {
      headers: data.headers,
      data: {
        expected_revision: data.cut.receipt.after.revision,
        content: { ...data.second.content, style: "Cool evening light." },
      },
    },
  );
  expect(changed.ok(), await changed.text()).toBeTruthy();
  const refused = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/cuts/${data.cut.id}/remove`) &&
      r.request().method() === "POST",
  );
  await dialog.getByRole("button", { name: "Remove cut", exact: true }).click();
  expect((await refused).status()).toBe(409);
  await expect(dialog.getByRole("alert")).toContainText(/revision|changed/i);
  await expect(page.locator(".cut-item")).toHaveCount(1);
  await page.screenshot({
    path: testInfo.outputPath("remove-cut-refusal-light.png"),
  });
  await dialog.getByRole("button", { name: "Remove cut", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(page.locator(".cut-item")).toHaveCount(0);
});

test("Delivered frame moves the cut and its authored phase together and Undo restores both", async ({
  page,
  request,
}) => {
  const data = await seedDirect(request);
  const created = await request.post(
    `/api/direction/segments/${data.second.id}/phases`,
    {
      headers: data.headers,
      data: {
        expected_revision: data.cut.receipt.after.revision,
        start_frame: 36,
        content: {
          description: "Arin turns toward the gate",
          screen_direction: "right to left",
          sound: "Footsteps pause",
          cast: data.second.content.subjects,
        },
      },
    },
  );
  expect(created.ok(), await created.text()).toBeTruthy();
  const phase: components["schemas"]["PhaseCommandDTO"] = await created.json();
  const form = await inspectCut(page, data.movie.title);
  const phaseMarker = page.getByRole("button", {
    name: "Phase: Arin turns toward the gate",
    exact: true,
  });
  await expect(phaseMarker).toHaveAttribute("data-frame", "156");
  await form.getByLabel("Delivered frame").fill("60");
  await form.getByRole("button", { name: "Save cut" }).click();
  await expect(
    page.getByRole("form", { name: "Edit cut at f60" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /Cut f60/ })).toHaveAttribute(
    "data-frame",
    "180",
  );
  await expect(phaseMarker).toHaveAttribute("data-frame", "180");
  const response = await request.get(`/api/direction/movies/${data.movie.id}`, {
    headers: data.headers,
  });
  const movie: components["schemas"]["MovieDetailDTO"] = await response.json();
  expect(movie.segments[1].phases.find((p) => p.id === phase.id)).toMatchObject(
    {
      start_frame: 60,
      content: phase.content,
    },
  );
  await undo(page);
  await expect(page.getByRole("button", { name: /Cut f36/ })).toHaveAttribute(
    "data-frame",
    "156",
  );
  await expect(phaseMarker).toHaveAttribute("data-frame", "156");
});

for (const frame of [12, 110]) {
  test(`a user can move to delivered frame ${frame} in a continuing segment and undo`, async ({
    page,
    request,
  }) => {
    const data = await seedDirect(request);
    const form = await inspectCut(page, data.movie.title);
    await form.getByLabel("Delivered frame").fill(String(frame));
    await form.getByRole("button", { name: "Save cut" }).click();
    const saved = page.getByRole("form", { name: `Edit cut at f${frame}` });
    await expect(saved.getByLabel("Delivered frame")).toHaveValue(
      String(frame),
    );
    await expect(saved.getByRole("alert")).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: new RegExp(`Cut f${frame} `) }),
    ).toHaveAttribute("data-frame", String(120 + frame));
    const response = await request.get(`/api/direction/cuts/${data.cut.id}`, {
      headers: data.headers,
    });
    expect(await response.json()).toMatchObject({ id: data.cut.id, frame });
    await undo(page);
    await expect(
      page.getByRole("form", { name: "Edit cut at f36" }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: /Cut f36/ })).toHaveAttribute(
      "data-frame",
      "156",
    );
    await expect(page.locator(".phase-item")).toHaveCount(2);
  });
}
