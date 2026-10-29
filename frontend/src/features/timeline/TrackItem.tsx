import { useRef, useState, type ReactNode, type RefObject } from "react";
import { ContextArea } from "../shell/Menus";
import type { ResizeEdge, TimelineEditor, TrackItemModel } from "./doc";

export type ItemProps = Omit<TrackItemModel, "key" | "compact"> & {
  scale: number;
  selected: boolean;
  editor: TimelineEditor;
  /** The row's own context menu resolves this item through `data-item-key`. */
  delegated?: string;
  /** A marker's room up to the next marker in its lane (`markerRoom`); it is drawn no wider. */
  room?: number;
};
export function TrackItem({
  editor,
  target,
  frame,
  length,
  scale,
  selected,
  className = "",
  label,
  children,
  move,
  snapFrames = [],
  lane,
  activate,
  reason,
  delegated,
  room,
  resize,
}: ItemProps) {
  const [preview, setPreview] = useState<number | null>(null);
  // A resize previews a length: the leading edge moves and the trailing edge stays where it is.
  const [span, setSpan] = useState<number | null>(null);
  const drag = useRef<{ x: number; frame: number; moved: boolean } | null>(
    null,
  );
  const edge = useRef<{ x: number; moved: boolean } | null>(null);
  const shown = span ?? length;
  return (
    <div
      className={`track-position ${length === undefined ? "track-marker" : ""}`}
      style={{
        left:
          span !== null && length !== undefined
            ? (frame + length - span) * scale
            : (preview ?? frame) * scale,
        width:
          shown === undefined
            ? undefined
            : // A resizable span ends exactly where it is anchored; other spans leave a 3 px gap.
              Math.max(shown * scale - (resize ? 0 : 3), 8),
        maxWidth:
          length === undefined && room !== undefined
            ? Math.max(room, 8)
            : undefined,
        ...(lane !== undefined
          ? { top: 5 + lane * 30, bottom: "auto", height: 25 }
          : {}),
      }}
    >
      <ItemMenu target={target} delegated={delegated}>
        <button
          data-item-key={delegated}
          aria-label={label}
          title={reason ?? label}
          disabled={!!reason}
          aria-pressed={selected}
          className={`track-item ${className}`}
          data-frame={preview ?? frame}
          onClick={(e) => {
            editor.select(target);
            activate?.(
              e.detail === 0
                ? 0
                : Math.round(
                    (e.clientX - e.currentTarget.getBoundingClientRect().left) /
                      scale,
                  ),
            );
          }}
          onDoubleClick={() =>
            document.getElementById("inspector-Properties-tab")?.click()
          }
          onPointerDown={(e) => {
            if (e.button !== 0 || !move || editor.busy) return;
            editor.select(target);
            drag.current = { x: e.clientX, frame, moved: false };
            e.currentTarget.setPointerCapture(e.pointerId);
          }}
          onPointerMove={(e) => {
            if (!drag.current) return;
            const delta = e.clientX - drag.current.x;
            if (Math.abs(delta) < 4 && !drag.current.moved) return;
            drag.current.moved = true;
            let next = Math.round(drag.current.frame + delta / scale);
            if (editor.snap) {
              const near = snapFrames.find(
                (f) => Math.abs(f - next) * scale < 7,
              );
              if (near !== undefined) next = near;
            }
            setPreview(next);
          }}
          onPointerUp={() => {
            if (drag.current?.moved && preview !== null && preview !== frame)
              move?.(preview);
            drag.current = null;
            setPreview(null);
          }}
          onLostPointerCapture={() => {
            drag.current = null;
            setPreview(null);
          }}
          onPointerCancel={() => {
            drag.current = null;
            setPreview(null);
          }}
        >
          {/* An empty compact box stays empty; only items without content show their label. */}
          {children === undefined ? label : children}
        </button>
      </ItemMenu>
      {resize && length !== undefined && resize.lengths.length > 0 && (
        <ResizeGrip
          resize={resize}
          length={length}
          shown={shown!}
          scale={scale}
          busy={editor.busy}
          dragging={span !== null}
          onStart={() => editor.select(target)}
          edge={edge}
          setSpan={setSpan}
        />
      )}
    </div>
  );
}

/** The leading edge of a resizable span: a slider over its allowed lengths, by pointer or keyboard. */
function ResizeGrip({
  resize,
  length,
  shown,
  scale,
  busy,
  dragging,
  onStart,
  edge,
  setSpan,
}: {
  resize: ResizeEdge;
  length: number;
  shown: number;
  scale: number;
  busy: boolean;
  dragging: boolean;
  onStart: () => void;
  edge: RefObject<{ x: number; moved: boolean } | null>;
  setSpan: (span: number | null) => void;
}) {
  const { lengths, label, commit } = resize;
  const nearest = (raw: number) =>
    lengths.reduce((best, n) =>
      Math.abs(n - raw) < Math.abs(best - raw) ? n : best,
    );
  const reset = () => {
    edge.current = null;
    setSpan(null);
  };
  const choose = (next: number | undefined) => {
    if (next !== undefined && next !== length && !busy) commit(next);
  };
  return (
    <span
      role="slider"
      tabIndex={0}
      className="resize-edge"
      aria-label={label}
      aria-valuemin={lengths[0]}
      aria-valuemax={lengths.at(-1)}
      aria-valuenow={shown}
      aria-valuetext={`${shown} frames`}
      aria-disabled={busy || undefined}
      data-dragging={dragging || undefined}
      title={`${label}: ${shown} frames. Drag the leading edge, or use the arrow keys.`}
      onPointerDown={(e) => {
        if (e.button !== 0 || busy) return;
        e.stopPropagation();
        onStart();
        edge.current = { x: e.clientX, moved: false };
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (!edge.current) return;
        const delta = e.clientX - edge.current.x;
        if (Math.abs(delta) < 4 && !edge.current.moved) return;
        edge.current.moved = true;
        // Dragging left lengthens: the span grows away from its anchored end.
        setSpan(nearest(length - delta / scale));
      }}
      onPointerUp={() => {
        if (edge.current?.moved) choose(shown);
        reset();
      }}
      onLostPointerCapture={reset}
      onPointerCancel={reset}
      onKeyDown={(e) => {
        if (e.key === "Escape" && edge.current) {
          e.preventDefault();
          reset();
          return;
        }
        // The edge leads on the left: ArrowLeft lengthens, as dragging left does.
        const longer = lengths.find((n) => n > length);
        const shorter = [...lengths].reverse().find((n) => n < length);
        const keys: Record<string, number | undefined> = {
          ArrowLeft: longer,
          ArrowUp: longer,
          ArrowRight: shorter,
          ArrowDown: shorter,
          Home: lengths[0],
          End: lengths.at(-1),
        };
        if (!(e.key in keys)) return;
        e.preventDefault();
        e.stopPropagation();
        choose(keys[e.key]);
      }}
    >
      {dragging && <output className="resize-edge-value">{shown} f</output>}
    </span>
  );
}

function ItemMenu({
  target,
  delegated,
  children,
}: {
  target: ItemProps["target"];
  delegated?: string;
  children: ReactNode;
}) {
  return delegated === undefined ? (
    <ContextArea target={target}>{children}</ContextArea>
  ) : (
    children
  );
}
