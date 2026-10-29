import { TriangleAlert } from "lucide-react";
import { useComfyOffline } from "./comfy";
import { CommandButton } from "../shell/Menus";
import { recasts, type Recast } from "./client";
import type { RecastController } from "./use-recast";

/** A failure in words: a refused connection means ComfyUI is not running; anything else is shown as it came. */
export function readable(error: string) {
  return /10061|refused|ECONNREFUSED|failed to establish|connection error/i.test(
    error,
  )
    ? "ComfyUI is not reachable. Start it, or check its address in Settings, then retry."
    : error;
}

/** ComfyUI out of reach (usability round 1, B4): face checks, analysis and renders wait for it without an error of
 * their own, so the film names the cause once, above the timeline, and points to Settings. Checked every 20 s. */
export function ComfyBanner() {
  // No raw error in a tooltip: it carried "[WinError 10061]…" (usability round 2, C1).
  if (!useComfyOffline()) return null;
  return (
    <p className="film-message warning comfy-offline">
      <TriangleAlert size={13} aria-hidden="true" />
      ComfyUI is not reachable, so face checks, analysis and renders wait until
      it is. Start it, or check its address in Settings.
      <CommandButton id="settings">Open Settings</CommandButton>
    </p>
  );
}

/** The film's analysis while it runs or after it failed; once it succeeded, the Analyse step says so. */
export function AnalysisBanner({
  r,
  value,
}: {
  r: RecastController;
  value: Recast;
}) {
  if (value.analysis.state === "succeeded") return null;
  return (
    <div
      id="film-analysis"
      tabIndex={-1}
      className="film-analysis"
      role="status"
    >
      Analysis: {value.analysis.state.replaceAll("_", " ")} · Shots → Faces →
      Cast · {value.analysis.stage ?? "Waiting"} {value.analysis.done} of{" "}
      {value.analysis.total}
      <progress
        max={Math.max(1, value.analysis.total)}
        value={value.analysis.done}
      />
      {value.analysis.error && (
        <span title={value.analysis.error}>
          {readable(value.analysis.error)}
        </span>
      )}
      {value.analysis.state === "failed" && (
        <button
          type="button"
          disabled={r.busy}
          onClick={() =>
            void r.change(() => recasts.retryAnalysis(r.id, r.revision))
          }
        >
          Retry analysis
        </button>
      )}
    </div>
  );
}
