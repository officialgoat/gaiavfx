import { request } from "../../shared/api";
import type { components } from "../../shared/api.generated";

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

const json = (method: string, body: unknown): RequestInit => ({
  method,
  body: JSON.stringify(body),
});

export const sets = {
  list: () => request<SetSummary[]>("/api/sets"),
  get: (id: string, versionId?: string) =>
    request<SetState>(
      `/api/sets/${id}${versionId ? `?version_id=${encodeURIComponent(versionId)}` : ""}`,
    ),
  map: (id: string, versionId: string) =>
    request<SetMapPayload>(`/api/sets/${id}/versions/${versionId}/map`),
  importBundle: (folder: string) =>
    request<SetState>("/api/sets/import", json("POST", { folder })),
  markIssue: (id: string, body: components["schemas"]["SetIssueInput"]) =>
    request<SetState>(`/api/sets/${id}/issues`, json("POST", body)),
  issueStatus: (
    id: string,
    issueId: string,
    body: components["schemas"]["SetIssueStatusInput"],
  ) =>
    request<SetState>(`/api/sets/${id}/issues/${issueId}`, json("PATCH", body)),
  queueFix: (id: string, body: components["schemas"]["SetFixInput"]) =>
    request<SetState>(`/api/sets/${id}/fixes`, json("POST", body)),
  cancelFix: (id: string, fixId: string, versionId?: string) =>
    request<SetState>(
      `/api/sets/${id}/fixes/${fixId}${versionId ? `?version_id=${versionId}` : ""}`,
      { method: "DELETE" },
    ),
  hide: (id: string, body: components["schemas"]["SetHideRegionInput"]) =>
    request<SetState>(`/api/sets/${id}/hide-regions`, json("POST", body)),
  hideRemoved: (
    id: string,
    regionId: string,
    removed: boolean,
    versionId?: string,
  ) =>
    request<SetState>(
      `/api/sets/${id}/hide-regions/${regionId}`,
      json("PATCH", { removed, version_id: versionId ?? null }),
    ),
  saveView: (id: string, body: components["schemas"]["SetViewInput"]) =>
    request<SetState>(`/api/sets/${id}/views`, json("POST", body)),
  viewRemoved: (
    id: string,
    viewId: string,
    removed: boolean,
    versionId?: string,
  ) =>
    request<SetState>(
      `/api/sets/${id}/views/${viewId}`,
      json("PATCH", { removed, version_id: versionId ?? null }),
    ),
  viewStill: (id: string, viewId: string, still: Blob) => {
    const body = new FormData();
    body.append("file", still, "view.png");
    return request<SetState>(`/api/sets/${id}/views/${viewId}/still`, {
      method: "POST",
      body,
    });
  },
  /** The places built as this set, with the angles its views can be the cameras of. */
  places: (id: string) =>
    request<components["schemas"]["SetPlaceDTO"][]>(`/api/sets/${id}/places`),
  aimView: (
    id: string,
    viewId: string,
    body: components["schemas"]["SetAimInput"],
  ) =>
    request<SetState>(
      `/api/sets/${id}/views/${viewId}/angle`,
      json("PUT", body),
    ),
  /** `looksFixed`: the issues the version's fixes addressed that the user saw fixed in the review. */
  accept: (
    id: string,
    versionId: string,
    expectedCurrentId: string | null,
    looksFixed: string[] = [],
  ) =>
    request<SetState>(
      `/api/sets/${id}/versions/${versionId}/accept`,
      json("POST", {
        expected_current_id: expectedCurrentId,
        looks_fixed: looksFixed,
      }),
    ),
  discard: (id: string, versionId: string) =>
    request<SetState>(`/api/sets/${id}/versions/${versionId}/discard`, {
      method: "POST",
    }),
  /** Fill more unseen space (ADR 0020): queue an extension that Run queued fixes runs. */
  fillMore: (id: string, versionId: string) =>
    request<SetState>(
      `/api/sets/${id}/fill-more`,
      json("POST", { version_id: versionId }),
    ),
  /** `retry`: queue the newest build's failed fixes again first (Retry build). */
  runFixes: (id: string, versionId: string, retry = false) =>
    request<SetState>(
      `/api/sets/${id}/build`,
      json("POST", { version_id: versionId, retry }),
    ),
  retryFix: (id: string, fixId: string, versionId: string) =>
    request<SetState>(
      `/api/sets/${id}/fixes/${fixId}/retry`,
      json("POST", { version_id: versionId }),
    ),
  /** A build is a Generation operation; cancelling it goes through the operations route. */
  cancelBuild: (operationId: string) =>
    request<unknown>(
      `/api/production/operations/${operationId}/cancel`,
      json("POST", {}),
    ),
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
