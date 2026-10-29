import type { Command } from "../shell/commands";
import type { Target } from "../timeline/editor-state";
import type { DirectController } from "../project/use-direction";
import { FILM_PLANS, type ChatController } from "./use-chat";
import { direction } from "../project/client";

export function timelineTarget(
  d: DirectController,
  id?: string,
): { target: Target; frame: number; label: string } | undefined {
  for (const s of d.segments) {
    const start = d.starts.get(s.id) ?? 0;
    if (s.id === id)
      return {
        target: { kind: "segment", id, segmentId: id },
        frame: start,
        label: `Segment ${String(s.ordinal + 1).padStart(2, "0")}`,
      };
    const cut = s.cuts.find((c) => c.id === id);
    if (cut)
      return {
        target: { kind: "cut", id: cut.id, segmentId: s.id },
        frame: start + cut.frame,
        label: `Cut f${cut.frame}`,
      };
    const phase = s.phases.find((p) => p.id === id);
    if (phase)
      return {
        target: { kind: "phase", id: phase.id, segmentId: s.id },
        frame: start + phase.start_frame,
        label: `Phase f${phase.start_frame}`,
      };
  }
}

export function chatCommands(
  d: DirectController,
  chat: ChatController,
  focus: (composer?: boolean) => void,
): Command[] {
  const film = chat.scope.kind === "film";
  const idle = () =>
    film && !chat.ready
      ? "Opening the film…"
      : !film && !d.movie.data
        ? "Open a movie first."
        : chat.loading
          ? "Opening the conversation…"
          : chat.busy
            ? "Saving the conversation…"
            : chat.readOnly
              ? "Earlier conversations are read-only. Return to the current conversation."
              : undefined;
  const plan = () =>
    idle() ??
    (chat.running
      ? "Stop the current turn first."
      : chat.session?.plan?.state !== "proposed" ||
          chat.session.plan.revision_requested
        ? "There is no plan awaiting approval."
        : undefined);
  const receipt = (t: Target) =>
    d.receipts.data?.find((r) => "receiptId" in t && r.id === t.receiptId);
  const knownReceipt = (t: Target) =>
    "receiptId" in t &&
    chat.turns.some((turn) =>
      turn.receipts.some(
        (r) => r.kind === "direction_receipt" && r.id === t.receiptId,
      ),
    );
  return [
    {
      id: "chat.send",
      label: "Send message",
      reason: (t) =>
        idle() ??
        (chat.running
          ? "The agent is still working."
          : !("text" in t && t.text?.trim())
            ? "Write a message first."
            : undefined),
      run: (t) => {
        if ("text" in t && t.text)
          return chat.send(t.text, t.attachments ?? []);
      },
    },
    { id: "chat", label: "Focus chat", run: () => focus() },
    { id: "agent.ask", label: "Ask about this cut…", run: () => focus() },
    {
      id: "agent.new",
      label: "New conversation",
      run: () => {
        void chat.newConversation().then((ok) => {
          if (ok) focus();
        });
      },
      reason: () =>
        idle() ?? (chat.running ? "Stop the current turn first." : undefined),
    },
    {
      id: "agent.stop-plan",
      label: "Stop plan",
      run: () => void chat.stopPlan(),
      reason: () =>
        idle() ??
        (chat.running
          ? "Stop the current turn first."
          : chat.session?.plan?.state !== "executing"
            ? "There is no executing plan to stop."
            : undefined),
    },
    {
      id: "chat.conversation",
      label: "Open conversation",
      run: (t) => {
        if ("id" in t) chat.viewConversation(t.id ?? "");
      },
      reason: () => (chat.busy ? "Saving the conversation…" : undefined),
    },
    {
      id: "agent.mode",
      label: "Toggle plan mode",
      shortcut: "Shift+Tab",
      checked: chat.planMode,
      run: chat.toggleMode,
      // ADR 0017 amendment 11: plan mode is always on in a film.
      reason: () =>
        film
          ? FILM_PLANS
          : (idle() ??
            (chat.running
              ? "Plan mode cannot change while the session is running."
              : undefined)),
    },
    {
      id: "agent.stop",
      label: "Stop",
      run: () => void chat.stop(),
      reason: () =>
        idle() ??
        (!chat.running && chat.session?.state !== "waiting_operation"
          ? "The session is not working."
          : undefined),
    },
    ...(["approve", "revise", "reject"] as const).map((action): Command => ({
      id: `chat.${action}`,
      label: action[0].toUpperCase() + action.slice(1),
      reason: (t) =>
        plan() ??
        (action === "revise" &&
        "note" in t &&
        t.note !== undefined &&
        !t.note.trim()
          ? "Describe how the plan should change."
          : undefined),
      run: (t) => {
        if (action === "approve") void chat.decide(action);
        else if ("note" in t && t.note !== undefined)
          void chat.decide(action, t.note);
        else {
          focus(false);
          window.dispatchEvent(
            new CustomEvent("gaiavfx-plan-note", { detail: action }),
          );
        }
      },
    })),
    {
      id: "chat.select",
      label: "Select on timeline",
      reason: (t) =>
        timelineTarget(d, "id" in t ? t.id : undefined)
          ? undefined
          : "This message has no available timeline target.",
      run: (t) => {
        const item = timelineTarget(d, "id" in t ? t.id : undefined);
        if (item) {
          d.editor.select(item.target);
          d.editor.seek(item.frame);
        }
      },
    },
    {
      id: "chat.undo",
      label: "Undo this receipt",
      reason: (t) =>
        d.busy
          ? "Saving the current command…"
          : !receipt(t) && !knownReceipt(t)
            ? "No editable receipt on this item."
            : receipt(t)?.undone_at
              ? "This change has already been undone."
              : undefined,
      run: (t) => {
        const value = receipt(t);
        if (value) void d.undo(value);
        else if (knownReceipt(t) && "receiptId" in t && t.receiptId)
          void d.perform(() => direction.undo(t.receiptId!), "Change undone");
      },
    },
    {
      id: "chat.review",
      label: "Open in Review",
      reason: () => "Review is coming in the next build.",
      run: () => {},
    },
    {
      id: "chat.copy",
      label: "Copy text",
      reason: (t) =>
        "text" in t && t.text ? undefined : "This item has no text to copy.",
      run: (t) => {
        if ("text" in t && t.text)
          void navigator.clipboard.writeText(t.text).catch(() => {
            // A denied clipboard remains selectable through the ordinary browser copy command.
            window.dispatchEvent(
              new CustomEvent("gaiavfx-chat-notice", {
                detail:
                  "Clipboard unavailable. Select the message text and copy it.",
              }),
            );
          });
      },
    },
  ];
}
