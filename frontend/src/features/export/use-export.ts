import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "../../shared/api";
import type { components } from "../../shared/api.generated";
import type { DirectController } from "../project/use-direction";

type Schema = components["schemas"];
const terminal = [
  "succeeded",
  "failed",
  "cancelled",
  "partial",
  "needs_attention",
];

export function useMovieExport(d: DirectController) {
  const movieId = d.editor.movieId;
  const client = useQueryClient();
  const read = useQuery({
    queryKey: ["movie-export", movieId],
    queryFn: () =>
      request<Schema["MovieExportDTO"]>(`/api/export/movies/${movieId}`),
    enabled: !!d.movie.data,
    refetchInterval: 1500,
  });
  const [confirmation, setConfirmation] = useState("");
  const [preview, setPreview] = useState({ movieId: "", assetId: "" });
  const [error, setError] = useState({ movieId: "", text: "" });
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const intent = useRef<{
    movieId: string;
    allow: boolean;
    key: string;
  } | null>(null);
  const segments = read.data?.segments ?? [];
  const confirmationKey = JSON.stringify([
    movieId,
    read.data?.movie_revision,
    segments,
  ]);
  const allowStale = confirmation === confirmationKey;
  const stale = segments.some((s) => s.stale_reasons.length > 0);
  const latest = read.data?.latest;
  const active = !!latest && !terminal.includes(latest.state);
  const readyReason = !movieId
    ? "Open a movie first."
    : read.isPending
      ? "Checking selected takes…"
      : read.isError
        ? "Refresh the selected takes before exporting."
        : !segments.length
          ? "Add a segment first."
          : segments.some((s) => !s.selection_id)
            ? "Select a take for every segment."
            : undefined;

  async function perform(action: () => Promise<unknown>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError({ movieId, text: "" });
    try {
      await action();
      await Promise.all([
        client.invalidateQueries({ queryKey: ["movie-export", movieId] }),
        client.invalidateQueries({ queryKey: ["operations"] }),
        client.invalidateQueries({ queryKey: ["assets"] }),
      ]);
    } catch (e) {
      setError({
        movieId,
        text: e instanceof Error ? e.message : "Export failed. Try again.",
      });
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  return {
    read,
    segments,
    latest,
    active,
    busy,
    stale,
    allowStale,
    readyReason,
    error: error.movieId === movieId ? error.text : "",
    previewId: preview.movieId === movieId ? preview.assetId : "",
    confirm: () => setConfirmation(allowStale ? "" : confirmationKey),
    preview: (assetId: string) => setPreview({ movieId, assetId }),
    refresh: () => void Promise.all([read.refetch(), d.refresh()]),
    start: () =>
      void perform(async () => {
        if (readyReason || active || (stale && !allowStale)) return;
        if (
          !intent.current ||
          intent.current.movieId !== movieId ||
          intent.current.allow !== allowStale
        )
          intent.current = {
            movieId,
            allow: allowStale,
            key: crypto.randomUUID(),
          };
        await request<Schema["OperationDTO"]>(`/api/export/movies/${movieId}`, {
          method: "POST",
          body: JSON.stringify({
            request_key: intent.current.key,
            allow_stale: allowStale,
          }),
        });
        intent.current = null;
      }),
    cancel: () =>
      void perform(async () => {
        if (active)
          await request(`/api/production/operations/${latest.id}/cancel`, {
            method: "POST",
          });
      }),
  };
}

export type MovieExportController = ReturnType<typeof useMovieExport>;
