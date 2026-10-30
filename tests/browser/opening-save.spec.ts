import { expect, test } from "@playwright/test";
import { openMovie, seedDirect } from "./direct-fixture";

test("a zero-cut first shot saves automatic technique and repeated saves are harmless", async ({
  page,
  request,
}) => {
  const data = await seedDirect(request);
  await openMovie(page, data.movie.title);
  await page.locator(".segment-item").first().click();
  await page.getByRole("tab", { name: "Properties", exact: true }).click();
  const opening = page.getByRole("region", { name: "First shot", exact: true });
  await expect(opening.getByLabel("Technique override")).toHaveCount(0);
  await opening.getByLabel("Opening angle").selectOption(data.from.id);
  await opening
    .getByRole("button", { name: "Save first shot", exact: true })
    .click();
  await expect(opening.getByLabel("Technique override")).toHaveValue("");

  const read = async () => {
    const response = await request.get(
      `/api/direction/segments/${data.first.id}`,
      { headers: data.headers },
    );
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  };
  const saved = await read();
  expect(saved.opening_angle_id).toBe(data.from.id);
  expect(saved.opening_technique).toBeNull();
  for (let attempt = 0; attempt < 3; attempt++) {
    await opening
      .getByRole("button", { name: "Save first shot", exact: true })
      .click();
    await expect(
      page.getByText("First shot unchanged", { exact: true }),
    ).toBeVisible();
    await expect(opening.getByRole("alert")).toHaveCount(0);
    expect((await read()).revision).toBe(saved.revision);
  }
  await page.reload();
  await page.locator(".segment-item").first().click();
  await page.getByRole("tab", { name: "Properties", exact: true }).click();
  await expect(opening.getByLabel("Opening angle")).toHaveValue(data.from.id);
  await expect(opening.getByLabel("Technique override")).toHaveValue("");
});
