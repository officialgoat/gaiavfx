import type { components } from "../../shared/api.generated";
import { request } from "../../shared/api";

export type Schema = components["schemas"];
export type Movie = Schema["MovieDetailDTO"];
export type Segment = Schema["SegmentDTO"];
export type Cut = Schema["CutDTO"];
export type Phase = Schema["PhaseDTO"];
export type SegmentPlan = Schema["DirectionSegmentPlanDTO"];
export type CutPlan = Schema["CutPlanDTO"];
export type OpeningPlan = Schema["OpeningPlanDTO"];
export type Receipt = Schema["ReceiptDTO"];
export type Relation = NonNullable<Cut["relation"]> | "unknown";

/** The one vocabulary of a cut's relation, for menus, forms and the timeline alike. */
export const RELATION_WORDS: Record<Relation, string> = {
  same_direction: "Same side / closer",
  side: "Side view",
  reverse: "Reverse",
  unknown: "Unknown",
};
export const relationWords = (relation: Cut["relation"] | undefined) =>
  RELATION_WORDS[relation ?? "unknown"];

const read = <T>(path: string) => request<T>("/api/direction/" + path);
const write = <T>(path: string, body: unknown, method = "POST") =>
  request<T>("/api/direction/" + path, { method, body: JSON.stringify(body) });

