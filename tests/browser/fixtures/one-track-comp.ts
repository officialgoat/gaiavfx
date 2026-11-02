import fs from "node:fs";
import path from "node:path";
import type { Scene } from "../recast-world";

// The comp state of ADR 0017's approved film view (`.impeccable/mocks/decision/one-track/a-tool-trackbar-u3.png`):
// Tears of Steel's 135 analysed shots at 24 fps, Character 2 and Character 5 cast, a draft run on 04:20–04:40 and
// shot 047 under review with four versions, the playhead on the comp's frame (04:33:00). The shot boundaries are
// committed (one-track-comp.json, from the 8 October trial analysis); the film itself is the Recast test world's.

const data = JSON.parse(
  fs.readFileSync(
    path.resolve("tests/browser/fixtures/one-track-comp.json"),
    "utf-8",
  ),
) as {
  fps: [number, number];
  frame_count: number;
  playhead: number;
  shots: [number, number][];
};

/** The shot under the comp's playhead. */
export const compShot = data.shots.findIndex(
  ([start, end]) => start <= data.playhead && data.playhead < end,
);
export const compPlayhead = data.playhead;
export const compFrames = data.frame_count;

const fps = data.fps[0] / data.fps[1];
/** The comp state as a Recast scene: the shots up to and including 047 rendered (047 four times), and a draft run
 * over 04:20–04:40 rendering the rest of its range. */
export const compScene: Scene = {
  title: "Tears of Steel",
  cuts: data.shots,
  rate: data.fps,
  characters: ["Character 2", "Character 5"],
  targets: ["confirmed", "confirmed"],
  labels: ["CS079", "CS084"],
  rendered: compShot + 1,
  retakes: { [compShot]: 3 },
  run: {
    state: "running",
    range: { start: Math.round(260 * fps), end: Math.round(280 * fps) },
    budget: 200,
  },
};
