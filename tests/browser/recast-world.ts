import {
  test as base,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { browserTestOrigin } from "../browser-origin";
import type { components } from "../../frontend/src/shared/api.generated";

type S = components["schemas"];

/** The recast a film spec starts from (tests/browser_recast.py Scene): its shots, frame rate and characters. */
export type Scene = {
  shots?: number;
  length?: number;
  rate?: [number, number];
  characters?: string[];
  title?: string;
  /** False: only the Library film, which asks before its analysis creates the recast. */
  recast?: boolean;
  analysed?: boolean;
  /** Each character's picture: the user's portrait, Gaia's portrait, the user's picture with no face, or none. */
  targets?: ("confirmed" | "proposed" | "faceless" | "unreadable" | null)[];
  /** Each picture's label (default Arin, Arin proposal or Hallway). */
  labels?: (string | null)[];
  /** Every character's picture is one Library picture. */
  shared?: boolean;
  /** The first shots in which every character is seen. */
  ensemble?: number;
  /** Pairs of characters (by index) whose faces look alike. */
  similar?: [number, number][];
  /** Characters (by index) the user has hidden. */
  hidden?: number[];
  /** Characters (by index) with no face crop yet. */
  faceless?: number[];
  /** Face tracks no character holds, from Shot 003 on. */
  unknown?: number;
  /** Each character's face sheet, made from its confirmed picture, taken to this state. */
  sheets?: ("waiting" | "approved" | "rejected" | null)[];
  planned?: boolean;
  /** The first shots with a finished Draft run's versions, `takes` of each. */
  rendered?: number;
  takes?: number;
  /** People a planned shot's window analysis found with no face, linked to a character (by index). */
  links?: {
    shot: number;
    character: number;
    state?: "proposed" | "confirmed";
    track?: string;
    evidence?: Record<string, unknown>;
  }[];
  /** More Library portraits, by name; the world names each `<name> · <tag>`. */
  pictures?: string[];
};

/** A real recast in the suite's library, seeded through the Recast test world (tests/browser_recast.py). */
export type RecastWorld = Awaited<ReturnType<typeof seedRecast>>;

async function authorize(request: APIRequestContext) {
  const session = await request.post("/api/v1/session", {
    headers: { "X-GAIAVFX-Bootstrap": "1", Origin: browserTestOrigin },
  });
  expect(session.ok(), await session.text()).toBeTruthy();
  return { Authorization: `Bearer ${(await session.json()).token}` };
}

export async function seedRecast(
  request: APIRequestContext,
  scene: Scene = {},
) {
  const headers = await authorize(request);
  const seeded = await request.post("/api/test/recast-world", {
    headers,
    data: scene,
  });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
  const world = (await seeded.json()) as {
    id: string;
    title: string;
    film: { asset_id: string; name: string };
    shots: string[];
    characters: string[];
    pictures: { id: string; label: string }[];
  };
  const at = `/api/test/recast-world/${world.id}`;
  async function call(method: "POST" | "DELETE", path: string, data = {}) {
    const response = await request.fetch(at + path, { method, headers, data });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }
  return {
    ...world,
    headers,
    /** End the waiting fixture jobs of one operation kind (the oldest `count` operations, or all; only head-pass
     * windows with `head`), then feed the recast as the runner's timer would. */
    settle: (
      kind: string,
      outcome = "succeeded",
      only: { count?: number; head?: boolean } = {},
    ) => call("POST", "/settle", { kind, outcome, ...only }),
    feed: () => call("POST", "/feed"),
    /** The user sets a new picture for a character; resolves once its face check has run. */
    target: (
      character: number,
      kind: "confirmed" | "faceless" | "unreadable",
    ) => call("POST", "/target", { character, kind }),
    /** The recast as the service reads it now. */
    read: async () => {
      const response = await request.get(`/api/recasts/${world.id}`, {
        headers,
      });
      expect(response.ok(), await response.text()).toBeTruthy();
      return (await response.json()) as S["RecastDTO"];
    },
    /** Retires the recast; for a film seeded with none, the one the page created. */
    retire: () =>
      world.id
        ? call("DELETE", "")
        : request
            .delete(`/api/test/recast-world/films/${world.film.asset_id}`, {
              headers,
            })
            .then((r) => r.json()),
    /** Another client's command at the recast's current revision (the agent, another window); a DELETE sends the
     * revision as a query. */
    elsewhere: async (
      method: "POST" | "PUT" | "PATCH" | "DELETE",
      path: string,
      data: Record<string, unknown> = {},
    ) => {
      const current = await request.get(`/api/recasts/${world.id}`, {
        headers,
      });
      const revision = ((await current.json()) as S["RecastDTO"]).revision;
      const response = await request.fetch(
        `/api/recasts/${world.id}${path}` +
          (method === "DELETE" ? `?expected_revision=${revision}` : ""),
        {
          method,
          headers,
          data:
            method === "DELETE"
              ? undefined
              : { expected_revision: revision, ...data },
        },
      );
      expect(response.ok(), await response.text()).toBeTruthy();
      return (await response.json()) as S["RecastDTO"];
    },
  };
}

/** `world` is a fresh recast for each test, retired after it; a spec sets its `scene` with test.use. */
export const test = base.extend<{ scene: Scene; world: RecastWorld }>({
  scene: [{}, { option: true }],
  // Seeding owns its time: a cold media cache encodes the film once.
  world: [
    async ({ request, scene }, use) => {
      const world = await seedRecast(request, scene);
      await use(world);
      await world.retire();
    },
    { timeout: 120_000 },
  ],
});
export { expect };

/** The Recast commands the page sends: every request under /api/recasts that is not a read, with its body. */
export function recastCommands(page: Page) {
  const calls: {
    method: string;
    path: string;
    body: Record<string, unknown>;
  }[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/recasts") && request.method() !== "GET")
      calls.push({
        method: request.method(),
        path: url.pathname.replace("/api/recasts", ""),
        body: (request.postDataJSON() as Record<string, unknown>) ?? {},
      });
  });
  return calls;
}

/** Opens the world's film from File › Open film. */
export async function openFilm(page: Page, world: RecastWorld) {
  await page.getByRole("button", { name: "File", exact: true }).click();
  await page.getByRole("menuitem", { name: /Open film/ }).click();
  await page
    .getByRole("dialog", { name: "Open film" })
    .getByRole("button", { name: new RegExp(world.title) })
    .first()
    .click();
  await expect(page.locator(".timeline-heading")).toContainText(world.title);
}

/** Reloads the page with the world's film open. */
export async function reopen(page: Page, world: RecastWorld) {
  await page.reload();
  await expect(page.locator(".timeline-heading")).toContainText(world.title);
}

/** No version's asset name reaches the page: text, titles, labels, alt text or values. */
export async function expectBlind(page: Page) {
  const leaks = await page.evaluate(() => {
    const secret = "PRIVATE SEED ARM ROUND";
    const found: string[] = [];
    if (document.body.innerText.includes(secret)) found.push("text");
    for (const element of Array.from(document.querySelectorAll("*")))
      for (const attribute of Array.from(element.attributes))
        if (attribute.value.includes(secret))
          found.push(`${element.tagName} ${attribute.name}`);
    return found;
  });
  expect(leaks).toEqual([]);
}

/** A character's lane header, the Properties card, the Project panel and the film's notice. */
export const laneHeader = (id: string) =>
  `[data-row="cast-${id}"] .cast-header`;
export const card = (page: Page) => page.locator(".film-properties");
export const panel = (page: Page) => page.locator(".film-project");
export const notice = (page: Page) => page.locator(".film-message.notice");
