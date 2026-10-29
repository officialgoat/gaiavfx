import { expect, test } from "@playwright/test";
import { seedProduction } from "./production-fixture";
import { openMovie } from "./direct-fixture";

test("a new plate loads in Source before approval and renews an unauthorized preview URL", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  const data = await seedProduction(request);
  const op = await data.post<any>("/api/production/removals", {
    place_id: data.from.place_id,
    angle_id: data.from.id,
    source_asset: data.asset.id,
    wording: {
      whom: "the walker",
      object_pronoun: "him",
      behind: "stone walls",
    },
    seeds: [9101],
    request_key: crypto.randomUUID(),
  });
  await data.finished(op.id);
  const review = await data.post<any>(
    `/api/production/removals/${op.id}/review`,
  );
  const asset = review.members[0].asset_id;
  let urlReads = 0;
  await page.route(`**/assets/${asset}/urls`, async (route) => {
    const response = await route.fetch();
    const urls = await response.json();
    if (++urlReads === 1)
      urls.preview = urls.preview.replace(
        /signature=[^&]+/,
        "signature=expired",
      );
    await route.fulfill({ json: urls });
  });
  await openMovie(page, data.movie.title);
  await page.getByRole("button", { name: /Cut f36/ }).click();
  await page
    .getByRole("navigation", { name: "Workspaces" })
    .getByRole("button", { name: "Prepare", exact: true })
    .click();
  const plate = page.getByRole("region", { name: "Plate", exact: true });
  await plate
    .locator(".prepare-attempt")
    .filter({ hasText: op.id.slice(0, 8) })
    .getByRole("button", { name: "Review in Source" })
    .click();
  const source = page.getByRole("region", { name: "Source monitor" });
  await expect
    .poll(() =>
      source
        .locator("img")
        .evaluate(
          (img: HTMLImageElement) => img.complete && img.naturalWidth > 0,
        ),
    )
    .toBe(true);
  expect(urlReads).toBe(2);
  await expect(
    page.getByRole("button", { name: /Candidate A.*Awaiting your review/ }),
  ).toBeVisible();
});
