import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import type { Target } from "./editor-state";

// ADR 0017: the timeline renders a presentation document. A builder turns a movie (or, later, a film) into rows of
// items on one frame axis; statuses and rules stay in Python and edits go to the owning module through the items'
// callbacks. Nothing here is stored.

/** One item on a row: a span when it has a length, otherwise a marker. */
export type TrackItemModel = {
  key: string;
  target: Target;
  frame: number;
  length?: number;
  lane?: number;
  className?: string;
  label: string;
  children?: ReactNode;
  move?: (frame: number) => void;
  snapFrames?: number[];
  activate?: (offset: number) => void;
  reason?: string;
  /** Drawn instead of `children` when the item is a narrow box, or a marker with little room (level of detail). */
  compact?: ReactNode;
};

// Every field below marked optional changes nothing when absent: a movie sets none of them, and its DOM stays as the
// lock test recorded it.
export type TrackRow = {
  /** Stable id, also the row's `data-row` attribute. */
  id: string;
  name: string;
  icon?: LucideIcon;
  lanes?: number;
  items: TrackItemModel[];
  /** Shown when the row has no items. */
  empty: string;
  /** Replaces the plain label: a lane header with its own controls. */
  header?: ReactNode;
  /** Sub-lanes, shown while the editor has this row expanded (a twirl button toggles them). */
  children?: TrackRow[];
  /** One context menu for the whole row, resolving the item under the pointer (many items stay cheap). */
  delegated?: boolean;
  /** Drawn instead of the items when they are too narrow to see one by one: on average under an item's 8 px
   * minimum width. */
  aggregate?: (items: TrackItemModel[], scale: number) => ReactNode;
};

export type TimelineDoc = {
  title: string;
  fps: number;
  /** Frames on the axis. */
  duration: number;
  rows: TrackRow[];
  /** The navigator's left text, for example the razor's instructions. */
  hint: string;
  /** The navigator's unit under selection, for example "Segment 01". */
  place: string;
  /** The exact frame rate (24000/1001); `fps` stays the number timecode uses. */
  rate?: { num: number; den: number };
  /** A timecode ruler with windowed ticks instead of frame numbers. */
  ruler?: "frames" | "timecode";
  /** Width of the row labels; lane headers need more than the default 102 px. */
  labelWidth?: number;
  /** The highest zoom; long films need more than a movie's 12. */
  zoomMax?: number;
  /** The context target of the timeline itself; a movie's is `{kind: "timeline"}`. */
  context?: Target;
  /** A tool's steering bar, between the timeline tools and the tracks. */
  bar?: ReactNode;
  /** Messages about the document (a failed action, a notice, a service that does not answer), under the heading. */
  notice?: ReactNode;
  /** A tool track pinned under the ruler while the rows scroll. */
  pinned?: TrackRow;
  /** The tool's work area, drawn on the ruler and over the rows. */
  workArea?: { start: number; end: number };
  /** Other ranges worth seeing on the ruler (a paused run's own range), each named by a visible label. */
  rulerSpans?: { start: number; end: number; label: string; title: string }[];
};

/** The parts of the editor state the renderer reads and writes. */
export type TimelineEditor = {
  selection: Target;
  select: (target: Target) => void;
  playhead: number;
  seek: (frame: number) => void;
  zoom: number;
  setZoom: (zoom: number) => void;
  snap: boolean;
  busy: boolean;
  /** Rows whose sub-lanes are shown, and the toggle (documents with sub-lanes only). */
  expanded?: string[];
  toggle?: (row: string) => void;
};

export const targetKey = (target: Target) => JSON.stringify(target);

/** The items that overlap the visible frames [from, to]; spans count by their whole length. */
/** A marker's room: the pixels up to the next marker in its lane, which it never draws over. A marker without a
 * later one in its lane has no bound beyond its own maximum width. */
export function markerRoom(items: TrackItemModel[], scale: number) {
  const markers = items
    .filter((item) => item.length === undefined)
    .sort((a, b) => a.frame - b.frame);
  const room = new Map<string, number>();
  markers.forEach((marker, i) => {
    const next = markers
      .slice(i + 1)
      .find((other) => (other.lane ?? 0) === (marker.lane ?? 0));
    if (next) room.set(marker.key, (next.frame - marker.frame) * scale - 3);
  });
  return room;
}

/** Below this room a marker shows its compact content. */
export const MARKER_DETAIL_PX = 64;

export function visibleItems(
  items: TrackItemModel[],
  from: number,
  to: number,
) {
  return items.filter(
    (item) => item.frame <= to && item.frame + (item.length ?? 0) >= from,
  );
}
