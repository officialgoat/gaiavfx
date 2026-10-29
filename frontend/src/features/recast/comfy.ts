import { useQuery } from "@tanstack/react-query";
import { studio } from "../generation/client";

/** ComfyUI's status, checked every 20 s; every caller shares one react-query cache. */
export function useComfyStatus() {
  return useQuery({
    queryKey: ["comfy-status"],
    queryFn: studio.comfy,
    refetchInterval: 20_000,
    retry: false,
  });
}

/** True only when ComfyUI answered that it is not connected (unknown is not offline). */
export const useComfyOffline = () => useComfyStatus().data?.connected === false;
