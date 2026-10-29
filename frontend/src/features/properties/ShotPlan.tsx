import type { CutPlan, OpeningPlan } from "../project/client";
import { CommandButton } from "../shell/Menus";

type ShotPlan = Pick<
  CutPlan | OpeningPlan,
  "assessment" | "recommended" | "alternatives" | "gaps" | "override"
>;

/** The technique choices a shot's override can name: the recommendation and the alternatives, once each. */
export function techniqueChoices(plan?: ShotPlan) {
  return plan
    ? [
        ...new Map(
          [plan.recommended, ...plan.alternatives]
            .filter((c) => c !== null)
            .map((c) => [c.technique_id, c]),
        ).values(),
      ]
    : [];
}

/** What one shot's plan says: its coverage, its technique, what is still missing and its limitations. A cut and a
 * segment's opening shot read the same way. */
export function ShotPlanSections({ plan }: { plan: ShotPlan }) {
  return (
    <>
      <section className="property-section">
        <h3>Coverage</h3>
        <ul>
          {plan.assessment.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      </section>
      <section className="property-section">
        <h3>{plan.override ? "Chosen technique" : "Recommended technique"}</h3>
        <p>{plan.recommended?.title ?? "No compatible technique"}</p>
        {plan.recommended && (
          <>
            <span className="plan-state">
              {plan.recommended.ready ? "Ready" : "Needs preparation"} ·{" "}
              {plan.recommended.status}
            </span>
            {plan.recommended.keyframe_offset_frames !== null && (
              <p className="muted">
                Pin at cut + {plan.recommended.keyframe_offset_frames} frames.
                Moving this cut keeps the pin anchored.
              </p>
            )}
          </>
        )}
      </section>
      <section className="property-section">
        <h3>Next actions</h3>
        {!plan.recommended?.actions.length && !plan.gaps.length && (
          <p className="muted">No outstanding preparation.</p>
        )}
        <ul>
          {plan.recommended?.actions.map((a, i) => (
            <li key={i}>
              <strong>{a.action.replaceAll("_", " ")}</strong>
              <p>{a.reason}</p>
            </li>
          ))}
          {plan.gaps.map((g) => (
            <li key={g.code}>{g.message}</li>
          ))}
        </ul>
        <CommandButton id="prepare" className="secondary-button" />
      </section>
      {!!plan.recommended?.limitations.length && (
        <section className="property-section">
          <h3>Limitations</h3>
          <ul>
            {plan.recommended.limitations.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
