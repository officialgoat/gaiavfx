import type { components } from "../../shared/api.generated";
import { route } from "../../shared/routes";

export type Schema = components["schemas"];
export type Recast = Schema["RecastDTO"];
export type Shot = Schema["RecastShotDTO"];
export type Character = Schema["CharacterDTO"];
export type Voice = Schema["VoiceDTO"];
const R = "/api/recasts";
const recastIds = (id: string) => ({ recast_id: id });
const characterIds = (id: string, character: string) => ({
  recast_id: id,
  character_id: character,
});
const shotIds = (id: string, shot: string) => ({
  recast_id: id,
  shot_id: shot,
});
const trackIds = (
  id: string,
  shot: string,
  link: { analysis_operation_id: string; track_id: string },
) => ({
  recast_id: id,
  shot_id: shot,
  analysis_operation_id: link.analysis_operation_id,
  track_id: link.track_id,
});
// Every route is typed from the generated contracts (shared/routes.ts): a renamed route or body fails `tsc`.
export const recasts = {
  list: () => route("get", R),
  get: (id: string) =>
    route("get", `${R}/{recast_id}`, { path: recastIds(id) }),
  create: (body: Schema["CreateRecastInput"]) => route("post", R, { body }),
  character: (
    id: string,
    character: string,
    body: Schema["CharacterPatchInput"],
  ) =>
    route("patch", `${R}/{recast_id}/characters/{character_id}`, {
      path: characterIds(id, character),
      body,
    }),
  target: (id: string, character: string, body: Schema["TargetInput"]) =>
    route("put", `${R}/{recast_id}/characters/{character_id}/target`, {
      path: characterIds(id, character),
      body,
    }),
  confirm: (id: string, character: string, body: Schema["RevisionInputDTO"]) =>
    route("post", `${R}/{recast_id}/characters/{character_id}/target/confirm`, {
      path: characterIds(id, character),
      body,
    }),
  approveReference: (
    id: string,
    character: string,
    body: Schema["RevisionInputDTO"],
  ) =>
    route(
      "post",
      `${R}/{recast_id}/characters/{character_id}/reference/approve`,
      { path: characterIds(id, character), body },
    ),
  rejectReference: (
    id: string,
    character: string,
    body: Schema["RevisionInputDTO"],
  ) =>
    route(
      "post",
      `${R}/{recast_id}/characters/{character_id}/reference/reject`,
      { path: characterIds(id, character), body },
    ),
  clear: (id: string, character: string, revision: number) =>
    route("delete", `${R}/{recast_id}/characters/{character_id}/target`, {
      path: characterIds(id, character),
      query: { expected_revision: revision },
    }),
  // A character's voice routes (ADR 0016 §1, §4; P5): every one is a human command.
  /** The character's voice recordings, in order: confirmed at once and journaled; building asks separately. */
  setVoice: (
    id: string,
    character: string,
    body: Schema["VoiceSamplesInput"],
  ) =>
    route("put", `${R}/{recast_id}/characters/{character_id}/voice`, {
      path: characterIds(id, character),
      body,
    }),
  /** Removes the recordings, or rejects proposed ones (journaled). */
  clearVoice: (id: string, character: string, revision: number) =>
    route("delete", `${R}/{recast_id}/characters/{character_id}/voice`, {
      path: characterIds(id, character),
      query: { expected_revision: revision },
    }),
  confirmVoice: (
    id: string,
    character: string,
    body: Schema["RevisionInputDTO"],
  ) =>
    route("post", `${R}/{recast_id}/characters/{character_id}/voice/confirm`, {
      path: characterIds(id, character),
      body,
    }),
  /** Starts the voice build (GPU hours): only after the Build voice sheet asked. */
  buildVoice: (
    id: string,
    character: string,
    body: Schema["BuildVoiceInput"],
  ) =>
    route("post", `${R}/{recast_id}/characters/{character_id}/voice/build`, {
      path: characterIds(id, character),
      body,
    }),
  /** Resumes a stopped build, starts the next attempt of a failed one, or auditions again. */
  retryVoice: (
    id: string,
    character: string,
    body: Schema["RevisionInputDTO"],
  ) =>
    route("post", `${R}/{recast_id}/characters/{character_id}/voice/retry`, {
      path: characterIds(id, character),
      body,
    }),
  approveVoice: (
    id: string,
    character: string,
    body: Schema["RevisionInputDTO"],
  ) =>
    route("post", `${R}/{recast_id}/characters/{character_id}/voice/approve`, {
      path: characterIds(id, character),
      body,
    }),
  rejectVoice: (
    id: string,
    character: string,
    body: Schema["RevisionInputDTO"],
  ) =>
    route("post", `${R}/{recast_id}/characters/{character_id}/voice/reject`, {
      path: characterIds(id, character),
      body,
    }),
  // Appearance links (ADR 0012 Amendment 6): a window track of a shot linked to a character, decided by a human.
  appearance: (
    id: string,
    shot: string,
    link: { analysis_operation_id: string; track_id: string },
    action: "propose" | "confirm" | "reject",
    body: Schema["AppearanceDecisionInput"] | Schema["AppearanceProposalInput"],
  ) =>
    route(
      "post",
      `${R}/{recast_id}/shots/{shot_id}/appearances/{analysis_operation_id}/{track_id}/${action}`,
      { path: trackIds(id, shot, link), body },
    ),
  clearAppearance: (
    id: string,
    shot: string,
    link: { analysis_operation_id: string; track_id: string },
    revision: number,
  ) =>
    route(
      "delete",
      `${R}/{recast_id}/shots/{shot_id}/appearances/{analysis_operation_id}/{track_id}`,
      {
        path: trackIds(id, shot, link),
        query: { expected_revision: revision },
      },
    ),
  /** What a run over a range would cost in a mode, before it starts. */
  estimate: (
    id: string,
    mode: "draft" | "final",
    range: Schema["FrameRangeDTO"] | null,
  ) =>
    route("get", `${R}/{recast_id}/estimate`, {
      path: recastIds(id),
      query: { mode, start: range?.start, end: range?.end },
    }),
  suggestAppearances: (
    id: string,
    shot: string,
    body: Schema["RevisionInputDTO"],
  ) =>
    route("post", `${R}/{recast_id}/shots/{shot_id}/appearances/suggest`, {
      path: shotIds(id, shot),
      body,
    }),
  merge: (id: string, body: Schema["MergeInput"]) =>
    route("post", `${R}/{recast_id}/characters/merge`, {
      path: recastIds(id),
      body,
    }),
  /** Sets pairs aside as "not the same person"; stored in the recast (ADR 0017, CP3). */
  notSame: (id: string, body: Schema["NotSameInput"]) =>
    route("post", `${R}/{recast_id}/not-same`, { path: recastIds(id), body }),
  move: (id: string, body: Schema["MoveTracksInput"]) =>
    route("post", `${R}/{recast_id}/tracks/move`, {
      path: recastIds(id),
      body,
    }),
  /** Undoes a stored cast edit (ADR 0012 Amendment 7 §1); redo is the undo of its inverse entry. */
  undoEdit: (id: string, editId: string, revision: number) =>
    route("post", `${R}/{recast_id}/edits/{edit_id}/undo`, {
      path: { recast_id: id, edit_id: editId },
      body: { expected_revision: revision },
    }),
  shot: (id: string, shot: string, body: Schema["ShotPatchInput"]) =>
    route("patch", `${R}/{recast_id}/shots/{shot_id}`, {
      path: shotIds(id, shot),
      body,
    }),
  plan: (id: string, body: Schema["PlanInput"]) =>
    route("post", `${R}/{recast_id}/plan`, { path: recastIds(id), body }),
  run: (id: string, body: Schema["RunInput"]) =>
    route("post", `${R}/{recast_id}/run`, { path: recastIds(id), body }),
  control: (
    id: string,
    action: "pause" | "cancel",
    body: Schema["RevisionInputDTO"],
  ) =>
    route("post", `${R}/{recast_id}/run/${action}`, {
      path: recastIds(id),
      body,
    }),
  retryAnalysis: (id: string, body: Schema["RevisionInputDTO"]) =>
    route("post", `${R}/{recast_id}/analysis/retry`, {
      path: recastIds(id),
      body,
    }),
  /** Starts the film's shared dialogue analysis (ADR 0016 §2): human only, after the Voice step asked. */
  startDialogue: (id: string, body: Schema["RevisionInputDTO"]) =>
    route("post", `${R}/{recast_id}/dialogue`, { path: recastIds(id), body }),
  /** A new attempt after the dialogue analysis failed or was cancelled. */
  retryDialogue: (id: string, body: Schema["RevisionInputDTO"]) =>
    route("post", `${R}/{recast_id}/dialogue/retry`, {
      path: recastIds(id),
      body,
    }),
  /** Every transcript segment as a line, with its decision or computed proposal (speaker lanes read it, U6/P8). */
  lines: (id: string) =>
    route("get", `${R}/{recast_id}/lines`, { path: recastIds(id) }),
  choice: (id: string, shot: string, body: Schema["ChoiceInput"]) =>
    route("post", `${R}/{recast_id}/shots/{shot_id}/choice`, {
      path: shotIds(id, shot),
      body,
    }),
  retake: (id: string, shot: string, body: Schema["RetakeInput"]) =>
    route("post", `${R}/{recast_id}/shots/{shot_id}/retake`, {
      path: shotIds(id, shot),
      body,
    }),
  headPass: (id: string, shot: string, body: Schema["HeadPassInput"]) =>
    route("post", `${R}/{recast_id}/shots/{shot_id}/head-pass`, {
      path: shotIds(id, shot),
      body,
    }),
  conform: (id: string, body: Schema["ConformInput"]) =>
    route("post", `${R}/{recast_id}/conform`, { path: recastIds(id), body }),
};

/** Retain request identity across a lost response and reload. */
export async function recastIntent<T>(
  key: string,
  call: (requestKey: string) => Promise<T>,
) {
  const storage = `gaiavfx-recast-intent:${key}`;
  const requestKey = localStorage.getItem(storage) ?? crypto.randomUUID();
  localStorage.setItem(storage, requestKey);
  const result = await call(requestKey);
  localStorage.removeItem(storage);
  return result;
}
