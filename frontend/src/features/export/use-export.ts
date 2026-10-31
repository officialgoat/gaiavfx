import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { route } from "../../shared/routes";
import { useEditRunner } from "../../shared/edit-runner";
import type { DirectController } from "../project/use-direction";

export function useMovieExport(d: DirectController) {
  const movieId = d.editor.movieId;
  const client = useQueryClient();
  const read = useQuery({
    queryKey: ["movie-export", movieId],
    queryFn: () =>
      route("get", "/api/export/movies/{movie_id}", {
        path: { movie_id: movieId },
      }),
    enabled: !!d.movie.data,
    refetchInterval: 1500,
  });
  const [confirmation, setConfirmation] = useState("");
  const [preview, setPreview] = useState({ movieId: "", assetId: "" });
  const runner = useEditRunner(movieId, {
    failed: "Export failed. Try again.",
    conflict: (message) => message,
  });
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
  const active = !!latest && !latest.settled;
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
    await runner.run(action, {
      done: () =>
        Promise.all([
          client.invalidateQueries({ queryKey: ["movie-export", movieId] }),
          client.invalidateQueries({ queryKey: ["operations"] }),
          client.invalidateQueries({ queryKey: ["assets"] }),
        ]),
    });
  }
  return {
    read,
    segments,
    latest,
    active,
    busy: runner.busy,
    stale,
    allowStale,
    readyReason,
    error: runner.error,
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
        await route("post", "/api/export/movies/{movie_id}", {
          path: { movie_id: movieId },
          body: { request_key: intent.current.key, allow_stale: allowStale },
        });
        intent.current = null;
      }),
    cancel: () =>
      void perform(async () => {
        if (active)
          await route(
            "post",
            "/api/production/operations/{operation_id}/cancel",
            { path: { operation_id: latest.id } },
          );
      }),
  };
}

export type MovieExportController = ReturnType<typeof useMovieExport>;
