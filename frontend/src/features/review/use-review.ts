import { useEffect, useRef, useState } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { frameRate } from "../../shared/frame-rate";
import { direction } from "../project/client";
import type { DirectController } from "../project/use-direction";
import { preparationShot } from "../prepare/shot";
import type { Operation } from "../generation/client";
import {
  production,
  producerRoute,
  rankingDraft,
  type Producer,
  type ProducerBody,
  type Ranking,
  type Review,
} from "./client";

export function useReview(
  d: DirectController,
  workspace: (name: string) => void,
) {
  const client = useQueryClient();
  const [id, setId] = useState("");
  // The shot whose references are under review in Prepare: a cut's id, or a segment's for its first shot.
  const [preparationShotId, setPreparationShotId] = useState("");
  const [candidate, setCandidate] = useState("");
  const [draft, setDraft] = useState<Ranking | null>(null);
  const [revealed, setRevealed] = useState<Review | null>(null);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const activeMovie = useRef(d.editor.movieId);
  activeMovie.current = d.editor.movieId;
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [rejectionOpen, setRejectionOpen] = useState(false);
  const [replacement, setReplacement] = useState<{
    candidateId: string;
    currentAsset: string;
    candidateAsset: string;
  } | null>(null);
  const [producer, setProducer] = useState<Producer | null>(null);
  const [lastOperation, setLastOperation] = useState("");
  const review = useQuery({
    queryKey: ["review", id],
    queryFn: () => production.review(id),
    enabled: !!id,
    refetchInterval: 5000,
  });
  const tags = useQuery({
    queryKey: ["review-tags"],
    queryFn: production.tags,
    enabled: !!id,
  });
  const value = review.data;
  const sourceFacts = value?.context;
  const sourceFps = frameRate(sourceFacts?.fps);
  const sourceRange =
    sourceFacts &&
    typeof sourceFacts.shot_start === "number" &&
    typeof sourceFacts.shot_end === "number" &&
    sourceFps > 0
      ? {
          start: sourceFacts.shot_start / sourceFps,
          duration: (sourceFacts.shot_end - sourceFacts.shot_start) / sourceFps,
        }
      : null;
  const approvalReads = useQueries({
    queries: d.angles
      .filter((a) => a.place_id === value?.context.place_id)
      .map((a) => ({
        queryKey: ["direction", "evidence", a.id],
        queryFn: () => direction.evidence(a.id),
      })),
  });
  const approvedAssets = new Set([
    ...approvalReads
      .flatMap((q) => q.data ?? [])
      .filter((e) => e.approved)
      .map((e) => e.asset_id),
    ...(d.places.data?.flatMap((p) =>
      p.plate_asset_id ? [p.plate_asset_id] : [],
    ) ?? []),
    ...[...d.pinMap.values()]
      .flat()
      .filter((p) => p.approved)
      .map((p) => p.asset_id),
  ]);
  const movieId =
    typeof value?.context.movie_id === "string" ? value.context.movie_id : "";
  const segmentId =
    typeof value?.context.segment_id === "string"
      ? value.context.segment_id
      : "";
  const selection = useQuery({
    queryKey: ["direction", "selection", segmentId],
    queryFn: () => direction.selections(movieId, segmentId),
    enabled: !!movieId && !!segmentId,
  });
  useEffect(() => {
    setId("");
    setPreparationShotId("");
    setRejectionOpen(false);
    setCandidate("");
    setDraft(null);
    setRevealed(null);
    setProducer(null);
  }, [d.editor.movieId]);
  useEffect(() => {
    setReplacement(null);
  }, [id, d.editor.movieId]);
  useEffect(() => {
    if (!value) return;
    setDraft((old) => old ?? rankingDraft(value));
    setCandidate((old) =>
      value.members.some((m) => m.candidate_id === old)
        ? old
        : (value.members[0]?.candidate_id ?? ""),
    );
    // Newly added members revoke reveal immediately, while an unsaved ranking stays editable.
    setRevealed((old) => (old?.revision === value.revision ? old : null));
  }, [value]);
  async function perform<T>(
    action: () => Promise<T>,
    message: string,
  ): Promise<T | undefined> {
    if (lock.current) return;
    const movie = activeMovie.current;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await action();
      await Promise.all([
        client.invalidateQueries({ queryKey: ["review"] }),
        client.invalidateQueries({ queryKey: ["operations"] }),
        d.refresh(),
        client.invalidateQueries({ queryKey: ["chat"] }),
      ]);
      if (movie !== activeMovie.current) return undefined;
      setNotice(message);
      return result;
    } catch (e) {
      if (movie === activeMovie.current)
        setError(
          `${e instanceof Error ? e.message : "The action could not be saved."} Refresh and try again.`,
        );
      return undefined;
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  function activate(data: Review) {
    setReplacement(null);
    setRejectionOpen(false);
    client.setQueryData(["review", data.id], data);
    setId(data.id);
    setDraft(rankingDraft(data));
    setRevealed(null);
    setCandidate(data.members[0]?.candidate_id ?? "");
    if (data.purpose === "character_replacement") {
      setPreparationShotId("");
      workspace("review");
      return;
    }
    if (data.purpose === "segment_takes") {
      const segment = d.segments.find((s) => s.id === data.context.segment_id);
      if (segment)
        d.editor.select({
          kind: "segment",
          id: segment.id,
          segmentId: segment.id,
        });
      setPreparationShotId("");
      workspace("review");
      return;
    }
    // A pin's review belongs to its cut; a place's views to the shot being prepared when it shows that place, else
    // to the first cut there.
    const selected = preparationShot(d);
    const ownsPlace = (angleId: string) =>
      d.angles.some(
        (a) => a.id === angleId && a.place_id === data.context.place_id,
      );
    const cut = data.context.cut_id
      ? d.cuts.find((c) => c.id === data.context.cut_id)
      : selected && ownsPlace(selected.angleId)
        ? undefined
        : d.cuts.find((c) => ownsPlace(c.to_angle_id));
    if (cut) {
      setPreparationShotId(cut.id);
      d.editor.select({ kind: "cut", id: cut.id, segmentId: cut.segment_id });
    } else setPreparationShotId(selected?.id ?? "");
    workspace(data.purpose === "segment_takes" ? "review" : "prepare");
  }
  async function openOperation(operation: Operation | string) {
    const data = await perform(async () => {
      const value =
        typeof operation === "string"
          ? await production.operationSummary(operation)
          : operation;
      const route = producerRoute[value.kind];
      if (!route || route === "sheets")
        throw new Error("This operation has no candidate review.");
      return production.open(route, value.id);
    }, "Review opened");
    if (data) activate(data);
  }
  async function openReview(reviewId: string) {
    const data = await perform(
      () => production.review(reviewId),
      "Review opened",
    );
    if (data) activate(data);
  }
  async function save() {
    if (!draft || !value) return;
    const saved = await perform(
      () => production.rank(value.id, draft),
      "Ranking saved",
    );
    if (saved) setDraft({ ...draft, expected_revision: saved.revision });
  }
  async function approve(candidateId = candidate, replace = false) {
    if (!value) return;
    const current = d.pinMap
      .get(String(value.context.cut_id))
      ?.find((p) => p.approved);
    const next = value.members.find((m) => m.candidate_id === candidateId);
    if (
      value.purpose === "insertion" &&
      current &&
      next &&
      current.asset_id !== next.asset_id
    ) {
      if (!replace || replacement?.currentAsset !== current.asset_id) {
        setCandidate(candidateId);
        setReplacement({
          candidateId,
          currentAsset: current.asset_id,
          candidateAsset: next.asset_id,
        });
        return;
      }
    }
    // Approval is distinct from ranking: approving one survey view must not judge its siblings.
    const saved = await perform(
      () => production.approve(value.purpose, candidateId, replace),
      replace
        ? "Pin replaced. Previous pin kept in history; Undo restores it."
        : value.purpose === "character_replacement"
          ? "Replacement approved as a derivative in Library"
          : "Reference approved",
    );
    if (saved) setReplacement(null);
  }
  async function reject(note: string) {
    if (!value) return;
    const saved = await perform(
      () =>
        production.reject(value.id, {
          expected_revision: value.revision,
          note,
        }),
      "Reference candidates rejected",
    );
    if (saved) setRejectionOpen(false);
  }
  async function start(kind: Producer, body: ProducerBody) {
    const operation = await perform(
      () => production.start(kind, body),
      "Operation ready in Jobs",
    );
    if (operation) {
      setLastOperation(operation.id);
      setProducer(null);
      setNotice(
        `Operation ${operation.id.slice(0, 8)} · ${operation.state.replaceAll("_", " ")}. Follow progress in Jobs.`,
      );
    }
    return !!operation;
  }
  const member = value?.members.find((m) => m.candidate_id === candidate);
  const dirty =
    !!value &&
    !!draft &&
    JSON.stringify(draft) !== JSON.stringify(rankingDraft(value));
  return {
    id,
    sourceRange,
    preparationShotId,
    value,
    review,
    tags,
    candidate,
    setCandidate: (next: string) => {
      setReplacement(null);
      setCandidate(next);
    },
    member,
    draft,
    setDraft,
    revealed:
      revealed?.revision === value?.revision && !value?.unranked.length
        ? revealed
        : null,
    busy,
    error,
    notice,
    producer,
    setProducer,
    lastOperation,
    perform,
    openOperation,
    openReview,
    save,
    approve,
    replacement,
    cancelReplacement: () => setReplacement(null),
    confirmReplacement: () =>
      replacement && approve(replacement.candidateId, true),
    reject,
    rejectionOpen,
    setRejectionOpen,
    start,
    selection,
    dirty,
    approvedAssets,
    close: () => {
      setReplacement(null);
      setId("");
      setDraft(null);
      setRevealed(null);
    },
    refresh: async () => {
      const read = await review.refetch();
      if (read.data) setDraft(rankingDraft(read.data));
      setError("");
    },
    reveal: async () => {
      if (!value) return;
      const data = await perform(
        () => production.reveal(value.id),
        "Generation details revealed",
      );
      if (data) setRevealed(data);
    },
    select: async (candidateId = candidate) => {
      if (!value || !selection.data) return;
      await perform(
        () =>
          production.select(value.id, {
            candidate_id: candidateId,
            expected_revision: selection.data.revision,
          }),
        "Take selected for the segment",
      );
    },
  };
}
export type ReviewController = ReturnType<typeof useReview>;
