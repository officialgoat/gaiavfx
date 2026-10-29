import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUp, AtSign, Paperclip, Square } from "lucide-react";
import { studio } from "../generation/client";
import { AssetImage } from "../generation/MediaPreview";
import { HIDE_PROCESS_MEDIA } from "../generation/process-media";
import { CommandButton, useCommands } from "../shell/Menus";
import type { DirectController } from "../project/use-direction";
import { FILM_PLANS, type ChatController, type Mention } from "./use-chat";
import { ASSET_DRAG_TYPE, Attachment } from "./ChatMedia";

export function Composer({
  chat,
  d,
}: {
  chat: ChatController;
  d: DirectController;
}) {
  const { text, attachments, mentions } = chat.draft;
  const setText = (text: string) => chat.setDraft((old) => ({ ...old, text }));
  const setAttachments = (update: (old: string[]) => string[]) =>
    chat.setDraft((old) => ({ ...old, attachments: update(old.attachments) }));
  const setMentions = (update: (old: Mention[]) => Mention[]) =>
    chat.setDraft((old) => ({ ...old, mentions: update(old.mentions) }));
  const [picker, setPicker] = useState<"media" | "mention" | null>(null);
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  const textarea = useRef<HTMLTextAreaElement>(null);
  const pickerElement = useRef<HTMLDivElement>(null);
  const { commands } = useCommands();
  const film = chat.scope.kind === "film";
  // A film's versions are reviewed blind: its picker never lists their composites or process files (Astra R-C).
  const media = useQuery({
    queryKey: ["assets", "chat-picker", chat.scope.kind, search, offset],
    queryFn: () =>
      studio.assets({
        q: search,
        limit: "30",
        offset: String(offset),
        ...(film ? HIDE_PROCESS_MEDIA : {}),
      }),
    enabled: picker === "media",
  });
  // A film's composer works once its recast is loaded; a movie's once the movie is.
  const ready = film ? chat.ready : !!d.movie.data;
  const available: Mention[] = film
    ? chat.filmCast
    : [
        ...(d.movie.data?.subjects.map((s) => ({
          id: s.id,
          name: s.name,
          kind: "cast",
        })) ?? []),
        ...(d.places.data?.map((p) => ({
          id: p.id,
          name: p.name,
          kind: "place",
        })) ?? []),
        ...d.segments.map((s) => ({
          id: s.id,
          name: `Segment ${String(s.ordinal + 1).padStart(2, "0")}`,
          kind: "segment",
        })),
      ];
  const mentionQuery =
    text
      .slice(0, textarea.current?.selectionStart ?? text.length)
      .match(/@([^@\n]*)$/)?.[1] ?? "";
  function openPicker(value: "media" | "mention") {
    setPicker(picker === value ? null : value);
    setSearch("");
    setOffset(0);
    if (picker !== value)
      requestAnimationFrame(() =>
        pickerElement.current
          ?.querySelector<HTMLElement>(
            value === "mention" ? ".chat-mention-option" : "input",
          )
          ?.focus(),
      );
  }
  async function attach(id: string) {
    setError("");
    try {
      const asset = await studio.asset(id);
      if (!["image", "video"].includes(asset.kind)) {
        setError("Attach an image or video from Library.");
        return;
      }
      setAttachments((old) => {
        if (old.includes(id)) return old;
        if (old.length >= 6) {
          setError("You can attach up to 6 references.");
          return old;
        }
        return [...old, id];
      });
    } catch {
      setError("That reference is unavailable. Choose it again from Library.");
    }
  }
  async function send() {
    if (!text.trim() || chat.busy || chat.running || !ready) return;
    const refs = mentions.filter((m) => text.includes(`@${m.name}`));
    // MessageInput has text and media IDs. Keep named references with their stable
    // IDs in the message text rather than inventing an unsupported API property.
    const message =
      text.trim() +
      (refs.length
        ? `\n\nReferences:\n${refs.map((m) => `@${m.name} (${m.kind}: ${m.id})`).join("\n")}`
        : "");
    if (message.length > 8000) {
      setError(
        "Shorten the message to fit 8,000 characters including references.",
      );
      return;
    }
    const command = commands.get("chat.send");
    const target = { kind: "chat" as const, text: message, attachments };
    if (!command?.reason?.(target) && (await command?.run(target))) {
      chat.setDraft((old) =>
        old.text === text &&
        JSON.stringify(old.attachments) === JSON.stringify(attachments) &&
        JSON.stringify(old.mentions) === JSON.stringify(mentions)
          ? { text: "", attachments: [], mentions: [] }
          : old,
      );
      setPicker(null);
      setError("");
    }
  }
  return (
    <form
      className="chat-composer"
      onSubmit={(e) => {
        e.preventDefault();
        void send();
      }}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes(ASSET_DRAG_TYPE)) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }
      }}
      onDrop={(e) => {
        const id = e.dataTransfer.getData(ASSET_DRAG_TYPE);
        if (id) {
          e.preventDefault();
          void attach(id);
        }
      }}
    >
      {picker && (
        <div
          className="chat-picker"
          ref={pickerElement}
          role="region"
          aria-label={
            picker === "media" ? "Attach from Library" : "Mention an item"
          }
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              e.stopPropagation();
              setPicker(null);
              textarea.current?.focus();
            }
            if (["ArrowDown", "ArrowUp"].includes(e.key)) {
              const options = Array.from(
                e.currentTarget.querySelectorAll<HTMLButtonElement>(
                  ".chat-mention-option,.chat-picker-asset",
                ),
              );
              const current = options.indexOf(
                document.activeElement as HTMLButtonElement,
              );
              if (options.length) {
                e.preventDefault();
                options[
                  (current +
                    (e.key === "ArrowDown" ? 1 : -1) +
                    options.length) %
                    options.length
                ].focus();
              }
            }
          }}
        >
          <div className="chat-picker-heading">
            <strong>
              {picker === "media"
                ? "Attach from Library"
                : film
                  ? "Characters"
                  : "Cast, places and segments"}
            </strong>
            <button
              type="button"
              onClick={() => {
                setPicker(null);
                textarea.current?.focus();
              }}
            >
              Close
            </button>
          </div>
          {picker === "media" ? (
            <>
              <input
                autoFocus
                aria-label="Search attachments"
                placeholder="Search Library"
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  setOffset(0);
                }}
              />
              {media.isPending && <p role="status">Loading references…</p>}
              {media.error && (
                <p role="alert">
                  {media.error.message}
                  <button type="button" onClick={() => void media.refetch()}>
                    Retry
                  </button>
                </p>
              )}
              {media.data?.items
                .filter((a) => a.kind === "image" || a.kind === "video")
                .map((a) => (
                  <button
                    type="button"
                    key={a.id}
                    className="chat-picker-asset"
                    aria-pressed={attachments.includes(a.id)}
                    disabled={
                      attachments.length >= 6 && !attachments.includes(a.id)
                    }
                    onClick={() => void attach(a.id)}
                  >
                    <AssetImage asset={a} />
                    <span>{a.name}</span>
                  </button>
                ))}
              {media.data?.items.length === 0 && <p>No matching references.</p>}
              <div className="chat-picker-pages">
                <button
                  type="button"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - 30))}
                >
                  Previous
                </button>
                <button
                  type="button"
                  disabled={offset + 30 >= (media.data?.total ?? 0)}
                  onClick={() => setOffset(offset + 30)}
                >
                  Next
                </button>
              </div>
            </>
          ) : (
            available
              .filter((m) =>
                m.name.toLowerCase().includes(mentionQuery.toLowerCase()),
              )
              .map((m) => (
                <button
                  type="button"
                  key={m.id}
                  className="chat-mention-option"
                  onClick={() => {
                    const cursor =
                      textarea.current?.selectionStart ?? text.length;
                    const before = text
                      .slice(0, cursor)
                      .replace(/@[^@\n]*$/, "");
                    setText(
                      `${before}${before && !/\s$/.test(before) ? " " : ""}@${m.name} ${text.slice(cursor)}`,
                    );
                    setMentions((old) => [
                      ...old.filter((v) => v.id !== m.id),
                      m,
                    ]);
                    setPicker(null);
                    textarea.current?.focus();
                  }}
                >
                  {m.name}
                  <small>{m.kind}</small>
                </button>
              ))
          )}
        </div>
      )}
      <div className="chat-attachments">
        {attachments.map((id) => (
          <Attachment
            key={id}
            id={id}
            remove={() => setAttachments((old) => old.filter((a) => a !== id))}
          />
        ))}
      </div>
      <label className="sr-only" htmlFor="chat-input">
        Message to Gaia
      </label>
      <textarea
        ref={textarea}
        id="chat-input"
        rows={2}
        maxLength={8000}
        placeholder={
          film
            ? "Ask about the cast, or @mention a character…"
            : "Discuss the shot, or @mention a reference…"
        }
        value={text}
        disabled={!ready || chat.loading}
        onChange={(e) => {
          setText(e.target.value);
          const tail = e.target.value
            .slice(0, e.target.selectionStart)
            .match(/@([^@\n]*)$/)?.[1];
          if (
            tail !== undefined &&
            !mentions.some((m) => tail.startsWith(`${m.name} `))
          )
            setPicker("mention");
          else if (picker === "mention") setPicker(null);
        }}
        onKeyDown={(e) => {
          if (e.nativeEvent.isComposing) return;
          if (picker === "mention" && e.key === "ArrowDown") {
            e.preventDefault();
            pickerElement.current
              ?.querySelector<HTMLButtonElement>(".chat-mention-option")
              ?.focus();
            return;
          }
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            void send();
          }
          if (e.key === "Escape") setPicker(null);
          if (e.key === "Tab" && e.shiftKey) {
            e.preventDefault();
            const command = commands.get("agent.mode");
            if (!command?.reason?.(d.editor.selection))
              command?.run(d.editor.selection);
          }
        }}
      />
      <div className="composer-tools">
        <button
          type="button"
          aria-label="Attach a reference"
          aria-expanded={picker === "media"}
          onClick={() => openPicker("media")}
        >
          <Paperclip size={15} />
        </button>
        <button
          type="button"
          aria-label={
            film ? "Mention a character" : "Mention cast, places or segments"
          }
          aria-expanded={picker === "mention"}
          onClick={() => openPicker("mention")}
        >
          <AtSign size={15} />
        </button>
        <CommandButton id="agent.mode" className="chat-mode">
          Plan mode <kbd>Shift+Tab</kbd>
        </CommandButton>
        <span className="tool-spacer" />
        {chat.running ? (
          <CommandButton id="agent.stop" className="chat-send">
            <Square size={14} />
            <span className="sr-only">Stop</span>
          </CommandButton>
        ) : (
          <button
            type="submit"
            className="primary-button chat-send"
            aria-label="Send message"
            disabled={!text.trim() || chat.busy || chat.loading || !ready}
          >
            <ArrowUp size={17} />
          </button>
        )}
      </div>
      {error && (
        <p className="chat-error" role="alert">
          {error}
        </p>
      )}
      <p className="composer-rule">
        {film
          ? `${FILM_PLANS} Approve its plan before a recast run starts.`
          : chat.planMode
            ? "Discuss first. Approve a plan before making changes."
            : "Plan mode is off. The agent follows your saved autonomy settings."}
      </p>
    </form>
  );
}
