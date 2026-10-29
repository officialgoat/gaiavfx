import { useEffect, useState } from "react";
import { Cpu } from "lucide-react";
import type { DirectController } from "../project/use-direction";
import type { ChatController } from "./use-chat";
import { Composer } from "./Composer";
import { Transcript } from "./Transcript";
import { PlanProgress } from "./PlanProgress";
import { CommandButton } from "../shell/Menus";
import "./chat.css";

export function ChatPanel({
  chat,
  d,
  compact = false,
}: {
  chat: ChatController;
  d: DirectController;
  compact?: boolean;
}) {
  const [debug, setDebug] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const show = (e: Event) => setNotice((e as CustomEvent).detail);
    window.addEventListener("gaiavfx-chat-notice", show);
    return () => window.removeEventListener("gaiavfx-chat-notice", show);
  }, []);
  return (
    <section
      className="chat-panel"
      aria-label={
        chat.scope.kind === "film" ? "Film conversation" : "Movie conversation"
      }
    >
      <div className="chat-model-line">
        <Cpu size={13} />
        <span>
          {chat.location} · {chat.model} · {chat.modelActivity}
        </span>
        <label className="chat-debug" title="Show rejected turns">
          <input
            type="checkbox"
            checked={debug}
            onChange={(e) => setDebug(e.target.checked)}
          />
          Debug
        </label>
      </div>
      {chat.conversations.length > 1 && (
        <details className="chat-earlier">
          <summary>Earlier conversations</summary>
          {chat.conversations.slice(1).map((s, i) => (
            <CommandButton
              key={s.id}
              id="chat.conversation"
              target={{ kind: "chat", id: s.id }}
            >
              Conversation {chat.conversations.length - i - 1} · {s.state}
            </CommandButton>
          ))}
        </details>
      )}
      {chat.readOnly && (
        <div className="chat-error">
          Earlier conversation · Read-only
          <CommandButton
            id="chat.conversation"
            target={{ kind: "chat", id: "" }}
          >
            Return to current conversation
          </CommandButton>
        </div>
      )}
      {compact &&
      chat.session?.plan &&
      chat.session.plan.state !== "proposed" ? (
        <div className="chat-compact-body">
          <PlanProgress
            plan={chat.session.plan}
            d={d}
            build={chat.session.build}
          />
          <details className="chat-earlier">
            <summary>Conversation and plan details</summary>
            <Transcript chat={chat} d={d} debug={debug} />
          </details>
        </div>
      ) : (
        <Transcript chat={chat} d={d} debug={debug} />
      )}
      {chat.error && (
        <div className="chat-error" role="alert">
          {chat.error}
          <button onClick={chat.refresh}>Refresh conversation</button>
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      {!chat.readOnly && chat.session?.build?.note && (
        // Why a message waits while the conversation's stage build runs, in Python's words.
        <p className="chat-build-note">{chat.session.build.note}</p>
      )}
      {!chat.readOnly && (
        <Composer
          key={`${chat.scope.kind}:${chat.scope.id}`}
          chat={chat}
          d={d}
        />
      )}
    </section>
  );
}
