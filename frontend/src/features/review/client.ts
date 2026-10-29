import { request } from "../../shared/api";
import type { Schema } from "../project/client";

export type Review = Schema["ReviewDTO"];
export type Member = Schema["ReviewMemberDTO"];
export type Ranking = Schema["RankingInput"];
export type Producer =
  | "removals"
  | "surveys"
  | "set-views"
  | "insertions"
  | "sheets"
  | "renders"
  | "replacements";
export type ProducerBody =
  | Schema["RemovalInput"]
  | Schema["EmptyPlateInput"]
  | Schema["SurveyInput"]
  | Schema["SetViewsInput"]
  | Schema["InsertionInput"]
  | Schema["SheetInput"]
  | Schema["RenderInput"]
  | Schema["ReplacementStartInput"];
export const producerRoute: Record<string, Producer> = {
  removal: "removals",
  survey: "surveys",
  set_views: "set-views",
  insert: "insertions",
  assemble: "sheets",
  render: "renders",
  character_replacement: "replacements",
};
const read = <T>(path: string) => request<T>(`/api/production/${path}`);
const write = <T>(path: string, body: unknown = {}, method = "POST") =>
  request<T>(`/api/production/${path}`, { method, body: JSON.stringify(body) });
export const production = {
  people: (assetId: string) =>
    read<Schema["ReplacementPeopleAnalysisDTO"][]>(
      `people-analyses?asset_id=${encodeURIComponent(assetId)}`,
    ),
  replacementPlan: (body: Schema["ReplacementPlanInput"]) =>
    write<Schema["ReplacementPlanDTO"]>("replacement-plans", body),
  recordedReplacementPlan: (id: string) =>
    read<Schema["ReplacementPlanDTO"]>(
      `replacements/${encodeURIComponent(id)}/plan`,
    ),
  sheetTiles: (placeId: string) =>
    read<Schema["ProductionSheetTilesDTO"]>(`places/${placeId}/sheet-tiles`),
  operation: (id: string) => read<Schema["OperationDTO"]>(`operations/${id}`),
  operationSummary: (id: string) =>
    read<Schema["OperationSummaryDTO"]>(
      `operations/${encodeURIComponent(id)}/summary`,
    ),
  start: (kind: Producer, body: ProducerBody) =>
    write<Schema["OperationDTO"]>(
      kind === "removals" && "empty" in body ? "plates" : kind,
      body,
    ),
  open: (kind: Producer, id: string) => write<Review>(`${kind}/${id}/review`),
  review: (id: string) => read<Review>(`reviews/${id}`),
  reveal: (id: string) => read<Review>(`reviews/${id}/reveal`),
  rank: (id: string, body: Ranking) =>
    write<Schema["RankingDTO"]>(`reviews/${id}/ranking`, body, "PUT"),
  reject: (id: string, body: Schema["ProductionReferenceRejectionInput"]) =>
    write<Review>(`reviews/${id}/reject`, body),
  select: (id: string, body: Schema["SelectInput"]) =>
    write<Schema["SelectionDTO"]>(`reviews/${id}/select`, body),
  approve: (purpose: string, candidate_id: string, replace = false) =>
    write<unknown>(
      `${({ plate: "plates", angle_views: "views", insertion: "pins", character_replacement: "replacements" } as Record<string, string>)[purpose]}/approve`,
      { candidate_id, ...(replace ? { replace: true } : {}) },
    ),
  tags: () => read<Schema["ReviewTaxonomyDTO"]>("review-tags"),
  prescreen: (id: string) =>
    write<Schema["PrescreenDTO"]>(`candidates/${id}/prescreen`),
  cancel: (id: string) =>
    write<Schema["OperationDTO"]>(`operations/${id}/cancel`),
};

export function rankingDraft(review: Review): Ranking {
  return {
    expected_revision: review.ranking.revision,
    tiers: review.ranking.tiers,
    usable_tiers: review.ranking.usable_tiers,
    notes: review.ranking.notes,
    tags: review.ranking.tags,
    general: review.ranking.general,
  };
}
