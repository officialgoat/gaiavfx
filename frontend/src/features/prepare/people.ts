import type { ContactTrack, TrackBox } from "./ContactSheet";

const record = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : {};
export function peopleReport(value: Record<string, unknown> | null) {
  if (!value) return null;
  const native = value.native;
  if (
    !Array.isArray(native) ||
    native.length !== 2 ||
    !native.every((n) => typeof n === "number" && n > 0) ||
    typeof value.shot_start !== "number" ||
    typeof value.shot_end !== "number" ||
    typeof value.fps !== "number" ||
    value.fps <= 0 ||
    !Array.isArray(value.tracks)
  )
    return null;
  const tracks: (ContactTrack & { id: string })[] = [];
  for (const item of value.tracks) {
    const t = record(item),
      b = record(t.box);
    if (
      typeof t.id !== "string" ||
      typeof t.concept !== "string" ||
      ![b.x0, b.y0, b.x1, b.y1].every(
        (n) => typeof n === "number" && Number.isFinite(n),
      ) ||
      !Array.isArray(t.presence)
    )
      return null;
    const box = b as TrackBox;
    if (box.x1 <= box.x0 || box.y1 <= box.y0) return null;
    const presence: Record<string, number> = {};
    for (const state of t.presence)
      if (typeof state === "string")
        presence[state] = (presence[state] ?? 0) + 1;
    tracks.push({
      id: t.id,
      track_index: tracks.length + 1,
      concept: t.concept,
      box,
      presence,
      position: `${(box.x0 + box.x1) / (2 * Number(native[0])) < 1 / 3 ? "left" : (box.x0 + box.x1) / (2 * Number(native[0])) > 2 / 3 ? "right" : "centre"} · ${box.y1 - box.y0 >= Number(native[1]) / 2 ? "foreground" : "background"}`,
      ambiguous: !presence.present || !!presence.unresolved,
    });
  }
  return {
    native: native as [number, number],
    frame: value.shot_start,
    end: value.shot_end,
    fps: value.fps,
    tracks: tracks.filter((t) => t.concept === "person"),
    objects: tracks.filter((t) => t.concept !== "person"),
  };
}
