import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "../../shared/api.generated";
import { route } from "../../shared/routes";

export type SetupStatus = components["schemas"]["SetupStatusDTO"];
export type SetupCheck = components["schemas"]["Check"];

export const featureNames: Record<string, string> = {
  core: "Core video",
  "image-edit": "Image edit",
  recast: "Recast",
  stages: "Stages",
  panoramas: "Panoramas (non-commercial models)",
  runtime: "Agent and tools",
};

export const isGap = (check: SetupCheck) =>
  ["missing", "mismatch", "unknown"].includes(check.status);

export function useSetup() {
  const client = useQueryClient();
  const queryKey = ["setup-status"];
  const query = useQuery({
    queryKey,
    queryFn: () => route("get", "/api/v1/setup/status"),
    staleTime: 15_000,
    refetchInterval: 20_000,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const checkAgain = () =>
    client.fetchQuery({
      queryKey,
      queryFn: () =>
        route("get", "/api/v1/setup/status", { query: { refresh: true } }),
      staleTime: 0,
    });
  return { ...query, checkAgain };
}
