import { useEffect, useRef } from "react";
import { Box } from "lucide-react";
import { CommandButton, ContextArea } from "../shell/Menus";
import type { DirectController } from "../project/use-direction";
import type { ChatController } from "./use-chat";
import { Markdown } from "./Markdown";
import { PlanCard, ReceiptLinks } from "./PlanCard";
import { Attachment } from "./ChatMedia";
import { timelineTarget } from "./commands";
import { PeopleReceipt } from "./PeopleReceipt";
import { SetReceipt } from "../sets/SetReceipt";

export const object = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
export const string = (value: unknown) =>
  typeof value === "string" ? value : "";

function PeopleGaps({ gaps }: { gaps: unknown }) {
  const wording: Record<string, string> = {
    choose_performer: "Choose by number.",
    presence_unresolved:
      "Presence is unresolved; choose by the numbered crops.",
    performer_absent:
      "Presence is ambiguous: this person is absent from the shot.",
  };
  const messages = Array.isArray(gaps)
    ? [
        ...new Set(
          gaps.map((gap) => wording[string(object(gap).code)]).filter(Boolean),
        ),
      ]
    : [];
  return (
    <>
      {messages.map((message) => (
        <p key={message} className="muted">
          {message}
        </p>
      ))}
    </>
  );
}
// Only authored edit inputs are displayable; production/candidate inputs and all
// result maps stay out of the DOM, clipboard and context-menu targets. This remains
// conservative after ranking until Review supplies an explicit revealed projection.
export function safeInput(tool: string, input: Record<string, unknown>) {
  if (!/^(edit_|create_|define_|set_|plan_step$)/.test(tool)) return {};
  const redact = (value: unknown, depth = 0): unknown => {
    if (depth > 8) return "…";
    if (Array.isArray(value)) return value.map((v) => redact(v, depth + 1));
    if (value && typeof value === "object")
      return Object.fromEntries(
        Object.entries(value)
          .filter(
            ([key]) =>
              !/candidate|operation|seed|technique|asset|provenance|result|request|setting|binding/i.test(
                key,
              ),
          )
          .map(([key, val]) => [key, redact(val, depth + 1)]),
      );
    return value;
  };
  return redact(input) as Record<string, unknown>;
}

