import { expect, test } from "@playwright/test";
import { openMovie, seedDirect } from "./direct-fixture";
import { setZoom, visibleEnd } from "./timeline-fixture";

// The user's polish requests: the player plays the whole timeline instead of stopping at each segment's end, and the
// scroll bar's ends zoom it instead of zoom buttons.

test("Space plays the movie on through its segments, takes or none", async ({
  page,
  request,
}) => {
  const data = await seedDirect(request);
  await openMovie(page, data.movie.title);
  const head = page.getByRole("slider", { name: "Playhead", exact: true });
  await head.focus();
  await page.keyboard.press("Home");
  await page.keyboard.press(" ");
  // The first segment is 120 frames; the playhead passes its end into the second and keeps going.
  await expect
    .poll(async () => Number(await head.getAttribute("aria-valuenow")), {
      timeout: 12000,
    })
    .toBeGreaterThan(130);
  await page.keyboard.press(" ");
  // Paused, it holds still (read once the pause has landed).
  await page.waitForTimeout(300);
  const stopped = Number(await head.getAttribute("aria-valuenow"));
  await page.waitForTimeout(600);
  expect(Number(await head.getAttribute("aria-valuenow"))).toBe(stopped);
});

test("the scroll bar's ends zoom the timeline, by pointer and by keyboard", async ({
  page,
  request,
}) => {
  const data = await seedDirect(request);
  await openMovie(page, data.movie.title);
  await expect(page.getByRole("button", { name: "Zoom in" })).toHaveCount(0);
  await expect(page.getByLabel("Timeline zoom")).toHaveCount(0);
  const fitted = await visibleEnd(page);
  await setZoom(page, 4);
  const zoomed = await visibleEnd(page);
  expect(zoomed).toBeGreaterThan(fitted / 4 - 4);
  expect(zoomed).toBeLessThan(fitted / 4 + 4);
  // The end handle's arrow shows a little more; the thumb's End scrolls to the timeline's end.
  await page.getByRole("slider", { name: "Visible end" }).focus();
  await page.keyboard.press("ArrowRight");
  expect(await visibleEnd(page)).toBeGreaterThan(zoomed);
  const thumb = page.getByRole("slider", { name: "Timeline scroll" });
  await thumb.focus();
  await page.keyboard.press("End");
  await expect(
    page.getByRole("slider", { name: "Visible end" }),
  ).toHaveAttribute("aria-valuenow", String(fitted));
  expect(
    Number(
      await page
        .getByRole("slider", { name: "Visible start" })
        .getAttribute("aria-valuenow"),
    ),
  ).toBeGreaterThan(0);
  await thumb.dblclick();
  await expect(
    page.getByRole("slider", { name: "Visible start" }),
  ).toHaveAttribute("aria-valuenow", "0");
  expect(await visibleEnd(page)).toBe(fitted);
});
