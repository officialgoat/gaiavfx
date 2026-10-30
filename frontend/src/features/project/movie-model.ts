import type { Movie, Schema } from "./client";

// Facts about the movie that Python owns, read one way by the timeline, Properties, dialogs and Export.

/** The movie's frame rate from its DTO (the integration's grid); before it loads, the authoring default. */
export const movieFps = (movie: Movie | undefined) => movie?.fps ?? 24;

/** Seconds of `frames` at the movie's rate, as Properties shows a duration. */
export const movieSeconds = (movie: Movie | undefined, frames: number) =>
  (frames / movieFps(movie)).toFixed(2);

type SplitPoints = {
  isError: boolean;
  data?: Schema["DirectionSplitPointDTO"][];
};

/** Why a segment cannot be split, in words; undefined when it can. With `frame`, at that frame; without, anywhere
 * (the razor). Python names each point's own reason. */
export function splitBlocker(points: SplitPoints | undefined, frame?: number) {
  if (points?.isError)
    return "Split points could not be loaded. Refresh the movie and retry.";
  if (!points?.data) return "Loading split points…";
  if (frame === undefined)
    return points.data.some((p) => !p.reason)
      ? undefined
      : (points.data[0]?.reason ??
          "Add a cut or phase boundary inside this segment to split it.");
  const point = points.data.find((p) => p.frame === frame);
  return point
    ? (point.reason ?? undefined)
    : "Split at a cut or a phase boundary";
}
