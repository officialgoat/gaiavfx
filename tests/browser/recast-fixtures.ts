import type { Page } from "@playwright/test";
import type { components } from "../../frontend/src/shared/api.generated";
import { execFileSync } from "node:child_process";
import path from "node:path";
import fs from "node:fs";

type S = components["schemas"];
export const filmAsset: S["AssetDTO"] = {
  id: "recast-film",
  name: "The crossing · fixture",
  kind: "video",
  sha256: "fixture",
  mime: "video/mp4",
  info: { frame_rate: "25/1", width: 1920, height: 800, duration: 80 },
  provenance: [{ type: "import", original_name: "film.mp4" }],
  review: "unreviewed",
  created_at: "2026-10-08T10:00:00Z",
};
export const pictureAsset: S["AssetDTO"] = {
  ...filmAsset,
  id: "recast-picture",
  name: "Arin portrait",
  kind: "image",
  mime: "image/jpeg",
  info: {},
};
/** Library audio recordings (P5): the target voice's takes, and the audition reel the feed makes. */
const audio = (id: string, name: string, duration: number): S["AssetDTO"] => ({
  ...filmAsset,
  id,
  name,
  kind: "audio",
  mime: "audio/wav",
  info: { duration, sample_rate: 48000, channels: 1 },
});
export const recordingAssets: S["AssetDTO"][] = [
  audio("voice-a", "mara-take-01.wav", 10),
  audio("voice-b", "mara-take-02.wav", 20),
  audio("voice-c", "mara-take-03.wav", 45),
];
export const reelAsset = audio("audition-reel", "audition.wav", 12);
/** The expressive recordings folder in Settings: the voice UI may count its clips, never show where it is. */
export const EXPRESSIVE_FOLDER = "X:\\Private\\Expressive";
/** The service's reason while a voice has no build: it names the next step and blocks nothing. */
export const NOT_BUILT = "Build the voice to hear an audition.";

/** A character's voice as the service reports it (ADR 0016 §1, P5): two recordings, 30 s, confirmed by the user, not
 * built. It also carries fields the DTO never has (an operation, an artifact, the folder): if the page showed one,
 * the blind checks would find it (A45). */
export const voiceFixture = (
  partial: Partial<S["VoiceDTO"]> = {},
): S["VoiceDTO"] => ({
  samples: [
    { asset_id: "voice-b", name: "mara-take-02.wav", seconds: 20 },
    { asset_id: "voice-a", name: "mara-take-01.wav", seconds: 10 },
  ],
  seconds: 30,
  state: "confirmed",
  proposed_by: "user",
  model_state: null,
  stage: null,
  done: 0,
  total: 0,
  error: null,
  reason: NOT_BUILT,
  audition_asset_id: null,
  library_changed: false,
  // The service decides what blocks Build voice (P5d); unless a test says otherwise, as it would for these
  // recordings: proposed, or outside 15–60 s.
  blocked:
    partial.state === "proposed" ||
    (partial.seconds ?? 30) < 15 ||
    (partial.seconds ?? 30) > 60,
  ...({
    operation_id: "hidden-operation",
    voice_model_artifact_id: "hidden-artifact",
    folder: EXPRESSIVE_FOLDER,
  } as object),
  ...partial,
});

/** One second of silence as a WAV file, so the audition player has something to load. */
function silentWav(seconds = 1, rate = 8000) {
  const data = Math.round(seconds * rate) * 2;
  const wav = Buffer.alloc(44 + data);
  wav.write("RIFF", 0);
  wav.writeUInt32LE(36 + data, 4);
  wav.write("WAVE", 8);
  wav.write("fmt ", 12);
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(rate, 24);
  wav.writeUInt32LE(rate * 2, 28);
  wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34);
  wav.write("data", 36);
  wav.writeUInt32LE(data, 40);
  return wav;
}
const face = (track: string, frame: number): S["FaceDTO"] => ({
  artifact_id: `crop-${track}`,
  url: "/recast-fixture/arin.jpg",
  frame,
  track_id: track,
});
/** A film's rational frame rate, e.g. [24000, 1001] for 23.976 fps. */
export type FilmRate = [number, number];
/** The service's refusal when a later change touched the rows an undo would write back. */
export const LATER_CHANGE =
  "A later change touched the same characters or face tracks. Undo that change first.";

