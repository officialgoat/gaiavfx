import { useRef, useState, type ReactNode } from "react";
import { ContextArea } from "../shell/Menus";
import type { TimelineEditor, TrackItemModel } from "./doc";

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
}: ItemProps) {
  const [preview, setPreview] = useState<number | null>(null);
  const drag = useRef<{ x: number; frame: number; moved: boolean } | null>(
    null,
  );
  return (
    <div
      className={`track-position ${length === undefined ? "track-marker" : ""}`}
      style={{
        left: (preview ?? frame) * scale,
        width:
          length === undefined ? undefined : Math.max(length * scale - 3, 8),
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
    </div>
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
