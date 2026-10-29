import { useQuery } from "@tanstack/react-query";
import { frameRate } from "../../shared/frame-rate";
import { ReplacementPreview } from "../prepare/ReplacementPreview";
import { shotPoster } from "../prepare/shot-poster";
import { production } from "../review/client";

export function ReplacementPlanPreview({
  operation,
  renders,
}: {
  operation?: string;
  renders: number;
}) {
  const preview = useQuery({
    queryKey: ["recorded-replacement-plan", operation],
    queryFn: () => production.recordedReplacementPlan(operation!),
    enabled: !!operation,
    staleTime: Infinity,
  });
  const facts = preview.data?.facts;
  const poster = useQuery({
    queryKey: [
      "shot-poster",
      facts?.source_asset,
      facts?.shot_start,
      facts?.fps,
    ],
    queryFn: () =>
      shotPoster(facts!.source_asset, facts!.shot_start / frameRate(facts!.fps)),
    enabled: !!facts,
    staleTime: Infinity,
  });
  if (preview.error || poster.error)
    return (
      <p role="alert">
        {preview.error?.message ?? poster.error?.message}
        <button
          onClick={() => {
            if (operation) void preview.refetch();
            if (facts) void poster.refetch();
          }}
        >
          Retry crop preview
        </button>
      </p>
    );
  return preview.data ? (
    <ReplacementPreview
      plan={preview.data}
      posterUrl={poster.data}
      renders={renders}
    />
  ) : (
    <p className="muted">
      {operation
        ? "Loading crop preview..."
        : "Crop preview awaits recorded performer and cast choices."}{" "}
      {renders} renders planned.
    </p>
  );
}
