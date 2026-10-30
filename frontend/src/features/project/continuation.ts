import { handoffWords, type Continuation } from "./client";

// A continuation's handoff in words (docs/CONTINUATION.md); its edits are in edits.ts.

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
