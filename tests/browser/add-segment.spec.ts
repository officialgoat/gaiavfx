import { test, expect, type APIRequestContext } from "@playwright/test";
import { browserTestOrigin } from "../browser-origin";

// Usability: Movie > Add segment works on a movie with no cast, a segment may have no actors, and a refused edit says
// why without asking to retry a conflict that did not happen.

type Row = { id: string; revision: number };
type Movie = Row & {
  segments: {
    content: { subjects: string[] };
    phases: { content: { cast?: string[] } }[];
  }[];
};

async function api(request: APIRequestContext) {
  const session = await request.post("/api/v1/session", {
    headers: { "X-GAIAVFX-Bootstrap": "1", Origin: browserTestOrigin },
  });
  const headers = { Authorization: `Bearer ${(await session.json()).token}` };
  return async <T>(method: string, url: string, data?: unknown) => {
    const response = await request.fetch(url, { method, headers, data });
    expect(response.ok(), await response.text()).toBeTruthy();
    return (await response.json()) as T;
  };
}

async function openMovie(page: import("@playwright/test").Page, title: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "File", exact: true }).click();
  await page.getByRole("menuitem", { name: /Open movie…/ }).click();
  await page
    .getByRole("dialog", { name: "Open movie", exact: true })
    .getByRole("button", { name: title, exact: true })
    .click();
}

async function addSegment(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: "Movie", exact: true }).click();
  await page.getByRole("menuitem", { name: /Add segment…/ }).click();
  const dialog = page.getByRole("dialog", { name: "Add segment", exact: true });
  await dialog.getByLabel("Place").fill("London Bridge at night");
  await dialog
    .getByLabel("Opening direction")
    .fill("A ship passes closely under London Bridge.");
  return dialog;
}

test("a movie with no cast gets a segment with no actors", async ({
  page,
  request,
}) => {
  const send = await api(request);
  const title = `No cast ${Date.now()}`;
  const movie = await send<Row>("POST", "/api/direction/movies", { title });
  await openMovie(page, title);
  const dialog = await addSegment(page);
  await expect(dialog.getByRole("group", { name: "Cast" })).toContainText(
    "This movie has no cast yet",
  );
  await dialog.getByRole("button", { name: "Create segment" }).click();
  await expect(dialog).toBeHidden();
  await expect(
    page.getByRole("button", { name: "Segment 01", exact: true }),
  ).toBeVisible();
  const saved = await send<Movie>("GET", `/api/direction/movies/${movie.id}`);
  expect(saved.segments[0].content.subjects).toEqual([]);
  expect(saved.segments[0].phases[0].content.cast).toEqual([]);
});

test("the ticked cast is the segment's and its opening phase's cast", async ({
  page,
  request,
}) => {
  const send = await api(request);
  const title = `Cast ${Date.now()}`;
  const movie = await send<Row>("POST", "/api/direction/movies", { title });
  const ivo = await send<Row>(
    "POST",
    `/api/direction/movies/${movie.id}/subjects`,
    {
      expected_revision: movie.revision,
      name: "Ivo",
      description: "An old fisher in a wool sweater",
      keeps: [],
      identity_asset_id: null,
    },
  );
  await openMovie(page, title);
  const dialog = await addSegment(page);
  await dialog.getByRole("checkbox", { name: "Ivo" }).check();
  await dialog.getByRole("button", { name: "Create segment" }).click();
  await expect(dialog).toBeHidden();
  const saved = await send<Movie>("GET", `/api/direction/movies/${movie.id}`);
  expect(saved.segments[0].content.subjects).toEqual([ivo.id]);
  expect(saved.segments[0].phases[0].content.cast).toEqual([ivo.id]);
});

test("a refused edit shows its reason without asking to retry a conflict", async ({
  page,
  request,
}) => {
  const send = await api(request);
  const title = `Refused ${Date.now()}`;
  await send<Row>("POST", "/api/direction/movies", { title });
  await openMovie(page, title);
  await page.route("**/api/direction/movies/*/segments", (route) =>
    route.request().method() === "POST"
      ? route.fulfill({
          status: 422,
          json: { detail: "place.noun must be non-empty" },
        })
      : route.continue(),
  );
  const dialog = await addSegment(page);
  await dialog.getByRole("button", { name: "Create segment" }).click();
  const alert = dialog.getByRole("alert");
  await expect(alert).toHaveText("place.noun must be non-empty");
});
