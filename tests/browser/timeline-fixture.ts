import type { Page } from "@playwright/test";

// The timeline's zoom is its scroll bar: the thumb is the visible span, and its end handles set how much shows
// (TimelineNavigator). These helpers zoom as a person does, by dragging an end or from the View menu.

/** Fits the timeline (double-click on the scroll bar), then drags its end handle so 1/zoom of the timeline shows. */
export async function setZoom(page: Page, zoom: number) {
  await page.getByRole("slider", { name: "Timeline scroll" }).dblclick();
  const width = await page
    .locator(".timeline-range")
    .evaluate((el) => el.clientWidth);
  const end = (await page
    .getByRole("slider", { name: "Visible end" })
    .boundingBox())!;
  const x = end.x + end.width / 2;
  const y = end.y + end.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x - width * (1 - 1 / zoom), y, { steps: 5 });
  await page.mouse.up();
}

/** A timeline command from the View menu ("Zoom in", "Zoom out", "Fit timeline"), `times` over. */
export async function viewCommand(page: Page, name: string, times = 1) {
  for (let i = 0; i < times; i++) {
    await page.getByRole("button", { name: "View", exact: true }).click();
    await page.getByRole("menuitem", { name: new RegExp(`^${name}`) }).click();
  }
}

/** The last visible frame: it falls as the timeline zooms in. */
export async function visibleEnd(page: Page) {
  return Number(
    await page
      .getByRole("slider", { name: "Visible end" })
      .getAttribute("aria-valuenow"),
  );
}
