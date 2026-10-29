import type { Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import type { components } from "../../../frontend/src/shared/api.generated";
import {
  filmAsset,
  mockRecast,
  noLines,
  notAnalysedDialogue,
} from "../recast-fixtures";

// The comp state of ADR 0017's approved film view (`.impeccable/mocks/decision/one-track/a-tool-trackbar-u3.png`):
// Tears of Steel with its 135 analysed shots, Character 2 and Character 5 cast, a draft run on 04:20–04:40 rendering,
// shot 047 under review with four versions and the playhead on the comp's frame (04:33:00). The shot boundaries are committed (one-track-comp.json); the media are
// prepared locally by one_track_comp_media.py into .cache/one-track-comp and never committed.

type S = components["schemas"];
export const compMedia = path.resolve(".cache/one-track-comp");
export const compMediaReady = () =>
  fs.existsSync(path.join(compMedia, "film.mp4"));

const data = JSON.parse(
  fs.readFileSync(
    path.resolve("tests/browser/fixtures/one-track-comp.json"),
    "utf-8",
  ),
) as {
  fps: [number, number];
  frame_count: number;
  playhead: number;
  width: number;
  height: number;
  shots: [number, number][];
};

const pictures: S["AssetDTO"][] = [
  ["comp-cs079", "CS079_character2.jpg", "character-2.png"],
  ["comp-cs084", "CS084_character5.jpg", "character-5.png"],
  ["comp-bridge", "amsterdam_bridge.jpg", "posters/100.png"],
  ["comp-street", "street_background.jpg", "posters/118.png"],
].map(([id, name]) => ({
  ...filmAsset,
  id,
  name,
  kind: "image",
  mime: "image/png",
  info: {},
}));
const pictureFile: Record<string, string> = {
  "comp-cs079": "character-2.png",
  "comp-cs084": "character-5.png",
  "comp-bridge": "posters/100.png",
  "comp-street": "posters/118.png",
};

const CHARACTER_2 = "character-2";
const CHARACTER_5 = "character-5";
const planned = { from: 40, to: 67 };
/** The shot under the comp's playhead. */
export const compShot = data.shots.findIndex(
  ([start, end]) => start <= data.playhead && data.playhead < end,
);
export const compPlayhead = data.playhead;
const status = (index: number): S["RecastShotDTO"]["status"] => {
  if (index < planned.from || index > planned.to) return "unplanned";
  const at = index - compShot;
  if ([-4, -2, 0, 3, 5, 7].includes(at)) return "needs_review";
  if (at === 1) return "rendering";
  if (at === 2) return "queued";
  if ([-5, 4].includes(at)) return "kept_original";
  if (at > 8) return "planned";
  return "approved";
};
const checks = (index: number, letter: string): S["ShotCheckDTO"][] => [
  {
    check: "identity",
    verdict: "pass",
    calibrated: true,
    measured: 0.91,
    reason: "The face matches the target picture.",
  },
  {
    check: "continuity",
    verdict: "pass",
    calibrated: true,
    measured: 0.97,
    reason: "Motion follows the original shot.",
  },
  {
    check: "residual_source",
    verdict: index === compShot && letter === "A" ? "fail" : "pass",
    calibrated: true,
    measured: index === compShot && letter === "A" ? 0.18 : 0.04,
    reason: "The original face shows at the profile edge.",
  },
];

export function compRecast(): S["RecastDTO"] {
  const fps = data.fps[0] / data.fps[1];
  const shots: S["RecastShotDTO"][] = data.shots.map(([start, end], index) => {
    const s = status(index);
    const inPlan = index >= planned.from && index <= planned.to;
    const letters =
      s === "needs_review" || s === "approved" || s === "kept_original"
        ? index === compShot
          ? ["A", "B", "C", "D"]
          : ["A", "B"]
        : [];
    const windows: S["WindowDTO"][] = [];
    for (let at = start, i = 0; inPlan && at < end; at += 73, i++)
      windows.push({
        index: i,
        start: at,
        end: Math.min(end, at + 73),
        length: 73,
        operation_id: null,
        state:
          s === "rendering"
            ? "running"
            : s === "queued" || s === "planned"
              ? null
              : "succeeded",
      });
    return {
      id: `shot-${index}`,
      retaking: false,
      index,
      start,
      end,
      poster: {
        artifact_id: `poster-${index}`,
        url: `/comp-fixture/posters/${String(index).padStart(3, "0")}.png`,
        frame: start,
        track_id: `track-${index}`,
      },
      appearances: [
        ...(index >= 30 && index <= 75
          ? [
              {
                character_id: CHARACTER_2,
                track_ids: [`track-${index}-2`],
                coverage:
                  index === compShot ? 0.26 : 0.6 + ((index * 7) % 30) / 100,
                frames_with_face: Math.round((end - start) * 0.6),
                confirmed: true,
              },
            ]
          : []),
        ...(index >= 40 && index <= 60 && index % 3 !== 1
          ? [
              {
                character_id: CHARACTER_5,
                track_ids: [`track-${index}-5`],
                coverage: 0.5,
                frames_with_face: Math.round((end - start) * 0.5),
                confirmed: true,
              },
            ]
          : []),
      ],
      included: true,
      priority: null,
      status: s,
      plan: inPlan
        ? {
            performers: [CHARACTER_2],
            grouping: "single",
            windows,
            renders_per_window: 1,
            gaps: [],
            canvas_scale: 0.5,
          }
        : null,
      versions: letters.map((letter) => ({
        id: `version-${index}-${letter}`,
        letter,
        asset_id: `preview-${letter}`,
        checks: checks(index, letter),
      })),
      choice:
        s === "approved"
          ? {
              version: `version-${index}-A`,
              chosen_at: "2026-10-21T10:00:00Z",
              revision: 1,
            }
          : s === "kept_original"
            ? {
                version: "original",
                chosen_at: "2026-10-21T10:00:00Z",
                revision: 1,
              }
            : null,
      layers:
        index === compShot - 2 || index === compShot + 3
          ? [
              {
                character_id: CHARACTER_5,
                state: "rendering",
                windows_settled: 1,
                windows_total: 2,
                renders_reserved: 2,
              },
            ]
          : [],
    };
  });
  const labels = [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 16, 17, 18, 19]
    .concat([20, 21, 22, 23, 24, 25])
    .map((n) => `Character ${n}`);
  const characters: S["CharacterDTO"][] = labels.map((label) => {
    const id = label.toLowerCase().replace(" ", "-");
    const seen = shots.filter((s) =>
      s.appearances.some((a) => a.character_id === id),
    );
    const target =
      id === CHARACTER_2
        ? { asset_id: "comp-cs079", label: "CS079" }
        : id === CHARACTER_5
          ? { asset_id: "comp-cs084", label: "CS084" }
          : null;
    const face = {
      artifact_id: `face-${id}`,
      url: `/comp-fixture/${target ? id : "character-2"}.png`,
      frame: seen[0]?.start ?? 0,
      track_id: `track-${id}`,
    };
    return {
      id,
      label,
      // The extras are hidden, as a user does once the two performers are cast.
      hidden: !target,
      similar: [],
      face,
      screen_time_frames: seen.reduce((n, s) => n + (s.end - s.start), 0),
      shot_count: seen.length,
      presence: seen.map((s) => [s.start, s.end]),
      track_count: seen.length,
      tracks: seen.map((s) => ({
        id: `track-${s.index}-${id}`,
        shot_id: s.id,
        frames_with_face: 40,
        face,
      })),
      target: target && {
        ...target,
        state: "confirmed",
        proposed_by: "user",
        face: "face",
      },
      lines_proposed: 0,
      lines_confirmed: 0,
    } satisfies S["CharacterDTO"];
  });
  return {
    id: "recast-1",
    title: "Tears of Steel",
    revision: 1,
    film: {
      asset_id: filmAsset.id,
      name: "Tears of Steel",
      fps_num: data.fps[0],
      fps_den: data.fps[1],
      frame_count: data.frame_count,
      width: data.width,
      height: data.height,
      has_audio: true,
      fps_status: "tested",
      letterbox: null,
    },
    analysis: {
      operation_id: null,
      state: "succeeded",
      stage: "done",
      done: 10,
      total: 10,
      error: null,
    },
    dialogue: notAnalysedDialogue(),
    lines: noLines(),
    characters,
    unknown_faces: [],
    not_same: [],
    edits: [],
    shots,
    run: {
      state: "running",
      mode: "draft",
      range: { start: Math.round(260 * fps), end: Math.round(280 * fps) },
      render_budget: 40,
      renders_used: 12,
      shots_done: 14,
      shots_total: 28,
      current_shot_id: `shot-${compShot + 1}`,
    },
    estimate: {
      shots: 28,
      windows: 52,
      windows_pending: 14,
      renders: 40,
      gpu_seconds_low: 14400,
      gpu_seconds_high: 26000,
      storage_bytes: 5200000000,
      basis: "seed",
    },
    conforms: [],
  };
}

/** The mocked recast in the comp state, with Tears of Steel media from .cache/one-track-comp. */
export async function mockCompRecast(page: Page) {
  const mock = await mockRecast(page, 135);
  mock.existing();
  mock.value = compRecast();
  const film = fs.readFileSync(path.join(compMedia, "film.mp4"));
  const version = fs.readFileSync(path.join(compMedia, "version.mp4"));
  // Later routes win: these replace the default fixture's media and assets.
  await page.route("**/comp-fixture/**", (route) => {
    const name = new URL(route.request().url()).pathname.replace(
      "/comp-fixture/",
      "",
    );
    if (!name.endsWith(".mp4"))
      return route.fulfill({
        path: path.join(compMedia, name),
        contentType: "image/png",
      });
    const body = name === "version.mp4" ? version : film;
    const range = /bytes=(\d+)-(\d*)/.exec(
      route.request().headers()["range"] ?? "",
    );
    const start = range ? Number(range[1]) : 0;
    const end = range?.[2] ? Number(range[2]) : body.length - 1;
    return route.fulfill({
      status: range ? 206 : 200,
      body: body.subarray(start, end + 1),
      contentType: "video/mp4",
      headers: {
        "accept-ranges": "bytes",
        ...(range
          ? { "content-range": `bytes ${start}-${end}/${body.length}` }
          : {}),
      },
    });
  });
  const film$: S["AssetDTO"] = {
    ...filmAsset,
    name: "tears_of_steel_1080p.webm",
    mime: "video/webm",
    info: { frame_rate: "24/1", width: 1920, height: 800, duration: 734.17 },
  };
  await page.route("**/api/v1/assets**", (route) => {
    const url = new URL(route.request().url());
    const id = url.pathname.split("/")[4];
    if (!id) {
      const items =
        url.searchParams.get("kind") === "image" ? pictures : [film$];
      return route.fulfill({
        json: { items, total: items.length, offset: 0, limit: 40 },
      });
    }
    if (url.pathname.endsWith("/urls"))
      return route.fulfill({
        json: pictureFile[id]
          ? {
              thumbnail: `/comp-fixture/${pictureFile[id]}`,
              preview: `/comp-fixture/${pictureFile[id]}`,
            }
          : {
              thumbnail: `/comp-fixture/posters/${String(compShot).padStart(3, "0")}.png`,
              preview: id.startsWith("preview")
                ? "/comp-fixture/version.mp4"
                : "/comp-fixture/film.mp4",
            },
      });
    return route.fulfill({
      json:
        pictures.find((p) => p.id === id) ??
        (id.startsWith("preview")
          ? { ...film$, id, name: "PRIVATE SEED ARM ROUND" }
          : film$),
    });
  });
  return mock;
}
