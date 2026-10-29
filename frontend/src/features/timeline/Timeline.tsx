import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import {
  ChevronRight,
  Magnet,
  Minus,
  Plus,
  Scan,
  SkipBack,
  MousePointer2,
  Scissors,
} from "lucide-react";
import { ContextArea, CommandButton } from "../shell/Menus";
import { timecode } from "./editor-state";
import { TrackItem } from "./TrackItem";
import { frameTicks, timecodeTicks } from "./time";
import {
  markerRoom,
  MARKER_DETAIL_PX,
  targetKey,
  visibleItems,
  type TimelineDoc,
  type TimelineEditor,
  type TrackRow,
} from "./doc";

/** Renders a timeline document: tools, ruler, rows of items, playhead and navigator (ADR 0017). */
export function Timeline({
  doc,
  editor,
}: {
  doc: TimelineDoc;
  editor: TimelineEditor;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(650);
  const [scroll, setScroll] = useState(0);
  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setWidth(element.clientWidth));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const labelWidth = doc.labelWidth ?? 102;
  const fps = doc.rate
    ? `${Number((doc.rate.num / doc.rate.den).toFixed(3))} fps`
    : `${doc.fps} fps`;
  const duration = Math.max(120, doc.duration);
  const available = Math.max(240, width - labelWidth - 20);
  const scale = (available / duration) * editor.zoom;
  const canvas = Math.max(available, duration * scale);
  const maxScroll = Math.max(0, canvas - available);
  // Windowing: only items within a viewport's width either side of the visible frames are drawn.
  const from = (scroll - available) / scale;
  const to = (scroll + 2 * available) / scale;
  const frames = doc.ruler === "timecode" ? null : frameTicks(duration, scale);
  const timecodes =
    doc.ruler === "timecode"
      ? timecodeTicks(duration, scale, doc.fps, Math.max(0, from), to)
      : null;
  const step = frames ? frames.step : timecodes!.step;
  const selected = targetKey(editor.selection);
  const frameAt = (e: React.PointerEvent) =>
    Math.max(
      0,
      Math.min(
        doc.duration - 1,
        Math.round(
          (e.clientX - e.currentTarget.getBoundingClientRect().left) / scale,
        ),
      ),
    );
  const seek = (frame: number) =>
    editor.seek(Math.min(Math.max(0, doc.duration - 1), frame));
  function lane(row: TrackRow) {
    const spans = row.items.filter((item) => item.length !== undefined);
    const width = spans.length
      ? (spans.reduce((sum, item) => sum + item.length!, 0) * scale) /
        spans.length
      : Infinity;
    // Items narrower than their 8 px minimum would overlap: a row that can aggregate draws one strip instead.
    if (row.aggregate && width < 8) return row.aggregate(row.items, scale);
    const rooms = markerRoom(row.items, scale);
    return visibleItems(row.items, from, to).map(
      ({ key, compact, children, ...item }) => {
        const room = rooms.get(key);
        // Level of detail per item: a box narrower than 24 px, or a marker with little room, shows its compact content.
        const narrow =
          item.length !== undefined
            ? item.length * scale < 24
            : room !== undefined && room < MARKER_DETAIL_PX;
        return (
          <TrackItem
            key={key}
            {...item}
            children={compact !== undefined && narrow ? compact : children}
            room={room}
            scale={scale}
            selected={targetKey(item.target) === selected}
            editor={editor}
            delegated={row.delegated ? key : undefined}
          />
        );
      },
    );
  }
  function trackRow(row: TrackRow, sub = false): ReactNode[] {
    const Icon = row.icon;
    const open = !!row.children && !!editor.expanded?.includes(row.id);
    const items = (
      <div className="track-lane">
        {lane(row)}
        {!row.items.length && <span className="empty-lane">{row.empty}</span>}
      </div>
    );
    return [
      <div
        className={sub ? "track-row sub-row" : "track-row"}
        data-row={row.id}
        key={row.id}
        style={
          row.lanes
            ? { minHeight: Math.max(43, row.lanes * 30 + 10) }
            : undefined
        }
      >
        <div className="track-label">
          {row.children && (
            <button
              type="button"
              className="twirl"
              aria-expanded={open}
              aria-label={`${row.name} lanes`}
              onClick={() => editor.toggle?.(row.id)}
            >
              <ChevronRight size={12} />
            </button>
          )}
          {row.header ?? (
            <>
              {Icon ? <Icon size={13} /> : null}
              {row.name}
            </>
          )}
        </div>
        {row.delegated ? (
          <ContextArea
            target={doc.context ?? { kind: "timeline" }}
            className="track-lane-menu"
            resolve={(element) => {
              const key = element
                .closest<HTMLElement>("[data-item-key]")
                ?.getAttribute("data-item-key");
              return row.items.find((item) => item.key === key)?.target;
            }}
          >
            {items}
          </ContextArea>
        ) : (
          items
        )}
      </div>,
      ...(open ? row.children!.flatMap((child) => trackRow(child, true)) : []),
    ];
  }
  const zoom = (
    <>
      <CommandButton id="snap" className="icon-button" title="Snap to markers">
        <Magnet size={15} />
        <span className="sr-only">Snap to markers</span>
      </CommandButton>
      <CommandButton id="fit" className="icon-button" title="Fit timeline">
        <Scan size={15} />
        <span className="sr-only">Fit timeline</span>
      </CommandButton>
      <CommandButton id="zoom.out" className="icon-button" title="Zoom out">
        <Minus size={15} />
        <span className="sr-only">Zoom out</span>
      </CommandButton>
      <label className="timeline-zoom">
        <span className="sr-only">Timeline zoom</span>
        <input
          type="range"
          min="0.5"
          max={doc.zoomMax ?? "12"}
          step="0.1"
          value={editor.zoom}
          onChange={(e) => editor.setZoom(Number(e.target.value))}
        />
      </label>
      <CommandButton id="zoom.in" className="icon-button" title="Zoom in">
        <Plus size={15} />
        <span className="sr-only">Zoom in</span>
      </CommandButton>
    </>
  );
  return (
    <>
      <div className="timeline-heading">
        <span>
          Timeline <span className="muted">· {doc.title}</span>
        </span>
        <span className="timeline-fps">{fps}</span>
      </div>
      {doc.notice}
      <div className="timeline-tools">
        <CommandButton
          id="tool.select"
          className="icon-button"
          title="Select tool"
        >
          <MousePointer2 size={15} />
          <span className="sr-only">Select tool</span>
        </CommandButton>
        <CommandButton
          id="tool.razor"
          className="icon-button"
          title="Razor tool"
        >
          <Scissors size={15} />
          <span className="sr-only">Razor tool</span>
        </CommandButton>
        {doc.bar ? (
          // A document with a tool bar (a film) steers its tool in this row; its zoom sits in the navigator.
          <div className="timeline-bar">{doc.bar}</div>
        ) : (
          <>
            <button
              className="icon-button"
              aria-label="Go to start"
              onClick={() => seek(0)}
            >
              <SkipBack size={14} />
            </button>
            <output className="timecode">
              {timecode(editor.playhead, doc.fps)}
            </output>
            <span className="tool-spacer" />
            {zoom}
          </>
        )}
      </div>
      <ContextArea
        target={doc.context ?? { kind: "timeline" }}
        className="timeline-context"
      >
        <div
          className="timeline-scroll"
          ref={viewport}
          onScroll={(e) => setScroll(e.currentTarget.scrollLeft)}
        >
          <div
            className={
              doc.labelWidth
                ? "timeline-canvas custom-labels"
                : "timeline-canvas"
            }
            style={
              {
                width: canvas + labelWidth + 20,
                "--frame-step": `${step * scale}px`,
                ...(doc.labelWidth
                  ? { "--label-width": `${labelWidth}px` }
                  : {}),
              } as CSSProperties
            }
          >
            <div className="timeline-ruler">
              <div className="track-label">
                {timecodes ? "Timecode" : "Frames"} <small>{fps}</small>
              </div>
              <div
                className="ruler-scale"
                role="slider"
                aria-label="Playhead"
                aria-valuemin={0}
                aria-valuemax={Math.max(0, doc.duration - 1)}
                aria-valuenow={editor.playhead}
                aria-valuetext={timecode(editor.playhead, doc.fps)}
                tabIndex={0}
                onPointerDown={(e) => {
                  e.currentTarget.setPointerCapture(e.pointerId);
                  seek(frameAt(e));
                }}
                onPointerMove={(e) => {
                  if (e.currentTarget.hasPointerCapture(e.pointerId))
                    seek(frameAt(e));
                }}
                onKeyDown={(e) => {
                  if (
                    ["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)
                  ) {
                    e.preventDefault();
                    seek(
                      e.key === "Home"
                        ? 0
                        : e.key === "End"
                          ? doc.duration - 1
                          : editor.playhead +
                            (e.key === "ArrowRight" ? 1 : -1) *
                              (e.shiftKey ? Math.round(doc.fps) : 1),
                    );
                  }
                }}
              >
                {doc.workArea && (
                  <div
                    className="work-area-band"
                    aria-hidden="true"
                    style={{
                      left: doc.workArea.start * scale,
                      width: (doc.workArea.end - doc.workArea.start) * scale,
                    }}
                  />
                )}
                {doc.rulerSpans?.map((span) => (
                  <div
                    key={`${span.label}-${span.start}`}
                    className="ruler-span"
                    aria-hidden="true"
                    title={span.title}
                    style={{
                      left: span.start * scale,
                      width: (span.end - span.start) * scale,
                    }}
                  >
                    <span>{span.label}</span>
                  </div>
                ))}
                {frames
                  ? frames.ticks.map((f) => (
                      <span key={f} style={{ left: f * scale }}>
                        {String(f).padStart(2, "0")}
                      </span>
                    ))
                  : timecodes!.ticks.map((tick) => (
                      <span
                        key={tick.frame}
                        style={{ left: tick.frame * scale }}
                      >
                        {tick.label}
                      </span>
                    ))}
              </div>
            </div>
            {doc.pinned && (
              <div className="timeline-pinned">{trackRow(doc.pinned)}</div>
            )}
            {doc.rows.flatMap((row) => trackRow(row))}
            {doc.workArea && (
              <div
                className="work-area"
                aria-hidden="true"
                style={{
                  left: labelWidth + doc.workArea.start * scale,
                  width: (doc.workArea.end - doc.workArea.start) * scale,
                }}
              />
            )}
            <div
              className="playhead-line"
              style={{ left: labelWidth + editor.playhead * scale }}
              aria-hidden="true"
            >
              <span />
            </div>
          </div>
        </div>
      </ContextArea>
      <div className="timeline-navigator">
        <span className="timeline-context-hint">{doc.hint}</span>
        <span>{doc.place}</span>
        <input
          type="range"
          aria-label="Timeline scroll"
          min={0}
          max={Math.ceil(maxScroll)}
          value={Math.min(scroll, maxScroll)}
          onChange={(e) => {
            if (viewport.current)
              viewport.current.scrollLeft = Number(e.target.value);
          }}
        />
        <span>{timecode(doc.duration, doc.fps)}</span>
        {doc.bar && <span className="navigator-zoom">{zoom}</span>}
      </div>
    </>
  );
}
