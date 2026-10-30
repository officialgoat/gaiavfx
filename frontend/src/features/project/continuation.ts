import {
  direction,
  handoffWords,
  type Continuation,
  type Segment,
} from "./client";
import type { DirectController } from "./use-direction";

// A continuation's handoff (docs/CONTINUATION.md). Python resolves the method, the lengths a join can carry and the
// recommendation, and stores the default length as null; these edits only name a choice, and the API refuses one
// that cannot render, saying why. A choice the join already makes is not sent: it would change nothing.

/** Dragging back to the default length follows the default again. */
export async function setContext(
  d: DirectController,
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
  d: DirectController,
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

/** Whether the join follows the recommendation, in words beside its colour. */
export function ruleWords(plan: Continuation, short = false) {
  return plan.method === plan.recommended
    ? "Recommended"
    : `${handoffWords(plan.recommended, short)} recommended`;
}

export function audioWords(plan: Continuation) {
  return plan.audio_follows_picture
    ? `Audio history follows the picture: ${plan.audio_context_frames} frames`
    : `Audio history keeps its own ${plan.audio_context_frames}-frame window`;
}
