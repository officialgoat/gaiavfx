import type { Cut } from "../project/client";
import type { DirectController } from "../project/use-direction";

/** The shot Prepare works on (ADR 0019 §1): the shot after a cut, or a segment's first shot. Its id is the cut's,
 * or the segment's for an opening. */
export type PreparationShot =
  | { kind: "cut"; id: string; angleId: string; cut: Cut; label: string }
  | { kind: "opening"; id: string; angleId: string; label: string };

function cutShot(cut: Cut): PreparationShot {
  return {
    kind: "cut",
    id: cut.id,
    angleId: cut.to_angle_id,
    cut,
    label: `Cut f${cut.frame}`,
  };
}

/** The selected cut's shot; with a segment selected, its first shot when it opens on an angle, else its first
 * cut's. */
export function preparationShot(d: DirectController): PreparationShot | null {
  const cut = d.getCut();
  if (cut) return cutShot(cut);
  const segment = d.getSegment();
  if (!segment) return null;
  if (segment.opening_angle_id)
    return {
      kind: "opening",
      id: segment.id,
      angleId: segment.opening_angle_id,
      label: "First shot",
    };
  return segment.cuts[0] ? cutShot(segment.cuts[0]) : null;
}

/** The shot's angle, its place and the place's angles. */
export function shotPlace(d: DirectController, shot: PreparationShot | null) {
  const angle = d.angles.find((a) => a.id === shot?.angleId);
  const place = d.places.data?.find((p) => p.id === angle?.place_id);
  return {
    angle,
    place,
    angles: d.angles.filter((a) => a.place_id === place?.id),
  };
}
