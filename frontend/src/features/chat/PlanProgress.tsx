import type { Plan } from "./client";
import type { DirectController } from "../project/use-direction";
import { CommandButton } from "../shell/Menus";
import { timelineTarget } from "./commands";
import { StageBuild, type StageBuildState } from "./StageBuild";

/** Compact approved-plan projection. The full plan remains available in the
 * conversation and in the default layout, including questions and receipts. */
export function PlanProgress({
  plan,
  d,
  build,
}: {
  plan: Plan;
  d: DirectController;
  build?: StageBuildState | null;
}) {
  return (
    <section className="chat-progress" aria-label="Plan progress">
      <strong className="chat-progress-goal">{plan.goal}</strong>
      <ol>
        {plan.steps.map((step, index) => {
          const link = timelineTarget(d, step.target);
          return (
            <li key={index}>
              <span className={`step-status step-${step.status}`}>
                {
                  {
                    pending: "Pending",
                    done: "Done",
                    skipped: "Skipped",
                    awaiting_user: "Awaiting you",
                  }[step.status]
                }
              </span>
              <span>{step.title}</span>
              {link && (
                <CommandButton
                  id="chat.select"
                  target={{ kind: "chat", id: step.target }}
                >
                  {link.label}
                </CommandButton>
              )}
              {step.kind === "build" && build?.step === index ? (
                <StageBuild build={build} />
              ) : (
                step.status === "awaiting_user" &&
                (step.review_of?.startsWith("recast:") ? (
                  <span>Awaiting your review in the film's review queue</span>
                ) : (
                  <CommandButton id="chat.review">
                    Awaiting your review
                  </CommandButton>
                ))
              )}
            </li>
          );
        })}
      </ol>
      <div className="chat-progress-budget">
        <strong>
          {plan.budget_jobs} {plan.budget_jobs === 1 ? "job" : "jobs"}
        </strong>
        <span>{plan.state}</span>
        {plan.state === "executing" && <CommandButton id="agent.stop-plan" />}
        <CommandButton id="layout.default">Plan details</CommandButton>
      </div>
    </section>
  );
}
