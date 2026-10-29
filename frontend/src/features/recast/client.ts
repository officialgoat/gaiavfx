import type { components } from "../../shared/api.generated";
import { request } from "../../shared/api";

export type Schema = components["schemas"];
export type Recast = Schema["RecastDTO"];
export type Shot = Schema["RecastShotDTO"];
export type Character = Schema["CharacterDTO"];
export type Voice = Schema["VoiceDTO"];
const root = "/api/recasts";
const json = (method: string, body: unknown) => ({
  method,
  body: JSON.stringify(body),
});
const path = (id: string, suffix = "") =>
  `${root}/${encodeURIComponent(id)}${suffix}`;
/** A character's voice routes (ADR 0016 §1, §4; P5): every one is a human command. */
const voice = (id: string, character: string, suffix = "") =>
  path(id, `/characters/${encodeURIComponent(character)}/voice${suffix}`);
export const recasts = {
  list: () => request<Schema["RecastSummaryDTO"][]>(root),
  get: (id: string) => request<Recast>(path(id)),
  create: (body: Schema["CreateRecastInput"]) =>
    request<Recast>(root, json("POST", body)),
  character: (
    id: string,
    character: string,
    body: Schema["CharacterPatchInput"],
  ) =>
    request<Recast>(
      path(id, `/characters/${encodeURIComponent(character)}`),
      json("PATCH", body),
    ),
  target: (id: string, character: string, body: Schema["TargetInput"]) =>
    request<Recast>(
      path(id, `/characters/${encodeURIComponent(character)}/target`),
      json("PUT", body),
    ),
  confirm: (id: string, character: string, body: Schema["RevisionInputDTO"]) =>
    request<Recast>(
      path(id, `/characters/${encodeURIComponent(character)}/target/confirm`),
      json("POST", body),
    ),
  approveReference: (
    id: string,
    character: string,
    body: Schema["RevisionInputDTO"],
  ) =>
    request<Recast>(
      path(
        id,
        `/characters/${encodeURIComponent(character)}/reference/approve`,
      ),
      json("POST", body),
    ),
  rejectReference: (
    id: string,
    character: string,
    body: Schema["RevisionInputDTO"],
  ) =>
    request<Recast>(
      path(id, `/characters/${encodeURIComponent(character)}/reference/reject`),
      json("POST", body),
    ),
  clear: (id: string, character: string, revision: number) =>
    request<Recast>(
      path(
        id,
        `/characters/${encodeURIComponent(character)}/target?expected_revision=${revision}`,
      ),
      { method: "DELETE" },
    ),
  /** The character's voice recordings, in order: confirmed at once and journaled; building asks separately. */
  setVoice: (
    id: string,
    character: string,
    body: Schema["VoiceSamplesInput"],
  ) => request<Recast>(voice(id, character), json("PUT", body)),
  /** Removes the recordings, or rejects proposed ones (journaled). */
  clearVoice: (id: string, character: string, revision: number) =>
    request<Recast>(voice(id, character, `?expected_revision=${revision}`), {
      method: "DELETE",
    }),
  confirmVoice: (
    id: string,
    character: string,
    body: Schema["RevisionInputDTO"],
  ) => request<Recast>(voice(id, character, "/confirm"), json("POST", body)),
  /** Starts the voice build (GPU hours): only after the Build voice sheet asked. */
  buildVoice: (
    id: string,
    character: string,
    body: Schema["BuildVoiceInput"],
  ) => request<Recast>(voice(id, character, "/build"), json("POST", body)),
  /** Resumes a stopped build, starts the next attempt of a failed one, or auditions again. */
  retryVoice: (
    id: string,
    character: string,
    body: Schema["RevisionInputDTO"],
  ) => request<Recast>(voice(id, character, "/retry"), json("POST", body)),
  approveVoice: (
    id: string,
    character: string,
    body: Schema["RevisionInputDTO"],
  ) => request<Recast>(voice(id, character, "/approve"), json("POST", body)),
  rejectVoice: (
    id: string,
    character: string,
    body: Schema["RevisionInputDTO"],
  ) => request<Recast>(voice(id, character, "/reject"), json("POST", body)),
  // Appearance links (ADR 0012 Amendment 6): a window track of a shot linked to a character, decided by a human.
  appearance: (
    id: string,
    shot: string,
    link: { analysis_operation_id: string; track_id: string },
    action: "propose" | "confirm" | "reject",
    body: Schema["AppearanceDecisionInput"] | Schema["AppearanceProposalInput"],
  ) =>
    request<Recast>(
      path(
        id,
        `/shots/${encodeURIComponent(shot)}/appearances/${encodeURIComponent(link.analysis_operation_id)}/${encodeURIComponent(link.track_id)}/${action}`,
      ),
      json("POST", body),
    ),
  clearAppearance: (
    id: string,
    shot: string,
    link: { analysis_operation_id: string; track_id: string },
    revision: number,
  ) =>
    request<Recast>(
      path(
        id,
        `/shots/${encodeURIComponent(shot)}/appearances/${encodeURIComponent(link.analysis_operation_id)}/${encodeURIComponent(link.track_id)}?expected_revision=${revision}`,
      ),
      { method: "DELETE" },
    ),
  /** What a run over a range would cost in a mode, before it starts. */
  estimate: (
    id: string,
    mode: "draft" | "final",
    range: Schema["FrameRangeDTO"] | null,
  ) =>
    request<Schema["EstimateDTO"]>(
      path(
        id,
        `/estimate?${new URLSearchParams({
          mode,
          ...(range
            ? { start: String(range.start), end: String(range.end) }
            : {}),
        })}`,
      ),
    ),
  suggestAppearances: (
    id: string,
    shot: string,
    body: Schema["RevisionInputDTO"],
  ) =>
    request<Recast>(
      path(id, `/shots/${encodeURIComponent(shot)}/appearances/suggest`),
      json("POST", body),
    ),
  merge: (id: string, body: Schema["MergeInput"]) =>
    request<Recast>(path(id, "/characters/merge"), json("POST", body)),
  /** Sets pairs aside as "not the same person"; stored in the recast (ADR 0017, CP3). */
  notSame: (id: string, body: Schema["NotSameInput"]) =>
    request<Recast>(path(id, "/not-same"), json("POST", body)),
  move: (id: string, body: Schema["MoveTracksInput"]) =>
    request<Recast>(path(id, "/tracks/move"), json("POST", body)),
  /** Undoes a stored cast edit (ADR 0012 Amendment 7 §1); redo is the undo of its inverse entry. */
  undoEdit: (id: string, editId: string, revision: number) =>
    request<Recast>(
      path(id, `/edits/${encodeURIComponent(editId)}/undo`),
      json("POST", { expected_revision: revision }),
    ),
  shot: (id: string, shot: string, body: Schema["ShotPatchInput"]) =>
    request<Recast>(
      path(id, `/shots/${encodeURIComponent(shot)}`),
      json("PATCH", body),
    ),
  plan: (id: string, body: Schema["PlanInput"]) =>
    request<Recast>(path(id, "/plan"), json("POST", body)),
  run: (id: string, body: Schema["RunInput"]) =>
    request<Recast>(path(id, "/run"), json("POST", body)),
  control: (
    id: string,
    action: "pause" | "cancel",
    body: Schema["RevisionInputDTO"],
  ) => request<Recast>(path(id, `/run/${action}`), json("POST", body)),
  retryAnalysis: (id: string, body: Schema["RevisionInputDTO"]) =>
    request<Recast>(path(id, "/analysis/retry"), json("POST", body)),
  /** Starts the film's shared dialogue analysis (ADR 0016 §2): human only, after the Voice step asked. */
  startDialogue: (id: string, body: Schema["RevisionInputDTO"]) =>
    request<Recast>(path(id, "/dialogue"), json("POST", body)),
  /** A new attempt after the dialogue analysis failed or was cancelled. */
  retryDialogue: (id: string, body: Schema["RevisionInputDTO"]) =>
    request<Recast>(path(id, "/dialogue/retry"), json("POST", body)),
  /** Every transcript segment as a line, with its decision or computed proposal (speaker lanes read it, U6/P8). */
  lines: (id: string) => request<Schema["LinesDTO"]>(path(id, "/lines")),
  choice: (id: string, shot: string, body: Schema["ChoiceInput"]) =>
    request<Recast>(
      path(id, `/shots/${encodeURIComponent(shot)}/choice`),
      json("POST", body),
    ),
  retake: (id: string, shot: string, body: Schema["RetakeInput"]) =>
    request<Recast>(
      path(id, `/shots/${encodeURIComponent(shot)}/retake`),
      json("POST", body),
    ),
  headPass: (id: string, shot: string, body: Schema["HeadPassInput"]) =>
    request<Recast>(
      path(id, `/shots/${encodeURIComponent(shot)}/head-pass`),
      json("POST", body),
    ),
  conform: (id: string, body: Schema["ConformInput"]) =>
    request<Schema["OperationDTO"]>(path(id, "/conform"), json("POST", body)),
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
