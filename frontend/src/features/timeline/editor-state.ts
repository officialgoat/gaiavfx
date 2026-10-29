import { create } from "zustand";
import { frameTimecode } from "../../shared/frame-rate";
import type { Producer, ProducerBody } from "../review/client";
import type { ContactTrack } from "../prepare/ContactSheet";

export type SourcePoster = {
  url?: string;
  native: [number, number];
  highlight: ContactTrack | null;
};
type SourceHighlight = {
  owner: string;
  sourceId: string;
  poster: SourcePoster;
};

export type Target =
  | {
      kind: "angle-edit";
      id: string;
      name: string;
      description: string;
      revision: number;
    }
  | { kind: "producer"; producer: Producer; body: ProducerBody }
  | { kind: "candidate"; id: string }
  | { kind: "preparation"; id: string }
  | {
      kind: "binding";
      id: string;
      cutId: string;
      referenceKind: "view" | "pin" | "sheet";
      assetId: string;
      note?: string;
    }
  | {
      kind: "chat" | "chat-plan";
      id?: string;
      receiptId?: string;
      text?: string;
      note?: string;
      attachments?: string[];
      reviewId?: string;
      reviewOperationId?: string;
    }
  | { kind: "timeline" | "monitor" }
  | { kind: "segment" | "cut" | "phase"; id: string; segmentId: string }
  // A segment's first-shot references (its place, and the set it is built as): its own kind, so Remove never
  // reaches the segment from the chip (ADR 0019 §5).
  | { kind: "opening"; id: string; segmentId: string }
  | { kind: "state" | "dialogue" | "sound"; index: number; segmentId: string }
  | {
      kind: "picture" | "keyframe";
      id: string;
      segmentId: string;
      assetId: string;
      cutId?: string;
    }
  | { kind: "asset" | "place" | "subject"; id: string; assetId?: string }
  // Film targets (ADR 0017). None carries a segmentId or cutId, and only a character carries an assetId (its
  // target picture), so movie commands and Library reveals never resolve a film item.
  | { kind: "shot"; id: string; shotId: string }
  | { kind: "take"; id: string; shotId: string }
  | { kind: "character"; id: string; assetId?: string }
  | {
      kind: "appearance";
      shotId: string;
      analysisOperationId: string;
      trackId: string;
      characterId?: string;
    }
  | { kind: "window"; shotId: string; index: number; characterId?: string }
  | { kind: "tool"; id: "recast" };

type EditorState = {
  movieId: string;
  selection: Target;
  playhead: number;
  zoom: number;
  snap: boolean;
  tool: "select" | "razor";
  setTool: (tool: "select" | "razor") => void;
  sourceId: string;
  sourceBeforeHighlight: string | null;
  sourcePoster: SourcePoster | undefined;
  sourceHover: SourceHighlight | undefined;
  sourceFocus: SourceHighlight | undefined;
  highlightSource: (
    owner: string,
    id: string,
    poster: SourcePoster | undefined,
    interaction: "focus" | "hover",
  ) => void;
  chooseMovie: (id: string) => void;
  select: (target: Target) => void;
  seek: (frame: number) => void;
  setZoom: (zoom: number) => void;
  setSnap: (value: boolean) => void;
  source: (id: string) => void;
};
export const useEditor = create<EditorState>((set) => ({
  movieId: "",
  selection: { kind: "timeline" },
  playhead: 0,
  zoom: 1,
  snap: true,
  tool: "select",
  setTool: (tool) => set({ tool }),
  sourceId: "",
  sourceBeforeHighlight: null,
  sourcePoster: undefined,
  sourceHover: undefined,
  sourceFocus: undefined,
  highlightSource: (owner, sourceId, poster, interaction) =>
    set((old) => {
      const key = interaction === "focus" ? "sourceFocus" : "sourceHover";
      if (!poster && old[key]?.owner !== owner) return old;
      const update = {
        ...old,
        [key]: poster ? { owner, sourceId, poster } : undefined,
        ...(interaction === "focus" && poster
          ? { sourceHover: undefined }
          : {}),
      };
      const active = update.sourceHover ?? update.sourceFocus;
      const previous = old.sourceBeforeHighlight ?? old.sourceId;
      return {
        sourceHover: update.sourceHover,
        sourceFocus: update.sourceFocus,
        sourceId: active?.sourceId ?? previous,
        sourceBeforeHighlight: active ? previous : null,
        sourcePoster: active?.poster,
      };
    }),
  chooseMovie: (movieId) =>
    set({
      movieId,
      selection: { kind: "timeline" },
      playhead: 0,
      sourceId: "",
      sourceBeforeHighlight: null,
      sourcePoster: undefined,
      sourceHover: undefined,
      sourceFocus: undefined,
      tool: "select",
    }),
  select: (selection) => set({ selection }),
  seek: (playhead) => set({ playhead: Math.max(0, Math.round(playhead)) }),
  setZoom: (zoom) => set({ zoom: Math.max(0.5, Math.min(12, zoom)) }),
  setSnap: (snap) => set({ snap }),
  source: (sourceId) =>
    set({
      sourceId,
      sourceBeforeHighlight: null,
      sourcePoster: undefined,
      sourceHover: undefined,
      sourceFocus: undefined,
    }),
}));

/** Timecode at the document's frame rate (24 fps for movies). */
export function timecode(frame: number, fps = 24) {
  return frameTimecode(Math.max(0, Math.floor(frame)), fps);
}