export function Transcript({
  chat,
  d,
  debug,
}: {
  chat: ChatController;
  d: DirectController;
  debug: boolean;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  useEffect(() => {
    if (follow.current && scroller.current)
      scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [chat.turns, chat.session?.revision]);
  const plan = chat.session?.plan;
  const history = chat.session?.plan_history ?? [];
  return (
    <div
      className="chat-transcript"
      ref={scroller}
      onScroll={(e) => {
        const el = e.currentTarget;
        follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
      }}
    >
      {!chat.turns.length &&
        (chat.scope.kind === "film" ? (
          <div className="chat-welcome">
            <h2>Ask about this film's cast, or plan a recast run.</h2>
            <p>
              The agent proposes pictures and plans; you confirm casts, approve
              every run, and choose the versions.
            </p>
          </div>
        ) : (
          <div className="chat-welcome">
            <h2>Shape the next shot.</h2>
            <p>
              Discuss your movie, attach references, and review a plan before it
              runs.
            </p>
          </div>
        ))}
      {chat.turns
        .filter(
          (t) => t.state === "accepted" || (debug && t.state === "rejected"),
        )
        .map((turn, index, turns) => {
          const action = object(turn.action),
            input = object(action.input),
            tool = string(action.tool);
          const payload = object(object(turn.result).payload);
          const peopleId =
            string(payload.id) ||
            string(
              turn.receipts?.find((r) => r.kind === "people_analysis")?.id,
            );
          if (turn.state === "rejected")
            return (
              <details key={turn.id} className="chat-tool">
                <summary>Rejected · {tool || "turn"}</summary>
                <p>The agent action was not accepted.</p>
              </details>
            );
          if (
            peopleId &&
            (Array.isArray(payload.tracks) ||
              turn.receipts?.some((r) => r.kind === "people_analysis"))
          ) {
            const next = turns
              .slice(index + 1)
              .find((t) => t.state === "accepted");
            const nextPayload = object(object(next?.result).payload);
            if (
              object(next?.action).tool === "ask_human" &&
              Array.isArray(nextPayload.analyses) &&
              nextPayload.analyses.includes(peopleId)
            )
              return null;
            return (
              <article
                key={turn.id}
                className="chat-message chat-assistant"
                tabIndex={0}
              >
                <PeopleReceipt id={peopleId} chat={chat} />
                <PeopleGaps gaps={payload.gaps} />
              </article>
            );
          }
          if (turn.role === "system") {
            // A stage build's turn carries the user's words as its notice; the agent's are its message.
            const built = turn.receipts?.find((r) => r.kind === "set_build");
            return (
              <div key={turn.id} className="chat-system">
                <p>
                  {(string(action.notice) || string(action.message)).replace(
                    /^The user /,
                    "You ",
                  ) || "Session updated"}
                </p>
                {built && (
                  <SetReceipt setId={string(built.set_id)} kind="set_build" />
                )}
              </div>
            );
          }
          if (turn.role === "human")
            return (
              <ContextArea
                key={turn.id}
                target={{ kind: "chat", text: string(action.message) }}
              >
                <article className="chat-message chat-human" tabIndex={0}>
                  <strong>You</strong>
                  <p>{string(action.message)}</p>
                  <div className="chat-attachments">
                    {(Array.isArray(action.attachments)
                      ? action.attachments
                      : []
                    ).map((a, i) => (
                      <Attachment key={i} id={string(object(a).asset_id)} />
                    ))}
                  </div>
                </article>
              </ContextArea>
            );
          if (tool === "propose_plan") {
            // Earlier proposal bodies appear once, in Revised plans, below the transcript.
            if (plan?.proposed_turn !== turn.ordinal) return null;
            return (
              <div key={turn.id} className="chat-message chat-assistant">
                <strong>Gaia</strong>
                <PlanCard
                  key={`${plan.id}-${plan.revision}`}
                  plan={plan}
                  d={d}
                  chat={chat}
                />
              </div>
            );
          }
          if (tool === "say" || tool === "ask_human") {
            const text = string(input.text) || string(input.question);
            return (
              <ContextArea key={turn.id} target={{ kind: "chat", text }}>
                <article className="chat-message chat-assistant" tabIndex={0}>
                  <strong>Gaia</strong>
                  <Markdown text={text} />
                  <PeopleGaps gaps={payload.gaps} />
                  {tool === "ask_human" &&
                    Array.isArray(payload.analyses) &&
                    payload.analyses.map((id) =>
                      typeof id === "string" ? (
                        <PeopleReceipt key={id} id={id} chat={chat} />
                      ) : null,
                    )}
                  {Array.isArray(input.options) && (
                    <ul>
                      {input.options.map((option, i) => (
                        <li key={i}>{string(option)}</li>
                      ))}
                    </ul>
                  )}
                </article>
              </ContextArea>
            );
          }
          if (tool === "build_set_from_take" || tool === "fill_set_more")
            // The build's start in Python's words; its progress shows on the plan.
            return (
              <article
                key={turn.id}
                className="chat-message chat-assistant"
                tabIndex={0}
              >
                <div className="stage-build" data-state="started">
                  <Box size={14} aria-hidden />
                  <span className="stage-build-text">
                    {string(payload.message) || "Building a stage"}
                  </span>
                </div>
              </article>
            );
          const setReceipt = (turn.receipts ?? []).find((r) =>
            ["set_read", "set_issue", "set_view", "set_link"].includes(
              string(r.kind),
            ),
          );
          if (setReceipt)
            return (
              <article
                key={turn.id}
                className="chat-message chat-assistant"
                tabIndex={0}
              >
                <SetReceipt
                  setId={string(setReceipt.set_id)}
                  kind={string(setReceipt.kind)}
                  issueId={string(setReceipt.issue_id) || undefined}
                  viewId={string(setReceipt.view_id) || undefined}
                  words={string(payload.message) || undefined}
                />
                {/* Using a stage edits the scene: its receipt keeps Undo. */}
                <ReceiptLinks receipts={turn.receipts ?? []} d={d} />
              </article>
            );
          const receipts = turn.receipts ?? [];
          const editReceipts = receipts.filter(
            (r) => r.kind === "direction_receipt",
          );
          const targetId = [
            input.cut_id,
            input.segment_id,
            input.phase_id,
            input.target,
            editReceipts[0]?.target_id,
          ].find((id) => timelineTarget(d, string(id)));
          const link = timelineTarget(d, string(targetId));
          const summary = [
            tool.replaceAll("_", " "),
            link?.label,
            editReceipts.length
              ? `${editReceipts.length} receipts`
              : receipts.some((r) => r.kind === "operation")
                ? "Operation"
                : "",
          ]
            .filter(Boolean)
            .join(" · ");
          const details = safeInput(tool, input);
          return (
            <ContextArea
              key={turn.id}
              target={{
                kind: "chat",
                id: string(targetId),
                receiptId: string(editReceipts[0]?.id),
                reviewId: string(
                  receipts.find((r) => r.kind === "review_set")?.id,
                ),
                text: summary,
              }}
            >
              <details className="chat-tool">
                <summary>{summary}</summary>
                {Object.keys(details).length > 0 ? (
                  <pre>{JSON.stringify(details, null, 2)}</pre>
                ) : (
                  <p>Action recorded. Candidate provenance stays hidden.</p>
                )}
                {link && (
                  <CommandButton
                    id="chat.select"
                    target={{ kind: "chat", id: string(targetId) }}
                  >
                    {link.label}
                  </CommandButton>
                )}
                <ReceiptLinks receipts={receipts} d={d} />
                {receipts.some((r) => r.kind === "review_set") && (
                  <CommandButton
                    id="chat.review"
                    target={{
                      kind: "chat",
                      reviewId: string(
                        receipts.find((r) => r.kind === "review_set")?.id,
                      ),
                    }}
                  />
                )}
              </details>
            </ContextArea>
          );
        })}
      {plan &&
        !chat.turns.some(
          (t) => t.ordinal === plan.proposed_turn && t.state === "accepted",
        ) && <PlanCard plan={plan} d={d} chat={chat} />}
      {!!history.length && (
        <details className="chat-plan-history">
          <summary>Revised plans ({history.length})</summary>
          {history.map((p, i) => (
            <PlanCard key={i} plan={p} d={d} chat={chat} archived />
          ))}
        </details>
      )}
      <p className="chat-activity" role="status">
        {chat.session
          ? chat.session.activity === "building_stage" && chat.session.build
            ? chat.session.build.label
            : chat.session.activity.replaceAll("_", " ")
          : chat.loading
            ? "Opening conversation…"
            : "Ready when you are"}
      </p>
      {chat.session?.stop_reason && (
        <div className="chat-stop-reason">
          <p>{chat.session.stop_reason}</p>
          {chat.session.state === "failed" && !chat.readOnly && (
            <CommandButton id="agent.new">
              Start a new conversation
            </CommandButton>
          )}
        </div>
      )}
    </div>
  );
}
