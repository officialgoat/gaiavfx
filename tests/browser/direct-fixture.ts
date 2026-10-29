import { expect, type APIRequestContext, type Page } from "@playwright/test";
import fs from "node:fs/promises";
import { browserTestOrigin } from "../browser-origin";
import type { components } from "../../frontend/src/shared/api.generated";
type Schema = components["schemas"];

export async function seedDirect(request: APIRequestContext) {
  const session = await request.post("/api/v1/session", {
    headers: { "X-GAIAVFX-Bootstrap": "1", Origin: browserTestOrigin },
  });
  expect(session.ok(), await session.text()).toBeTruthy();
  const headers = { Authorization: `Bearer ${(await session.json()).token}` };
  async function post<T>(path: string, data: unknown): Promise<T> {
    const response = await request.post(path, { headers, data });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }
  const imported = await request.post("/api/v1/imports", {
    headers,
    multipart: {
      file: {
        name: "Arin reference.jpg",
        mimeType: "image/jpeg",
        buffer: await fs.readFile("tests/fixtures/concept/arin.jpg"),
      },
    },
  });
  expect(imported.ok(), await imported.text()).toBeTruthy();
  const asset = (await imported.json()).asset as Schema["AssetDTO"];
  const plateResponse = await request.post("/api/v1/imports", {
    headers,
    multipart: {
      file: {
        name: "Courtyard.jpg",
        mimeType: "image/jpeg",
        buffer: await fs.readFile("tests/fixtures/concept/view-0.jpg"),
      },
    },
  });
  expect(plateResponse.ok(), await plateResponse.text()).toBeTruthy();
  const place = await post<Schema["PlaceDTO"]>("/api/direction/places", {
    name: "Arcade courtyard",
  });
  const from = await post<Schema["AngleDTO"]>(
    `/api/direction/places/${place.id}/angles`,
    { name: "Approach" },
  );
  const to = await post<Schema["AngleDTO"]>(
    `/api/direction/places/${place.id}/angles`,
    { name: "Reverse" },
  );
  const movie = await post<Schema["MovieCommandDTO"]>("/api/direction/movies", {
    title: `Arcade courtyard · ${Math.random().toString(36).slice(2, 6)}`,
  });
  const subject = await post<Schema["SubjectCommandDTO"]>(
    `/api/direction/movies/${movie.id}/subjects`,
    {
      expected_revision: movie.revision,
      name: "Arin",
      description: "A walker in plaid, carrying a satchel.",
      keeps: ["plaid shirt", "satchel"],
      identity_asset_id: asset.id,
    },
  );
  const content = {
    subjects: [subject.id],
    place: {
      noun: "courtyard",
      description: "Stone arches behind a black iron railing.",
      landmarks: "arched arcade, railing",
    },
    style: "Natural late-afternoon light.",
    events: [{ at_frame: 70, description: "A gate clicks shut." }],
    speech: [
      {
        speaker_id: subject.id,
        at_frame: 60,
        text: "Meet me beyond the arcade.",
        language: "English",
        delivery: "quietly",
        off_screen: false,
      },
    ],
    states: [
      {
        at_frame: 0,
        subject_id: subject.id,
        attribute: "satchel",
        value: "over the left shoulder",
      },
    ],
  };
  const phase = {
    start_frame: 0,
    content: {
      description: "Arin walks toward the iron railing.",
      screen_direction: "left to right",
      sound: "Footsteps on stone; a soft courtyard breeze.",
      cast: [subject.id],
    },
  };
  const first = await post<Schema["SegmentCommandDTO"]>(
    `/api/direction/movies/${movie.id}/segments`,
    {
      expected_revision: subject.receipt.after.revision,
      content,
      delivered_frames: 120,
      continues: false,
      phases: [phase],
    },
  );
  const second = await post<Schema["SegmentCommandDTO"]>(
    `/api/direction/movies/${movie.id}/segments`,
    {
      expected_revision: first.receipt.after.revision,
      content,
      delivered_frames: 120,
      continues: true,
      phases: [phase],
    },
  );
  const cut = await post<Schema["CutCommandDTO"]>("/api/direction/cuts", {
    expected_revision: second.revision,
    segment_id: second.id,
    frame: 36,
    from_angle_id: from.id,
    to_angle_id: to.id,
    relation: "reverse",
    relation_source: "authored",
    crosses_barrier: true,
    receives_predecessor: true,
  });
  return { movie, first, second, cut, from, to, asset, headers };
}
export async function openMovie(page: Page, title: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "File", exact: true }).click();
  await page
    .getByRole("menuitem", { name: "Open movie…", exact: false })
    .click();
  await page
    .getByRole("dialog", { name: "Open movie", exact: true })
    .getByRole("button", { name: title, exact: true })
    .click();
  await expect(
    page.locator(".cut-item").filter({ hasText: "f36" }),
  ).toBeVisible();
}