export const direction = {
  movies: () => read<Schema["DirectionMovieDTO"][]>("movies"),
  movie: (id: string) => read<Movie>(`movies/${id}`),
  createMovie: (body: Schema["MovieInput"]) =>
    write<Schema["MovieCommandDTO"]>("movies", body),
  updateMovie: (id: string, body: Schema["MoviePatch"]) =>
    write<Schema["MovieCommandDTO"]>(`movies/${id}`, body, "PATCH"),
  createSegment: (id: string, body: Schema["SegmentCreate"]) =>
    write<Schema["SegmentCommandDTO"]>(`movies/${id}/segments`, body),
  updateSegment: (id: string, body: Schema["SegmentPatch"]) =>
    write<Schema["SegmentCommandDTO"]>(`segments/${id}`, body, "PATCH"),
  removeSegment: (id: string, body: Schema["DirectionRevisionInput"]) =>
    write<Schema["RemovedDTO"]>(`segments/${id}/remove`, body),
  moveSegment: (id: string, body: Schema["SegmentMove"]) =>
    write<Schema["SegmentCommandDTO"]>(`segments/${id}/move`, body),
  /** What using a set for this scene would change (ADR 0019 §5), before it does. */
  useSetPreview: (
    id: string,
    query: {
      set_id: string;
      angle_id?: string;
      place_id?: string;
      angle_name?: string;
    },
  ) =>
    read<Schema["UseSetPreviewDTO"]>(
      `segments/${id}/use-set?${new URLSearchParams(
        Object.entries(query).filter(([, v]) => v) as [string, string][],
      )}`,
    ),
  useSet: (id: string, body: Schema["UseSetInput"]) =>
    write<Schema["SegmentDTO"]>(`segments/${id}/use-set`, body),
  // A set from a take (ADR 0019 §4): the preview reads Direction; the seed itself is a production operation.
  buildSetPreview: (id: string, shot: number, fillUnseen: boolean) =>
    read<Schema["BuildSetPreviewDTO"]>(
      `segments/${id}/build-set?shot=${shot}&fill_unseen=${fillUnseen}`,
    ),
  buildSet: (body: Schema["SetSeedInput"]) =>
    request<Schema["OperationDTO"]>("/api/production/set-seeds", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  retrySeed: (operationId: string) =>
    request<Schema["OperationDTO"]>(
      `/api/production/set-seeds/${encodeURIComponent(operationId)}/retry`,
      { method: "POST", body: "{}" },
    ),
  splitPoints: (id: string) =>
    read<Schema["DirectionSplitPointDTO"][]>(`segments/${id}/split-points`),
  splitSegment: (id: string, body: Schema["DirectionSegmentSplitInput"]) =>
    write<Schema["DirectionSegmentSplitCommandDTO"]>(
      `segments/${id}/split`,
      body,
    ),
  createPhase: (id: string, body: Schema["PhaseCreate"]) =>
    write<Schema["PhaseCommandDTO"]>(`segments/${id}/phases`, body),
  updatePhase: (id: string, body: Schema["PhasePatch"]) =>
    write<Schema["PhaseCommandDTO"]>(`phases/${id}`, body, "PATCH"),
  removePhase: (id: string, body: Schema["DirectionRevisionInput"]) =>
    write<Schema["RemovedDTO"]>(`phases/${id}/remove`, body),
  createCut: (body: Schema["CutCreate"]) =>
    write<Schema["CutCommandDTO"]>("cuts", body),
  updateCut: (id: string, body: Schema["CutPatch"]) =>
    write<Schema["CutCommandDTO"]>(`cuts/${id}`, body, "PATCH"),
  removeCut: (id: string, body: Schema["DirectionRevisionInput"]) =>
    write<Schema["RemovedDTO"]>(`cuts/${id}/remove`, body),
  segmentPlan: (id: string, context = "", sheet = "") => {
    const params = new URLSearchParams();
    if (context) params.set("context_artifact", context);
    if (sheet) params.set("sheet_asset", sheet);
    return read<SegmentPlan>(`segments/${id}/plan?${params}`);
  },
  plan: (id: string) => read<CutPlan>(`cuts/${id}/plan`),
  suitability: (id: string, body: Schema["CutSuitabilityInput"]) =>
    write<Schema["CutSuitabilityDTO"]>(`cuts/${id}/suitability`, body),
  approveEvidence: (id: string) =>
    write<Schema["EvidenceDTO"]>(`evidence/${id}/approve`, {}),
  keyframes: (id: string) =>
    read<Schema["KeyframeDTO"][]>(`cuts/${id}/keyframes`),
  receipts: (id: string) => read<Receipt[]>(`movies/${id}/receipts?limit=100`),
  undo: (id: string) => write<Schema["UndoDTO"]>(`receipts/${id}/undo`, {}),
  places: () => read<Schema["PlaceDTO"][]>("places"),
  angles: (id: string) => read<Schema["AngleDTO"][]>(`places/${id}/angles`),
  updateAngle: (id: string, body: Schema["DirectionAngleTextPatch"]) =>
    write<Schema["DirectionAngleTextCommandDTO"]>(
      `angles/${id}`,
      body,
      "PATCH",
    ),
  evidence: (id: string) =>
    read<Schema["EvidenceDTO"][]>(`angles/${id}/evidence`),
  selections: (movie: string, segment: string) =>
    request<Schema["SelectionsDTO"]>(
      `/api/production/selections?${new URLSearchParams({ movie_id: movie, segment_id: segment })}`,
    ),
};

// A monitor receives only this projection. Review will supply it from the blind
// endpoint: never join candidate previews to operation/job/seed metadata.
export type CandidatePreview = Pick<
  Schema["ReviewMemberDTO"],
  "candidate_id" | "asset_id" | "kind" | "letter"
>;

/** Direct-only preview. Blind Review must use its own server-issued member letters. */
export function directPreviews(
  operations: Schema["OperationDTO"][],
  cutIds: string[],
  segmentId?: string,
): CandidatePreview[] {
  return operations
    .filter(
      (o) =>
        (segmentId &&
          o.kind === "render" &&
          o.request.segment_id === segmentId) ||
        cutIds.includes(String(o.request.cut_id)),
    )
    .flatMap((o) => o.candidates)
    .map((c, i) => ({
      candidate_id: c.id,
      asset_id: c.asset_id,
      kind: c.kind,
      letter: String(i + 1),
    }));
}
