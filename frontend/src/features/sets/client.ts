import { request } from "../../shared/api";
import type { components } from "../../shared/api.generated";
import { route } from "../../shared/routes";

export type SetSummary = components["schemas"]["SetSummaryDTO"];
export type SetState = components["schemas"]["SetStateDTO"];
export type SetVersion = components["schemas"]["SetVersionDTO"];
export type SetKeyframe = components["schemas"]["SetKeyframeDTO"];
export type SetIssue = components["schemas"]["SetIssueDTO"];
export type SetFix = components["schemas"]["SetFixDTO"];
export type SetView = components["schemas"]["SetViewDTO"];
export type SetHideRegion = components["schemas"]["SetHideRegionDTO"];
export type SetMapPayload = components["schemas"]["SetMapDTO"];
export type IssueKind = SetIssue["kind"];

const S = "/api/sets/{set_id}" as const;
const set = (id: string) => ({ set_id: id });

// Routes are typed from the generated contracts (shared/routes.ts): a renamed route or body fails `tsc`.
export const sets = {
  list: () => route("get", "/api/sets"),
  get: (id: string, versionId?: string) =>
    route("get", S, { path: set(id), query: { version_id: versionId } }),
  map: (id: string, versionId: string) =>
    route("get", `${S}/versions/{version_id}/map`, {
      path: { ...set(id), version_id: versionId },
    }),
  importBundle: (folder: string) =>
    route("post", "/api/sets/import", { body: { folder } }),
  markIssue: (id: string, body: components["schemas"]["SetIssueInput"]) =>
    route("post", `${S}/issues`, { path: set(id), body }),
  issueStatus: (
    id: string,
    issueId: string,
    body: components["schemas"]["SetIssueStatusInput"],
  ) =>
    route("patch", `${S}/issues/{issue_id}`, {
      path: { ...set(id), issue_id: issueId },
      body,
    }),
  queueFix: (id: string, body: components["schemas"]["SetFixInput"]) =>
    route("post", `${S}/fixes`, { path: set(id), body }),
  cancelFix: (id: string, fixId: string, versionId?: string) =>
    route("delete", `${S}/fixes/{fix_id}`, {
      path: { ...set(id), fix_id: fixId },
      query: { version_id: versionId },
    }),
  hide: (id: string, body: components["schemas"]["SetHideRegionInput"]) =>
    route("post", `${S}/hide-regions`, { path: set(id), body }),
  hideRemoved: (
    id: string,
    regionId: string,
    removed: boolean,
    versionId?: string,
  ) =>
    route("patch", `${S}/hide-regions/{region_id}`, {
      path: { ...set(id), region_id: regionId },
      body: { removed, version_id: versionId ?? null },
    }),
  saveView: (id: string, body: components["schemas"]["SetViewInput"]) =>
    route("post", `${S}/views`, { path: set(id), body }),
  viewRemoved: (
    id: string,
    viewId: string,
    removed: boolean,
    versionId?: string,
  ) =>
    route("patch", `${S}/views/{view_id}`, {
      path: { ...set(id), view_id: viewId },
      body: { removed, version_id: versionId ?? null },
    }),
  // Stays on request(): a picture uploads as a form, not JSON.
  viewStill: (id: string, viewId: string, still: Blob) => {
    const body = new FormData();
    body.append("file", still, "view.png");
    return request<SetState>(
      `/api/sets/${encodeURIComponent(id)}/views/${encodeURIComponent(viewId)}/still`,
      { method: "POST", body },
    );
  },
  /** The places built as this set, with the angles its views can be the cameras of. */
  places: (id: string) => route("get", `${S}/places`, { path: set(id) }),
  aimView: (
    id: string,
    viewId: string,
    body: components["schemas"]["SetAimInput"],
  ) =>
    route("put", `${S}/views/{view_id}/angle`, {
      path: { ...set(id), view_id: viewId },
      body,
    }),
  /** `looksFixed`: the issues the version's fixes addressed that the user saw fixed in the review. */
  accept: (
    id: string,
    versionId: string,
    expectedCurrentId: string | null,
    looksFixed: string[] = [],
  ) =>
    route("post", `${S}/versions/{version_id}/accept`, {
      path: { ...set(id), version_id: versionId },
      body: { expected_current_id: expectedCurrentId, looks_fixed: looksFixed },
    }),
  discard: (id: string, versionId: string) =>
    route("post", `${S}/versions/{version_id}/discard`, {
      path: { ...set(id), version_id: versionId },
    }),
  /** Fill more unseen space (ADR 0020): queue an extension that Run queued fixes runs. */
  fillMore: (id: string, versionId: string) =>
    route("post", `${S}/fill-more`, {
      path: set(id),
      body: { version_id: versionId },
    }),
  /** Run queued fixes as its preview said (`fixIds`, the run's `fix_ids`; refused when the queue changed since),
   * or, with `retry` (Retry build), the newest build's failed fixes queued again first. */
  runFixes: (id: string, versionId: string, retry = false, fixIds?: string[]) =>
    route("post", `${S}/build`, {
      path: set(id),
      body: { version_id: versionId, retry, fix_ids: fixIds ?? null },
    }),
  retryFix: (id: string, fixId: string, versionId: string) =>
    route("post", `${S}/fixes/{fix_id}/retry`, {
      path: { ...set(id), fix_id: fixId },
      body: { version_id: versionId },
    }),
  /** A build is a Generation operation; cancelling it goes through the operations route. */
  cancelBuild: (operationId: string) =>
    route("post", "/api/production/operations/{operation_id}/cancel", {
      path: { operation_id: operationId },
    }),
};

