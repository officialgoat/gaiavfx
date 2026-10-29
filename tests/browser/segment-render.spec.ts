import { expect, test } from "@playwright/test";
import { openMovie, seedDirect } from "./direct-fixture";
import type { components } from "../../frontend/src/shared/api.generated";
import { reviewCommands } from "../../frontend/src/features/review/commands";
import { directPreviews } from "../../frontend/src/features/project/client";
import type { DirectController } from "../../frontend/src/features/project/use-direction";
import type { ReviewController } from "../../frontend/src/features/review/use-review";

type S = components["schemas"];

test("a zero-cut segment reviews and previews only its own render", () => {
  let opened = "";
  const d = {
    getCut: () => undefined,
    getSegment: () => ({ id: "zero", cuts: [] }),
  } as unknown as DirectController;
  const r = {
    openOperation: (o: { id: string }) => {
      opened = o.id;
    },
  } as unknown as ReviewController;
  const operations = ["other", "zero"].map((id) => ({
    id,
    kind: "render",
    state: "succeeded",
    request: { segment_id: id },
    candidates: [{ id, asset_id: id, kind: "video" }],
  })) as unknown as S["OperationDTO"][];
  reviewCommands(d, r, operations, "prepare", () => {})
    .find((c) => c.id === "review")!
    .run({ kind: "segment", id: "zero", segmentId: "zero" });
  expect(opened).toBe("zero");
  expect(
    directPreviews(operations, [], "zero").map((c) => c.candidate_id),
  ).toEqual(["zero"]);
});

// Usability B19 (T8-F2): render requirements and the Render menu name a cut as the timeline does, never by its id.
test("render requirements name a cut by number and frame, not by its id", async ({
  page,
  request,
}) => {
  const data = await seedDirect(request);
  const uuid = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
  await openMovie(page, data.movie.title);
  await page.getByRole("button", { name: /Cut f36/ }).click();
  await page.getByRole("tab", { name: "Properties", exact: true }).click();
  const requirements = page
    .locator(".properties-content")
    .getByLabel("Render requirements");
  await expect(requirements).toContainText("Cut 1 at f36: ");
  expect(await requirements.innerText()).not.toMatch(uuid);
  await page.getByRole("button", { name: "Render", exact: true }).click();
  const render = page.getByRole("menuitem", { name: /Render segment/ });
  await expect(render).toHaveAttribute("aria-disabled", "true");
  await expect(render).toHaveAttribute("title", /^Cut 1 at f36: /);
  expect(await render.getAttribute("title")).not.toMatch(uuid);
  await page.keyboard.press("Escape");
});

test("Prepare and Properties render continuous and two-cut segments", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  const data = await seedDirect(request);
  const headers = data.headers;
  async function get<T>(url: string): Promise<T> {
    const response = await request.get(url, { headers });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }
  async function write<T>(
    url: string,
    body: unknown,
    method = "POST",
  ): Promise<T> {
    const response = await request.fetch(url, { headers, method, data: body });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }
  for (const s of [data.first, data.second]) {
    const row = await get<S["SegmentDTO"]>(`/api/direction/segments/${s.id}`);
    await write(
      `/api/direction/segments/${s.id}`,
      {
        expected_revision: row.revision,
        content: { ...row.content, states: [] },
      },
      "PATCH",
    );
  }
  await openMovie(page, data.movie.title);
  await page.getByRole("button", { name: "Segment 01", exact: true }).click();
  await page
    .getByRole("navigation", { name: "Workspaces" })
    .getByRole("button", { name: "Prepare", exact: true })
    .click();
  const prepare = page.locator(".prepare-panel");
  await expect(
    prepare.getByText("Single continuous take", { exact: true }),
  ).toBeVisible();
  await expect(
    prepare.getByText("Ready to render", { exact: true }),
  ).toBeVisible();
  await prepare.getByRole("button", { name: /Render segment/ }).click();
  const form = page.getByRole("dialog", {
    name: "Render segment",
    exact: true,
  });
  await expect(
    form.getByText("Single continuous take", { exact: true }),
  ).toBeVisible();
  const sent = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/production/renders") &&
      r.request().method() === "POST",
  );
  await form
    .getByRole("button", { name: "Render segment", exact: true })
    .click();
  const response = await sent;
  expect(response.request().postDataJSON().segment_id).toBe(data.first.id);
  expect(response.request().postDataJSON()).not.toHaveProperty("cut_id");
  const first = (await response.json()) as S["OperationDTO"];
  await expect(form).toHaveCount(0);
  await expect
    .poll(
      async () =>
        (await get<S["OperationDTO"]>(`/api/production/operations/${first.id}`))
          .state,
      { timeout: 90_000 },
    )
    .toBe("succeeded");
  const review = await write<S["ReviewDTO"]>(
    `/api/production/renders/${first.id}/review`,
    {},
  );
  // An explicit user selection provides the next segment's motion context.
  await write(`/api/production/reviews/${review.id}/select`, {
    candidate_id: review.members[0].candidate_id,
    expected_revision: 0,
  });
  await write(
    `/api/direction/cuts/${data.cut.id}`,
    {
      expected_revision: data.cut.revision,
      relation: "same_direction",
      crosses_barrier: false,
    },
    "PATCH",
  );
  let second = await get<S["SegmentDTO"]>(
    `/api/direction/segments/${data.second.id}`,
  );
  for (const frame of [36, 72]) {
    if (!second.phases.some((p) => p.start_frame === frame)) {
      await write(`/api/direction/segments/${second.id}/phases`, {
        expected_revision: second.revision,
        start_frame: frame,
        content: {
          description: "Arin pauses.",
          screen_direction: "",
          sound: "Wind.",
        },
      });
      second = await get(`/api/direction/segments/${second.id}`);
    }
  }
  await write("/api/direction/cuts", {
    expected_revision: second.revision,
    segment_id: second.id,
    frame: 72,
    from_angle_id: data.to.id,
    to_angle_id: data.from.id,
    relation: "same_direction",
    relation_source: "authored",
    crosses_barrier: false,
    receives_predecessor: true,
  });
  await page.reload();
  await page.getByRole("button", { name: /Cut f36/ }).click();
  await page.getByRole("tab", { name: "Properties", exact: true }).click();
  const properties = page.locator(".properties-content");
  await expect(
    properties.getByLabel("Cut readiness").getByRole("listitem"),
  ).toHaveCount(2);
  await properties.getByRole("button", { name: /Render segment/ }).click();
  await expect(
    form.getByLabel("Cut readiness").getByRole("listitem"),
  ).toHaveCount(2);
  await expect(
    form.getByText("Ready to render", { exact: true }),
  ).toBeVisible();
  await page.screenshot({ path: ".cache/g1-segment-form.png" });
  const multi = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/production/renders") &&
      r.request().method() === "POST",
  );
  await form
    .getByRole("button", { name: "Render segment", exact: true })
    .click();
  const rendered = (await (await multi).json()) as S["OperationDTO"];
  expect(rendered.request.segment_id).toBe(second.id);
  expect(rendered.request.cut_ids).toHaveLength(2);
});
