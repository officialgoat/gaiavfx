import { expect, test, type Page } from "@playwright/test";
import { seedDirect, openMovie } from "./direct-fixture";
import type { components } from "../../frontend/src/shared/api.generated";

async function inspectCut(page: Page, title: string) {
  await openMovie(page, title);
  await page.getByRole("button", { name: /Cut f36/ }).click();
  await page.getByRole("tab", { name: "Properties", exact: true }).click();
  return page.getByRole("form", { name: "Edit cut at f36" });
}

test("Properties retargets a cut within its place, clears the relation and undoes it", async ({
  page,
  request,
}) => {
  const data = await seedDirect(request);
  const response = await request.post(
    `/api/direction/places/${data.to.place_id}/angles`,
    {
      headers: data.headers,
      data: { name: "Inside the gallery" },
    },
  );
  expect(response.ok(), await response.text()).toBeTruthy();
  const angle: components["schemas"]["AngleDTO"] = await response.json();
  const other = await request.post("/api/direction/places", {
    headers: data.headers,
    data: { name: "Another place" },
  });
  expect(other.ok()).toBeTruthy();
  const foreign = await request.post(
    `/api/direction/places/${(await other.json()).id}/angles`,
    {
      headers: data.headers,
      data: { name: "Unrelated view" },
    },
  );
  expect(foreign.ok()).toBeTruthy();
  const form = await inspectCut(page, data.movie.title);
  for (const label of ["From angle", "To angle"]) {
    await expect(form.getByLabel(label).locator("option")).toHaveText([
      "Approach",
      "Reverse",
      "Inside the gallery",
    ]);
  }
  await expect(form.getByLabel("From angle")).toHaveValue(data.from.id);
  await form.getByLabel("To angle").selectOption(angle.id);
  await form.getByRole("button", { name: "Save cut" }).click();
  await expect(page.getByText("Cut saved", { exact: true })).toBeVisible();
  await expect(form.getByLabel("To angle")).toHaveValue(angle.id);
  await expect(page.locator(".properties-content")).toContainText(
    "Relation: Unknown",
  );
  const saved = await request.get(`/api/direction/cuts/${data.cut.id}`, {
    headers: data.headers,
  });
  expect(saved.ok()).toBeTruthy();
  expect(await saved.json()).toMatchObject({
    from_angle_id: data.from.id,
    to_angle_id: angle.id,
    relation: null,
    relation_source: "unknown",
  });
  await page.screenshot({ path: ".cache/u4/cut-angles-dark.png" });
  await page.getByRole("button", { name: "Switch to light mode" }).click();
  await page.screenshot({ path: ".cache/u4/cut-angles-light.png" });
  await page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Edit", exact: true })
    .click();
  await page.getByRole("menuitem", { name: /^Undo/ }).click();
  await expect(form.getByLabel("To angle")).toHaveValue(data.to.id);
  await expect(page.locator(".properties-content")).not.toContainText(
    "Relation: Unknown",
  );
  const restored = await request.get(`/api/direction/cuts/${data.cut.id}`, {
    headers: data.headers,
  });
  expect(await restored.json()).toMatchObject({
    from_angle_id: data.from.id,
    to_angle_id: data.to.id,
    relation: "reverse",
    relation_source: "authored",
  });
});

test("Properties refuses matching cut angles before sending an edit", async ({
  page,
  request,
}) => {
  const data = await seedDirect(request);
  const form = await inspectCut(page, data.movie.title);
  const edits: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "PATCH" && r.url().endsWith(`/cuts/${data.cut.id}`))
      edits.push(r.url());
  });
  for (const [label, value, original] of [
    ["To angle", data.from.id, data.to.id],
    ["From angle", data.to.id, data.from.id],
  ]) {
    await form.getByLabel(label).selectOption(value);
    await form.getByRole("button", { name: "Save cut" }).click();
    await expect(form.getByRole("alert")).toHaveText(
      "A cut needs a different target angle",
    );
    expect(edits).toEqual([]);
    await form.getByLabel(label).selectOption(original);
    await expect(form.getByRole("alert")).toHaveCount(0);
  }
});

test("Properties shows the server's cut refusal inline and permits retry", async ({
  page,
  request,
}) => {
  const data = await seedDirect(request);
  const form = await inspectCut(page, data.movie.title);
  await page.route(`**/api/direction/cuts/${data.cut.id}`, async (route) => {
    if (route.request().method() !== "PATCH") return route.continue();
    await route.fulfill({
      status: 422,
      json: { detail: "A cut needs a different target angle" },
    });
  });
  await form.getByLabel("Delivered frame").fill("40");
  await form.getByRole("button", { name: "Save cut" }).click();
  await expect(form.getByRole("alert")).toContainText(
    "A cut needs a different target angle",
  );
  await expect(form.getByLabel("Delivered frame")).toHaveValue("40");
  await page.unrouteAll({ behavior: "wait" });
  await form.getByRole("button", { name: "Save cut" }).click();
  const saved = page.getByRole("form", { name: "Edit cut at f40" });
  await expect(saved).toBeVisible();
  await expect(saved.getByRole("alert")).toHaveCount(0);
  // Saving unrelated fields must retain the authored angle relation.
  const response = await request.get(`/api/direction/cuts/${data.cut.id}`, {
    headers: data.headers,
  });
  expect(await response.json()).toMatchObject({
    relation: "reverse",
    from_angle_id: data.from.id,
    to_angle_id: data.to.id,
  });
});