/** The film's dialogue analysis before anyone asked for it (ADR 0016 §2, P4): no operation, no lines. */
export const notAnalysedDialogue = (): S["DialogueStatusDTO"] => ({
  operation_id: null,
  state: "not_started",
  stage: null,
  done: 0,
  total: 0,
  error: null,
  attempt: 0,
  version: 1,
  sample_rate: 48000,
  reason: null,
  gap: null,
});
export const noLines = (): S["LineCountsDTO"] => ({
  segments: 0,
  proposed: 0,
  confirmed: 0,
  rejected: 0,
});

export function recastFixture(
  count = 40,
  rate: FilmRate = [25, 1],
): S["RecastDTO"] {
  const statuses: S["RecastShotDTO"]["status"][] = [
    "needs_review",
    "needs_review",
    "absent",
    "excluded",
    "gap",
    "planned",
    "queued",
    "rendering",
    "approved",
    "kept_original",
    "failed",
  ];
  const shots: S["RecastShotDTO"][] = Array.from(
    { length: count },
    (_, index) => ({
      id: `shot-${index}`,
      retaking: false,
      index,
      start: index * 50,
      end: (index + 1) * 50,
      poster: {
        ...face(`track-${index}-0`, index * 50),
        url: "/recast-fixture/before-cut.jpg",
      },
      appearances: [0, 1, 2]
        .filter((c) => (index + c) % 3 !== 2)
        .map((c) => ({
          character_id: `character-${c}`,
          track_ids: [`track-${index}-${c}`],
          coverage: 0.8,
          frames_with_face: 40,
          confirmed: true,
        })),
      included: index % 11 !== 3,
      priority: null,
      status: statuses[index % statuses.length],
      plan: {
        performers: ["character-0"],
        grouping: "single",
        windows: [
          {
            index: 0,
            start: index * 50,
            end: (index + 1) * 50,
            length: 56,
            operation_id: "hidden-operation",
            state: index % 11 === 7 ? "running" : "succeeded",
          },
        ],
        renders_per_window: 1,
        gaps:
          index % 11 === 4
            ? [
                {
                  code: "overlap",
                  message:
                    "Performers overlap. Review this shot before rendering.",
                },
              ]
            : [],
        canvas_scale: 0.47,
      },
      versions: ["A", "B"].map((letter) => ({
        id: `version-${index}-${letter}`,
        letter,
        asset_id: `preview-${letter}`,
        checks: [
          {
            check: "identity",
            verdict: "pass",
            calibrated: false,
            measured: 0.87,
            reason: "Identity scoring is not calibrated.",
          },
          {
            check: "seam",
            verdict: "unsupported",
            calibrated: false,
            measured: null,
            reason: "Window seams have no qualified check.",
          },
        ],
      })),
      choice:
        index % 11 === 8 || index % 11 === 9
          ? {
              version: index % 11 === 8 ? `version-${index}-B` : "original",
              chosen_at: "2026-10-08T10:00:00Z",
              revision: 1,
            }
          : null,
    }),
  );
  const characters: S["CharacterDTO"][] = ["Mara", "Elias", "The courier"].map(
    (label, i) => ({
      id: `character-${i}`,
      label,
      hidden: false,
      similar:
        i === 0
          ? [{ character_id: "character-2", similarity: 0.88 }]
          : i === 2
            ? [{ character_id: "character-0", similarity: 0.88 }]
            : [],
      face: face(`track-0-${i}`, 0),
      screen_time_frames: (3 - i) * 400,
      shot_count: shots.filter((s) =>
        s.appearances.some((a) => a.character_id === `character-${i}`),
      ).length,
      presence: shots
        .filter((s) =>
          s.appearances.some((a) => a.character_id === `character-${i}`),
        )
        .map((s) => [s.start, s.end]),
      track_count: 1,
      tracks: [
        {
          id: `track-0-${i}`,
          shot_id: "shot-0",
          frames_with_face: 40,
          face: face(`track-0-${i}`, 0),
        },
      ],
      target:
        i === 0
          ? {
              asset_id: pictureAsset.id,
              label: "Arin",
              state: "confirmed",
              proposed_by: "user",
              face: "face",
            }
          : i === 1
            ? {
                asset_id: pictureAsset.id,
                label: "Arin proposal",
                state: "proposed",
                proposed_by: "agent",
                face: "face",
              }
            : null,
      lines_proposed: 0,
      lines_confirmed: 0,
      voice: null,
    }),
  );
  for (const character of characters) {
    character.tracks = shots.flatMap((s) =>
      s.appearances
        .filter((a) => a.character_id === character.id)
        .flatMap((a) =>
          a.track_ids.map((id) => ({
            id,
            shot_id: s.id,
            frames_with_face: a.frames_with_face,
            face: face(id, s.start),
          })),
        ),
    );
    character.track_count = character.tracks.length;
  }
  return {
    id: "recast-1",
    title: filmAsset.name,
    revision: 1,
    film: {
      asset_id: filmAsset.id,
      name: filmAsset.name,
      fps_num: rate[0],
      fps_den: rate[1],
      frame_count: count * 50,
      width: 1920,
      height: 800,
      has_audio: true,
      fps_status: "developing",
      letterbox: null,
    },
    analysis: {
      operation_id: null,
      state: "succeeded",
      stage: "done",
      done: 3,
      total: 3,
      error: null,
    },
    dialogue: notAnalysedDialogue(),
    lines: noLines(),
    characters,
    unknown_faces: [face("unknown-track", 100)],
    not_same: [],
    edits: [],
    shots,
    run: {
      state: "idle",
      mode: "draft",
      range: null,
      render_budget: 20,
      renders_used: 0,
      shots_done: 0,
      shots_total: count,
      current_shot_id: null,
    },
    estimate: {
      shots: count,
      windows: 17,
      windows_pending: 0,
      renders: 17,
      gpu_seconds_low: 6120,
      gpu_seconds_high: 11160,
      storage_bytes: 2100000000,
      basis: "seed",
    },
    conforms: [],
  };
}