export type SetBuild = NonNullable<SetState["build"]>;
/** A build still on its way: the page polls while one is. */
export const BUILDING = new Set(["queued", "running", "awaiting_review"]);
export const STAGE_WORDS: Record<NonNullable<SetBuild["stage"]>, string> = {
  prepare: "laying out the keyframes and the map",
  splat: "fitting the splat",
};

/** Words for every state the workbench shows; colour never carries a state alone (DESIGN.md). */
export const VERSION_WORDS: Record<SetVersion["state"], string> = {
  proposed: "Proposed",
  accepted: "Accepted",
  discarded: "Discarded",
};
export const ORIGIN_WORDS: Record<SetVersion["origin"], string> = {
  import: "imported",
  seed: "built",
  expand: "expanded",
  repair: "rebuilt",
  human: "edited",
};
export const AUTHOR_WORDS: Record<SetIssue["author"], string> = {
  human: "You",
  agent: "Agent",
  judge: "Judge",
};
export const ISSUE_WORDS: Record<IssueKind, string> = {
  shape: "Shape",
  space: "Extra space",
  duplicate: "Duplicate",
  stairs: "Stairs",
  obstacle: "Obstacle",
  pocket: "Unseen pocket",
  floaters: "Floaters",
  hole: "Hole",
  split: "Split object",
  doubled: "Doubled object",
  "two-way": "Faces two ways",
  other: "Other",
};

/** A splat's size in words: thousands as "412k", a handful exactly. */
export const STATUS_WORDS: Record<SetIssue["status"], string> = {
  open: "Open",
  queued: "Fix queued",
  fixed: "Fixed",
  dismissed: "Dismissed",
  failed: "Fix failed",
};
export const ROLE_WORDS: Record<SetKeyframe["role"], string> = {
  observed: "Panorama",
  generated: "Explored view",
  trajectory: "In-between frame",
  "appearance-only": "Colour only",
};

/** Where an issue was marked: a surface point, or the camera and the spot on it where nothing was hit. */
export type IssueRegion = {
  point?: number[];
  viewmat?: number[][];
  screen?: number[];
};
export const regionOf = (issue: SetIssue) =>
  (issue.region ?? {}) as IssueRegion;

export function gaussianCount(n: number) {
  return n >= 1000 ? `${(n / 1000).toFixed(0)}k` : String(n);
}

export function versionLabel(version: {
  ordinal: number;
  state: SetVersion["state"];
}) {
  return `${VERSION_WORDS[version.state]} v${version.ordinal}`;
}
