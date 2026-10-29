import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { BUILDING, sets, type SetState } from "./client";
import { decodeMap } from "./set-map";

/** One open set: its state for the shown version, the decoded map, and the human commands, each returning the new
 * state (the server's answer replaces the cache, so the page never shows a guess). While a build is on its way the
 * state is read again every two seconds; nothing it brings moves the camera or switches the version. */
export function useSet(setId: string, versionId: string | null) {
  const client = useQueryClient();
  const key = ["set", setId, versionId ?? "shown"];
  const state = useQuery({
    queryKey: key,
    queryFn: () => sets.get(setId, versionId ?? undefined),
    // Another version of the same set keeps the page (and its splat and camera) until it arrives.
    placeholderData: (previous) =>
      previous?.id === setId ? keepPreviousData(previous) : undefined,
    // A build's fixes settle a sweep after the build itself does: read again until they have.
    refetchInterval: (query) =>
      BUILDING.has(query.state.data?.build?.state ?? "") ||
      query.state.data?.fixes.some((f) => f.state === "running")
        ? 2000
        : false,
  });
  const shownVersion = state.data?.version.id;
  const mapQuery = useQuery({
    queryKey: ["set-map", setId, shownVersion],
    queryFn: () => sets.map(setId, shownVersion!),
    enabled: !!shownVersion && !!state.data?.version.has_map,
    staleTime: Infinity,
  });
  const map = useMemo(
    () => (mapQuery.data ? decodeMap(mapQuery.data) : null),
    [mapQuery.data],
  );
  const [error, setError] = useState("");
  const command = useMutation({
    mutationFn: (run: () => Promise<SetState>) => run(),
    onMutate: () => setError(""),
    onSuccess: (next) => {
      // An answer can show another version (Retry build answers with its base): it belongs under that version.
      client.setQueryData(["set", setId, next.version.id], next);
      if (versionId === null || versionId === next.version.id)
        client.setQueryData(key, next);
      else void client.invalidateQueries({ queryKey: key, exact: true });
      void client.invalidateQueries({ queryKey: ["sets"] });
    },
    onError: (failure) =>
      setError(
        failure instanceof Error
          ? failure.message
          : "That did not work. Try again.",
      ),
  });
  return {
    state: state.data ?? null,
    loading: state.isPending,
    loadError: state.error instanceof Error ? state.error.message : "",
    map,
    error,
    clearError: () => setError(""),
    busy: command.isPending,
    run: (call: () => Promise<SetState>) =>
      command.mutateAsync(call).catch(() => null),
  };
}

export type SetController = ReturnType<typeof useSet>;