export async function mockRecast(
  page: Page,
  count = 40,
  rate: FilmRate = [25, 1],
) {
  let value = recastFixture(count, rate);
  const film = {
    ...filmAsset,
    info: { ...filmAsset.info, frame_rate: rate.join("/") },
  };
  let exists = false;
  const comfy = { connected: true };
  const calls: {
    path: string;
    method: string;
    body: Record<string, unknown>;
  }[] = [];
  // The cast-edit journal (ADR 0012 Amendment 7 §1): a journaled command keeps the cast as it was before and after;
  // an undo puts it back while the cast still matches, marks the entry undone and adds the inverse, as the service
  // does. The images stay here: the page sees only the entries.
  const entries: S["RecastEditDTO"][] = [];
  const images = new Map<string, { before: string; after: string }>();
  const undo = { refuse: "" };
  // The dialogue analysis's state after a start (the mock's next state; a test may change it), and the page's reads
  // of the film's lines.
  const dialogue: {
    next: Partial<S["DialogueStatusDTO"]>;
    lineReads: number;
  } = {
    next: { state: "running", stage: "separate", done: 2, total: 6 },
    lineReads: 0,
  };
  // The expressive recordings folder as Settings reports it (P5); a test may change it.
  const voiceLibrary: S["VoiceLibraryDTO"] = {
    folder: "",
    state: "not_set",
    clips: 0,
    categories: {},
    skipped: 0,
  };
  // A voice's state right after Build voice (or a retry of a failed build), as the next read reports it.
  const voice: { afterBuild: Partial<S["VoiceDTO"]> } = {
    afterBuild: {
      model_state: "making",
      stage: "material",
      done: 3,
      total: 27,
      error: null,
      reason: null,
    },
  };
  let clock = Date.parse("2026-10-22T10:00:00Z");
  const cast = () =>
    JSON.stringify({
      characters: value.characters,
      unknown_faces: value.unknown_faces,
      not_same: value.not_same ?? [],
    });
  // Row by row, as the service compares: each character, the unknown faces and the set-aside pairs.
  type Cast = Pick<S["RecastDTO"], "characters" | "unknown_faces"> & {
    not_same: S["NotSamePairDTO"][];
  };
  const rows = (c: Cast) =>
    new Map<string, string>([
      ...c.characters.map(
        (ch) => [`character:${ch.id}`, JSON.stringify(ch)] as const,
      ),
      ["unknown_faces", JSON.stringify(c.unknown_faces)],
      ["not_same", JSON.stringify(c.not_same)],
    ]);
  /** The rows an edit changed, put back as they were before it; every other row stays as it is now. */
  const restore = (current: Cast, was: Cast, keys: string[]): Cast => {
    const touched = (id: string) => keys.includes(`character:${id}`);
    const characters = current.characters
      .filter(
        (c) => !touched(c.id) || was.characters.some((b) => b.id === c.id),
      )
      .map((c) =>
        touched(c.id) ? was.characters.find((b) => b.id === c.id)! : c,
      );
    was.characters.forEach((b, index) => {
      if (touched(b.id) && !characters.some((c) => c.id === b.id))
        characters.splice(Math.min(index, characters.length), 0, b);
    });
    return {
      characters,
      unknown_faces: keys.includes("unknown_faces")
        ? was.unknown_faces
        : current.unknown_faces,
      not_same: keys.includes("not_same") ? was.not_same : current.not_same,
    };
  };
  const journal = (
    command: string,
    summary: string,
    before: string,
    kind: S["RecastEditDTO"]["kind"] = "edit",
  ) => {
    const after = cast();
    if (after === before) return;
    const entry: S["RecastEditDTO"] = {
      id: `edit-${entries.length + 1}`,
      command,
      kind,
      summary,
      created_at: new Date((clock += 1000)).toISOString(),
      undone_at: null,
    };
    entries.unshift(entry);
    images.set(entry.id, { before, after });
    value.edits = entries.slice(0, 20);
  };
  const directory = path.resolve(".cache/recast-review");
  fs.mkdirSync(directory, { recursive: true });
  const video = path.join(
    directory,
    rate.join("/") === "25/1"
      ? "fixture-25fps.mp4"
      : `fixture-${rate[0]}-${rate[1]}.mp4`,
  );
  if (!fs.existsSync(video))
    execFileSync(
      path.join(
        process.env.GAIAVFX_MEDIA_DIR ?? path.resolve(".runtime/media"),
        "ffmpeg.exe",
      ),
      [
        "-nostdin",
        "-y",
        "-loglevel",
        "error",
        "-loop",
        "1",
        "-i",
        "tests/fixtures/concept/before-cut.jpg",
        "-t",
        "80",
        "-vf",
        "scale=320:-2",
        "-r",
        rate.join("/"),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        video,
      ],
      { windowsHide: true, timeout: 60000 },
    );
  const movie = fs.readFileSync(video);
  await page.route("**/recast-fixture/*", (route) => {
    if (route.request().url().endsWith(".wav"))
      return route.fulfill({ body: silentWav(), contentType: "audio/wav" });
    if (!route.request().url().endsWith(".mp4"))
      return route.fulfill({
        path: path.resolve(
          "tests/fixtures/concept",
          route.request().url().split("/").at(-1)!,
        ),
        contentType: "image/jpeg",
      });
    // Byte ranges, as the Library serves them, so the browser can seek the fixture film.
    const range = /bytes=(\d+)-(\d*)/.exec(
      route.request().headers()["range"] ?? "",
    );
    if (!range)
      return route.fulfill({
        body: movie,
        contentType: "video/mp4",
        headers: { "accept-ranges": "bytes" },
      });
    const start = Number(range[1]);
    const end = range[2] ? Number(range[2]) : movie.length - 1;
    return route.fulfill({
      status: 206,
      body: movie.subarray(start, end + 1),
      contentType: "video/mp4",
      headers: {
        "accept-ranges": "bytes",
        "content-range": `bytes ${start}-${end}/${movie.length}`,
      },
    });
  });
  await page.route("**/api/v1/assets**", async (route) => {
    const url = new URL(route.request().url());
    const id = url.pathname.split("/")[4];
    const recording = [...recordingAssets, reelAsset].find((a) => a.id === id);
    if (!id) {
      const kind = url.searchParams.get("kind");
      const items =
        kind === "image"
          ? [pictureAsset]
          : kind === "audio"
            ? recordingAssets
            : [film];
      return route.fulfill({
        json: {
          items,
          total: items.length,
          offset: 0,
          limit: 40,
        } satisfies S["AssetPage"],
      });
    }
    if (url.pathname.endsWith("/urls"))
      return route.fulfill({
        json: {
          thumbnail: "/recast-fixture/before-cut.jpg",
          preview:
            id === pictureAsset.id
              ? "/recast-fixture/arin.jpg"
              : recording
                ? "/recast-fixture/audition.wav"
                : "/recast-fixture/video.mp4",
        },
      });
    return route.fulfill({
      json:
        id === pictureAsset.id
          ? pictureAsset
          : (recording ?? {
              ...film,
              id,
              name: id.startsWith("preview")
                ? "PRIVATE SEED ARM ROUND"
                : filmAsset.name,
            }),
    });
  });
  await page.route("**/api/v1/settings/voice-library", (route) =>
    route.fulfill({ json: voiceLibrary }),
  );
  await page.route("**/api/v1/settings/comfy", (route) =>
    route.fulfill({
      json: comfy.connected
        ? { connected: true, version: "0.35" }
        : { connected: false, error: "Connection refused" },
    }),
  );
  await page.route("**/api/recasts**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const suffix = url.pathname.replace("/api/recasts", "");
    const method = req.method();
    const body = req.postDataJSON() ?? {};
    if (method === "GET") {
      if (!suffix)
        return route.fulfill({
          json: exists
            ? [
                {
                  id: value.id,
                  title: value.title,
                  source_asset_id: value.film.asset_id,
                  revision: value.revision,
                  analysis_state: value.analysis.state,
                  shots_total: value.shots.length,
                  shots_approved: 0,
                  updated_at: "2026-10-08T10:00:00Z",
                } satisfies S["RecastSummaryDTO"],
              ]
            : [],
        });
      // A run's estimate before it starts: the planned, included shots in the range, one render per window in
      // Draft and two in Final; time and storage are the recast's own estimate.
      if (suffix.endsWith("/estimate")) {
        const start = Number(url.searchParams.get("start") ?? 0);
        const end = Number(url.searchParams.get("end") ?? Infinity);
        const per = url.searchParams.get("mode") === "final" ? 2 : 1;
        const shots = value.shots.filter(
          (s) =>
            s.included &&
            s.plan?.performers.length &&
            s.start < end &&
            s.end > start,
        );
        const zero = {
          windows: 0,
          gpu_seconds_low: 0,
          gpu_seconds_high: 0,
          storage_bytes: 0,
          basis: "seed" as const,
        };
        return route.fulfill({
          json: {
            ...zero,
            ...(shots.length ? value.estimate : {}),
            windows_pending: 0,
            shots: shots.length,
            renders: shots.reduce(
              (sum, s) => sum + s.plan!.windows.length * per,
              0,
            ),
          } satisfies S["EstimateDTO"],
        });
      }
      // The film's lines (P4): part of the client, not shown by the page until the speaker lanes (U6/P8).
      if (suffix.endsWith("/lines")) {
        dialogue.lineReads++;
        return route.fulfill({
          json: {
            dialogue_operation_id: value.dialogue.operation_id,
            rule_version: 1,
            sample_rate: 48000,
            lines: [],
            profiles: {},
          } satisfies S["LinesDTO"],
        });
      }
      return route.fulfill({ json: value });
    }
    calls.push({ path: suffix, method, body });
    if (!suffix) {
      exists = true;
      return route.fulfill({ status: 201, json: value });
    }
    if (
      body.expected_revision !== undefined &&
      body.expected_revision !== value.revision
    )
      return route.fulfill({
        status: 409,
        json: { detail: "The recast changed. Refresh and try again." },
      });
    // The dialogue analysis (ADR 0016 §2, P4): refused as the service refuses it, else started (or retried) as one
    // shared operation, whose id the page never shows.
    if (/^\/[^/]+\/dialogue(\/retry)?$/.test(suffix)) {
      if (value.dialogue.gap)
        return route.fulfill({
          status: 422,
          json: { detail: value.dialogue.gap.message },
        });
      if (value.analysis.state !== "succeeded")
        return route.fulfill({
          status: 409,
          json: { detail: "Wait for the film analysis to finish." },
        });
      const retry = suffix.endsWith("/retry");
      if (retry && value.dialogue.state !== "failed")
        return route.fulfill({
          status: 409,
          json: { detail: "The dialogue analysis has not failed." },
        });
      // A start is idempotent: an analysis already running or done is the one the service returns.
      if (retry || value.dialogue.state === "not_started")
        value.dialogue = {
          ...value.dialogue,
          ...(retry
            ? { state: "running", stage: "audio", done: 0, total: 3 }
            : dialogue.next),
          operation_id: "hidden-operation",
          error: null,
          reason: null,
          attempt: retry ? value.dialogue.attempt + 1 : 1,
        };
      value.revision++;
      return route.fulfill({ json: value });
    }
    const undoing = /^\/[^/]+\/edits\/([^/]+)\/undo$/.exec(suffix);
    if (undoing) {
      const refuse = (status: number, detail: string) =>
        route.fulfill({ status, json: { detail } });
      if (undo.refuse) {
        const detail = undo.refuse;
        undo.refuse = "";
        return refuse(409, detail);
      }
      const entry = entries.find((e) => e.id === undoing[1]);
      const image = images.get(undoing[1]);
      if (!entry || !image)
        return refuse(404, "Edit not found in this recast.");
      if (entry.undone_at) return refuse(409, "This edit was already undone.");
      const was: Cast = JSON.parse(image.before);
      const then: Cast = JSON.parse(image.after);
      const before = cast();
      const now: Cast = JSON.parse(before);
      const [rowsWas, rowsThen, rowsNow] = [rows(was), rows(then), rows(now)];
      const keys = [...new Set([...rowsWas.keys(), ...rowsThen.keys()])].filter(
        (k) => rowsWas.get(k) !== rowsThen.get(k),
      );
      if (keys.some((k) => rowsNow.get(k) !== rowsThen.get(k)))
        return refuse(409, LATER_CHANGE);
      Object.assign(value, restore(now, was, keys));
      entry.undone_at = new Date((clock += 1000)).toISOString();
      const kind = entry.kind === "undo" ? "redo" : "undo";
      journal(
        entry.command,
        `${kind === "redo" ? "Redo" : "Undo"}: ${entry.summary.replace(/^(Undo|Redo): /, "")}`,
        before,
        kind,
      );
      value.revision++;
      return route.fulfill({ json: value });
    }
    const parts = suffix.split("/");
    const character = value.characters.find((c) => c.id === parts[3]);
    const shot = value.shots.find((s) => s.id === parts[3]);
    // A journaled cast edit, in the service's words, from the cast before it changes.
    const before = cast();
    const named = (id: unknown) =>
      value.characters.find((c) => c.id === id)?.label ?? "a character";
    const count = ((body.track_ids as string[] | undefined) ?? []).length;
    const edit: { command: string; summary: string } | undefined =
      character && parts[4] === "target" && !parts[5]
        ? method === "DELETE"
          ? {
              command: "clear_target",
              summary:
                character.target?.state === "proposed"
                  ? `Rejected the proposed picture for ${character.label}`
                  : `Cleared ${character.label}'s picture`,
            }
          : {
              command: "set_target",
              summary: `Set ${character.label}'s picture to ${body.label}`,
            }
        : character && method === "PATCH" && !parts[4]
          ? {
              command: "patch_character",
              summary:
                body.label !== undefined && body.label !== character.label
                  ? `Renamed ${character.label} to ${body.label}`
                  : `${body.hidden ? "Hid" : "Showed"} ${character.label}`,
            }
          : suffix.endsWith("/not-same")
            ? {
                command: "set_not_same",
                summary: `Set aside ${(body.pairs as S["NotSamePairDTO"][])
                  .map(
                    (p) =>
                      `${named(p.character_a)} and ${named(p.character_b)}`,
                  )
                  .join(", ")}`,
              }
            : suffix.endsWith("/characters/merge")
              ? {
                  command: "merge_characters",
                  summary: `Merged ${(body.sources as string[]).map(named).join(" and ")} into ${named(body.into)}`,
                }
              : suffix.endsWith("/tracks/move")
                ? {
                    command: "move_tracks",
                    summary: `Moved ${count} face ${count === 1 ? "track" : "tracks"} to ${
                      body.to === "unknown"
                        ? "the unknown faces"
                        : body.to === "new"
                          ? "a new character"
                          : named(body.to)
                    }`,
                  }
                : undefined;
    // The voice's journaled commands, in the service's words (recast_edits.summary_for).
    const recordings = ((body.asset_ids as string[] | undefined) ?? []).length;
    const recordedSeconds = ((body.asset_ids as string[] | undefined) ?? [])
      .map((id) =>
        Number(recordingAssets.find((a) => a.id === id)?.info.duration),
      )
      .reduce((sum, s) => sum + s, 0);
    const voiceEdit: { command: string; summary: string } | undefined =
      character && parts[4] === "voice"
        ? !parts[5] && method === "DELETE"
          ? {
              command: "clear_voice",
              summary:
                character.voice?.state === "proposed"
                  ? `Rejected the proposed voice for ${character.label}`
                  : `Cleared ${character.label}'s voice recordings`,
            }
          : !parts[5]
            ? {
                command: "set_voice",
                summary: `Set ${character.label}'s voice from ${recordings} ${recordings === 1 ? "recording" : "recordings"} (${Math.round(recordedSeconds)} s)`,
              }
            : parts[5] === "confirm"
              ? {
                  command: "confirm_voice",
                  summary: `Confirmed ${character.label}'s voice recordings`,
                }
              : undefined
        : undefined;
    if (character && parts[4] === "target") {
      if (method === "DELETE") character.target = null;
      else if (parts[5] === "confirm" && character.target)
        character.target.state = "confirmed";
      else
        character.target = {
          asset_id: body.asset_id as string,
          label: body.label as string,
          state: "confirmed",
          proposed_by: "user",
          face: "face",
        };
    } else if (character?.target?.reference && parts[4] === "reference") {
      character.target.reference.state =
        parts[5] === "approve" ? "approved" : "rejected";
    } else if (character && parts[4] === "voice") {
      // A character's voice (ADR 0016, P5): the recordings are journaled cast edits; the build, its retry and the
      // audition's review are not, and change only the model's state, as the service's feed would report it.
      const refuse = (status: number, detail: string) =>
        route.fulfill({ status, json: { detail } });
      const v = character.voice;
      const step = parts[5];
      if (!step && method === "DELETE") {
        if (!v) return refuse(409, "There are no voice recordings to clear.");
        character.voice = null;
      } else if (!step) {
        const samples = (body.asset_ids as string[]).map((id) => {
          const a = recordingAssets.find((x) => x.id === id)!;
          return {
            asset_id: id,
            name: a.name,
            seconds: Number(a.info.duration),
          };
        });
        const seconds = samples.reduce((sum, s) => sum + s.seconds, 0);
        if (seconds < 15 || seconds > 60)
          return refuse(
            422,
            `Choose between 15 and 60 seconds of recordings; these are ${seconds} s.`,
          );
        character.voice = voiceFixture({ samples, seconds });
      } else if (!v) return refuse(409, "Choose the voice recordings first.");
      else if (step === "confirm")
        Object.assign(v, {
          state: "confirmed",
          reason: NOT_BUILT,
          blocked: false,
        });
      else if (step === "build") {
        if (v.model_state === "making")
          return refuse(409, "This voice is already being built.");
        Object.assign(v, voice.afterBuild, { library_changed: false });
      } else if (step === "retry") {
        if (v.model_state !== "failed" && v.model_state !== "rejected")
          return refuse(409, "Nothing to retry.");
        Object.assign(
          v,
          v.model_state === "rejected"
            ? { stage: "audition", done: 0, total: 3 }
            : voice.afterBuild,
          { model_state: "making", error: null, reason: null },
        );
      } else {
        if (v.model_state !== "waiting")
          return refuse(409, "There is no audition waiting for review.");
        v.model_state = step === "approve" ? "approved" : "rejected";
      }
    } else if (character && method === "PATCH") {
      Object.assign(
        character,
        Object.fromEntries(
          Object.entries(body).filter(([k]) => k !== "expected_revision"),
        ),
      );
    }
    if (suffix.endsWith("/not-same")) {
      // Stored in the recast; the service leaves the pair out of the suggestions.
      value.not_same ??= [];
      for (const pair of body.pairs as S["NotSamePairDTO"][]) {
        const [a, b] = [pair.character_a, pair.character_b].sort();
        if (
          !value.not_same.some(
            (n) => n.character_a === a && n.character_b === b,
          )
        )
          value.not_same.push({ character_a: a, character_b: b });
        for (const c of value.characters)
          if (c.id === a || c.id === b)
            c.similar = (c.similar ?? []).filter(
              (s) => s.character_id !== (c.id === a ? b : a),
            );
      }
    }
    if (suffix.endsWith("/characters/merge")) {
      const into = value.characters.find((c) => c.id === body.into)!;
      for (const c of value.characters.filter((c) =>
        (body.sources as string[]).includes(c.id),
      ))
        into.tracks.push(...c.tracks);
      value.characters = value.characters.filter(
        (c) => !(body.sources as string[]).includes(c.id),
      );
    }
    if (suffix.endsWith("/tracks/move")) {
      const moving = value.characters
        .flatMap((c) => c.tracks)
        .filter((t) => (body.track_ids as string[]).includes(t.id));
      value.characters.forEach((c) => {
        c.tracks = c.tracks.filter(
          (t) => !(body.track_ids as string[]).includes(t.id),
        );
      });
      value.characters.find((c) => c.id === body.to)?.tracks.push(...moving);
    }
    if (shot && parts[4] === "choice") {
      shot.choice =
        body.version === "unchosen"
          ? null
          : {
              version: body.version as string,
              chosen_at: "2026-10-08T10:00:00Z",
              revision: value.revision + 1,
            };
      shot.status =
        body.version === "unchosen"
          ? "needs_review"
          : body.version === "original"
            ? "kept_original"
            : "approved";
    }
    if (shot && method === "PATCH") shot.included = body.included as boolean;
    if (shot && parts[4] === "retake") {
      shot.status = "rendering";
      shot.retaking = true;
      if (shot.plan)
        shot.plan.renders_per_window = body.mode === "final" ? 4 : 1;
    }
    if (shot && parts[4] === "head-pass") {
      shot.status = "rendering";
      shot.layers ??= [];
      shot.layers.push({
        character_id: body.character_id as string,
        state: "rendering",
        windows_settled: 0,
        windows_total: shot.plan?.windows.length ?? 0,
        renders_reserved: shot.plan?.windows.length ?? 0,
      });
    }
    if (suffix.endsWith("/plan")) {
      value.run.range = body.range as S["FrameRangeDTO"] | null;
      for (const shot of value.shots) {
        if (
          shot.status === "unplanned" &&
          (!value.run.range ||
            (shot.start < value.run.range.end &&
              shot.end > value.run.range.start))
        ) {
          shot.status = "analysing";
          shot.plan = {
            performers: ["character-0"],
            grouping: "single",
            windows: [],
            renders_per_window: 1,
            gaps: [],
            canvas_scale: null,
          };
        }
      }
    }
    if (suffix.endsWith("/run"))
      value.run = {
        ...value.run,
        state: "running",
        mode: body.mode as "draft" | "final",
        range: body.range as S["FrameRangeDTO"] | null,
        render_budget: body.render_budget as number,
      };
    if (suffix.endsWith("/pause")) value.run.state = "paused";
    if (suffix.endsWith("/cancel")) value.run.state = "finished";
    const recorded = voiceEdit ?? edit;
    if (recorded) journal(recorded.command, recorded.summary, before);
    value.revision++;
    if (suffix.endsWith("/conform")) {
      value.conforms.push({
        operation_id: "conform-1",
        mode: body.mode as "chosen",
        range: body.range as S["FrameRangeDTO"] | null,
        state: "running",
        progress: 0.4,
        asset_id: null,
        created_at: "2026-10-08T10:00:00Z",
        codec: body.codec as "h264" | "prores",
        error: null,
      });
      const operation: S["OperationDTO"] = {
        id: "conform-1",
        kind: "recast_conform",
        state: "running",
        awaits_review: false,
        request: {},
        budget_reserved: 0,
        budget_released_at: null,
        cancel_requested: false,
        review: null,
        error: null,
        created_at: "2026-10-08T10:00:00Z",
        updated_at: "2026-10-08T10:00:00Z",
        jobs: [],
        candidates: [],
      };
      return route.fulfill({ status: 201, json: operation });
    }
    return route.fulfill({ json: value });
  });
  return {
    calls,
    comfy,
    get value() {
      return value;
    },
    set value(next: S["RecastDTO"]) {
      value = next;
    },
    existing: () => {
      exists = true;
    },
    /** Every journaled cast edit, newest first (the page sees the newest 20). */
    edits: entries,
    /** The next cast-edit undo answers 409 with this sentence, as the service does when a later change intervened. */
    refuseNextUndo: (detail: string) => {
      undo.refuse = detail;
    },
    /** The dialogue analysis's state after a start, and how often the page read the film's lines (P4). */
    dialogue,
    /** The expressive recordings folder Settings reports, and a voice's state after Build voice (P5). */
    voiceLibrary,
    voice,
  };
}
