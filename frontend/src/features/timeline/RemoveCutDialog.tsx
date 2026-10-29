import { Sheet } from "../../shared/ui";
import type { DirectController } from "../project/use-direction";

export function RemoveCutDialog({ d }: { d: DirectController }) {
  const cut = d.cuts.find((c) => c.id === d.removeCutId);
  const segment = d.segments.find((s) => s.id === cut?.segment_id);
  return (
    <Sheet
      title="Remove cut"
      description="Remove the camera change. Authored phases stay in the segment. Undo restores the cut."
      open={!!d.removeCutId}
      onOpenChange={(open) => {
        if (!open && !d.busy) d.cancelRemoveCut();
      }}
    >
      <div className="property-form">
        {cut && segment ? (
          <p>
            Remove the cut at frame {cut.frame} in Segment{" "}
            {String(segment.ordinal + 1).padStart(2, "0")}?
          </p>
        ) : (
          <p>This cut is no longer in the movie.</p>
        )}
        {d.error && <p role="alert">{d.error}</p>}
        <div className="split-actions">
          <button
            className="secondary-button"
            disabled={d.busy}
            onClick={d.cancelRemoveCut}
          >
            Cancel
          </button>
          <button
            className="primary-button"
            disabled={d.busy || !cut || !segment}
            onClick={() => void d.confirmRemoveCut()}
          >
            {d.busy ? "Removing…" : "Remove cut"}
          </button>
        </div>
      </div>
    </Sheet>
  );
}
