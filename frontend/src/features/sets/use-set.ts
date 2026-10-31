import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useMemo } from "react";
import { useEditRunner } from "../../shared/edit-runner";
import { BUILDING, sets, type SetState } from "./client";
import { decodeMap } from "./set-map";

/** One open set: its state for the shown version, the decoded map, and the human commands, each returning the new
 * state (the server's answer replaces the cache, so the page never shows a guess). The commands run one at a time
 * through the shared edit runner; a refusal says why in Python's words. While a build is on its way the state is read
 * again every two seconds; nothing it brings moves the camera or switches the version. */
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
  const runner = useEditRunner(setId, {
    failed: "That did not work. Try again.",
    conflict: (message) => message,
  });
  function land(next: SetState) {
    // An answer can show another version (Retry build answers with its base): it belongs under that version.
    client.setQueryData(["set", setId, next.version.id], next);
    if (versionId === null || versionId === next.version.id)
      client.setQueryData(key, next);
    else void client.invalidateQueries({ queryKey: key, exact: true });
    void client.invalidateQueries({ queryKey: ["sets"] });
  }
  return {
    state: state.data ?? null,
    loading: state.isPending,
    loadError: state.error instanceof Error ? state.error.message : "",
    map,
    error: runner.error,
    clearError: runner.clearError,
    busy: runner.busy,
    /** The new state, or null when the command was refused or another is still running. */
    run: async (call: () => Promise<SetState>) => {
      const result = await runner.run(async () => {
        const next = await call();
        land(next);
        return next;
      });
      return result.ok ? result.value : null;
    },
  };
}

export type SetController = ReturnType<typeof useSet>;
