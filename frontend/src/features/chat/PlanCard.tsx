import { useEffect, useState } from "react";
import { CommandButton, ContextArea, useCommands } from "../shell/Menus";
import type { DirectController } from "../project/use-direction";
import type { Plan } from "./client";
import type { ChatController } from "./use-chat";
import { timelineTarget } from "./commands";
import { ReplacementPlanPreview } from "./ReplacementPlanPreview";
import { StageBuild } from "./StageBuild";

export function ReceiptLinks({
  receipts,
  d,
}: {
  receipts: Record<string, unknown>[];
  d: DirectController;
}) {
  return (
    <div className="chat-receipts">
      {receipts
        .filter((r) => r.kind === "direction_receipt")
        .map((r, i) => {
          const id = typeof r.id === "string" ? r.id : "";
          const targetId =
            typeof r.target_id === "string" ? r.target_id : undefined;
          const label = timelineTarget(d, targetId)?.label ?? "Change";
          return (
            <ContextArea
              key={id || i}
              target={{
                kind: "chat",
                id: targetId,
                receiptId: id,
                text: label,
              }}
            >
              <span className="chat-receipt">
                <CommandButton
                  id="chat.select"
                  target={{ kind: "chat", id: targetId }}
                >
                  {label} · receipt {i + 1}
                </CommandButton>
                <CommandButton
                  id="chat.undo"
                  target={{ kind: "chat", receiptId: id }}
                />
              </span>
            </ContextArea>
          );
        })}
    </div>
  );
}

