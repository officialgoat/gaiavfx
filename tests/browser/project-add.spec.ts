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

  // A segment's cast is ticked in its Properties (round 2, R1-F2: a segment made without cast stayed so).
  await page.getByRole("button", { name: "Segment 01", exact: true }).click();
  await page.getByRole("tab", { name: "Properties" }).click();
  const cast = page.getByRole("group", { name: "Cast in this segment" });
  await cast.getByRole("checkbox", { name: "Tomas" }).click();
  await expect(cast.getByRole("checkbox", { name: "Tomas" })).toBeChecked();

  // Home goes to the start from anywhere in the editor (R3-F1).
  const head = page.getByRole("slider", { name: "Playhead", exact: true });
  await head.focus();
  await page.keyboard.press("End");
  await page.getByRole("button", { name: "Segment 01", exact: true }).focus();
  await page.keyboard.press("Home");
  await expect(head).toHaveAttribute("aria-valuenow", "0");
});
