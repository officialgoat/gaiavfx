import {
  direction,
  handoffWords,
  type Continuation,
  type Cut,
  type Phase,
  type Relation,
  type Schema,
  type Segment,
} from "./client";
import type { DirectController } from "./use-direction";

// Direction edits by kind. Each picks the revision Python checks (the movie's to add or remove a segment, the
// segment's to add or remove a phase or cut, a row's own to change it), builds the payload, words the notice and
// runs through the controller's edit runner. Views name the edit; they never pick a revision.

type Editor = Pick<DirectController, "perform">;

/** A relation choice as Python stores it: unknown clears the relation and its barrier; a known one is authored. */
export function relationPatch(
  relation: Relation,
  crossesBarrier: boolean | null | undefined,
) {
  return relation === "unknown"
    ? {
        relation: null,
        relation_source: "unknown" as const,
        crosses_barrier: null,
      }
    : {
        relation,
        relation_source: "authored" as const,
        crosses_barrier: crossesBarrier ?? false,
      };
}

const changeCut = (
  d: Editor,
  cut: Cut,
  patch: Omit<Schema["CutPatch"], "expected_revision">,
  notice: string,
) =>
  d.perform(
    () =>
      direction.updateCut(cut.id, {
        expected_revision: cut.revision,
        ...patch,
      }),
    notice,
  );

export const moveCut = (d: Editor, cut: Cut, frame: number) =>
  changeCut(d, cut, { frame }, "Cut moved; its shot direction and pin follow");
// The cut menu's relation, barrier and technique choices have always said "Cut updated".
export const setRelation = (d: Editor, cut: Cut, relation: Relation) =>
  changeCut(
    d,
    cut,
    relationPatch(relation, cut.crosses_barrier),
    "Cut updated",
  );
export const setBarrier = (d: Editor, cut: Cut, crosses: boolean) =>
  changeCut(d, cut, { crosses_barrier: crosses }, "Cut updated");
/** Null follows the plan's recommended technique again. */
export const setTechnique = (d: Editor, cut: Cut, technique: string | null) =>
  changeCut(d, cut, { override_technique: technique }, "Cut updated");
export const saveCut = (
  d: Editor,
  cut: Cut,
  patch: Omit<Schema["CutPatch"], "expected_revision">,
) => changeCut(d, cut, patch, "Cut saved");

export const movePhase = (d: Editor, phase: Phase, startFrame: number) =>
  d.perform(
    () =>
      direction.updatePhase(phase.id, {
        expected_revision: phase.revision,
        start_frame: startFrame,
      }),
    "Phase moved",
  );
export const savePhase = (
  d: Editor,
  phase: Phase,
  startFrame: number,
  content: Partial<Phase["content"]>,
) =>
  d.perform(
    () =>
      direction.updatePhase(phase.id, {
        expected_revision: phase.revision,
        start_frame: startFrame,
        content: { ...phase.content, ...content },
      }),
    "Phase saved",
  );

/** Only the fields that change are sent; an unchanged first shot is no edit. */
export function saveOpening(
  d: DirectController,
  segment: Segment,
  angle: string | null,
  technique: string | null,
) {
  const changes = {
    ...(angle !== segment.opening_angle_id ? { opening_angle_id: angle } : {}),
    ...(technique !== segment.opening_technique
      ? { opening_technique: technique }
      : {}),
  };
  if (!Object.keys(changes).length) {
    d.tell("First shot unchanged");
    return Promise.resolve(undefined);
  }
  return d.perform(
    () =>
      direction.updateSegment(segment.id, {
        expected_revision: segment.revision,
        ...changes,
      }),
    "First shot saved",
  );
}

export const savePlace = (
  d: Editor,
  movieId: string,
  place: Schema["PlaceDTO"],
  text: { description: string; sound: string; overhead: string },
) =>
  d.perform(
    () =>
      direction.updatePlace(place.id, {
        movie_id: movieId,
        expected_revision: place.revision,
        ...text,
      }),
    "Place saved",
  );
export const saveAngle = (
  d: Editor,
  movieId: string,
  angle: { id: string; revision: number; name: string; description: string },
) =>
  d.perform(
    () =>
      direction.updateAngle(angle.id, {
        movie_id: movieId,
        expected_revision: angle.revision,
        name: angle.name,
        description: angle.description,
      }),
    "Angle saved",
  );

