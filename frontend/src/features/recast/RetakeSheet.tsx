import { Sheet } from "../../shared/ui";
import { shotNumber } from "./film-doc";
import { useFilmEditor } from "./film-editor";
import type { RecastController } from "./use-recast";

/** Retake asks first, naming the work it reserves (usability round 2, C9): one key spent renders at once. The primary
 * button has the focus, so Enter confirms and Escape keeps the shot as it is. */
export function RetakeSheet({ r }: { r: RecastController }) {
  const dialog = useFilmEditor((s) => s.dialog);
  const close = () => useFilmEditor.getState().openDialog(null);
  const shot =
    dialog?.kind === "retake"
      ? r.value?.shots.find((s) => s.id === dialog.shotId)
      : undefined;
  const windows = shot?.plan?.windows.length ?? 0;
  const renders = windows * (shot?.plan?.renders_per_window ?? 0);
  const left = r.value
    ? r.value.run.render_budget - r.value.run.renders_used
    : 0;
  return (
    <Sheet
      open={!!shot}
      onOpenChange={(open) => !open && close()}
      title="Retake"
      className="film-cast-sheet"
      description={
        shot
          ? `Render Shot ${shotNumber(shot)} again in ${r.mode === "final" ? "Final" : "Draft"} mode: ${windows} ${windows === 1 ? "window" : "windows"}, up to ${renders} ${renders === 1 ? "render" : "renders"} of the ${left} left. Its versions so far stay.`
          : ""
      }
    >
      <div className="film-card-actions">
        <button
          type="button"
          className="primary-button"
          autoFocus
          disabled={r.busy}
          onClick={() => {
            close();
            void r.retake();
          }}
        >
          Retake
        </button>
        <button type="button" onClick={close}>
          Cancel
        </button>
      </div>
    </Sheet>
  );
}
