import { test, expect } from "@playwright/test";
import { seedProduction } from "./production-fixture";
import { openMovie } from "./direct-fixture";
import type { components } from "../../frontend/src/shared/api.generated";
type S = components["schemas"];

test("replace a pin explicitly, mark a dependent take stale, and undo", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  const data = await seedProduction(request, true);
  const pins = await data.get<S["KeyframeDTO"][]>(
    `/api/direction/cuts/${data.cut.id}/keyframes`,
  );
  const old = pins.find((p) => p.approved)!;
  await data.post(`/api/production/reviews/${data.review!.id}/select`, {
    candidate_id: data.review!.members[0].candidate_id,
    expected_revision: 0,
  });
  const insertion = await data.post<S["OperationDTO"]>(
    "/api/production/insertions",
    {
      cut_id: data.cut.id,
      evidence_id: old.evidence_id,
      subject_asset: data.asset.id,
      seeds: [74],
      wording: {
        who: "Arin",
        looks: "plaid shirt",
        pronouns: "he",
        facing: "toward_camera",
        plate: "empty courtyard",
        keep: "walls",
        where: "by rail",
        size: "one third",
        behind: "the arcade",
      },
    },
  );
  await data.finished(insertion.id);
  const review = await data.post<S["ReviewDTO"]>(
    `/api/production/insertions/${insertion.id}/review`,
  );
  const refusal = await request.post("/api/production/pins/approve", {
    headers: data.headers,
    data: { candidate_id: review.members[0].candidate_id },
  });
  expect(refusal.status()).toBe(409);
  expect(await refusal.text()).toContain("replace: true");
  await openMovie(page, data.movie.title);
  await page.getByRole("button", { name: /Cut f36/ }).click();
  await page
    .getByRole("navigation", { name: "Workspaces" })
    .getByRole("button", { name: "Prepare", exact: true })
    .click();
  const flow = page.getByRole("region", { name: "Prepare reference flow" });
  await flow
    .getByRole("region", { name: "Pin", exact: true })
    .getByRole("button", { name: "Review in Source" })
    .first()
    .click();
  await page
    .getByRole("button", { name: "Approve reference", exact: true })
    .click();
  const confirmation = page.getByRole("region", {
    name: "Replace the current pin?",
  });
  await expect(confirmation).toBeVisible();
  await expect(confirmation.locator("img")).toHaveCount(2);
  await expect
    .poll(() =>
      confirmation
        .locator("img")
        .evaluateAll((imgs) =>
          imgs.every((img) => (img as HTMLImageElement).naturalWidth > 0),
        ),
    )
    .toBe(true);
  await confirmation.getByRole("button", { name: "Keep current pin" }).click();
  await expect(confirmation).toHaveCount(0);
  await expect(page.locator(".take-stale")).toHaveCount(0);
  await page
    .getByRole("button", { name: "Approve reference", exact: true })
    .click();
  await expect(
    confirmation.getByRole("button", { name: "Replace pin", exact: true }),
  ).toBeInViewport({ ratio: 1 });
  await page.screenshot({ path: ".cache/u6-pin-replacement.png" });
  const approved = page.waitForResponse(
    (response) =>
      response.url().endsWith("/pins/approve") &&
      response.request().method() === "POST",
  );
  await confirmation
    .getByRole("button", { name: "Replace pin", exact: true })
    .click();
  const response = await approved;
  expect(response.status()).toBe(200);
  expect(response.request().postDataJSON().replace).toBe(true);
  const replacement = await response.json();
  expect(replacement.receipt.command).toBe("replace_pin");
  await expect(confirmation).toHaveCount(0);
  await expect(page.locator(".take-stale")).toContainText(
    "Changed since this take",
  );
  const history = page.getByRole("region", { name: "Pin history" });
  await expect(history.getByText("Replacement history (1)")).toBeVisible();
  await history.getByText("Replacement history (1)").click();
  await expect(
    history.getByText("Superseded pin", { exact: true }),
  ).toBeVisible();
  await flow.focus();
  await page.keyboard.press("Control+z");
  await expect(page.locator(".take-stale")).toHaveCount(0);
  const restored = await data.get<S["KeyframeDTO"][]>(
    `/api/direction/cuts/${data.cut.id}/keyframes`,
  );
  expect(restored.find((p) => p.approved)?.id).toBe(old.id);
  expect(restored).toHaveLength(2);
});
