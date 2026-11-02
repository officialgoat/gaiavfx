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
  analysed?: boolean;
  /** Each character's picture: the user's portrait, Gaia's portrait, the user's picture with no face, or none. */
  targets?: ("confirmed" | "proposed" | "faceless" | null)[];
  /** Pairs of characters (by index) whose faces look alike. */
  similar?: [number, number][];
  planned?: boolean;
  /** The first shots with a finished Draft run's versions, `takes` of each. */
  rendered?: number;
  takes?: number;
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
    /** End the waiting fixture jobs of one operation kind, then feed the recast as the runner's timer would. */
    settle: (kind: string, outcome = "succeeded") =>
      call("POST", "/settle", { kind, outcome }),
    feed: () => call("POST", "/feed"),
    /** The recast as the service reads it now. */
    read: async () => {
      const response = await request.get(`/api/recasts/${world.id}`, {
        headers,
      });
      expect(response.ok(), await response.text()).toBeTruthy();
      return (await response.json()) as S["RecastDTO"];
    },
    retire: () => call("DELETE", ""),
  };
}

/** `world` is a fresh recast for each test, retired after it; a spec sets its `scene` with test.use. */
export const test = base.extend<{ scene: Scene; world: RecastWorld }>({
  scene: [{}, { option: true }],
  world: async ({ request, scene }, use) => {
    const world = await seedRecast(request, scene);
    await use(world);
    await world.retire();
  },
});
export { expect };

/** The Recast commands the page sends: every request under /api/recasts that is not a read. */
export function recastCommands(page: Page) {
  const calls: { method: string; path: string }[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/recasts") && request.method() !== "GET")
      calls.push({
        method: request.method(),
        path: url.pathname.replace("/api/recasts", ""),
      });
  });
  return calls;
}
