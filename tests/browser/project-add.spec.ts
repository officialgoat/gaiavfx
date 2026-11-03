import { expect, test } from "@playwright/test";
import { openMovie, seedDirect } from "./direct-fixture";

// Round 1 of the blind usability campaign (T1-F1): a newcomer found no way to put characters or places into a movie.
// The Project panel's Cast and Places bins now add them, and Undo names what it undoes.

test("the Project panel adds a character and a place, and Undo names the change", async ({
  page,
  request,
}) => {
  const data = await seedDirect(request);
  await openMovie(page, data.movie.title);
  const project = page.locator(".project-bins");
  await project.getByRole("button", { name: /^Cast/ }).click();
  await page.getByRole("button", { name: /Add a character/ }).click();
  await page.getByLabel("Name", { exact: true }).fill("Tomas");
  await page.getByLabel("Who they are").fill("An elderly bookseller");
  await page.getByLabel(/Always the same/).fill("round glasses, cardigan");
  await page
    .getByLabel("Identity picture", { exact: true })
    .selectOption({ index: 1 });
  await page.getByRole("button", { name: "Add to the cast" }).click();
  await expect(
    page.locator(".project-content").getByRole("button", { name: /^Tomas/ }),
  ).toBeVisible();

  await project.getByRole("button", { name: /^Places/ }).click();
  await page.getByRole("button", { name: /Add a place/ }).click();
  await page.getByLabel("Place", { exact: true }).fill("Rooftop");
  await page.getByRole("button", { name: "Add the place" }).click();
  await expect(
    page.locator(".project-content").getByRole("button", { name: /^Rooftop/ }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.getByRole("menuitem", { name: /^Undo: / })).toBeVisible();
  await page.keyboard.press("Escape");
});
