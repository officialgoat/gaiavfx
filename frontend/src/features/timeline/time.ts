import { frameTimecode } from "../../shared/frame-rate";

const TIMECODE_STEPS = [1, 2, 5, 10, 30, 60, 120, 300, 600, 1800, 3600];

/** Timecode ruler ticks at least 80 px apart, only for the visible frames [from, to]. */
export function timecodeTicks(
  duration: number,
  scale: number,
  fps: number,
  from: number,
  to: number,
) {
  const seconds =
    TIMECODE_STEPS.find((s) => s * fps * scale >= 80) ?? TIMECODE_STEPS.at(-1)!;
  const step = seconds * fps;
  const ticks: { frame: number; label: string }[] = [];
  const last = Math.min(Math.ceil(duration / step), Math.ceil(to / step));
  for (let k = Math.max(0, Math.floor(from / step)); k <= last; k += 1) {
    // The first frame at or after the second: at 23.976 or 29.97 fps a rounded-down frame still belongs to the
    // second before and would read ss:23 (or ss:29).
    const frame = Math.ceil(k * step - 1e-6);
    ticks.push({ frame, label: frameTimecode(frame, fps) });
  }
  return { step, ticks };
}

/** Frame ruler ticks: a step of 48, 24, 12 or 6 frames by how many pixels a frame takes. */
export function frameTicks(duration: number, scale: number) {
  const step = scale < 1 ? 48 : scale < 2 ? 24 : scale < 6 ? 12 : 6;
  return {
    step,
    ticks: Array.from(
      { length: Math.ceil(duration / step) + 1 },
      (_, i) => i * step,
    ),
  };
}
