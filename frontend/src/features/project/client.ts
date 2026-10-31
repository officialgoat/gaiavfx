import type { components } from "../../shared/api.generated";
import { route } from "../../shared/routes";

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

export type Continuation = Schema["ContinuationPlanDTO"];
/** The one vocabulary of a continuation's handoff methods. */
export const HANDOFF_WORDS: Record<string, [name: string, short: string]> = {
  paired: ["Paired latents", "Paired"],
  masked: ["Masked handoff", "Masked"],
};
export const handoffWords = (method: string, short = false) =>
  HANDOFF_WORDS[method]?.[short ? 1 : 0] ?? method;

const D = "/api/direction";

// Every route is typed from the generated contracts (shared/routes.ts): a renamed route or body fails `tsc`.
export const direction = {
  movies: () => route("get", `${D}/movies`),
  /** The movie as the workspace reads it, in one read: plans, pins, selections, history and Undo's targets. */
  read: (id: string) =>
    route("get", `${D}/movies/{movie_id}/read`, { path: { movie_id: id } }),
  createMovie: (body: Schema["MovieInput"]) =>
    route("post", `${D}/movies`, { body }),
  updateMovie: (id: string, body: Schema["MoviePatch"]) =>
    route("patch", `${D}/movies/{movie_id}`, { path: { movie_id: id }, body }),
  createSegment: (id: string, body: Schema["SegmentCreate"]) =>
    route("post", `${D}/movies/{movie_id}/segments`, {
      path: { movie_id: id },
      body,
    }),
  updateSegment: (id: string, body: Schema["SegmentPatch"]) =>
    route("patch", `${D}/segments/{segment_id}`, {
      path: { segment_id: id },
      body,
    }),
  /** A continuation's handoff (docs/CONTINUATION.md); null returns a field to the rule. Python refuses with why. */
  setContinuation: (id: string, body: Schema["SegmentContinuationPatch"]) =>
    route("patch", `${D}/segments/{segment_id}/continuation`, {
      path: { segment_id: id },
      body,
    }),
  removeSegment: (id: string, body: Schema["DirectionRevisionInput"]) =>
    route("post", `${D}/segments/{segment_id}/remove`, {
      path: { segment_id: id },
      body,
    }),
  moveSegment: (id: string, body: Schema["SegmentMove"]) =>
    route("post", `${D}/segments/{segment_id}/move`, {
      path: { segment_id: id },
      body,
    }),
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
    route("get", `${D}/segments/{segment_id}/use-set`, {
      path: { segment_id: id },
      query,
    }),
  useSet: (id: string, body: Schema["UseSetInput"]) =>
    route("post", `${D}/segments/{segment_id}/use-set`, {
      path: { segment_id: id },
      body,
    }),
  // A set from a take (ADR 0019 §4): the preview reads Direction; the seed itself is a production operation.
  buildSetPreview: (id: string, shot: number, fillUnseen: boolean) =>
    route("get", `${D}/segments/{segment_id}/build-set`, {
      path: { segment_id: id },
      query: { shot, fill_unseen: fillUnseen },
    }),
  buildSet: (body: Schema["SetSeedInput"]) =>
    route("post", "/api/production/set-seeds", { body }),
  retrySeed: (operationId: string) =>
    route("post", "/api/production/set-seeds/{operation_id}/retry", {
      path: { operation_id: operationId },
    }),
  splitPoints: (id: string) =>
    route("get", `${D}/segments/{segment_id}/split-points`, {
      path: { segment_id: id },
    }),
  splitSegment: (id: string, body: Schema["DirectionSegmentSplitInput"]) =>
    route("post", `${D}/segments/{segment_id}/split`, {
      path: { segment_id: id },
      body,
    }),
  createPhase: (id: string, body: Schema["PhaseCreate"]) =>
    route("post", `${D}/segments/{segment_id}/phases`, {
      path: { segment_id: id },
      body,
    }),
  updatePhase: (id: string, body: Schema["PhasePatch"]) =>
    route("patch", `${D}/phases/{phase_id}`, { path: { phase_id: id }, body }),
  removePhase: (id: string, body: Schema["DirectionRevisionInput"]) =>
    route("post", `${D}/phases/{phase_id}/remove`, {
      path: { phase_id: id },
      body,
    }),
  createCut: (body: Schema["CutCreate"]) =>
    route("post", `${D}/cuts`, { body }),
  updateCut: (id: string, body: Schema["CutPatch"]) =>
    route("patch", `${D}/cuts/{cut_id}`, { path: { cut_id: id }, body }),
  removeCut: (id: string, body: Schema["DirectionRevisionInput"]) =>
    route("post", `${D}/cuts/{cut_id}/remove`, { path: { cut_id: id }, body }),
  segmentPlan: (id: string, context = "", sheet = "") =>
    route("get", `${D}/segments/{segment_id}/plan`, {
      path: { segment_id: id },
      query: { context_artifact: context, sheet_asset: sheet },
    }),
  plan: (id: string) =>
    route("get", `${D}/cuts/{cut_id}/plan`, { path: { cut_id: id } }),
  suitability: (id: string, body: Schema["CutSuitabilityInput"]) =>
    route("post", `${D}/cuts/{cut_id}/suitability`, {
      path: { cut_id: id },
      body,
    }),
  approveEvidence: (id: string) =>
    route("post", `${D}/evidence/{evidence_id}/approve`, {
      path: { evidence_id: id },
    }),
  keyframes: (id: string) =>
    route("get", `${D}/cuts/{cut_id}/keyframes`, { path: { cut_id: id } }),
  receipts: (id: string) =>
    route("get", `${D}/movies/{movie_id}/receipts`, {
      path: { movie_id: id },
      query: { limit: 100 },
    }),
  undo: (id: string) =>
    route("post", `${D}/receipts/{receipt_id}/undo`, {
      path: { receipt_id: id },
    }),
  places: () => route("get", `${D}/places`),
  angles: (id: string) =>
    route("get", `${D}/places/{place_id}/angles`, { path: { place_id: id } }),
  updatePlace: (id: string, body: Schema["DirectionPlaceTextPatch"]) =>
    route("patch", `${D}/places/{place_id}`, { path: { place_id: id }, body }),
  updateAngle: (id: string, body: Schema["DirectionAngleTextPatch"]) =>
    route("patch", `${D}/angles/{angle_id}`, { path: { angle_id: id }, body }),
  evidence: (id: string) =>
    route("get", `${D}/angles/{angle_id}/evidence`, {
      path: { angle_id: id },
    }),
  selections: (movie: string, segment: string) =>
    route("get", "/api/production/selections", {
      query: { movie_id: movie, segment_id: segment },
    }),
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
