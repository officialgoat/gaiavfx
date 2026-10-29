import { create } from "zustand";
import type { Target } from "../timeline/editor-state";
import type { QueueName } from "./progress";

// ADR 0017: a film's view state on the timeline. It is never the movie's editor (`useEditor`), so opening a film
// leaves the movie's selection, playhead and zoom exactly as they were. Which shot is current stays with the recast
// controller (`useRecast`); this store holds only what the view needs, for one recast at a time, including a mirror
// of the current shot's id, so the few parts that mark it follow it without rebuilding the film's rows.
const TOOL: Target = { kind: "tool", id: "recast" };

/** A cast edit that needs a question first: which picture, which name, or which character to merge with. */
export type CastDialog =
  | { kind: "target"; characterId: string }
  | { kind: "rename"; characterId: string }
  // `characterId` "unknown": the face tracks no character holds yet.
  | { kind: "tracks"; characterId: string }
  | { kind: "merge"; from: string; into?: string }
  // Retake reserves renders, so it asks first (usability round 2, C9).
  | { kind: "retake"; shotId: string }
  // A character's voice (ADR 0016, P5): which Library recordings, and the build's GPU hours, asked first.
  | { kind: "voice-samples"; characterId: string }
  | { kind: "build-voice"; characterId: string };

type FilmEditor = {
  recastId: string;
  /** The controller's current shot, mirrored (FilmWorkspace's `current`). */
  shotId: string;
  selection: Target;
  playhead: number;
  zoom: number;
  snap: boolean;
  expanded: string[];
  dialog: CastDialog | null;
  /** The Project panel's bin (Shots, Review, Cast, Assets), the bin before Review, and the review queue's filter. */
  bin: string;
  previousBin: string;
  queue: QueueName;
  /** Counts requests to open the queue, so each one can bring the Project panel forward. */
  queueRequest: number;
  setBin: (bin: string) => void;
  /** Shows a bin and brings the Project panel forward, as opening the queue does. */
  showBin: (bin: string) => void;
  openQueue: (queue: QueueName) => void;
  closeQueue: () => void;
  reset: (recastId: string) => void;
  select: (target: Target) => void;
  seek: (frame: number) => void;
  setZoom: (zoom: number) => void;
  setSnap: (snap: boolean) => void;
  toggle: (row: string) => void;
  openDialog: (dialog: CastDialog | null) => void;
};

export const useFilmEditor = create<FilmEditor>((set) => ({
  recastId: "",
  shotId: "",
  selection: TOOL,
  playhead: 0,
  zoom: 1,
  snap: true,
  expanded: [],
  dialog: null,
  bin: "Shots",
  previousBin: "Shots",
  queue: "waiting",
  queueRequest: 0,
  setBin: (bin) =>
    set((old) => ({
      bin,
      previousBin:
        bin === "Review" && old.bin !== "Review" ? old.bin : old.previousBin,
    })),
  showBin: (bin) =>
    set((old) => ({
      bin,
      previousBin:
        bin === "Review" && old.bin !== "Review" ? old.bin : old.previousBin,
      queueRequest: old.queueRequest + 1,
    })),
  openQueue: (queue) =>
    set((old) => ({
      bin: "Review",
      queue,
      previousBin: old.bin === "Review" ? old.previousBin : old.bin,
      queueRequest: old.queueRequest + 1,
    })),
  closeQueue: () => set((old) => ({ bin: old.previousBin || "Shots" })),
  reset: (recastId) =>
    set((old) =>
      old.recastId === recastId
        ? old
        : {
            recastId,
            shotId: "",
            selection: TOOL,
            playhead: 0,
            zoom: 1,
            expanded: [],
            dialog: null,
            bin: "Shots",
            previousBin: "Shots",
            queue: "waiting",
          },
    ),
  select: (selection) => set({ selection }),
  seek: (playhead) => set({ playhead: Math.max(0, Math.round(playhead)) }),
  // A film is long: zoom reaches far further in than a movie's 12x.
  setZoom: (zoom) => set({ zoom: Math.max(0.5, Math.min(400, zoom)) }),
  setSnap: (snap) => set({ snap }),
  toggle: (row) =>
    set((old) => ({
      expanded: old.expanded.includes(row)
        ? old.expanded.filter((r) => r !== row)
        : [...old.expanded, row],
    })),
  openDialog: (dialog) => set({ dialog }),
}));
