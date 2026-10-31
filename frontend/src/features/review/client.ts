import { request } from "../../shared/api";
import { route } from "../../shared/routes";
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
const P = "/api/production";
/** The route that approves a candidate, by its review's purpose (a plain string in ReviewDTO). */
const approvals = {
  plate: "plates",
  angle_views: "views",
  insertion: "pins",
  character_replacement: "replacements",
} as const;
// Routes are typed from the generated contracts (shared/routes.ts): a renamed route or body fails `tsc`.
export const production = {
  people: (assetId: string) =>
    route("get", `${P}/people-analyses`, { query: { asset_id: assetId } }),
  replacementPlan: (body: Schema["ReplacementPlanInput"]) =>
    route("post", `${P}/replacement-plans`, { body }),
  recordedReplacementPlan: (id: string) =>
    route("get", `${P}/replacements/{operation_id}/plan`, {
      path: { operation_id: id },
    }),
  sheetTiles: (placeId: string) =>
    route("get", `${P}/places/{place_id}/sheet-tiles`, {
      path: { place_id: placeId },
    }),
  operation: (id: string) =>
    route("get", `${P}/operations/{operation_id}`, {
      path: { operation_id: id },
    }),
  operationSummary: (id: string) =>
    route("get", `${P}/operations/{operation_id}/summary`, {
      path: { operation_id: id },
    }),
  start: (kind: Producer, body: ProducerBody) =>
    route(
      "post",
      `${P}/${kind === "removals" && "empty" in body ? "plates" : kind}`,
      { body },
    ),
  // Stays on request(): Producer includes sheets, which has no review route (callers refuse it first).
  open: (kind: Producer, id: string) =>
    request<Review>(`${P}/${kind}/${id}/review`, {
      method: "POST",
      body: "{}",
    }),
  review: (id: string) =>
    route("get", `${P}/reviews/{review_id}`, { path: { review_id: id } }),
  reveal: (id: string) =>
    route("get", `${P}/reviews/{review_id}/reveal`, {
      path: { review_id: id },
    }),
  rank: (id: string, body: Ranking) =>
    route("put", `${P}/reviews/{review_id}/ranking`, {
      path: { review_id: id },
      body,
    }),
  reject: (id: string, body: Schema["ProductionReferenceRejectionInput"]) =>
    route("post", `${P}/reviews/{review_id}/reject`, {
      path: { review_id: id },
      body,
    }),
  select: (id: string, body: Schema["SelectInput"]) =>
    route("post", `${P}/reviews/{review_id}/select`, {
      path: { review_id: id },
      body,
    }),
  approve: (
    purpose: string,
    candidate_id: string,
    replace = false,
  ): Promise<unknown> =>
    route(
      "post",
      `${P}/${approvals[purpose as keyof typeof approvals]}/approve`,
      { body: { candidate_id, ...(replace ? { replace: true } : {}) } },
    ),
  tags: () => route("get", `${P}/review-tags`),
  prescreen: (id: string) =>
    route("post", `${P}/candidates/{candidate_id}/prescreen`, {
      path: { candidate_id: id },
    }),
  cancel: (id: string) =>
    route("post", `${P}/operations/{operation_id}/cancel`, {
      path: { operation_id: id },
    }),
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