/** Who is in a segment (its subjects); round 2, R1: a segment made without cast could never be given one. */
export const saveSegmentCast = (d: Editor, segment: Segment, cast: string[]) =>
  d.perform(
    () =>
      direction.updateSegment(segment.id, {
        expected_revision: segment.revision,
        content: { ...segment.content, subjects: cast },
      }),
    "Segment cast saved",
  );

export const removeSegment = (
  d: Editor,
  movie: { revision: number },
  segment: Segment,
) =>
  d.perform(
    () =>
      direction.removeSegment(segment.id, {
        expected_revision: movie.revision,
      }),
    "Segment removed",
  );
export const removePhase = (d: Editor, segment: Segment, phase: Phase) =>
  d.perform(
    () =>
      direction.removePhase(phase.id, { expected_revision: segment.revision }),
    "Phase removed",
  );
export const removeCut = (d: Editor, segment: Segment, cut: Cut) =>
  d.perform(
    () => direction.removeCut(cut.id, { expected_revision: segment.revision }),
    "Cut removed. Undo restores it.",
  );
export const splitSegment = (d: Editor, segment: Segment, frame: number) =>
  d.perform(
    () =>
      direction.splitSegment(segment.id, {
        expected_revision: segment.revision,
        frame,
      }),
    "Segment split",
  );

export const createMovie = (d: Editor, title: string) =>
  d.perform(() => direction.createMovie({ title }), "Movie created");
export const createSegment = (
  d: Editor,
  movie: { id: string; revision: number },
  segment: Omit<Schema["SegmentCreate"], "expected_revision">,
) =>
  d.perform(
    () =>
      direction.createSegment(movie.id, {
        expected_revision: movie.revision,
        ...segment,
      }),
    "Segment created",
  );
export const createPhase = (
  d: Editor,
  segment: Segment,
  phase: Omit<Schema["PhaseCreate"], "expected_revision">,
) =>
  d.perform(
    () =>
      direction.createPhase(segment.id, {
        expected_revision: segment.revision,
        ...phase,
      }),
    "Phase created",
  );
export const createCut = (
  d: Editor,
  segment: Segment,
  cut: Omit<
    Schema["CutCreate"],
    "expected_revision" | "segment_id" | keyof ReturnType<typeof relationPatch>
  > & { relation: Relation; crosses_barrier: boolean },
) =>
  d.perform(
    () =>
      direction.createCut({
        ...cut,
        expected_revision: segment.revision,
        segment_id: segment.id,
        ...relationPatch(cut.relation, cut.crosses_barrier),
      }),
    "Cut created",
  );

// A continuation's handoff (docs/CONTINUATION.md). Python resolves the method, the lengths a join can carry and the
// recommendation, and stores the default length as null; these edits only name a choice, and the API refuses one
// that cannot render, saying why. A choice the join already makes is not sent: it would change nothing.

/** Dragging back to the default length follows the default again. */
export async function setContext(
  d: Editor,
  segment: Segment,
  plan: Continuation,
  frames: number,
) {
  if (frames === plan.context_frames) return undefined;
  return d.perform(
    () =>
      direction.setContinuation(segment.id, {
        expected_revision: segment.revision,
        context_frames: frames,
      }),
    `Continuation context: ${frames} frames`,
  );
}

/** Null follows the research rule again. */
export async function setHandoff(
  d: Editor,
  segment: Segment,
  method: "paired" | "masked" | null,
) {
  if (method === segment.continuation_method) return undefined;
  return d.perform(
    () =>
      direction.setContinuation(segment.id, {
        expected_revision: segment.revision,
        method,
      }),
    method ? `Handoff: ${handoffWords(method)}` : "Handoff follows the rule",
  );
}

/** A Properties save: only the fields that change are sent. */
export function saveContinuation(
  d: Editor,
  segment: Segment,
  changes: Omit<Schema["SegmentContinuationPatch"], "expected_revision">,
) {
  if (!Object.keys(changes).length) return Promise.resolve(undefined);
  return d.perform(
    () =>
      direction.setContinuation(segment.id, {
        expected_revision: segment.revision,
        ...changes,
      }),
    "Continuation saved",
  );
}
