import { useEffect, useRef, useState } from "react";
import {
  useQueries,
  useQuery,
  useQueryClient,
  type Query,
} from "@tanstack/react-query";
import {
  direction,
  type Receipt,
  type Segment,
  type SegmentPlan,
} from "./client";
import { useEditor, type Target } from "../timeline/editor-state";
import { useOpenDocument } from "../../app/open-document";

export function useDirection(enabled: boolean) {
  const editor = useEditor();
  const openDocument = useOpenDocument((state) => state.document);
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [redoIds, setRedoIds] = useState<string[]>([]);
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
    setRedoIds([]);
    setError("");
    setSplitRequest(null);
    setRemoveCutId(null);
  }, [editor.movieId]);
  const movie = useQuery({
    queryKey: ["direction", "movie", editor.movieId],
    queryFn: () => direction.movie(editor.movieId),
    enabled: enabled && !!editor.movieId,
  });
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
  const splitPoints = useQueries({
    queries: segments.map((s) => ({
      queryKey: ["direction", "split-points", s.id],
      queryFn: () => direction.splitPoints(s.id),
    })),
  });
  const splitPointMap = new Map(segments.map((s, i) => [s.id, splitPoints[i]]));
  const splitReason = (segment: Segment | undefined, frame: number) => {
    if (!segment) return "Select a segment first.";
    const query = splitPointMap.get(segment.id);
    if (query?.isError)
      return "Split points could not be loaded. Refresh the movie and retry.";
    if (!query?.data) return "Loading split points…";
    const point = query.data.find((p) => p.frame === frame);
    return point
      ? (point.reason ?? undefined)
      : "Split at a cut or a phase boundary";
  };
  const requestSplit = (segment: Segment, frame: number) => {
    if (!busy && !splitReason(segment, frame))
      setSplitRequest({ segment, frame });
  };
  const plans = useQueries({
    queries: cuts.map((c) => ({
      queryKey: ["direction", "plan", c.id],
      queryFn: () => direction.plan(c.id),
    })),
  });
  const segmentPlans = useQueries({
    queries: segments.map((s) => ({
      queryKey: ["direction", "segment-plan", s.id, "", ""],
      queryFn: () => direction.segmentPlan(s.id),
      // A stage built from the take moves through its fill rounds without settling: the References row reads its
      // words again while it builds.
      refetchInterval: (query: Query<SegmentPlan>) =>
        query.state.data?.seed?.state === "building" ? 2500 : false,
    })),
  });
  const segmentPlanMap = new Map(
    segments.map((s, i) => [s.id, segmentPlans[i]?.data]),
  );
  const pins = useQueries({
    queries: cuts.map((c) => ({
      queryKey: ["direction", "keyframes", c.id],
      queryFn: () => direction.keyframes(c.id),
    })),
  });
  const selections = useQueries({
    queries: segments.map((s) => ({
      queryKey: ["direction", "selection", s.id],
      queryFn: () => direction.selections(s.movie_id, s.id),
    })),
  });
  const receipts = useQuery({
    queryKey: ["direction", "receipts", editor.movieId],
    queryFn: () => direction.receipts(editor.movieId),
    enabled: enabled && !!editor.movieId,
  });
  const planMap = new Map(cuts.map((c, i) => [c.id, plans[i]?.data]));
  const pinMap = new Map(cuts.map((c, i) => [c.id, pins[i]?.data ?? []]));
  const selectionMap = new Map(
    segments.map((s, i) => [s.id, selections[i]?.data?.current]),
  );
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
    preserveRedo = false,
  ): Promise<T | undefined> {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await action();
      if (!preserveRedo) setRedoIds([]);
      await refresh();
      setNotice(label);
      return result;
    } catch (e) {
      setError(
        `${e instanceof Error ? e.message : "The edit could not be saved."} Review the refreshed details, then retry your edit.`,
      );
      await refresh();
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  // The API owns revision guards and inverse commands; undoing the inverse is redo.
  const latest = receipts.data?.find(
    (r) =>
      !r.undone_at && !redoIds.includes(r.id) && r.command !== "create_movie",
  );
  async function undo(receipt: Receipt | undefined = latest) {
    if (!receipt) return;
    const inverse = await perform(
      () => direction.undo(receipt.id),
      "Change undone",
      true,
    );
    if (inverse) setRedoIds((ids) => [...ids, inverse.id]);
  }
  async function redo() {
    const id = redoIds.at(-1);
    if (!id) return;
    const inverse = await perform(
      () => direction.undo(id),
      "Change redone",
      true,
    );
    if (inverse) setRedoIds((ids) => ids.slice(0, -1));
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
      if (lock.current) return;
      setError("");
      setRemoveCutId(id);
    },
    cancelRemoveCut: () => setRemoveCutId(null),
    confirmRemoveCut: async () => {
      const cut = cuts.find((c) => c.id === removeCutId);
      const segment = segments.find((s) => s.id === cut?.segment_id);
      if (!cut || !segment) return;
      const result = await perform(
        () =>
          direction.removeCut(cut.id, { expected_revision: segment.revision }),
        "Cut removed. Undo restores it.",
      );
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
      const result = await perform(
        () =>
          direction.splitSegment(segment.id, {
            expected_revision: segment.revision,
            frame,
          }),
        "Segment split",
      );
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
    canRedo: !!redoIds.length,
    starts,
    duration,
    getSegment,
    getCut,
    getPhase,
    busy,
    error,
    notice,
    perform,
    undo,
    redo,
    refresh,
    clearError: () => setError(""),
    readError:
      movie.error ??
      places.error ??
      plans.find((q) => q.error)?.error ??
      segmentPlans.find((q) => q.error)?.error ??
      pins.find((q) => q.error)?.error ??
      selections.find((q) => q.error)?.error ??
      angleQueries.find((q) => q.error)?.error,
  };
}
export type DirectController = ReturnType<typeof useDirection>;
