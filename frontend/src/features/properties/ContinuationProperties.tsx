import type { FormEvent } from "react";
import type { DirectController } from "../project/use-direction";
import {
  direction,
  handoffWords,
  type Continuation,
  type Segment,
} from "../project/client";
import { audioWords, ruleWords } from "../project/continuation";

/** How a continuing segment hands over its predecessor's motion (docs/CONTINUATION.md): the method, the context
 * length, and the research rule's recommendation with its reason. Python decides what qualifies. */
export function ContinuationProperties({
  d,
  segment,
  plan,
}: {
  d: DirectController;
  segment: Segment;
  plan: Continuation;
}) {
  const follows = plan.method === plan.recommended;
  const lengths = plan.context_choices;
  const explicit = segment.continuation_method;
  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    d.clearError();
    const form = new FormData(event.currentTarget);
    const method = (String(form.get("method")) || null) as
      "paired" | "masked" | null;
    const typed = String(form.get("context"));
    // The default length is stored as null: typing it, or clearing the field, follows the default again.
    const context =
      typed === "" || Number(typed) === plan.default_context
        ? null
        : Number(typed);
    const changes = {
      ...(method !== explicit ? { method } : {}),
      ...(context !== segment.context_frames
        ? { context_frames: context }
        : {}),
    };
    if (!Object.keys(changes).length) return;
    void d.perform(
      () =>
        direction.setContinuation(segment.id, {
          expected_revision: segment.revision,
          ...changes,
        }),
      "Continuation saved",
    );
  }
  return (
    <section className="property-section" aria-label="Continuation">
      <div className="inspector-title">
        <h3>Continuation</h3>
        <span className="rule-chip" data-follows={follows}>
          {ruleWords(plan)}
        </span>
      </div>
      <dl>
        <dt>Handoff</dt>
        <dd>
          {handoffWords(plan.method)}
          {plan.explicit_method === null && " · automatic"}
        </dd>
        <dt>Context</dt>
        <dd>
          {plan.context_frames} frames
          {plan.explicit_context === null && " · default"}
        </dd>
        <dt>Audio</dt>
        <dd>{audioWords(plan)}</dd>
      </dl>
      <p className="muted">{plan.reason}</p>
      <form
        key={`${segment.id}-${segment.revision}`}
        className="property-form"
        aria-label="Edit the continuation"
        onSubmit={save}
      >
        <label>
          Handoff method
          <select name="method" defaultValue={explicit ?? ""}>
            <option value="">
              Automatic · {handoffWords(plan.recommended)}
            </option>
            <option value="paired">{handoffWords("paired")}</option>
            <option
              value="masked"
              disabled={!!plan.masked_problem && explicit !== "masked"}
            >
              {handoffWords("masked")}
              {plan.masked_problem ? " · unavailable" : ""}
            </option>
          </select>
        </label>
        {plan.masked_problem && <p className="muted">{plan.masked_problem}</p>}
        <label>
          Context length (frames)
          <input
            name="context"
            type="number"
            inputMode="numeric"
            min={lengths[0]}
            max={lengths.at(-1)}
            step={lengths.length > 1 ? lengths[1] - lengths[0] : undefined}
            placeholder={`Default · ${plan.default_context}`}
            defaultValue={plan.explicit_context ?? ""}
            aria-describedby={`context-lengths-${segment.id}`}
          />
        </label>
        <p className="muted" id={`context-lengths-${segment.id}`}>
          This join can carry {lengths.join(", ")} frames of the predecessor's
          end. Drag the band's leading edge on the Continuation track to choose
          as well.
        </p>
        {d.error && <p role="alert">{d.error}</p>}
        <button className="secondary-button" disabled={d.busy}>
          Save continuation
        </button>
      </form>
    </section>
  );
}