export function PlanCard({
  plan,
  d,
  chat,
  archived = false,
}: {
  plan: Plan;
  d: DirectController;
  chat: ChatController;
  archived?: boolean;
}) {
  const [action, setAction] = useState<"revise" | "reject" | null>(null);
  const [note, setNote] = useState("");
  const { commands } = useCommands();
  useEffect(() => {
    if (archived) return;
    const open = (e: Event) => {
      setAction((e as CustomEvent).detail);
      setNote("");
    };
    window.addEventListener("gaiavfx-plan-note", open);
    return () => window.removeEventListener("gaiavfx-plan-note", open);
  }, [archived]);
  const proposed =
    !archived && plan.state === "proposed" && !plan.revision_requested;
  const build = chat.session?.build;
  const target = { kind: "chat-plan" as const, text: plan.goal };
  return (
    <ContextArea target={archived ? { kind: "chat", text: plan.goal } : target}>
      <section
        className="chat-plan"
        aria-label={`Plan revision ${plan.revision}`}
        tabIndex={0}
      >
        <div className="plan-heading">
          <strong>{plan.goal}</strong>
          <span>rev {plan.revision}</span>
        </div>
        <ol className="plan-steps">
          {plan.steps.map((step, i) => {
            const link = timelineTarget(d, step.target);
            const replacement = step.receipts?.find(
              (receipt) =>
                receipt.kind === "operation" &&
                receipt.operation_kind === "character_replacement",
            );
            return (
              <li key={i}>
                <div className="plan-step-heading">
                  <strong>{step.title}</strong>
                  <span className={`step-status step-${step.status}`}>
                    {
                      // A build step whose build runs says so; the live trial's read "Pending" for 14 minutes.
                      !archived &&
                      step.status === "pending" &&
                      build?.step === i &&
                      build.state === "building"
                        ? "Building"
                        : {
                            pending: "Pending",
                            done: "Done",
                            skipped: "Skipped",
                            awaiting_user: "Awaiting you",
                          }[step.status]
                    }
                  </span>
                </div>
                <div className="plan-step-meta">
                  <span>{step.kind}</span>
                  {link && (
                    <CommandButton
                      id="chat.select"
                      target={{ kind: "chat", id: step.target }}
                    >
                      {link.label}
                    </CommandButton>
                  )}
                  <span>
                    {step.jobs} {step.jobs === 1 ? "job" : "jobs"}
                  </span>
                </div>
                <ReceiptLinks receipts={step.receipts ?? []} d={d} />
                {step.kind === "render" &&
                  (replacement ||
                    chat.turns.some((turn) => {
                      const payload = turn.result?.payload as
                        Record<string, unknown> | undefined;
                      return (
                        turn.receipts?.some(
                          (receipt) =>
                            receipt.kind === "people_analysis" &&
                            receipt.id === step.target,
                        ) ||
                        (Array.isArray(payload?.analyses) &&
                          payload.analyses.includes(step.target))
                      );
                    })) && (
                    <ReplacementPlanPreview
                      operation={
                        typeof replacement?.id === "string"
                          ? replacement.id
                          : undefined
                      }
                      renders={
                        typeof replacement?.budget_reserved === "number"
                          ? replacement.budget_reserved
                          : step.jobs
                      }
                    />
                  )}
                {step.kind === "build" &&
                  (!archived && build?.step === i ? (
                    <StageBuild build={build} />
                  ) : (
                    <div className="plan-review">
                      {step.status === "pending"
                        ? // A step on a scene builds a new stage from its take; one on a stage fills more of it.
                          link
                          ? "Builds a new stage from this scene's take; you decide its first version"
                          : "Fills more of a stage you accepted; you decide its new version"
                        : step.status === "done"
                          ? "Built"
                          : step.status === "skipped"
                            ? "Not built"
                            : "Awaiting your decision in the stage workbench"}
                    </div>
                  ))}
                {step.kind === "review" &&
                  (step.review_of?.startsWith("recast:") ? (
                    // A recast run's results wait in the film's own review queue, not in Review.
                    <div className="plan-review">
                      {step.status === "awaiting_user"
                        ? "Awaiting your review in the film's review queue"
                        : step.status === "done"
                          ? "Reviewed"
                          : "Human review stop"}
                    </div>
                  ) : (
                    <div className="plan-review">
                      {step.status === "awaiting_user"
                        ? "Awaiting your review"
                        : step.status === "done"
                          ? "Reviewed"
                          : "Human review stop"}
                      <CommandButton
                        id="chat.review"
                        target={{
                          kind: "chat",
                          reviewOperationId: step.review_of,
                        }}
                      />
                    </div>
                  ))}
              </li>
            );
          })}
        </ol>
        {!!plan.questions?.length && (
          <div className="plan-questions">
            <strong>Questions</strong>
            <ul>
              {plan.questions.map((q, i) => (
                <li key={i}>{q}</li>
              ))}
            </ul>
          </div>
        )}
        <div className="plan-budget">
          <strong>
            {plan.budget_jobs} {plan.budget_jobs === 1 ? "job" : "jobs"}
          </strong>
          <span>
            {plan.revision_requested
              ? "Revision requested"
              : plan.state === "proposed"
                ? "Awaiting approval"
                : plan.state}
          </span>
        </div>
        {!archived && plan.state === "executing" && !chat.readOnly && (
          <div className="plan-actions">
            <CommandButton id="agent.stop-plan" />
          </div>
        )}
        {proposed && !chat.readOnly && (
          <div className="plan-actions">
            <CommandButton
              id="chat.approve"
              target={target}
              className="primary-button"
            />
            <CommandButton id="chat.revise" target={target} />
            <CommandButton id="chat.reject" target={target} />
          </div>
        )}
        {proposed && action && (
          <form
            className="plan-note"
            onSubmit={(e) => {
              e.preventDefault();
              const command = commands.get(`chat.${action}`);
              if (!command?.reason?.({ ...target, note }))
                command?.run({ ...target, note });
            }}
          >
            <label>
              {action === "revise"
                ? "Revision note"
                : "Rejection note (optional)"}
              <textarea
                autoFocus
                maxLength={1000}
                required={action === "revise"}
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
            </label>
            <CommandButton id={`chat.${action}`} target={{ ...target, note }}>
              {action === "revise" ? "Send revision" : "Confirm rejection"}
            </CommandButton>
            <button type="button" onClick={() => setAction(null)}>
              Cancel
            </button>
          </form>
        )}
      </section>
    </ContextArea>
  );
}
