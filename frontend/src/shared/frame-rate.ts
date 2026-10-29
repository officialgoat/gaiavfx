import type { Asset } from "../features/generation/client";

/** Display/playback conversion; persisted timing remains a rational string. */
export function frameRate(value: unknown): number {
  if (typeof value === "number") return value > 0 ? value : 0;
  if (typeof value !== "string" || !/^[0-9]+(\/[0-9]+)?$/.test(value))
    return 0;
  const [numerator, denominator = 1] = value.split("/").map(Number);
  const rate = numerator / denominator;
  return Number.isFinite(rate) && rate > 0 ? rate : 0;
}

/** Library stores the probed rational rate, e.g. 30000/1001. */
export function assetFrameRate(asset?: Asset): number | undefined {
  const fps = frameRate(asset?.info.frame_rate);
  return fps > 0 ? fps : undefined;
}

/** Non-drop frame labels; seconds use the actual rational timebase. */
export function frameTimecode(frame: number, fps: number): string {
  const seconds = Math.floor(frame / fps);
  const remainder = Math.max(0, frame - Math.ceil(seconds * fps));
  return [
    Math.floor(seconds / 3600),
    Math.floor(seconds / 60) % 60,
    seconds % 60,
    remainder,
  ]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
}
