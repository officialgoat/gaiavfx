import { test, expect, type Page } from "@playwright/test";
import { openMovie, seedDirect } from "./direct-fixture";

// ADR 0017 makes the timeline a generic renderer of a presentation document. The movie timeline must not change:
// the accessibility and geometry snapshots were recorded on the renderer before that refactor (f6373b2); the
// normalized DOM and the zoomed and scrolled geometry were recorded after it (U1), before the film extensions.
// Deliberately re-recorded once since, for the Continuation row (docs/CONTINUATION.md): a 52 px row under Segment,
// the rows below it moved down by its height and nothing else changed.

function geometry(page: Page, origin = ".timeline-canvas") {
  return page.evaluate((origin) => {
    const canvas = document.querySelector(origin)!.getBoundingClientRect();
    const box = (element: Element) => {
      const rect = element.getBoundingClientRect();
      return [
        Math.round(rect.left - canvas.left),
        Math.round(rect.top - canvas.top),
        Math.round(rect.width),
        Math.round(rect.height),
      ];
    };
    return {
      rows: [...document.querySelectorAll(".track-row")].map((row) => ({
        label: row.querySelector(".track-label")?.textContent,
        box: box(row),
      })),
      items: [...document.querySelectorAll(".track-item")].map((item) => ({
        label: item.getAttribute("aria-label"),
        frame: item.getAttribute("data-frame"),
        className: item.className,
        box: box(item),
      })),
      ticks: [...document.querySelectorAll(".ruler-scale span")].map((tick) => [
        tick.textContent,
        box(tick)[0],
      ]),
      playhead: box(document.querySelector(".playhead-line")!),
      scrollLeft: document.querySelector(".timeline-scroll")!.scrollLeft,
    };
  }, origin);
}

test.describe("movie timeline lock", () => {
  let data: Awaited<ReturnType<typeof seedDirect>>;
  test.beforeAll(async ({ request }) => {
    data = await seedDirect(request);
  });

  test("accessibility tree and item geometry stay the same", async ({
    page,
  }) => {
    await openMovie(page, data.movie.title);
    await expect(page.locator(".timeline-heading")).toContainText("24 fps");
    await expect(page.locator(".timeline-tools")).toMatchAriaSnapshot({
      name: "movie-timeline-tools.aria.yml",
    });
    await expect(page.locator(".timeline-context")).toMatchAriaSnapshot({
      name: "movie-timeline-tracks.aria.yml",
    });
    await expect(page.locator(".timeline-navigator")).toMatchAriaSnapshot({
      name: "movie-timeline-navigator.aria.yml",
    });
    expect(JSON.stringify(await geometry(page), null, 1)).toMatchSnapshot(
      "movie-timeline-geometry.json",
    );
  });

  test("normalized DOM and zoomed and scrolled geometry stay the same", async ({
    page,
  }) => {
    await openMovie(page, data.movie.title);
    const dom = await page.evaluate((title) => {
      const parts = [
        ".timeline-heading",
        ".timeline-tools",
        ".timeline-context",
        ".timeline-navigator",
      ].map((selector) => document.querySelector(selector)!.outerHTML);
      return parts
        .join("\n")
        .replaceAll(title, "MOVIE TITLE")
        .replace(/radix-[\w:-]+/g, "radix-id")
        .replace(/></g, ">\n<");
    }, data.movie.title);
    expect(dom).toMatchSnapshot("movie-timeline-dom.html");
    for (let step = 0; step < 4; step += 1)
      await page.getByRole("button", { name: "Zoom in" }).click();
    expect(JSON.stringify(await geometry(page), null, 1)).toMatchSnapshot(
      "movie-timeline-geometry-zoomed.json",
    );
    await page.locator(".timeline-scroll").evaluate((element) => {
      element.scrollLeft = 400;
      element.dispatchEvent(new Event("scroll"));
    });
    await page.waitForTimeout(100);
    expect(
      JSON.stringify(await geometry(page, ".timeline-scroll"), null, 1),
    ).toMatchSnapshot("movie-timeline-geometry-scrolled.json");
  });
});
