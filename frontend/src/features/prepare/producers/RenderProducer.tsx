import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { direction, type Segment } from "../../project/client";
import type { ReviewController } from "../../review/use-review";
import { SegmentReadiness } from "../SegmentReadiness";
import { ProducerSheet } from "./ProducerSheet";

/** Render candidates of the whole segment, from its current plan. */
export function RenderProducer({
  r,
  segment,
}: {
  r: ReviewController;
  segment: Segment;
}) {
  const [context, setContext] = useState("");
  const [sheet, setSheet] = useState("");
  const renderPlan = useQuery({
    queryKey: ["direction", "segment-plan", segment.id, context, sheet],
    queryFn: () => direction.segmentPlan(segment.id, context.trim(), sheet),
  });
  const sheets = Array.from(
    new Map(
      [
        ...(renderPlan.data?.opening ? [renderPlan.data.opening] : []),
        ...(renderPlan.data?.cuts ?? []),
      ]
        .flatMap((p) => p.bindings.filter((b) => b.kind === "sheet"))
        .map((b) => [b.asset_id, b]),
    ).values(),
  );
  return (
    <ProducerSheet
      r={r}
      kind="renders"
      title="Render segment"
      description={`Segment ${segment.ordinal + 1}: ${segment.cuts.length ? `${segment.cuts.length} cuts in one window` : "Continuous take"}`}
      formKey={`renders-${segment.id}`}
      seeds="many"
      ready={
        !!renderPlan.data?.ready &&
        !renderPlan.isFetching &&
        !renderPlan.isError
      }
      build={(get, seeds) => ({
        segment_id: segment.id,
        seeds,
        context_artifact: get("context") || null,
        sheet_asset: get("sheet") || null,
      })}
    >
      <SegmentReadiness plan={renderPlan.data} />
      {renderPlan.isError && (
        <p role="alert">
          {renderPlan.error.message}{" "}
          <button type="button" onClick={() => void renderPlan.refetch()}>
            Retry plan
          </button>
        </p>
      )}
      <label>
        Continuation artifact (if continuing)
        <textarea
          name="context"
          value={context}
          onChange={(e) => setContext(e.target.value)}
          rows={2}
        />
      </label>
      <label>
        Place sheet
        <select
          name="sheet"
          value={sheet}
          onChange={(e) => setSheet(e.target.value)}
        >
          <option value="">Resolve from current approved references</option>
          {sheets.map((b, i) => (
            <option value={b.asset_id} key={b.id}>
              Place sheet {i + 1}
            </option>
          ))}
        </select>
      </label>
    </ProducerSheet>
  );
}
