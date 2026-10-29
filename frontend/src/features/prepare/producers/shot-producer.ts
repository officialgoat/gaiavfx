import type { Schema } from "../../project/client";
import type { DirectController } from "../../project/use-direction";
import type { ReviewController } from "../../review/use-review";
import type { PreparationShot } from "../shot";

/** What a reference producer prepares for: the shot, its place and the place's angles. */
export type ShotProducerProps = {
  d: DirectController;
  r: ReviewController;
  shot: PreparationShot;
  place: Schema["PlaceDTO"];
  angles: Schema["AngleDTO"][];
};

/** How a producer's sheet names what it prepares: the place and the shot. */
export const shotDescription = ({ place, shot }: ShotProducerProps) =>
  `${place.name} · ${shot.label}`;

/** A form's identity: a new shot or producer starts a fresh form. */
export const shotFormKey = (kind: string, { d, shot }: ShotProducerProps) =>
  `${kind}-${d.getSegment()?.id ?? ""}-${shot.id}`;
