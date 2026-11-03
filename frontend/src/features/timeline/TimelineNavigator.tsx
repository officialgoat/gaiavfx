import { useRef, type KeyboardEvent, type PointerEvent } from "react";
import { timecode } from "./editor-state";

type Drag = {
  part: Part;
  x: number;
  start: number;
  end: number;
  width: number;
};
type Part = "thumb" | "start" | "end";

/** The timeline's scroll bar, which is also its zoom: the thumb is the visible span. Drag it to scroll; drag its
 * start or end handle to show less or more of the timeline (the user's request, replacing the zoom buttons);
 * double-click it to fit. Each part is a slider for the keyboard; Escape cancels a drag. */
export function TimelineNavigator({
  duration,
  start,
  span,
  minSpan,
  fps,
  onChange,
}: {
  /** The document's length in frames. */
  duration: number;
  /** The first visible frame and the number of visible frames. */
  start: number;
  span: number;
  /** The fewest frames the timeline can show (its closest zoom). */
  minSpan: number;
  fps: number;
  onChange: (start: number, span: number) => void;
}) {
  const track = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const shown = Math.min(span, duration);
  const first = Math.max(0, Math.min(start, duration - shown));
  const end = first + shown;
  const set = (from: number, to: number) => {
    const length = Math.max(minSpan, Math.min(duration, to - from));
    onChange(Math.max(0, Math.min(from, duration - length)), length);
  };
  const move = (part: Part, at: Drag, delta: number) => {
    if (part === "thumb") set(at.start + delta, at.end + delta);
    else if (part === "start")
      set(Math.max(0, Math.min(at.start + delta, at.end - minSpan)), at.end);
    else
      set(
        at.start,
        Math.min(duration, Math.max(at.end + delta, at.start + minSpan)),
      );
  };
  const down = (part: Part) => (e: PointerEvent<HTMLElement>) => {
    if (e.button !== 0 || !track.current) return;
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = {
      part,
      x: e.clientX,
      start: first,
      end,
      width: track.current.clientWidth,
    };
  };
  const pointerMove = (e: PointerEvent<HTMLElement>) => {
    const at = drag.current;
    if (!at) return;
    move(at.part, at, ((e.clientX - at.x) / at.width) * duration);
  };
  const up = () => {
    drag.current = null;
  };
  const keys = (part: Part) => (e: KeyboardEvent<HTMLElement>) => {
    if (e.key === "Escape" && drag.current) {
      const at = drag.current;
      drag.current = null;
      set(at.start, at.end);
      e.preventDefault();
      return;
    }
    const step = Math.max(1, Math.round(shown * (e.shiftKey ? 0.5 : 0.1)));
    const at: Drag = { part, x: 0, start: first, end, width: 1 };
    const delta =
      e.key === "ArrowLeft" || e.key === "ArrowDown"
        ? -step
        : e.key === "ArrowRight" || e.key === "ArrowUp"
          ? step
          : e.key === "Home"
            ? -duration
            : e.key === "End"
              ? duration
              : 0;
    if (!delta) return;
    e.preventDefault();
    move(part, at, delta);
  };
  const pct = (frames: number) => `${(frames / duration) * 100}%`;
  const range = `${timecode(Math.round(first), fps)} to ${timecode(Math.round(end), fps)}`;
  return (
    <div
      className="timeline-range"
      ref={track}
      onPointerDown={(e) => {
        // A press on the bare track centres the visible span there.
        if (e.button !== 0 || e.target !== e.currentTarget) return;
        const box = e.currentTarget.getBoundingClientRect();
        const at = ((e.clientX - box.left) / box.width) * duration;
        set(at - shown / 2, at + shown / 2);
      }}
    >
      {/* The window is drawn at least 28 px wide, so between its two handles there is always a thumb to drag. */}
      <div
        className="timeline-range-window"
        style={{
          left: `min(${pct(first)}, calc(100% - 28px))`,
          width: pct(shown),
        }}
      >
        <div
          className="timeline-range-thumb"
          role="slider"
          tabIndex={0}
          aria-label="Timeline scroll"
          aria-valuemin={0}
          aria-valuemax={Math.max(0, Math.round(duration - shown))}
          aria-valuenow={Math.round(first)}
          aria-valuetext={`Showing ${range}`}
          title="Drag to scroll; drag an end to zoom; double-click to fit"
          onPointerDown={down("thumb")}
          onPointerMove={pointerMove}
          onPointerUp={up}
          onPointerCancel={up}
          onDoubleClick={() => set(0, duration)}
          onKeyDown={keys("thumb")}
        />
        {(["start", "end"] as const).map((part) => (
          <span
            key={part}
            className={`timeline-range-handle ${part}`}
            role="slider"
            tabIndex={0}
            aria-label={part === "start" ? "Visible start" : "Visible end"}
            aria-valuemin={part === "start" ? 0 : Math.round(first + minSpan)}
            aria-valuemax={
              part === "start"
                ? Math.round(end - minSpan)
                : Math.round(duration)
            }
            aria-valuenow={Math.round(part === "start" ? first : end)}
            aria-valuetext={timecode(
              Math.round(part === "start" ? first : end),
              fps,
            )}
            title={
              part === "start"
                ? "Drag to show more or less before"
                : "Drag to show more or less after"
            }
            onPointerDown={down(part)}
            onPointerMove={pointerMove}
            onPointerUp={up}
            onPointerCancel={up}
            onDoubleClick={() => set(0, duration)}
            onKeyDown={keys(part)}
          />
        ))}
      </div>
    </div>
  );
}
