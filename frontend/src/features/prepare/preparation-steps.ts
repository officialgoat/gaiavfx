import { Camera, Image, Images, Layers3, MapPin } from "lucide-react";
import type { Operation } from "../generation/client";
import type { Schema } from "../project/client";
import type { Producer } from "../review/client";
import type { PreparationShot } from "./shot";

export type PreparationStep = {
  title: string;
  /** The operation kind whose attempts this step lists; `camera` lists none. */
  kind: "removal" | "survey" | "set_views" | "insert" | "assemble" | "camera";
  producer: Producer | null;
  icon: typeof Image;
  asset: string | null | undefined;
  approved: boolean;
  /** Which candidates of the step's attempts are this shot's. */
  ownsCandidate: (candidate: Operation["candidates"][number]) => boolean;
};

const any = () => true;

/** The steps that prepare one shot's references (ADR 0019 §3), by the place's view producer, which Python names:
 * a set's views (the angle's camera, then renders reviewed like a survey's), or a plate and a survey. A pin
 * belongs to a cut, so a segment's first shot has none. */
export function preparationSteps({
  place,
  shot,
  evidence,
  pins,
  related,
  camera,
}: {
  place: Schema["PlaceDTO"];
  shot: PreparationShot;
  evidence: Schema["EvidenceDTO"][];
  pins: Schema["KeyframeDTO"][];
  related: Operation[];
  camera: { asset_id?: string | null } | null;
}): PreparationStep[] {
  const view = evidence.find((e) => e.approved && e.angle_id === shot.angleId);
  const ownAngle = (c: Operation["candidates"][number]) =>
    c.output_key === shot.angleId;
  const sheet = related.find(
    (o) => o.kind === "assemble" && o.state === "succeeded",
  );
  const views: PreparationStep[] =
    place.view_producer === "set_views"
      ? [
          {
            title: "Camera",
            kind: "camera",
            producer: null,
            icon: Camera,
            asset: camera?.asset_id,
            approved: !!camera,
            ownsCandidate: any,
          },
          {
            title: "Views",
            kind: "set_views",
            producer: "set-views",
            icon: Images,
            asset: view?.asset_id,
            approved: !!view,
            ownsCandidate: ownAngle,
          },
        ]
      : [
          {
            title: "Plate",
            kind: "removal",
            producer: "removals",
            icon: Image,
            asset: place.plate_asset_id,
            approved: !!place.plate_asset_id,
            ownsCandidate: any,
          },
          {
            title: "Views",
            kind: "survey",
            producer: "surveys",
            icon: Images,
            asset: view?.asset_id,
            approved: !!view,
            ownsCandidate: ownAngle,
          },
        ];
  const pin =
    shot.kind === "cut"
      ? [
          {
            title: "Pin",
            kind: "insert" as const,
            producer: "insertions" as const,
            icon: MapPin,
            asset: pins.find((p) => p.approved)?.asset_id,
            approved: pins.some((p) => p.approved),
            ownsCandidate: any,
          },
        ]
      : [];
  return [
    ...views,
    ...pin,
    {
      title: "Sheet",
      kind: "assemble",
      producer: "sheets",
      icon: Layers3,
      asset: sheet?.candidates[0]?.asset_id,
      approved: !!sheet,
      ownsCandidate: any,
    },
  ];
}
