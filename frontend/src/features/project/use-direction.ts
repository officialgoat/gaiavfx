import { useEffect, useState } from "react";
import {
  useQueries,
  useQuery,
  useQueryClient,
  type Query,
} from "@tanstack/react-query";
import { direction, type Receipt, type Schema, type Segment } from "./client";
import { removeCut, splitSegment } from "./edits";
import { splitBlocker } from "./movie-model";
import { useEditor, type Target } from "../timeline/editor-state";
import { useNavigator } from "../../app/navigator";
import { retryAfterRefresh, useEditRunner } from "../../shared/edit-runner";

export function useDirection(enabled: boolean) {
  const editor = useEditor();
  const openDocument = useNavigator((state) => state.document);
  const client = useQueryClient();
  const runner = useEditRunner(editor.movieId, {
    failed: "The edit could not be saved.",
    conflict: retryAfterRefresh,
  });
  const [removeCutId, setRemoveCutId] = useState<string | null>(null);
  const [splitRequest, setSplitRequest] = useState<{
    segment: Segment;
    frame: number;
  } | null>(null);
  const movies = useQuery({
    queryKey: ["direction", "movies"],
    queryFn: direction.movies,
    enabled,
  });
  useEffect(() => {
    if (!editor.movieId && movies.data?.length)
      editor.chooseMovie(movies.data[0].id);
  }, [movies.data, editor.movieId, editor.chooseMovie]);
  useEffect(() => {
    setSplitRequest(null);
    setRemoveCutId(null);
  }, [editor.movieId]);
  // The movie as Python reads it, in one read (application/movie_read.py): plans, pins, selections, history and
  // what Undo and Redo reverse next. A stage built from a take moves through its fill rounds without settling: the
  // References row reads its words again while it builds.
  const read = useQuery({
    queryKey: ["direction", "read", editor.movieId],
    queryFn: () => direction.read(editor.movieId),
    enabled: enabled && !!editor.movieId,
    refetchInterval: (query: Query<Schema["MovieReadDTO"]>) =>
      Object.values(query.state.data?.segment_plans ?? {}).some(
        (plan) => plan.seed?.state === "building",
      )
        ? 2500
        : false,
  });
  const movie = {
    data: read.data?.movie,
    isPending: read.isPending,
    error: read.error,
  };
  const places = useQuery({
    queryKey: ["direction", "places"],
    queryFn: direction.places,
    enabled,
  });
  const angleQueries = useQueries({
    queries: (places.data ?? []).map((p) => ({
      queryKey: ["direction", "angles", p.id],
      queryFn: () => direction.angles(p.id),
    })),
  });
  const angles = angleQueries.flatMap((q) => q.data ?? []);
  const segments = movie.data?.segments ?? [];
  const cuts = segments.flatMap((s) => s.cuts);
  const splitPointMap = new Map(
    segments.map((s) => [
      s.id,
      { isError: read.isError, data: read.data?.split_points[s.id] },
    ]),
  );
  const splitReason = (segment: Segment | undefined, frame: number) =>
    segment
      ? splitBlocker(splitPointMap.get(segment.id), frame)
      : "Select a segment first.";
  const requestSplit = (segment: Segment, frame: number) => {
    if (!runner.busy && !splitReason(segment, frame))
      setSplitRequest({ segment, frame });
  };
  const segmentPlanMap = new Map(
    segments.map((s) => [s.id, read.data?.segment_plans[s.id]]),
  );
  // A cut's plan is inside its segment's: no cut is planned twice.
  const planMap = new Map(
    Object.values(read.data?.segment_plans ?? {}).flatMap((plan) =>
      plan.cuts.map((cut) => [cut.cut_id, cut] as const),
    ),
  );
  const pinMap = new Map(
    cuts.map((c) => [c.id, read.data?.keyframes[c.id] ?? []]),
  );
  const selectionMap = new Map(
    segments.map((s) => [s.id, read.data?.selections[s.id]?.current]),
  );
  const receipts = { data: read.data?.receipts };
  const starts = new Map<string, number>();
  let duration = 0;
  for (const segment of segments) {
    starts.set(segment.id, duration);
    duration += segment.delivered_frames;
  }
  // With a film open nothing resolves a movie segment, cut or phase (ADR 0017 §11): no key, menu or effect can then
  // act on the movie that stays loaded behind the film.
  const film = openDocument.kind === "film";
  const getSegment = (target: Target = editor.selection) =>
    film
      ? undefined
      : "segmentId" in target
        ? segments.find((s) => s.id === target.segmentId)
        : (segments.find(
            (s) =>
              editor.playhead >= starts.get(s.id)! &&
              editor.playhead < starts.get(s.id)! + s.delivered_frames,
          ) ?? segments[0]);
  const getCut = (target: Target = editor.selection) =>
    film
      ? undefined
      : cuts.find(
          (c) =>
            (target.kind === "cut" && c.id === target.id) ||
            ((target.kind === "keyframe" || target.kind === "binding") &&
              c.id === target.cutId),
        );
  const getPhase = (target: Target = editor.selection) =>
    film
      ? undefined
      : segments
          .flatMap((s) => s.phases)
          .find((p) => target.kind === "phase" && p.id === target.id);
  const refresh = () => client.invalidateQueries({ queryKey: ["direction"] });
  async function perform<T>(
    action: () => Promise<T>,
    label: string,
  ): Promise<T | undefined> {
    const outcome = await runner.run(action, {
      notice: label,
      done: refresh,
      failed: refresh,
    });
    return outcome.ok ? outcome.value : undefined;
  }
  // Python says what Undo and Redo reverse next; undoing an Undo redoes, so a reload keeps the history.
  const targets = read.data?.undo;
  const latest = receipts.data?.find((r) => r.id === targets?.undo);
  async function undo(receipt: Receipt | undefined = latest) {
    if (receipt)
      await perform(() => direction.undo(receipt.id), "Change undone");
  }
  async function redo() {
    const id = targets?.redo;
    if (id) await perform(() => direction.undo(id), "Change redone");
  }
  return {
    editor,
    movies,
    movie,
    places,
    angles,
    segments,
    cuts,
    planMap,
    segmentPlanMap,
    splitPointMap,
    splitReason,
    requestSplit,
    splitRequest,
    removeCutId,
    requestRemoveCut: (id: string) => {
      if (runner.locked()) return;
      runner.clearError();
      setRemoveCutId(id);
    },
    cancelRemoveCut: () => setRemoveCutId(null),
    confirmRemoveCut: async () => {
      const cut = cuts.find((c) => c.id === removeCutId);
      const segment = segments.find((s) => s.id === cut?.segment_id);
      if (!cut || !segment) return;
      const result = await removeCut({ perform }, segment, cut);
      if (result) {
        setRemoveCutId(null);
        editor.select({
          kind: "segment",
          id: segment.id,
          segmentId: segment.id,
        });
      }
    },
    cancelSplit: () => setSplitRequest(null),
    confirmSplit: async () => {
      if (!splitRequest) return;
      const { segment, frame } = splitRequest;
      const result = await splitSegment({ perform }, segment, frame);
      setSplitRequest(null);
      if (result) {
        const next = result.segments[1];
        editor.select({ kind: "segment", id: next.id, segmentId: next.id });
        editor.seek((starts.get(segment.id) ?? 0) + frame);
      }
    },
    pinMap,
    selectionMap,
    receipts,
    latest,
    canRedo: !!targets?.redo,
    /** Where each cut's pin sits and how many identity lanes the References row needs, from Python. */
    pinOffsets: read.data?.pin_offsets ?? {},
    identityLanes: read.data?.identity_lanes ?? 0,
    starts,
    duration,
    getSegment,
    getCut,
    getPhase,
    busy: runner.busy,
    error: runner.error,
    notice: runner.notice,
    perform,
    /** Say something that is not an edit's outcome, in the same place an edit's notice shows. */
    tell: runner.showNotice,
    undo,
    redo,
    refresh,
    clearError: runner.clearError,
    readError:
      read.error ?? places.error ?? angleQueries.find((q) => q.error)?.error,
  };
}
export type DirectController = ReturnType<typeof useDirection>;
