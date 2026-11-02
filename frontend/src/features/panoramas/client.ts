import type { components } from "../../shared/api.generated";
import { route } from "../../shared/routes";

export type Panorama = components["schemas"]["PanoramaDTO"];
export type PanoramaCandidate = components["schemas"]["PanoramaCandidateDTO"];
export type PanoramaInput = components["schemas"]["PanoramaInput"];
export type PanoramaPreview = components["schemas"]["PanoramaPreviewDTO"];
export type PanoramaStagePreview =
  components["schemas"]["PanoramaStagePreviewDTO"];
export type PanoramaStageInput = components["schemas"]["PanoramaStageInput"];

const P = "/api/panoramas" as const;
const candidate = (id: string) => ({ candidate_id: id });

// Routes are typed from the generated contracts (shared/routes.ts): a renamed route or body fails `tsc`.
export const panoramas = {
  list: (placeId?: string) =>
    route("get", P, { query: placeId ? { place_id: placeId } : {} }),
  get: (id: string) =>
    route("get", `${P}/{operation_id}`, { path: { operation_id: id } }),
  preview: (body: PanoramaInput) => route("post", `${P}/preview`, { body }),
  start: (body: PanoramaInput) => route("post", P, { body }),
  more: (id: string) =>
    route("post", `${P}/{operation_id}/more`, { path: { operation_id: id } }),
  choose: (candidateId: string) =>
    route("post", `${P}/candidates/{candidate_id}/choose`, {
      path: candidate(candidateId),
    }),
  stagePreview: (candidateId: string, fillUnseen: boolean) =>
    route("get", `${P}/candidates/{candidate_id}/stage-preview`, {
      path: candidate(candidateId),
      query: { fill_unseen: fillUnseen },
    }),
  buildStage: (candidateId: string, body: PanoramaStageInput) =>
    route("post", `${P}/candidates/{candidate_id}/stage`, {
      path: candidate(candidateId),
      body,
    }),
};

/** An attempt is still being drawn, or a stage is being built from it: read it again until it settles. */
export const unsettled = (panorama: Panorama) =>
  panorama.state === "building" || panorama.build?.state === "building";

/** Headings in the words a person uses when standing in the place. */
export const COMPASS = [
  [0, "Ahead"],
  [45, "Ahead right"],
  [90, "Right"],
  [135, "Behind right"],
  [180, "Behind"],
  [225, "Behind left"],
  [270, "Left"],
  [315, "Ahead left"],
] as const;

export function headingWords(heading: number) {
  const h = ((Math.round(heading) % 360) + 360) % 360;
  const exact = COMPASS.find(([deg]) => deg === h);
  if (exact) return `${exact[1]} (${h}°)`;
  return h <= 180 ? `${h}° right` : `${360 - h}° left`;
}
